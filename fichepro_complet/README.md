# FichePro Manager — Guide d'installation et déploiement

## Pour l'utilisateur final (votre client)

### Ce qu'il reçoit
- **FichePro Manager.exe** — double-cliquer, c'est tout

### Ce qui se passe
1. L'application démarre en arrière-plan (pas de fenêtre noire)
2. Le navigateur s'ouvre automatiquement sur `http://localhost:5000`
3. L'utilisateur entre sa clé de licence
4. Il importe son registre Rainforest Alliance
5. Il génère ses fiches

### Note Windows au premier lancement
Windows affiche : *"Windows a protégé votre ordinateur"*
→ Cliquer **"Informations complémentaires"**
→ Cliquer **"Exécuter quand même"**
→ Cette alerte ne réapparaît plus jamais

---

## Pour vous (Wilfried) — Compiler le .exe

### Prérequis
- Python 3.10+ installé sur Windows
- Tous les fichiers du projet dans un dossier

### Étapes
```
1. Ouvrir un terminal dans le dossier du projet
2. Double-cliquer sur build.bat
3. Le .exe se crée dans le dossier dist/
```

---

## Serveur de licences (à configurer une fois)

Le serveur de vérification des licences doit tourner en permanence.

### Option gratuite : PythonAnywhere
1. Créer un compte sur pythonanywhere.com (gratuit)
2. Déployer le fichier `license_server.py`
3. Mettre à jour l'URL dans `license_manager.py`

### Générer une clé de licence
Format : `FPM-XXXX-XXXX-XXXX-XXXX`
Les clés sont générées depuis votre panneau admin sur le serveur.

---

## Structure du projet

```
fichepro/
├── app.py              — Serveur Flask (point d'entrée)
├── engine.py           — Moteur métier (logique VBA réécrite en Python)
├── license_manager.py  — Gestion des licences
├── export_pdf.py       — Export PDF des fiches
├── requirements.txt    — Dépendances Python
├── build.bat           — Script de compilation .exe
├── templates/
│   ├── index.html      — Page d'accueil / présentation
│   └── app.html        — Interface principale de l'application
├── static/
│   ├── css/            — Styles
│   └── js/             — Scripts
└── data/
    ├── config.json     — Configuration utilisateur
    └── license.json    — Licence locale
```

---

## Contact concepteur

**Wilfried VABOU**
Responsable Durabilité · Concepteur FichePro Manager
- Tél : +225 07 08 73 55 67
- WhatsApp : +225 01 02 02 50 25
- Email : vaboutoussaint@gmail.com
- Organisation : CAPRESSA · Côte d'Ivoire
