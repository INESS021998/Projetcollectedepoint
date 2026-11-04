from datetime import date
from pathlib import Path
from sys import path
from uuid import uuid4
from decimal import Decimal
import random

from faker import Faker
from PIL import Image, ImageDraw, ImageFont, ImageOps

#Implementer faker pour generer des photos dans un folder appeler data

fake = Faker("fr_CA")
#creation de data
dossier_data = Path(__file__).resolve().parent / "data"
dossier_data.mkdir(exist_ok=True)

#charger une police lisible pour OCR
try:
    police = ImageFont.truetype(str(dossier_data / "police.ttf"))
except OSError:
    police = ImageFont.load_default()

    #generer 5 recus
for numero in range(1,6):
    magasin = fake.company()
    date_recu =fake.date_between(
        start_date="-30d",
        end_date="today",
    )
    #génerer le montant en 100 pour garder les decimales
    montant = Decimal(random.randint(100,2000))/Decimal(100)
    points = int(montant)* 2

    #génerer une image blanche de 900pixels sur 600
    image = Image.new("RGB", (900,600), "white")
    dessin = ImageDraw.Draw(image)

    lignes =[
        magasin,
        "",
        "ReCU FICTIF POUR TEST",
        f"Date : {date_recu:%Y-%m-%d}",
        f"Numero : {numero}",
        "",
        f"Total : {montant:.2f} CAD",
        "",
        "Merci de votre visite"

    ]
    position_y = 40
    for ligne in lignes:
        dessin.text((40,position_y), ligne, fill='black', font=police)
        position_y += 50

    # Nom compatible avec ta validation des fichiers PNG
    nom_photo = f"{ uuid4().hex}.png"
    chemin_photo = dossier_data / nom_photo
    image.save(chemin_photo)

    print(f"Photo : {nom_photo}")
    print(f"Magasin attendu : {magasin}")
    print(f"Date attendue : {date_recu:%Y-%m-%d}")
    print(f"Montant attendu : {montant:.2f}")
    print(f"Points attendus : {points}")
    print("-" * 50)

print(f"Images disponibles dans : {dossier_data}")
