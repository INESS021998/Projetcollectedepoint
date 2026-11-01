import os

from flask_sqlalchemy import SQLAlchemy
from flask import Flask
from pymongo import MongoClient
app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///collecte_points.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'maitre corbeau'

db = SQLAlchemy(app)

# Lire la chaîne de connexion
Mongo_client = MongoClient(os.environ['MONGODB_URI'],
    serverSelectionTimeoutMS=5000
)

mongo_db = Mongo_client["projets_points"]
collection_ocr = mongo_db["collecte_points"]

# Sélectionner la base et la collection
mongo_db = Mongo_client["projets_points"]
collection_ocr = mongo_db["collecte_points"]

# Tester la connexion en exécutant settings.py directement
if __name__ == "__main__":
    Mongo_client.admin.command("ping")
    print("Connexion MongoDB réussie.")