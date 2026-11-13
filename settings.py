

from flask import Flask
from flask_sqlalchemy import SQLAlchemy

# Création de l'application Flask
app = Flask(__name__)

# Configuration SQLite
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///collecte_points.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Clé secrète Flask (pour les tests locaux)
app.config['SECRET_KEY'] = 'maitre corbeau'

# Limite des photos envoyées : 5 Mo
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024

# Initialisation de SQLAlchemy
db = SQLAlchemy(app)

# Tester la configuration
if __name__ == "__main__":
    print("Application Flask configurée")
    print("Base de données : collecte_points.db")

