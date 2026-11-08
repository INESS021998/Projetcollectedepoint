# Projet de collecte de points

API Flask de gestion de reçus et de fidélité. Elle comprend une API principale, un service OCR Tesseract et des récompenses en magasin : chaque échange en magasin de 15 000 points donne un crédit de 15 CAD.

## Version et adresses

- Version documentée dans Swagger : **2.0.0**.
- Les URL ci-dessous ne comportent pas encore de préfixe de version.
- API principale : http://127.0.0.1:5000
- Swagger : http://127.0.0.1:5000/apidocs/
- Service OCR : http://127.0.0.1:8000
- Base SQLite : collecte_points.db, configurée dans settings.py.

## Prérequis

- Windows 10 ou 11.
- Python 3 avec le lanceur py dans PowerShell.
- Tesseract OCR pour Windows, avec la langue française fra.traineddata.
- Les paquets Python du fichier requirements.txt.

Le service OCR attend Tesseract à C:\Program Files\Tesseract-OCR\tesseract.exe. Si le moteur est installé ailleurs, modifiez pytesseract.pytesseract.tesseract_cmd dans service_orc/serveur_ocr.py.

## Installation

Dans PowerShell, depuis le dossier du projet :

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Installez Tesseract séparément de pytesseract, puis vérifiez la langue :

```powershell
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --list-langs
```

La liste doit contenir fra.

## Démarrer le projet

L’API et le service OCR sont deux serveurs. Ouvrez deux fenêtres PowerShell dans la racine du projet et laissez-les tourner.

Fenêtre 1 — service OCR :

```powershell
.\.venv\Scripts\python.exe service_orc\serveur_ocr.py
```

Fenêtre 2 — API principale :

```powershell
.\.venv\Scripts\python.exe app_recu.py
```

L’API écoute sur le port 5000 et appelle le service OCR sur le port 8000. Les deux serveurs sont requis pour le parcours OCR. Au démarrage, l’application crée les tables et comptes de démonstration. Si le dossier data ne contient aucune photo, cinq images de reçus fictifs sont générées; ces images ne sont pas enregistrées automatiquement comme reçus dans la base.

## Utiliser le JWT dans Swagger

Les routes métier de l’API principale exigent un JWT, sauf la connexion. Le jeton expire après 2 heures. Les jetons déjà émis conservent leur date d’expiration précédente; reconnectez-vous pour obtenir un jeton de deux heures.

1. Ouvrez http://127.0.0.1:5000/apidocs/.
2. Dépliez POST /api/recu/login, entrez le JSON de connexion ci-dessous et cliquez Exécuter.
3. Copiez le champ token de la réponse.
4. Cliquez sur le cadenas Authorize.
5. Dans Valeur, entrez exactement Bearer, un espace, puis le JWT complet: `Bearer <token>`.
6. Cliquez Autoriser, puis Fermer.
7. Exécutez une route protégée. Swagger transmettra Authorization: Bearer <jeton>.
8. Après expiration, reconnectez-vous et autorisez le nouveau JWT.

Ne collez pas le jeton seul dans Valeur; le préfixe Bearer et l’espace sont obligatoires. Ne partagez pas le JWT. Si le préfixe manque ou si le jeton est expiré, reconnectez-vous; un JWT invalide retourne une erreur 400. Comptes de démonstration : admin / admin1234 et client2 / client1234.

Connexion :

```json
{
  "username": "client2",
  "password": "client1234"
}
```

## Routes de l’API principale

| Méthode | Route | JWT | But |
|---|---|---|---|
| GET | / | Non | Vérifier que l’API répond; message d’accueil. |
| POST | /api/recu/login | Non | Valider la connexion et retourner le JWT. |
| POST | /api/recu/ocr | Oui | Analyser une photo et proposer les champs du reçu. |
| POST | /api/recu | Oui | Confirmer un reçu et attribuer les points. |
| GET | /api/recu/ | Oui | Lister les reçus du compte connecté. |
| GET | /api/points/conversion | Oui | Convertir les points accumulés en valeur CAD (consultation sans dépense). |
| GET | /api/magasins | Oui | Voir les magasins éligibles à l’échange et le solde de points du client. |
| POST | /api/echanges/points | Oui | Dépenser 15 000 points pour un crédit de 15 CAD. |
| GET | /api/historique | Oui | Voir les échanges en magasin du compte connecté. |

Pour exécuter une route dans Swagger, dépliez-la, cliquez Essayer, renseignez les paramètres indiqués, puis cliquez Exécuter. Swagger décrit les corps et les réponses sous chaque route.

### Parcours d’un reçu

1. POST /api/recu/ocr : chargez photo comme fichier multipart/form-data (JPG, JPEG ou PNG, maximum 5 Mo). Réponse 200 : informations détectées, nom photo à conserver et confirmation_requise.
2. Vérifiez les valeurs détectées, puis POST /api/recu avec ce JSON. Le champ photo reprend le nom retourné par OCR :

```json
{
  "date_recu": "2026-10-05",
  "magasin": "Soucy-Chouinard",
  "montant": 17.88,
  "photo": "nom-retourne-par-ocr.png"
}
```

La date doit être AAAA-MM-JJ, le montant positif avec au maximum deux décimales et la photo doit exister dans data. Succès : 201 avec les détails et les points. Un doublon retourne 409; des données ou une photo invalides retournent 400. La règle actuelle attribue int(montant) × 2 points.

### Points, magasins et récompenses

GET /api/points/conversion est une consultation sans dépense : il retourne points_accumules, points_utilises, solde, valeur_points_accumules_cad et valeur_solde_cad (1 000 points = 1 CAD), puis points_requis_pour_echange_en_magasin, points_restants_pour_echange_en_magasin, credit_obtenu_si_echange_en_magasin_cad et echange_en_magasin_autorise. La réponse indique aussi devise: CAD. À 15 000 points, le client peut faire un échange en magasin et recevoir un crédit de 15 CAD.

GET /api/magasins renvoie la liste magasins_eligibles_pour_echange et un objet points_client calculé pour le JWT connecté : total accumulé sur tous ses reçus, points déjà utilisés, solde disponible, valeur CAD du total et du solde, points restants avant le seuil de 15 000, crédit qui serait obtenu après un échange en magasin (15 CAD) et indicateur echange_en_magasin_autorise. Le taux de conversion est de 1 000 points pour 1 CAD.

Pour échanger, POST /api/echanges/points demande le nom d’un magasin de la liste magasins_eligibles_pour_echange :

```json
{
  "magasin": "Soucy-Chouinard"
}
```

Réponse 201 : identifiant d’échange, magasin, 15 000 points utilisés, valeur du crédit de 15 CAD et nouveau solde. Magasin absent/inconnu : 400. Solde insuffisant : 422 avec le solde, les points manquants et les points requis; aucun échange n’est créé.

GET /api/historique retourne la date, le magasin, les points utilisés et la valeur CAD des échanges en magasin du compte connecté.

Cette récompense est enregistrée comme crédit à utiliser en magasin; l’application ne choisit pas le produit, ne gère pas de catalogue ni de stock, et ne confirme pas le passage en caisse. L’historique antérieur est conservé; son magasin est nul, car il n’était pas enregistré auparavant. La liste des magasins est dérivée des reçus, et ne constitue pas une liste certifiée de partenaires.

## Service OCR séparé

Ce service n’est pas inclus dans Swagger de l’API principale. L’API l’appelle à POST http://127.0.0.1:8000/api/recus/tesseract avec photo en multipart/form-data. Le service accepte JPG/JPEG/PNG et retourne le texte extrait et un nom de fichier temporaire. Sa route ne vérifie pas directement le JWT; elle est prévue comme composant local derrière l’API principale.

## Configuration et limites

- Les photos OCR temporaires sont placées dans C:\temp\photos_tesseract; les photos utilisées pour confirmer les reçus se trouvent dans data.
- Flask tourne en mode développement/debug; ne pas exposer ce serveur sur Internet.
- Les comptes de démonstration et le secret JWT sont codés dans les sources. Les externaliser et les remplacer avant tout déploiement.
- Le dépôt ne contient pas actuellement de suite de tests automatisés.
