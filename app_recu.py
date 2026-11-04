import functools
import jwt
import requests
import re
from pathlib import Path
from uuid import uuid4
from flasgger import Flasgger, Swagger
from marshmallow import ValidationError
from modele.recu import Recu, User,Echange
from datetime import datetime, timezone, timedelta
from werkzeug.security import generate_password_hash
from flask import request, jsonify

from schema import recu_schema
from settings import app, db

# Initialisation de Swagger

swagger = Swagger(app, template={
    "swagger": "2.0",
    "info": {
        "title": "ProjetCollectedePoint",
        "version": "1.0",
        "description": "API REST de gestion des reçus et points"
    },
    "securityDefinitions": {
        "Bearer": {
            "type": "apiKey",
            "name": "Authorization",
            "in": "header",
            "description": "Saisir Bearer suivi du jeton JWT"
        }
    }
})

def extraire_informations(texte):
    lignes = [
        ligne.strip()
        for ligne in texte.splitlines()
        if ligne.strip()
    ]
    magasin = None
    date_recu = None
    montant = None
    # 1. Chercher le magasin dans le haut du reçu
    for ligne in lignes[:8]:
        lettres = re.findall(r"[A-Za-zÀ-ÿ]", ligne)
        ligne_a_exclure = re.search(
            r"\d|www|https?://|@|"
            r"\b(bienvenue|merci|tel|tél|téléphone|"
            r"internet|servi|facture|reçu)\b",
            ligne,
            flags=re.IGNORECASE,
        )
        if len(lettres) >= 3 and not ligne_a_exclure:
            magasin = ligne
            break
    # 2. Chercher les dates sans exiger le mot DATE
    candidats_dates = re.findall(
        r"(?<!\d)(?:"
        r"\d{4}[-/]\d{2}[-/]\d{2}"
        r"|"
        r"\d{2}[-/]\d{2}[-/](?:\d{4}|\d{2})"
        r")(?!\d)",
        texte,
    )
    dates_valides = set()
    for candidat in candidats_dates:
        candidat = candidat.replace("-", "/")
        if len(candidat.split("/")[0]) == 4:
            formats = ["%Y/%m/%d"]
        else:
            formats = [
                "%m/%d/%Y",
                "%d/%m/%Y",
                "%m/%d/%y",
                "%d/%m/%y",
            ]
        for format_date in formats:
            try:
                date = datetime.strptime(candidat, format_date)
                dates_valides.add(date.strftime("%Y-%m-%d"))
            except ValueError:
                continue

    # Proposer la date seulement si le résultat est unique
    if len(dates_valides) == 1:
        date_recu = next(iter(dates_valides))

    # 3. Chercher le montant sur une ligne de total/paiement
    montants_valides = set()
    for ligne in lignes:
        # Écarter les sous-totaux, rabais et économies
        if re.search(
            r"sous[\s-]*tot|sub[\s-]*tot|"
            r"valeur|rabais|économ|econom|savings",
            ligne,
            flags=re.IGNORECASE,
        ):
            continue
        etiquette = re.search(
            r"\b(?:total|achat|montant\s+pay[ée]|"
            r"débit|debit)\b",
            ligne,
            flags=re.IGNORECASE,
        )
        if not etiquette:
            continue
        partie_montant = ligne[etiquette.end():]
        resultats = re.findall(
            r"(?<![\d.,])"
            r"(\d+(?:[ \u00a0]\d{3})*[.,]\d{2})"
            r"(?![\d.,])",
            partie_montant,
        )
        for valeur in resultats:
            valeur = (
                valeur
                .replace(" ", "")
                .replace("\u00a0", "")
                .replace(",", ".") )
            montants_valides.add(float(valeur))
    # Proposer le montant seulement si le résultat est unique
    if len(montants_valides) == 1:
        montant = next(iter(montants_valides))
    return {"magasin": magasin, "date_recu": date_recu, "montant": montant}

# ======================
#Authentification JWT
#=====================
def token_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({
                'message': 'Token manquant'
            }), 401
        token = auth_header[7:].strip()
        try:
            data = jwt.decode(
                token,
                app.config['SECRET_KEY'],
                algorithms=['HS256']
            )
            request.current_user = data['sub']
        except jwt.ExpiredSignatureError:
            return jsonify({
                'message': 'Le jeton a expiré'
            }), 401
        except jwt.InvalidTokenError:
            return jsonify({
                'message': 'Le jeton est invalide'
            }), 400
        return f(*args, **kwargs)
    return decorated

# ==================================================
# COMPTE DE DÉMONSTRATION
# ==================================================
users =  {
    'admin': 'admin1234',
    'client2': 'client1234'
}

# Créer les comptes de démonstration
with app.app_context():
    db.create_all()
    for username, password in users.items():
        utilisateur = db.session.execute(
            db.select(User).where(User.username == username)
        ).scalar_one_or_none()
        if utilisateur is None:
            utilisateur = User(
                username=username,
                password_hash=generate_password_hash(password)
            )
            db.session.add(utilisateur)
    db.session.commit()

# ==================================================
# CONNEXION
# ==================================================
@app.route('/api/recu/login', methods=['POST'])
def login():

    """
    Connexion utilisateur
    ---
    tags:
      - Authentification
    parameters:
      - in: body
        name: identifiants
        required: true
        schema:
          type: object
          properties:
            username:
              type: string
              example: admin
            password:
              type: string
              format: password
          required:
            - username
            - password
    responses:
      200:
        description: Connexion réussie, jeton JWT retourné
      400:
        description: Données invalides
      401:
        description: Identifiants incorrects
    """

    auth_data = request.get_json(silent=True)
    if ( not isinstance(auth_data, dict)
        or not isinstance(auth_data.get('username'), str)
        or not isinstance(auth_data.get('password'), str)
    ):
        return jsonify({
            'message': 'username ou password manquant'
        }), 400
    username = auth_data['username']
    password = auth_data['password']

    if username in users and users[username] == password:
        # Récupérer le compte pour obtenir son identifiant
        utilisateur = db.session.execute(
            db.select(User).where(User.username == username)
        ).scalar_one_or_none()

        if utilisateur is None:
            return jsonify({
                'message': 'Compte de démonstration absent de la base.'
            }), 401
        expiration = datetime.now(timezone.utc) + timedelta(minutes=30)
        payload = {
            'sub': str(utilisateur.id),
            'exp': expiration
        }
        token = jwt.encode(
            payload,
            app.config['SECRET_KEY'],
            algorithm='HS256' )
        return jsonify({'token': token}), 200
    return jsonify({'message': 'Login invalide'}), 401
@app.route('/')
def hello_world():
    return jsonify({
        'message': 'bienvenue dans notre tp de session'
    })
def calculer_points(montant):
    points = int(montant) * 2
    return points


# Extraction OCR du reçu
@app.route("/api/recu/ocr", methods=["POST"])
@token_required
def lire_photo():

    """
    Extraire les informations d'un reçu avec Tesseract
    ---
    tags:
      - OCR
    security:
      - Bearer: []
    consumes:
      - multipart/form-data
    parameters:
      - in: formData
        name: photo
        type: file
        required: true
        description: Photo du reçu au format JPG, JPEG ou PNG
    responses:
      200:
        description: Informations extraites, confirmation requise
      400:
        description: Photo manquante ou invalide
      401:
        description: Jeton JWT manquant ou invalide
      502:
        description: Réponse du serveur OCR invalide
      503:
        description: Serveur Tesseract inaccessible
      504:
        description: Délai de réponse dépassé
    """

    photo = request.files.get("photo")
    if photo is None or not photo.filename:
        return jsonify({"erreur": "La photo est obligatoire."}), 400
    try:
        # Envoyer la photo au serveur OCR Tesseract
        reponse = requests.post(
            "http://127.0.0.1:8000/api/recus/tesseract",
            files={"photo": (
                photo.filename,
                photo.stream,
                photo.mimetype or "application/octet-stream"
            )}, timeout=180
        )
        donnees = reponse.json()
        if reponse.status_code != 200:
            return jsonify(donnees), reponse.status_code

        # Extraire les informations essentielles
        texte = donnees.get("text", "")
        informations = extraire_informations(texte)

        # Enregistrer la photo dans data
        dossier = Path(__file__).resolve().parent / "data"
        dossier.mkdir(exist_ok=True)
        extension = Path(photo.filename).suffix.lower()
        nom_photo = uuid4().hex + extension
        photo.stream.seek(0)
        photo.save(dossier / nom_photo)

        return jsonify({
            "informations": informations,
            "photo": nom_photo,
            "confirmation_requise": True
        }), 200
    except requests.exceptions.Timeout:
        return jsonify({"erreur": "Le service OCR a mis trop de temps."}), 504
    except requests.exceptions.ConnectionError:
        return jsonify({"erreur": "Le service Tesseract est inaccessible."}), 503
    except (requests.exceptions.RequestException, ValueError):
        return jsonify({"erreur": "Réponse du service OCR invalide."}), 502

# Consultation des reçus
@app.route('/api/recu/', methods=['GET'])
@token_required
def get_all_recu():
    """
    Consulter les reçus de l'utilisateur connecté
    ---
    tags:
      - Recus
    security:
      - Bearer: []
    responses:
      200:
        description: Liste des reçus de l'utilisateur
      401:
        description: Jeton JWT manquant ou invalide
    """
    # Consulter les reçus de l'utilisateur connecté
    recus = db.session.execute(
        db.select(Recu).where(
            Recu.user_id == int(request.current_user)
        )
    ).scalars().all()
    print("Nombre de reçus enregistrés :", len(recus))
    listing_recu = []
    for recu in recus:
        listing_recu.append({
            'montant': recu.montant,
            'points': recu.points,
            'date_recu': recu.date_recu.strftime('%Y-%m-%d'),
            'magasin': recu.magasin,
            'photo': recu.photo,
            'id': recu.id,
            'user_id': recu.user_id
        })
    return jsonify({'recus': listing_recu}), 200

#creation du recu

@app.route('/api/recu', methods=['POST'])
@token_required
def creer_recu():

    """
    Confirmer et enregistrer un reçu
    ---
    tags:
      - Recus
    security:
      - Bearer: []
    consumes:
      - application/json
    parameters:
      - in: body
        name: recu
        required: true
        schema:
          type: object
          required:
            - magasin
            - date_recu
            - montant
            - photo
          properties:
            magasin:
              type: string
              example: Soucy-Chouinard
            date_recu:
              type: string
              format: date
              example: "2026-10-05"
            montant:
              type: number
              format: float
              example: 17.88
            photo:
              type: string
              example: c0908158b0314e33b96fe164278bd694.png
    responses:
      201:
        description: Reçu enregistré et points attribués
      400:
        description: Données ou photo invalides
      401:
        description: Jeton JWT manquant ou invalide
      409:
        description: Reçu déjà enregistré
    """
    # Récupérer l'utilisateur connecté
    try:
        user_id = int(request.current_user)
    except (ValueError, TypeError):
        return jsonify({'erreur': 'Identifiant du token invalide.'}), 401
    utilisateur = db.session.get(User, user_id)
    if utilisateur is None:
        return jsonify({'erreur': 'Utilisateur introuvable.'}), 401

    # Vérifier le format JSON
    if not request.is_json:
        return jsonify({'erreur': 'Envoyez les données au format JSON.'}), 400

    # Valider les champs avec Marshmallow
    data = recu_schema.load(request.get_json(silent=True))
    montant = data['montant']
    magasin = data['magasin']
    photo = data.get('photo')
    date_recu = datetime.combine(data['date_recu'], datetime.min.time())

    # Vérifier la photo
    dossier = Path(__file__).resolve().parent / "data"
    if not photo or Path(photo).name != photo or not (dossier / photo).is_file():
        return jsonify({'erreur': 'Photo introuvable ou invalide.'}), 400

    # Chercher un doublon potentiel
    recu_existant = db.session.execute(
        db.select(Recu).where(
            Recu.user_id == user_id,
            Recu.magasin == magasin,
            Recu.date_recu == date_recu,
            Recu.montant == montant
        )
    ).scalars().first()
    if recu_existant is not None:
        return jsonify({
            'erreur': 'Un reçu avec ce magasin, cette date et ce montant est déjà enregistré.'
        }), 409
    # Calculer les points
    points = calculer_points(montant)
    # Enregistrer le reçu
    recu = Recu(
        montant=montant,
        date_recu=date_recu,
        magasin=magasin,
        points=points,
        photo=photo,
        user_id=utilisateur.id
    )
    db.session.add(recu)
    db.session.commit()
    return jsonify({
        'message': 'Reçu enregistré avec succès.',
        'montant': str(montant),
        'points': points,
        'date_recu': date_recu.strftime('%Y-%m-%d'),
        'magasin': magasin,
        'photo': photo,
        'monnaie': 'CAD'
    }), 201

# consulter les points
@app.route('/api/recus/points', methods=['GET'])
@token_required
def consulter_points():

    """
    Consulter le solde des points
    ---
    tags:
      - Points
    security:
      - Bearer: []
    responses:
      200:
        description: Solde des points de l'utilisateur connecté
        schema:
          type: object
          properties:
            user_id:
              type: integer
            points_accumules:
              type: integer
            points_utilises:
              type: integer
            solde:
              type: integer
            objectif:
              type: integer
              example: 15000
            points_restants:
              type: integer
            echange_autorise:
              type: boolean
      401:
        description: Jeton JWT manquant ou invalide
    """
    try:
        user_id = int(request.current_user)
    except (ValueError, TypeError):
        return jsonify({
            'message': 'Identifiant du token invalide.'
        }), 401

    utilisateur = db.session.get(User, user_id)

    if utilisateur is None:
        return jsonify({
            'message': 'Utilisateur introuvable.'
        }), 401

    # Points gagnés grâce aux reçus
    points_gagnes = db.session.execute(
        db.select(
            db.func.coalesce(db.func.sum(Recu.points), 0)
        ).where(Recu.user_id == user_id)
    ).scalar_one()

    # Points déjà dépensés dans les échanges
    points_utilises = db.session.execute(
        db.select(
            db.func.coalesce(db.func.sum(Echange.points_utilises), 0)
        ).where(Echange.user_id == user_id)
    ).scalar_one()

    solde = int(points_gagnes - points_utilises)
    objectif = 15000

    return jsonify({
        'user_id': user_id,
        'points_accumules': int(points_gagnes),
        'points_utilises': int(points_utilises),
        'solde': solde,
        'objectif': objectif,
        'points_restants': max(0, objectif - solde),
        'echange_autorise': solde >= objectif
    }), 200


# Échanger 15 000 points
@app.route('/api/echanges/points', methods=['POST'])
@token_required
def echanger_points():

    """
    Échanger 15 000 points
    ---
    tags:
      - Echanges
    security:
      - Bearer: []
    responses:
      201:
        description: Échange effectué avec succès
        schema:
          type: object
          properties:
            message:
              type: string
              example: Échange effectué avec succès.
            echange_id:
              type: integer
            points_utilises:
              type: integer
              example: 15000
            nouveau_solde:
              type: integer
      401:
        description: Jeton JWT manquant ou invalide
      422:
        description: Points insuffisants pour effectuer l'échange
        schema:
          type: object
          properties:
            message:
              type: string
              example: Points insuffisants.
            solde:
              type: integer
            points_manquants:
              type: integer
    """

    user_id = int(request.current_user)
    utilisateur = db.session.get(User, user_id)
    if utilisateur is None:
        return jsonify({'erreur': 'Utilisateur introuvable.'}), 401

    # Calculer les points gagnés
    points_gagnes = db.session.execute(
        db.select(db.func.coalesce(db.func.sum(Recu.points), 0))
        .where(Recu.user_id == user_id)

    ).scalar_one()
    # Calculer les points déjà utilisés
    points_utilises = db.session.execute(
        db.select(db.func.coalesce(db.func.sum(Echange.points_utilises), 0))
        .where(Echange.user_id == user_id)
    ).scalar_one()
    solde = int(points_gagnes - points_utilises)
    if solde < 15000:
        return jsonify({
            'message': 'Points insuffisants.',
            'solde': solde,
            'points_manquants': 15000 - solde
        }), 422

    # Enregistrer l'échange
    echange = Echange(
        user_id=user_id,
        points_utilises=15000,
        date_echange=datetime.now(timezone.utc)
    )
    db.session.add(echange)
    db.session.commit()
    return jsonify({
        'message': 'Échange effectué avec succès.',
        'echange_id': echange.id,
        'points_utilises': 15000,
        'nouveau_solde': solde - 15000
    }), 201

@app.route('/api/historique', methods=['GET'])
@token_required
def consulter_historique():

    """
    Consulter l'historique des échanges
    ---
    tags:
      - Echanges
    security:
      - Bearer: []
    responses:
      200:
        description: Historique des échanges de l'utilisateur connecté
        schema:
          type: object
          properties:
            user_id:
              type: integer
            historique:
              type: array
              items:
                type: object
                properties:
                  echange_id:
                    type: integer
                  points_utilises:
                    type: integer
                  date_echange:
                    type: string
                    format: date-time
      401:
        description: Jeton JWT manquant ou invalide
    """

    try:
        user_id = int(request.current_user)
    except (ValueError, TypeError):
        return jsonify({
            'message': 'Identifiant du token invalide.'
        }), 401
    utilisateur = db.session.get(User, user_id)
    if utilisateur is None:
        return jsonify({
            'message': 'Utilisateur introuvable.'
        }), 401
    # Récupérer uniquement les échanges de cet utilisateur
    echanges = db.session.execute(
        db.select(Echange)
        .where(Echange.user_id == user_id)
        .order_by(Echange.date_echange.desc(), Echange.id.desc())
    ).scalars().all()
    # Préparer la liste à retourner
    historique = []
    for echange in echanges:
        historique.append({
            'echange_id': echange.id,
            'points_utilises': echange.points_utilises,
            'date_echange': echange.date_echange.isoformat()
        })
    return jsonify({
        'user_id': user_id,
        'historique': historique
    }), 200

@app.errorhandler(ValidationError)
def gerer_erreur_validation(erreur):
    return jsonify({
        "message": "Données invalides.",
        "erreurs": erreur.messages
    }), 400

if __name__ == "__main__":
    app.run(debug=True)