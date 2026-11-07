# Projet de collecte de points

API Flask de gestion de reçus et de points de fidélité. Le projet comprend une API principale et un service OCR séparé basé sur Tesseract. Au démarrage, cinq reçus fictifs sont créés automatiquement si le dossier `data/` ne contient aucune photo.

## Prérequis Windows

- Windows 10 ou 11
- Python 3.11 ou 3.12, avec `py` disponible dans PowerShell
- Tesseract OCR installé séparément, avec les données de langue française (`fra`)

Le service OCR attend Tesseract à l’emplacement `C:\Program Files\Tesseract-OCR\tesseract.exe`. Si Tesseract est installé ailleurs, adaptez `pytesseract.pytesseract.tesseract_cmd` dans `service_orc/serveur_ocr.py`.

## Installation

Ouvrez PowerShell dans le dossier du projet :

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Si PowerShell bloque l’activation du script, vous pouvez ouvrir une nouvelle fenêtre PowerShell et lancer directement les exécutables de l’environnement, par exemple `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`.

Installez ensuite Tesseract OCR pour Windows et vérifiez que le fichier de langue française est disponible. Le paquet Python `pytesseract` ne fournit pas le moteur Tesseract lui-même.

## Démarrage

L’API et le service OCR sont deux serveurs distincts. Laissez chacun tourner dans sa propre fenêtre PowerShell, depuis la racine du projet.

**Fenêtre 1 — service OCR :**

```powershell
.\.venv\Scripts\Activate.ps1
python service_orc\serveur_ocr.py
```

Le service écoute sur le port `8000`.

**Fenêtre 2 — API principale :**

```powershell
.\.venv\Scripts\Activate.ps1
python app_recu.py
```

L’API Flask écoute par défaut sur `http://127.0.0.1:5000`. La route OCR de l’API principale transmet les images au service situé à `http://127.0.0.1:8000`. Les deux serveurs doivent donc être démarrés pour utiliser le parcours OCR.

Au démarrage, le contexte Flask crée les tables SQLite, ajoute les comptes de démonstration et vérifie le dossier `data/`. Si le dossier est absent ou ne contient aucune image JPG, JPEG ou PNG, cinq reçus fictifs sont générés automatiquement. Si une photo est déjà présente, aucune photo ne sera ajoutée ou remplacée. La base est configurée dans `settings.py` sous le nom `collecte_points.db`.

## Documentation et routes principales

La documentation Swagger est disponible à l’adresse `http://127.0.0.1:5000/apidocs/` lorsque l’API principale est lancée.

| Méthode | Route | Description |
|---|---|---|
| `POST` | `/api/recu/login` | Connexion et obtention d’un JWT |
| `POST` | `/api/recu/ocr` | Envoi d’une photo pour préremplir les données d’un reçu (JWT requis) |
| `POST` | `/api/recu` | Confirmation et enregistrement d’un reçu (JWT requis) |
| `GET` | `/api/recu/` | Liste des reçus du compte connecté (JWT requis) |
| `GET` | `/api/recus/points` | Solde et progression des points (JWT requis) |
| `POST` | `/api/echanges/points` | Échange de 15 000 points (JWT requis) |
| `GET` | `/api/historique` | Historique des échanges (JWT requis) |

Les identifiants de démonstration définis dans le code sont `admin` / `admin1234` et `client2` / `client1234`. La connexion attend un JSON comme :

```json
{
  "username": "client2",
  "password": "client1234"
}
```

Envoyez le jeton retourné dans l’en-tête `Authorization` sous la forme `Bearer <jeton>` pour les routes protégées.

## Configuration et limites actuelles

- Le fichier OCR utilise un chemin Tesseract spécifique à Windows et stocke temporairement les images sous `C:\temp\photos_tesseract`.
- Le lancement de `app_recu.py` active le mode debug. Ce projet est configuré pour le développement local ; ne l’exposez pas tel quel sur Internet.
- Les identifiants de démonstration et la clé secrète JWT sont codés dans les sources. Remplacez-les et externalisez les secrets avant tout déploiement.
- Le dépôt ne contient actuellement pas de suite de tests automatisés.



