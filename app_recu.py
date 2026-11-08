import functools
import jwt
import requests
import re
from sqlalchemy import inspect
from pathlib import Path
from uuid import uuid4
from flasgger import Flasgger, Swagger
from marshmallow import ValidationError
from modele.recu import Recu, User,EchangeMagasin
from datetime import datetime, timezone, timedelta
from werkzeug.security import generate_password_hash
from flask import request, jsonify

from schema import recu_schema
from settings import app, db
from faker__genphoto import generer_recus_fictifs_si_absents

POINTS_PAR_ECHANGE_MAGASIN = 15000
POINTS_PAR_DOLLAR_CAD = 1000
VALEUR_PAR_ECHANGE_MAGASIN_CAD = 15.0
JWT_EXPIRATION_HOURS = 2

# Initialisation de Swagger

swagger = Swagger(app, template={
    "swagger": "2.0",
    "info": {
        "title": "ProjetCollectedePoint",
        "version": "2.0.0",
        "description": "API de fidélité : reçus, points et crédits de récompense en magasin. 15 000 points donnent 15 CAD."
    },
    "securityDefinitions": {
        "Bearer": {
            "type": "apiKey",
            "name": "Authorization",
            "in": "header",
            "description": "Dans Valeur, saisir Bearer, un espace, puis le JWT. Le jeton expire après 2 heures."
        }
    },
    "definitions": {
        "Recu": {
            "type": "object",
            "description": "Reçu confirmé appartenant au compte connecté.",
            "properties": {
                "id": {"type": "integer", "description": "Identifiant du reçu.", "example": 42},
                "user_id": {"type": "integer", "description": "Identifiant du propriétaire.", "example": 2},
                "date_recu": {"type": "string", "format": "date", "description": "Date du reçu.", "example": "2026-10-05"},
                "magasin": {"type": "string", "description": "Magasin indiqué sur le reçu.", "example": "Soucy-Chouinard"},
                "montant": {"type": "string", "description": "Montant du reçu en CAD.", "example": "17.88"},
                "points": {"type": "integer", "description": "Points attribués pour ce reçu.", "example": 34},
                "photo": {"type": "string", "description": "Nom du fichier image associé.", "example": "receipt.png"}
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

def assurer_colonne_magasin_echange():
    """Ajoute le champ magasin aux bases SQLite existantes sans perte d'historique."""
    with db.engine.begin() as connexion:
        colonnes = {colonne["name"] for colonne in inspect(connexion).get_columns("echange")}
        if "magasin" not in colonnes:
            connexion.exec_driver_sql("ALTER TABLE echange ADD COLUMN magasin VARCHAR(150)")

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
    assurer_colonne_magasin_echange()
    generer_recus_fictifs_si_absents()
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
Connexion et création d’un jeton JWT.
---
tags:
  - Authentification
consumes:
  - application/json
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
          example: client2
        password:
          type: string
          format: password
          example: client1234
responses:
  200:
    description: Connexion réussie; le JWT expire après 2 heures.
    schema:
      type: object
      properties:
        token:
          type: string
          description: Jeton à utiliser dans l’en-tête Authorization avec le préfixe Bearer.
  400:
    description: JSON absent ou identifiants manquants/mal formés.
  401:
    description: Identifiants incorrects ou compte de démonstration absent.
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
        expiration = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRATION_HOURS)
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
    """
    Vérifier que l’API principale répond.
    ---
    tags:
      - Général
    responses:
      200:
        description: Message d’accueil
        schema:
          type: object
          properties:
            message:
              type: string
    """
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
Analyser une photo de reçu avec le service OCR sans encore créer le reçu.
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
    description: JPG, JPEG ou PNG; maximum 5 Mo.
responses:
  200:
    description: Résultat OCR à vérifier, puis à confirmer avec POST /api/recu.
    schema:
      type: object
      properties:
        informations:
          type: object
          properties:
            magasin:
              type: string
            date_recu:
              type: string
              format: date
            montant:
              type: number
        photo:
          type: string
          description: Nom à transmettre ensuite à POST /api/recu.
        confirmation_requise:
          type: boolean
  400:
    description: JWT invalide, photo absente ou invalide.
  401:
    description: JWT absent ou expiré.
  502:
    description: Réponse OCR invalide.
  503:
    description: Service OCR inaccessible.
  422:
    description: Aucun texte détecté sur l’image.
  500:
    description: Erreur de lecture Tesseract.
  504:
    description: Délai OCR dépassé.
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
Retourner la liste des reçus du compte associé au JWT. La réponse est un tableau JSON direct; chaque élément est un reçu.
---
tags:
  - Reçus
security:
  - Bearer: []
produces:
  - application/json
responses:
  200:
    description: Liste des reçus de l’utilisateur connecté. Retourne [] si aucun reçu n’est enregistré.
    schema:
      type: array
      items:
        $ref: '#/definitions/Recu'
    examples:
      application/json:
        - id: 42
          user_id: 2
          date_recu: "2026-10-05"
          magasin: Soucy-Chouinard
          montant: "17.88"
          points: 34
          photo: receipt.png
        - id: 43
          user_id: 2
          date_recu: "2026-10-08"
          magasin: Marché du Coin
          montant: "22.50"
          points: 44
          photo: ticket-43.jpg
  400:
    description: JWT invalide.
  401:
    description: JWT absent ou expiré.
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
            'id': recu.id,
            'user_id': recu.user_id,
            'date_recu': recu.date_recu.strftime('%Y-%m-%d'),
            'magasin': recu.magasin,
            'montant': str(recu.montant),
            'points': recu.points,
            'photo': recu.photo
        })
    return jsonify(listing_recu), 200

#creation du recu

@app.route('/api/recu', methods=['POST'])
@token_required
def creer_recu():

    """
Confirmer un reçu déjà analysé et attribuer les points au compte connecté.
---
tags:
  - Reçus
security:
  - Bearer: []
consumes:
  - application/json
parameters:
  - in: body
    name: recu
    required: true
    description: Le champ photo doit être le nom retourné par POST /api/recu/ocr.
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
          example: 17.88
        photo:
          type: string
          example: receipt.png
responses:
  201:
    description: Reçu enregistré; points calculés par int(montant) × 2.
  400:
    description: JWT invalide, JSON, données ou photo invalides.
  401:
    description: JWT absent ou expiré.
  409:
    description: Même utilisateur, magasin, date et montant déjà enregistrés.
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
    """Calcule les points gagnés, leur équivalent CAD et l'éligibilité magasin."""
    points_gagnes = db.session.execute(
        db.select(db.func.coalesce(db.func.sum(Recu.points), 0))
        .where(Recu.user_id == user_id)
    ).scalar_one()
    points_utilises = db.session.execute(
        db.select(db.func.coalesce(db.func.sum(EchangeMagasin.points_utilises), 0))
        .where(EchangeMagasin.user_id == user_id)
    ).scalar_one()

    points_accumules = int(points_gagnes)
    total_utilise = int(points_utilises)
    solde = points_accumules - total_utilise
    return {
        "user_id": user_id,
        "points_accumules": points_accumules,
        "points_utilises": total_utilise,
        "solde": solde,
        "valeur_points_accumules_cad": round(
            points_accumules / POINTS_PAR_DOLLAR_CAD, 2
        ),
        "valeur_solde_cad": round(
            solde / POINTS_PAR_DOLLAR_CAD, 2
        ),
        "points_requis_pour_echange_en_magasin": POINTS_PAR_ECHANGE_MAGASIN,
        "points_restants_pour_echange_en_magasin": max(
            0, POINTS_PAR_ECHANGE_MAGASIN - solde
        ),
        "credit_obtenu_si_echange_en_magasin_cad": VALEUR_PAR_ECHANGE_MAGASIN_CAD,
        "echange_en_magasin_autorise": solde >= POINTS_PAR_ECHANGE_MAGASIN
    }


@app.route('/api/magasins', methods=['GET'])
@token_required
def lister_magasins():
    """
Lister les magasins du programme et le total de points du client connecté.
Les points accumulés sont additionnés sur tous ses reçus; le solde tient compte
des points déjà utilisés dans ses échanges.
---
tags:
  - Magasins et échanges en magasin
security:
  - Bearer: []
produces:
  - application/json
responses:
  200:
    description: Magasins du programme, progression du client et équivalents en CAD.
    schema:
      type: object
      properties:
        magasins_eligibles_pour_echange:
          type: array
          items:
            type: string
          example:
            - Epicerie Adonai
            - metro
        points_client:
          type: object
          properties:
            user_id:
              type: integer
              example: 2
            points_accumules:
              type: integer
              description: Total gagné sur tous les reçus, avant déduction des échanges.
              example: 15000
            points_utilises:
              type: integer
              description: Total de points déjà dépensés.
              example: 0
            solde:
              type: integer
              description: Points encore disponibles.
              example: 15000
            valeur_points_accumules_cad:
              type: number
              description: Valeur CAD du total accumulé, au taux de 1 000 points par CAD.
              example: 15.0
            valeur_solde_cad:
              type: number
              description: Valeur CAD du solde disponible.
              example: 15.0
            points_requis_pour_echange_en_magasin:
              type: integer
              example: 15000
            points_restants_pour_echange_en_magasin:
              type: integer
              example: 0
            credit_obtenu_si_echange_en_magasin_cad:
              type: number
              description: Crédit remis uniquement après un échange en magasin.
              example: 15.0
            echange_en_magasin_autorise:
              type: boolean
              example: true
        devise:
          type: string
          example: CAD
    examples:
      application/json:
        magasins_eligibles_pour_echange:
          - Epicerie Adonai
          - metro
          - Moreau, Pouliot et Fortier
        points_client:
          user_id: 2
          points_accumules: 15000
          points_utilises: 0
          solde: 15000
          valeur_points_accumules_cad: 15.0
          valeur_solde_cad: 15.0
          points_requis_pour_echange_en_magasin: 15000
          points_restants_pour_echange_en_magasin: 0
          credit_obtenu_si_echange_en_magasin_cad: 15.0
          echange_en_magasin_autorise: true
        devise: CAD
  400:
    description: JWT invalide.
  401:
    description: JWT absent ou expiré, ou compte absent.
    """

    try:
        user_id = int(request.current_user)
    except (ValueError, TypeError):
        return jsonify({"message": "Identifiant du token invalide."}), 401

    if db.session.get(User, user_id) is None:
        return jsonify({"message": "Utilisateur introuvable."}), 401

    points_client = obtenir_resume_conversion_points(user_id)
    return jsonify({
        "magasins_eligibles_pour_echange": obtenir_magasins_eligibles_pour_echange(),
        "points_client": points_client,
        "devise": "CAD"
    }), 200

# Conversion indicative des points accumulés en valeur monétaire CAD.
@app.route('/api/points/conversion', methods=['GET'])
@token_required
def consulter_conversion_points_en_cad():
    """
Convertir les points accumulés et le solde en équivalent CAD.
Cette consultation ne dépense pas de points. L’échange en magasin se fait
séparément avec POST /api/echanges/points.
---
tags:
  - Conversion des points en CAD
security:
  - Bearer: []
produces:
  - application/json
responses:
  200:
    description: Total de points, solde disponible, conversion indicative en CAD et éligibilité magasin.
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
        valeur_points_accumules_cad:
          type: number
          description: Valeur indicative du total gagné au taux de 1 000 points par CAD.
        valeur_solde_cad:
          type: number
          description: Valeur indicative des points encore disponibles.
        points_requis_pour_echange_en_magasin:
          type: integer
          example: 15000
        points_restants_pour_echange_en_magasin:
          type: integer
        credit_obtenu_si_echange_en_magasin_cad:
          type: number
          description: Crédit remis uniquement si le client réalise ensuite un échange en magasin.
          example: 15
        echange_en_magasin_autorise:
          type: boolean
        devise:
          type: string
          example: CAD
    examples:
      application/json:
        user_id: 2
        points_accumules: 464
        points_utilises: 0
        solde: 464
        valeur_points_accumules_cad: 0.46
        valeur_solde_cad: 0.46
        points_requis_pour_echange_en_magasin: 15000
        points_restants_pour_echange_en_magasin: 14536
        credit_obtenu_si_echange_en_magasin_cad: 15.0
        echange_en_magasin_autorise: false
        devise: CAD
  400:
    description: JWT invalide.
  401:
    description: JWT absent ou expiré, ou compte absent.
    """
    try:
        user_id = int(request.current_user)
    except (ValueError, TypeError):
        return jsonify({"message": "Identifiant du token invalide."}), 401
    if db.session.get(User, user_id) is None:
        return jsonify({"message": "Utilisateur introuvable."}), 401
    return jsonify({**obtenir_resume_conversion_points(user_id), "devise": "CAD"}), 200


# Échanger 15 000 points contre un crédit de 15 CAD dans un magasin.
@app.route('/api/echanges/points', methods=['POST'])
@token_required
def echanger_points_en_magasin():
    """
Échanger 15 000 points contre un crédit de 15 CAD au magasin choisi.
Le magasin doit figurer dans GET /api/magasins. Le crédit est enregistré par
l’API; le choix du produit et l’application à la caisse se font en magasin.
---
tags:
  - Échange en magasin
security:
  - Bearer: []
consumes:
  - application/json
parameters:
  - in: body
    name: echange
    required: true
    description: Choisir un nom exactement présent dans GET /api/magasins.
    schema:
      type: object
      required:
        - magasin
      properties:
        magasin:
          type: string
          example: Soucy-Chouinard
responses:
  201:
    description: Crédit de 15 CAD enregistré.
    schema:
      type: object
      properties:
        message:
          type: string
        echange_id:
          type: integer
        magasin:
          type: string
        points_utilises:
          type: integer
        valeur_credit_echange_en_magasin_cad:
          type: number
        devise:
          type: string
        nouveau_solde:
          type: integer
  400:
    description: JWT invalide, magasin absent ou nom non présent dans la liste.
  401:
    description: JWT absent ou expiré, ou compte absent.
  422:
    description: Solde insuffisant pour un échange en magasin.
    schema:
      type: object
      properties:
        message:
          type: string
          example: Points insuffisants.
        solde:
          type: integer
          example: 464
        points_manquants:
          type: integer
          example: 14536
        points_requis_pour_echange_en_magasin:
          type: integer
          example: 15000
    """
    user_id = int(request.current_user)
    utilisateur = db.session.get(User, user_id)
    if utilisateur is None:
        return jsonify({'erreur': 'Utilisateur introuvable.'}), 401

    donnees = request.get_json(silent=True)
    if not isinstance(donnees, dict) or not isinstance(donnees.get("magasin"), str):
        return jsonify({"message": "Envoyez un nom de magasin dans le champ magasin."}), 400
    magasin_demande = donnees["magasin"].strip()
    magasins = obtenir_magasins_eligibles_pour_echange()
    magasin = next((nom for nom in magasins if nom.casefold() == magasin_demande.casefold()), None)
    if magasin is None:
        return jsonify({
            "message": "Choisissez un magasin dans la liste disponible.",
            "magasins_disponibles": magasins
        }), 400

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
            "points_manquants": POINTS_PAR_ECHANGE_MAGASIN - solde,
            "points_requis_pour_echange_en_magasin": POINTS_PAR_ECHANGE_MAGASIN
        }), 422

    echange = EchangeMagasin(
        user_id=user_id,
        points_utilises=POINTS_PAR_ECHANGE_MAGASIN,
        date_echange=datetime.now(timezone.utc),
        magasin=magasin
    )
    db.session.add(echange)
    db.session.commit()
    return jsonify({
        "message": "Crédit magasin enregistré avec succès.",
        "echange_id": echange.id,
        "magasin": magasin,
        "points_utilises": POINTS_PAR_ECHANGE_MAGASIN,
        "valeur_credit_echange_en_magasin_cad": VALEUR_PAR_ECHANGE_MAGASIN_CAD,
        "devise": "CAD",
        "nouveau_solde": solde - POINTS_PAR_ECHANGE_MAGASIN
    }), 201

@app.route('/api/historique', methods=['GET'])
@token_required
def consulter_historique():

    """
Lister les échanges du compte connecté, du plus récent au plus ancien.
---
tags:
  - Historique des échanges en magasin
security:
  - Bearer: []
responses:
  200:
    description: Historique des échanges et crédits.
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
              magasin:
                type: string
                description: Null sur les anciennes transactions.
              valeur_credit_echange_en_magasin_cad:
                type: number
              devise:
                type: string
              date_echange:
                type: string
                format: date-time
  400:
    description: JWT invalide.
  401:
    description: JWT absent ou expiré, ou compte absent.
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
            'magasin': echange.magasin,
            'valeur_credit_echange_en_magasin_cad': round(echange.points_utilises / POINTS_PAR_DOLLAR_CAD, 2),
            'devise': 'CAD',
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
