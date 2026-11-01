from pathlib import Path
from uuid import uuid4
import pytesseract
from PIL import ImageOps,Image, UnidentifiedImageError
from flask import request, jsonify
from settings import app

# ==================================================
# 1. INDIQUER OÙ TESSERACT EST INSTALLÉ
# ==================================================
pytesseract.pytesseract.tesseract_cmd = (
     r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)
# ==================================================
# 2. PRÉPARER LE DOSSIER DES PHOTOS
# ==================================================
dossier_tesseract= Path((r"C:\temp\photos_tesseract"))
dossier_tesseract.mkdir(parents=True,exist_ok=True)

#limiter la taille de requête à 5mo
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024

# ==================================================
# 3. FONCTION QUI LIT LE TEXTE D'UNE PHOTO
# ==================================================
def lire_photo_tesseract(chemin):
    with Image.open(chemin) as image:

        # MODIFICATION 1 : corriger l'orientation
        image = ImageOps.exif_transpose(image)

        # MODIFICATION 2 : améliorer le contraste
        image = ImageOps.grayscale(image)
        image = ImageOps.autocontrast(image)

        # MODIFICATION 3 : agrandir les petites images
        # Maximum : trois fois la taille initiale
        if image.width < 1500:
            facteur = min(3, 1500 / image.width)

            image = image.resize(
                (
                    round(image.width * facteur),
                    round(image.height * facteur)
                ),
                Image.Resampling.LANCZOS
            )

        # PREMIÈRE LECTURE : le reçu entier
        # psm 3 : Tesseract cherche les différentes zones de texte
        texte_complet = pytesseract.image_to_string(
            image,
            lang='fra',
            config='--psm 6',
            timeout=60
        ).strip()

        # MODIFICATION 4 : relire la partie inférieure
        # Elle peut contenir la date et le montant du paiement
        partie_inferieure = image.crop(
            (
                0,
                image.height // 2,
                image.width,
                image.height
            )
        )

        # psm 6 : traiter cette zone comme un bloc de texte
        texte_inferieur = pytesseract.image_to_string(
            partie_inferieure,
            lang='fra',
            config='--psm 11',
            timeout=60
        ).strip()

    # Conserver les deux lectures pour comparer les résultats
    return texte_complet, texte_inferieur
# ==================================================
# 4. ROUTE QUI REÇOIT LA PHOTO ET RETOURNE LE TEXTE
# ==================================================
@app.route('/api/recus/tesseract', methods=['POST'])
def annalyse_recu_tesseract():

    # Vérifier la présence du fichier du reçu
    if 'photo' not in request.files:
        return jsonify({
            'message': 'veuillez envoyer la photo du reçu'
        }), 400

    photo = request.files['photo']

    # Vérifier que le fichier a été sélectionné
    if not photo.filename:
        return jsonify({
            'message': 'aucun fichier sélectionné'
        }), 400

    # Vérifier l'extension
    extension = Path(photo.filename).suffix.lower()

    if extension not in ['.jpg', '.jpeg', '.png']:
        return jsonify({
            'message': 'formats acceptés : jpg, jpeg, png'
        }), 400

    # Créer un nom unique et enregistrer la photo
    nom_fichier = f'{uuid4().hex}{extension}'
    chemin = dossier_tesseract / nom_fichier
    photo.save(chemin)

    try:
        # Appel de la fonction de lecture
        texte, texte_inferieur = lire_photo_tesseract(chemin)

    except pytesseract.TesseractNotFoundError:
        return jsonify({
            'message': (
                'Tesseract est introuvable. '
                'Vérifiez le chemin tesseract.exe'
            )
        }), 500

    except UnidentifiedImageError:
        chemin.unlink(missing_ok=True)
        return jsonify({
            'message': 'le fichier n''est pas une image valide'
        }),400

    except pytesseract.TesseractError:
        app.logger.exception('Erreur Tesseract')

        return jsonify({
            'message': (
                'Erreur de lecture Tesseract. '
                'Vérifiez aussi les langues installées.'
            )
        }), 500
    except RuntimeError:
        return jsonify({

            'message': 'Une lecture OCR a dépassé le délai de 60 secondes.'
        }), 504

    except OSError:
        return jsonify({
            'message': 'impossible d''ouvrir la photo'
        }),400
 #verifier si du texte a été detecté
    if not texte and not texte_inferieur:
        return jsonify({
            'message': 'Aucun texte détecté. Essayez une photo plus nette.',
            'fichier': nom_fichier
        }), 422

    return jsonify({
        'message': 'Photo analysée avec Tesseract.',
        'fichier': nom_fichier,
        'text': texte,
        'text_partie_inferieure': texte_inferieur
    }), 200



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)