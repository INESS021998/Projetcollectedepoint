import functools
import jwt
import requests
import re
from decimal import Decimal, InvalidOperation
from modele.recu import Recu, User,Echange
from datetime import datetime, timezone, timedelta
from werkzeug.security import generate_password_hash
from flask import request, jsonify
from settings import app, db
from settings import collection_ocr

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
    # AJOUT : enregistrer le résultat dans MongoDB
    document = {
        'texte_orc': texte,
        'informations_extraites':
            {'magasin':magasin,
              'date_recu':date_recu,
              'montant': str(montant) if montant is not None else None,} }
    collection_ocr.insert_one(document)

    return {
        "magasin": magasin,
        "date_recu": date_recu,
        "montant": montant,
        "points": int(montant) * 2 if montant is not None else None,
        "confirmation_requise": True,}

@app.route("/api/recu/ocr", methods=["POST"])
def lire_photo():
    photo = request.files.get("photo")
    if photo is None or not photo.filename:
        return jsonify({"erreur": "La photo est obligatoire."}), 400
    try:
        # Envoyer la photo au service OCR Docker
        reponse = requests.post(
            "http://127.0.0.1:8000/api/recus/tesseract",
            files={"photo": (
                    photo.filename,
                    photo.stream,
                    photo.mimetype or "application/octet-stream", )},timeout=180,
        )
        donnees = reponse.json()
        # Transmettre une éventuelle erreur du service OCR
        if reponse.status_code != 200:
            return jsonify(donnees), reponse.status_code
        # Extraire les informations du texte reçu
        texte = donnees.get("text", "")
        informations = extraire_informations(texte)
        return jsonify({
            "fichier": donnees.get("fichier"),
            "text": texte,
            "informations": informations,
        }), 200
    except requests.exceptions.Timeout:
        return jsonify({
            "erreur": "Le service OCR a mis trop de temps."
        }), 504
    except requests.exceptions.ConnectionError:
        return jsonify({
            "erreur": "Le service Tesseract est inaccessible."
        }), 503
    except (requests.exceptions.RequestException, ValueError):
        return jsonify({
            "erreur": "Réponse du service OCR invalide."
        }), 502

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
users = {'admin': 'admin1234'}

# Créer le compte en base uniquement s'il est absent
with app.app_context():
    db.create_all()
    utilisateur = db.session.execute(
        db.select(User).where(User.username == 'admin')
    ).scalar_one_or_none()

    if utilisateur is None:
        utilisateur = User(
            username='admin',
            password_hash=generate_password_hash(users['admin'])
        )
        db.session.add(utilisateur)
        db.session.commit()

        print('Compte de démonstration créé.')

# ==================================================
# CONNEXION
# ==================================================
@app.route('/api/recu/login', methods=['POST'])
def login():
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

# Consultation des reçus
@app.route('/api/recus/', methods=['GET'])
@token_required
def get_all_recu():
    # Consulter uniquement les reçus de l'utilisateur connecté
    recus = db.session.execute(
        db.select(Recu).where(
            Recu.user_id == int(request.current_user)
        )
    ).scalars().all()
    # Registre mémoire
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
            'user_id': recu.user_id,
        })
    return jsonify({'recus': listing_recu}), 200

@app.route('/api/recus', methods=['POST'])
@token_required
def creer_recu():
    #1 verifier que la requête contient du json
    if not request.json:
        return jsonify({
            'erreur':'envoyez les données au format json'
        }),400
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            'erreur':'le contenu doit être un objet JSON valide'
        }),400
    #Vérifier les champs obligatoires
    for champ in ['montant', 'date_recu', 'magasin']:
        if champ not in data or data[champ] is None:
            return jsonify({
                'erreur': f'le champ {champ} est obligatoire'
            }),400

    magasin = data['magasin'].strip()
    # vérifier que le magasin est un texte
    if not isinstance(data['magasin'], str):
        return jsonify({
            'erreur': 'le magasin doit être un texte'
        }), 400
    # refuser un texte vide ou contenant seulement des espaces
    if not magasin:
        return jsonify({
            'erreur':'le magasin ne doit pas être vide'
        }),400
    #verifier le montant
    #refuser les booléens, listes et objts json
    if isinstance(data['montant'], bool) or not isinstance(data['montant'],(int,float,str)):
        return jsonify({
            'erreur': 'le montant doit être un nombre'
        }),400
    try:
        montant = Decimal(str(data['montant']))
    except InvalidOperation:
        return jsonify({
            'erreur':'le montant doit être un nombre par exemple 2.78'
        }),400
    if not montant.is_finite() or montant<= 0:
        return jsonify({
            'erreur': 'le montant doit être un nombre superieur à zero'
        }),400
    if montant != montant.quantize(Decimal('0.01')):
        return jsonify({
            'erreur:' 'le montant doit avoir au maximum deux décimales'
        }),400
    #Vérifier la date
    valeur_date = data['date_recu']
    if not isinstance(valeur_date, str) or not re.fullmatch(
    r'\d{4}-\d{2}-\d{2}',valeur_date
    ):
        return jsonify({
            'erreur': 'la date doit respecter le format AAAA-MM-JJ'
        }),400
    try:
        date_recu = datetime.strptime(valeur_date, '%Y-%m-%d')
    except ValueError:
        return jsonify({
            'erreur':'la date indiquée est invalide'
        }),
    #Vérifier le nom de la photo
    photo = data.get('photo')
    if not isinstance(photo, str) or not re.fullmatch(
    r'[0-9a-f]{32}\.(?:jpeg|png)',photo
    ):
        return jsonify({
            'erreur':'une photo est obligatoire avec un nom de fichier valide'
        }),400

    # Chercher un doublon potentiel
    recu_existant = db.session.execute(
        db.select(Recu).where(
            Recu.magasin == magasin,
            Recu.date_recu == date_recu,
            Recu.montant == montant,
        )
    ).scalars().first()
    #refuser uniquement si un recu a été retourné
    if  recu_existant is not None:
        return jsonify({
            'erreur':(
                'un recu avec cette date et ce montant sont déjà enregistré'
            )
        }),409
    # sinon Calculer les points et continue l'enregistremnent
    points = calculer_points(montant)
    # Insertion
    recu = Recu(
        montant=montant,
        date_recu=date_recu,
        magasin=magasin,
        points=points,
        photo=photo,
        user_id = utilisateur.id
    )
    db.session.add(recu)
    db.session.commit()
    return jsonify({
        'message':'recu enregistre avec succés',
        'montant': montant,
        'points': points,
        'date_recu': date_recu.strftime('%Y-%m-%d'),
        'magasin': magasin,
        'monnaie': 'CAD'
    }), 201

# Échanger 15 000 points
@app.route('/api/recus/points', methods=['GET'])
@token_required
def consulter_points():
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
@app.route('/api/recus/historique', methods=['GET'])
@token_required
def consulter_historique():
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



if __name__ == "__main__":
    app.run(debug=True)