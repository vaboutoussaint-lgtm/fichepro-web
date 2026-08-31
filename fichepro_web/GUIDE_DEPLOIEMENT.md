# 🚀 Guide de déploiement FichePro Web

## Vue d'ensemble

```
Client → https://fichepro-web.onrender.com
           ↓
       Flask (Render.com)
           ↓
       Supabase (base de données cloud)
```

---

## ÉTAPE 1 — Créer la base de données Supabase (5 min)

1. Allez sur **https://supabase.com** → Créez un compte gratuit
2. Cliquez **New project** → Donnez un nom (ex: `fichepro`)
3. Choisissez une région proche (ex: EU West)
4. Attendez la création (~2 min)

5. Menu gauche → **SQL Editor** → **New query**
6. Copiez-collez tout le contenu de **`supabase_setup.sql`**
7. Cliquez **Run** → Vérifiez "Success"

8. Menu gauche → **Project Settings** → **API**
9. Copiez :
   - **Project URL** → ce sera votre `SUPABASE_URL`
   - **service_role** secret key → ce sera votre `SUPABASE_KEY`
   ⚠️ Utilisez bien la clé `service_role`, pas `anon`

---

## ÉTAPE 2 — Déployer sur Render.com (10 min)

1. Allez sur **https://render.com** → Créez un compte gratuit

2. Publiez votre code sur GitHub :
   - Créez un dépôt GitHub (public ou privé)
   - Uploadez tous les fichiers du dossier `fichepro_web/`

3. Sur Render.com → **New** → **Web Service**
4. Connectez votre dépôt GitHub
5. Paramètres :
   - **Name** : `fichepro-web`
   - **Runtime** : Python 3
   - **Build Command** : `pip install -r requirements.txt`
   - **Start Command** : `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2`

6. Ajoutez les **Variables d'environnement** :
   - `SUPABASE_URL` = votre URL Supabase
   - `SUPABASE_KEY` = votre clé service_role Supabase
   - `SECRET_KEY` = une chaîne aléatoire (ex: `fp2025xjkl9abc`)

7. Cliquez **Create Web Service** → Render déploie automatiquement

8. Votre URL : `https://fichepro-web.onrender.com`
   ✅ C'est le lien que vous donnez à vos clients !

---

## ÉTAPE 3 — Ajouter un nouveau client

Dans Supabase → SQL Editor :

```sql
INSERT INTO licenses (license_key, client_name, expiry_date)
VALUES ('CLI-ABCDEF123456', 'Nom du Client', '2026-12-31');
```

Le client reçoit :
- 🔗 Le lien : `https://fichepro-web.onrender.com`
- 🔑 Sa clé : `CLI-ABCDEF123456`

---

## Renouveler/révoquer une licence

```sql
-- Prolonger
UPDATE licenses SET expiry_date = '2027-12-31'
WHERE license_key = 'CLI-ABCDEF123456';

-- Révoquer (désactive immédiatement l'accès)
UPDATE licenses SET expiry_date = '2020-01-01'
WHERE license_key = 'CLI-ABCDEF123456';
```

---

## Fonctionnement multi-clients

- Tous les clients utilisent **le même lien**
- Chacun se connecte avec **sa propre clé**
- Les données sont **100% isolées** (chaque client voit uniquement les siennes)
- Aucune installation côté client

---

## Plan gratuit Render.com

⚠️ Le plan gratuit Render met l'application en veille après 15 min d'inactivité.
Le premier chargement peut prendre 30-60 secondes.

→ Pour éviter cela : passez au plan **Starter** ($7/mois) ou utilisez **UptimeRobot** (gratuit) pour pinger votre app toutes les 5 min.

---

## Ajouter votre propre clé legacy existante

Votre clé `WIL03943487HAY` est déjà insérée dans le SQL.
Elle ne sera pas perdue lors des redéploiements (stockée en base).
