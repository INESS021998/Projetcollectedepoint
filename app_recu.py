import functools
import jwt
import requests
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4
from flasgger import Swagger
from marshmallow import ValidationError
from modele.recu import Recu, User, EchangeMagasin
from datetime import datetime, timezone, timedelta
from werkzeug.security import generate_password_hash
from flask import request, jsonify

from schema import recu_schema
from settings import app, db

POINTS_PAR_ECHANGE_MAGASIN = 15000
POINTS_PAR_DOLLAR_CAD = 1000
REDUCTION_ECHANGE_CAD  = 15.0
JWT_EXPIRATION_HOURS = 2


# Initialisation de Swagger
swagger = Swagger(app, template={
    "swagger": "2.0",
    "info": {
        "title": "ProjetCollectedePoint",
        "version": "2.2.0",
        "description": "API REST de gestion des reçus et points"
    },
    "securityDefinitions": {
        "Bearer": {
            "type": "apiKey",
            "name": "Authorization",
            "in": "header",
            "description": "Saisir Bearer suivi du jeton JWT"
        }
    },
    "definitions": {
        "Recu": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "example": 1},
                "magasin": {"type": "string", "example": "IGA"},
                "montant": {"type": "number", "example": 45.78},
                "date_recu": {
                    "type": "string",
                    "format": "date",
                    "example": "2026-11-13"
                },
                "photo": {"type": "string", "example": "recu_iga.png"},
                "points": {"type": "integer", "example": 90},
                "user_id": {"type": "integer", "example": 1}
            }
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
@app.route('/api/recu/n', methods=['POST'])
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
          required:
            - username
            - password
          properties:
            username:
              type: string
              example: admin
            password:
              type: string
              format: password
              example: admin1234
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

#========================
#creation du recu
#============================
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
              example: "Soucy-Chouinard"
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
              example: "c0908158b0314e33b96fe164278bd694.png"
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

def obtenir_magasins_eligibles_pour_echange():
    """Liste distincte des magasins figurant sur les reçus enregistrés."""
    noms = db.session.execute(
        db.select(Recu.magasin)
        .where(Recu.magasin.is_not(None), Recu.magasin != "")
        .distinct()
    ).scalars().all()
    uniques = {}
    for nom in noms:
        if isinstance(nom, str) and nom.strip():
            uniques.setdefault(nom.strip().casefold(), nom.strip())
    return sorted(uniques.values(), key=str.casefold)

def obtenir_resume_conversion_points(user_id):
    # Calculer les points accumulés
    points_accumules = db.session.execute(
        db.select(db.func.coalesce(db.func.sum(Recu.points), 0))
        .where(Recu.user_id == user_id)
    ).scalar_one()

    # Calculer les points déjà utilisés
    total_utilise = db.session.execute(
        db.select(db.func.coalesce(db.func.sum(EchangeMagasin.points_utilises), 0))
        .where(EchangeMagasin.user_id == user_id)
    ).scalar_one()

    points_accumules = int(points_accumules)
    total_utilise = int(total_utilise)
    solde = points_accumules - total_utilise

    return {
        "user_id": user_id,
        #"points_accumules": points_accumules,
        "points_utilises": total_utilise,
        "solde": solde,
        #"valeur_points_accumules_cad": round(
           # points_accumules / POINTS_PAR_DOLLAR_CAD, 2
        #),
        "valeur_solde_cad": round(
            solde / POINTS_PAR_DOLLAR_CAD, 2
        ),
        #"points_requis_pour_echange_en_magasin": POINTS_PAR_ECHANGE_MAGASIN,
        #"points_restants_pour_echange_en_magasin": max(
           # 0, POINTS_PAR_ECHANGE_MAGASIN - solde
       # ),
        #"credit_obtenu_si_echange_en_magasin_cad": REDUCTION_ECHANGE_CAD,
        "echange_en_magasin_autorise": solde >= POINTS_PAR_ECHANGE_MAGASIN
    }

def obtenir_magasins_eligibles_pour_echange(user_id):
    magasins = db.session.execute(
        db.select(Recu.magasin)
        .where(Recu.user_id == user_id)
        .distinct()
        .order_by(Recu.magasin)
    ).scalars().all()

    return magasins

#==============================
#different magasin
#================================
@app.route('/api/magasins', methods=['GET'])
@token_required
def lister_magasins():
    """
    Lister les magasins présents sur les factures du client connecté
    et consulter son solde de points.
    ---
    tags:
      - LISTE DES MAGASINS
    security:
      - Bearer: []
    produces:
      - application/json
    responses:
      200:
        description: Magasins admissibles et résumé des points du client.
        schema:
          type: object
          properties:
            magasins_eligibles_pour_echange:
              type: array
              items:
                type: string
              example:
                - IGA
                - Metro
            points_client:
              type: object
              properties:
                user_id:
                  type: integer
                  example: 2
                points_utilises:
                  type: integer
                  example: 0
                solde:
                  type: integer
                  example: 18000
                valeur_solde_cad:
                  type: number
                  example: 18.0
                echange_en_magasin_autorise:
                  type: boolean
                  description: Indique si le solde de points est suffisant.
                  example: true
            devise:
              type: string
              example: CAD
      401:
        description: JWT absent, invalide ou expiré.
    """

    # Identifier le client connecté
    try:
        user_id = int(request.current_user)
    except (TypeError, ValueError):
        return jsonify({
            "message": "Identifiant du token invalide."
        }), 401

    # Vérifier si le client existe
    utilisateur = db.session.get(User, user_id)

    if utilisateur is None:
        return jsonify({
            "message": "Utilisateur introuvable."
        }), 401

    # Récupérer les magasins présents sur ses factures
    magasins = obtenir_magasins_eligibles_pour_echange(user_id)

    # Calculer le résumé des points
    points_client = obtenir_resume_conversion_points(user_id)

    return jsonify({
        "magasins_eligibles_pour_echange": magasins,
        "points_client": points_client,
        "devise": "CAD"
    }), 200

#====================================
# Échanger 15 000 points
#================================
@app.route('/api/echanges/points', methods=['POST'])
@token_required
def echanger_points():
    """
    Échanger 15 000 points contre une réduction de 15 CAD.
    Le prix du produit doit être d'au moins 15 CAD.
    ---
    tags:
      - Echanges
    security:
      - Bearer: []
    consumes:
      - application/json
    produces:
      - application/json
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - magasin
            - prix_produit
          properties:
            magasin:
              type: string
              description: Magasin où le produit est acheté.
              example: IGA
            prix_produit:
              type: number
              format: double
              description: Prix du produit en CAD; minimum 15.00.
              example: 15.00
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
              example: 1
            magasin:
              type: string
              example: IGA
            points_utilises:
              type: integer
              example: 15000
            reduction_cad:
              type: number
              example: 15.0
            prix_produit:
              type: number
              example: 15.0
            montant_a_payer:
              type: number
              example: 0.0
            nouveau_solde:
              type: integer
              example: 3000
            devise:
              type: string
              example: CAD
      400:
        description: Magasin ou prix invalide
      401:
        description: Jeton JWT absent, invalide ou expiré
      422:
        description: Solde insuffisant ou prix inférieur à 15 CAD
    """
    user_id = int(request.current_user)
    utilisateur = db.session.get(User, user_id)
    if utilisateur is None:
        return jsonify({"erreur": "Utilisateur introuvable."}), 401

    donnees = request.get_json(silent=True)
    if not isinstance(donnees, dict):
        return jsonify({
            "message": "Envoyez un objet JSON avec magasin et prix_produit."
        }), 400

    magasin = donnees.get("magasin")
    if not isinstance(magasin, str) or not magasin.strip():
        return jsonify({
            "message": "Le champ magasin doit contenir un nom non vide."
        }), 400
    magasin = magasin.strip()
    if len(magasin) > 150:
        return jsonify({
            "message": "Le nom du magasin ne peut pas dépasser 150 caractères."
        }), 400

    prix_brut = donnees.get("prix_produit")
    if isinstance(prix_brut, bool) or not isinstance(prix_brut, (int, float)):
        return jsonify({
            "message": "Le champ prix_produit doit être un nombre en CAD."
        }), 400
    try:
        prix_produit = Decimal(str(prix_brut))
        if not prix_produit.is_finite():
            raise InvalidOperation
        prix_arrondi = prix_produit.quantize(Decimal("0.01"))
    except InvalidOperation:
        return jsonify({
            "message": "Le prix doit être un nombre valide avec au plus deux décimales."
        }), 400
    if prix_arrondi != prix_produit:
        return jsonify({
            "message": "Le prix doit avoir au plus deux décimales."
        }), 400
    prix_produit = prix_arrondi

    reduction_cad = Decimal(str(REDUCTION_ECHANGE_CAD)).quantize(Decimal("0.01"))
    if prix_produit < reduction_cad:
        return jsonify({
            "message": "Le produit doit coûter au moins 15,00 CAD pour cet échange.",
            "prix_minimum_cad": float(reduction_cad),
            "reduction_cad": float(reduction_cad)
        }), 422

    points_gagnes = db.session.execute(
        db.select(db.func.coalesce(db.func.sum(Recu.points), 0))
        .where(Recu.user_id == user_id)
    ).scalar_one()
    points_utilises = db.session.execute(
        db.select(db.func.coalesce(db.func.sum(EchangeMagasin.points_utilises), 0))
        .where(EchangeMagasin.user_id == user_id)
    ).scalar_one()
    solde = int(points_gagnes - points_utilises)
    if solde < POINTS_PAR_ECHANGE_MAGASIN:
        return jsonify({
            "message": "Points insuffisants.",
            "solde": solde,
            "points_manquants": POINTS_PAR_ECHANGE_MAGASIN - solde
        }), 422

    echange = EchangeMagasin(
        user_id=user_id,
        points_utilises=POINTS_PAR_ECHANGE_MAGASIN,
        date_echange=datetime.now(timezone.utc),
        magasin=magasin
    )
    db.session.add(echange)
    db.session.commit()

    montant_a_payer = (prix_produit - reduction_cad).quantize(Decimal("0.01"))
    return jsonify({
        "message": "Échange effectué avec succès.",
        "echange_id": echange.id,
        "magasin": magasin,
        "points_utilises": POINTS_PAR_ECHANGE_MAGASIN,
        "reduction_cad": float(reduction_cad),
        "prix_produit": float(prix_produit),
        "montant_a_payer": float(montant_a_payer),
        "nouveau_solde": solde - POINTS_PAR_ECHANGE_MAGASIN,
        "devise": "CAD"
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
        db.select(EchangeMagasin)
        .where(EchangeMagasin.user_id == user_id)
        .order_by(EchangeMagasin.date_echange.desc(), EchangeMagasin.id.desc())
    ).scalars().all()
    # Préparer la liste à retourner
    historique = []
    for echange in echanges:
        historique.append({
            'echange_id': echange.id,
            'points_utilises': echange.points_utilises,
            'date_echange': echange.date_echange.isoformat(),
            'magasin': echange.magasin
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
