from decimal import Decimal
from pathlib import Path
from uuid import uuid4
import random

from faker import Faker
from PIL import Image, ImageDraw, ImageFont


PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def generer_recus_fictifs_si_absents(dossier_data=None):
    """Génère cinq reçus uniquement si le dossier ne contient aucune photo."""
    if dossier_data is None:
        dossier_data = Path(__file__).resolve().parent / "data"
    else:
        dossier_data = Path(dossier_data)

    if dossier_data.is_dir() and any(
        fichier.is_file() and fichier.suffix.lower() in PHOTO_EXTENSIONS
        for fichier in dossier_data.iterdir()
    ):
        return []

    dossier_data.mkdir(parents=True, exist_ok=True)
    fake = Faker("fr_CA")

    try:
        police = ImageFont.truetype(str(dossier_data / "police.ttf"))
    except OSError:
        police = ImageFont.load_default()

    photos_creees = []
    for numero in range(1, 6):
        magasin = fake.company()
        date_recu = fake.date_between(start_date="-30d", end_date="today")
        montant = Decimal(random.randint(100, 2000)) / Decimal(100)
        points = int(montant) * 2

        image = Image.new("RGB", (900, 600), "white")
        dessin = ImageDraw.Draw(image)
        lignes = [
            magasin,
            "",
            "ReCU FICTIF POUR TEST",
            f"Date : {date_recu:%Y-%m-%d}",
            f"Numero : {numero}",
            "",
            f"Total : {montant:.2f} CAD",
            "",
            "Merci de votre visite",
        ]

        position_y = 40
        for ligne in lignes:
            dessin.text((40, position_y), ligne, fill="black", font=police)
            position_y += 50

        chemin_photo = dossier_data / f"{uuid4().hex}.png"
        image.save(chemin_photo)
        photos_creees.append(chemin_photo)

    return photos_creees


if __name__ == "__main__":
    photos = generer_recus_fictifs_si_absents()
    print(f"Photos générées : {len(photos)}")
