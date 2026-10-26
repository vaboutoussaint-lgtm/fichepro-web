"""
FichePro v4 - Backend Flask complet
Gestion répartition cacao certifié RA/FT
"""

import os
import json
import random
import hashlib
import secrets
import math
from datetime import datetime, date, timedelta
from io import BytesIO

import requests
from flask import Flask, request, jsonify, session, send_file
from flask_cors import CORS
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

app = Flask(__name__, static_folder='.', static_url_path='')

# ── SECRET_KEY ──────────────────────────────────────────────
_env_secret = os.environ.get('SECRET_KEY')
if _env_secret:
    app.secret_key = _env_secret
else:
    app.secret_key = secrets.token_hex(32)
    print(
        "[WARN] SECRET_KEY non définie — une clé aléatoire a été générée.\n"
        "       Les utilisateurs seront déconnectés à chaque redémarrage.\n"
        "       Définissez la variable d'environnement SECRET_KEY en production."
    )

CORS(app, supports_credentials=True)

# ============================================================
# CONFIGURATION SUPABASE
# ============================================================
SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')

def sb_headers():
    return {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=representation'
    }

# FIX #2 — Supabase/PostgREST renvoie max 1000 lignes par défaut (plan gratuit).
# On pagine automatiquement par tranches de 1000 pour tout ramener.
def sb_get(table, params=None):
    url = f'{SUPABASE_URL}/rest/v1/{table}'
    if params is None:
        params = {}

    # Si l'appelant a fixé une limit explicite (ex: '50' pour le journal), on respecte
    explicit_limit = 'limit' in params

    if explicit_limit:
        r = requests.get(url, headers=sb_headers(), params=params, timeout=30)
        if r.status_code in (200, 206):
            return r.json()
        return []

    # Pagination automatique : on lit par tranches de 1000 jusqu'à épuisement
    PAGE_SIZE = 1000
    all_rows = []
    offset = 0
    while True:
        page_params = dict(params)
        page_params['limit'] = str(PAGE_SIZE)
        page_params['offset'] = str(offset)
        r = requests.get(url, headers=sb_headers(), params=page_params, timeout=30)
        if r.status_code not in (200, 206):
            break
        batch = r.json()
        if not batch:
            break
        all_rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            # Dernière page
            break
        offset += PAGE_SIZE
    return all_rows

def sb_post(table, data):
    url = f'{SUPABASE_URL}/rest/v1/{table}'
    r = requests.post(url, headers=sb_headers(), json=data, timeout=15)
    if r.status_code in (200, 201):
        return r.json()
    return None

def sb_patch(table, filters, data):
    url = f'{SUPABASE_URL}/rest/v1/{table}'
    params = {k: f'eq.{v}' for k, v in filters.items()}
    r = requests.patch(url, headers=sb_headers(), params=params, json=data, timeout=15)
    return r.status_code in (200, 204)

def sb_delete(table, filters):
    url = f'{SUPABASE_URL}/rest/v1/{table}'
    params = {k: f'eq.{v}' for k, v in filters.items()}
    r = requests.delete(url, headers=sb_headers(), params=params, timeout=15)
    return r.status_code in (200, 204)

def get_config():
    rows = sb_get('config')
    return {r['cle']: r['valeur'] for r in rows}

def journaliser(nom_agent, profil, action, details='', reference=''):
    sb_post('journal', {
        'nom_agent': nom_agent,
        'profil': profil,
        'action': action,
        'details': details,
        'reference': reference
    })

# ============================================================
# AUTHENTIFICATION
# ============================================================

def hash_code(code):
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()

def get_current_user():
    token = session.get('token')
    if not token:
        return None
    rows = sb_get('sessions', {'token': f'eq.{token}', 'date_deconnexion': 'is.null', 'select': '*'})
    return rows[0] if rows else None

def require_auth(profils=None):
    def decorator(f):
        def wrapper(*args, **kwargs):
            user = get_current_user()
            if not user:
                return jsonify({'error': 'Non authentifié'}), 401
            if profils and user['profil'] not in profils:
                return jsonify({'error': 'Accès refusé'}), 403
            return f(*args, **kwargs)
        wrapper.__name__ = f.__name__
        return wrapper
    return decorator

# FIX #1 — Accepte les codes HASHÉS (créés via /api/codes-acces) ET les codes
# EN CLAIR (seedés via la migration SQL). Sans ça, personne ne peut se connecter
# au premier déploiement avec DIR2024 / TRACE2024.
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    code = data.get('code_acces', '').strip()
    nom = data.get('nom_agent', '').strip()
    if not code or not nom:
        return jsonify({'error': 'Code d\'accès et nom requis'}), 400

    code_hash = hash_code(code)
    # 1. Chercher par hash (codes créés via /api/codes-acces)
    users = sb_get('utilisateurs', {'code_acces': f'eq.{code_hash}', 'actif': 'eq.true', 'select': '*'})
    # 2. Fallback en clair (codes seedés via SQL : DIR2024, TRACE2024)
    if not users:
        users = sb_get('utilisateurs', {'code_acces': f'eq.{code.strip().upper()}', 'actif': 'eq.true', 'select': '*'})
    if not users:
        return jsonify({'error': 'Code d\'accès invalide'}), 401

    user = users[0]
    token = secrets.token_hex(32)

    sb_post('sessions', {
        'nom_agent': nom,
        'profil': user['profil'],
        'token': token
    })

    session['token'] = token
    journaliser(nom, user['profil'], 'CONNEXION', 'Connexion réussie')

    return jsonify({'profil': user['profil'], 'nom_agent': nom})

@app.route('/api/logout', methods=['POST'])
def logout():
    user = get_current_user()
    if user:
        sb_patch('sessions', {'token': session.get('token')}, {'date_deconnexion': datetime.utcnow().isoformat()})
        journaliser(user['nom_agent'], user['profil'], 'DECONNEXION')
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/me', methods=['GET'])
def me():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Non authentifié'}), 401
    return jsonify({'nom_agent': user['nom_agent'], 'profil': user['profil']})

# ============================================================
# CONFIG
# ============================================================

@app.route('/api/config', methods=['GET'])
def get_config_api():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Non authentifié'}), 401
    return jsonify(get_config())

@app.route('/api/config', methods=['POST'])
def update_config():
    user = get_current_user()
    if not user or user['profil'] not in ('DIRECTEUR', 'TRACABILITE'):
        return jsonify({'error': 'Accès refusé'}), 403
    data = request.json
    headers_upsert = dict(sb_headers())
    headers_upsert['Prefer'] = 'resolution=merge-duplicates,return=minimal'
    for cle, valeur in data.items():
        # UPSERT : crée la ligne si elle n'existe pas, met à jour sinon
        requests.post(
            f"{SUPABASE_URL}/rest/v1/config",
            headers=headers_upsert,
            json={'cle': cle, 'valeur': str(valeur)},
            timeout=15
        )
    journaliser(user['nom_agent'], user['profil'], 'CONFIG_MODIFIEE', json.dumps(data))
    return jsonify({'ok': True})

# ============================================================
# IMPORT REGISTRE
# ============================================================

@app.route('/api/import-registre', methods=['POST'])
def import_registre():
    user = get_current_user()
    if not user or user['profil'] != 'TRACABILITE':
        return jsonify({'error': 'Accès refusé'}), 403

    if 'file' not in request.files:
        return jsonify({'error': 'Fichier requis'}), 400

    f = request.files['file']
    filename = f.filename.lower()

    try:
        import csv
        import io
        from openpyxl import load_workbook

        def parse_file(f, filename):
            if filename.endswith('.csv'):
                content = f.read().decode('utf-8-sig')
                sep = ';' if content.count(';') > content.count(',') else ','
                reader = csv.DictReader(io.StringIO(content), delimiter=sep)
                rows = list(reader)
                return [{k.strip().upper().replace(' ', '_'): v for k, v in r.items()} for r in rows]
            elif filename.endswith(('.xlsx', '.xlsm')):
                # NOTE: .xls (ancien format binaire) n'est PAS supporté par openpyxl
                wb = load_workbook(io.BytesIO(f.read()), read_only=True, data_only=True)
                ws = wb.active
                rows_iter = ws.iter_rows(values_only=True)
                headers = [str(c).strip().upper().replace(' ', '_') if c else '' for c in next(rows_iter)]
                result = []
                for row in rows_iter:
                    result.append({headers[i]: row[i] for i in range(len(headers))})
                wb.close()
                return result
            else:
                return None

        rows = parse_file(f, filename)
        if rows is None:
            return jsonify({'error': 'Format non supporté (CSV, XLSX ou XLSM requis)'}), 400
        if not rows:
            return jsonify({'error': 'Fichier vide'}), 400

        all_cols = list(rows[0].keys())
        required = ['CODE_PARCELLE', 'CODE_PRODUCTEUR', 'NOM_PRENOMS', 'ESTIMATION_KG']
        missing = [c for c in required if c not in all_cols]
        if missing:
            return jsonify({'error': f'Colonnes manquantes: {", ".join(missing)}'}), 400

        ra_col = next((c for c in all_cols if 'RAINFOREST' in c or c == 'RA'), None)
        ft_col = next((c for c in all_cols if 'FAIRTRADE' in c or c == 'FT'), None)

        def to_bool(val):
            if val is None:
                return False
            return str(val).strip().upper() in ('OUI', 'YES', 'TRUE', '1', 'O')

        def to_float(val, default=0):
            try:
                return float(val) if val not in (None, '', 'None') else default
            except (ValueError, TypeError):
                return default

        parcelles_list = []
        errors = []

        for row in rows:
            try:
                code_parcelle = str(row.get('CODE_PARCELLE') or '').strip()
                if not code_parcelle or code_parcelle.lower() in ('none', 'nan', ''):
                    continue
                lat = row.get('LATITUDE')
                lon = row.get('LONGITUDE')
                parcelles_list.append({
                    'code_parcelle': code_parcelle,
                    'code_producteur': str(row.get('CODE_PRODUCTEUR') or '').strip(),
                    'nom_producteur': str(row.get('NOM_PRENOMS') or '').strip(),
                    'section': str(row.get('SECTION') or '').strip() or None,
                    'village': str(row.get('VILLAGE') or '').strip() or None,
                    'superficie_ha': to_float(row.get('SUPERFICIE_HA'), 0),
                    'estimation_kg': to_float(row.get('ESTIMATION_KG'), 0),
                    'latitude': to_float(lat) if lat not in (None, '', 'None') else None,
                    'longitude': to_float(lon) if lon not in (None, '', 'None') else None,
                    'rainforest_alliance': to_bool(row.get(ra_col)) if ra_col else False,
                    'fairtrade': to_bool(row.get(ft_col)) if ft_col else False,
                    'actif': True,
                })
            except Exception as e:
                errors.append(str(e))

        nb_insere = 0
        BATCH = 200
        for i in range(0, len(parcelles_list), BATCH):
            batch = parcelles_list[i:i+BATCH]
            url = f'{SUPABASE_URL}/rest/v1/parcelles'
            hdrs = {**sb_headers(), 'Prefer': 'resolution=merge-duplicates,return=representation'}
            r = requests.post(url, headers=hdrs, json=batch, timeout=30)
            if r.status_code in (200, 201):
                result = r.json()
                nb_insere += len(result)
            else:
                errors.append(f'Batch {i//BATCH+1}: {r.text[:200]}')

        nb_total = len(parcelles_list)
        journaliser(user['nom_agent'], user['profil'], 'IMPORT_REGISTRE',
                    f'{nb_total} parcelles importées (upsert batch)')

        return jsonify({
            'ok': True,
            'insere': nb_total,
            'maj': 0,
            'erreurs': errors[:10]
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================================
# PARCELLES
# ============================================================

@app.route('/api/parcelles', methods=['GET'])
def get_parcelles():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Non authentifié'}), 401

    label = request.args.get('label')
    params = {'actif': 'eq.true', 'select': '*', 'order': 'nom_producteur.asc'}
    if label == 'RA':
        params['rainforest_alliance'] = 'eq.true'
    elif label == 'FT':
        params['fairtrade'] = 'eq.true'

    parcelles = sb_get('parcelles', params)
    return jsonify(parcelles)

# ============================================================
# ALGORITHME DE REPARTITION
# ============================================================

def calculer_stock_disponible(p, cfg, saison):
    estim = float(p.get('estimation_kg', 0))
    livre_gt = float(p.get('volume_livre_gt', 0))
    livre_pt = float(p.get('volume_livre_pt', 0))
    reserve_gt = float(p.get('reserve_gt', 0))
    reserve_pt = float(p.get('reserve_pt', 0))
    report = float(p.get('report_gt_vers_pt', 0))
    quota_gt = float(cfg.get('QUOTA_GT', 0.80))

    if saison == 'GT':
        plafond = estim * quota_gt
        dispo = plafond - livre_gt - reserve_gt
    else:
        quota_gt_vol = estim * quota_gt
        dispo_pt_base = estim - quota_gt_vol
        dispo = dispo_pt_base + report - livre_pt - reserve_pt

    return max(0, dispo)

def quota_producteur_atteint(p, cfg, saison):
    """Retourne True si le producteur a atteint son quota de période (VBA: PCT_GT/PCT_PT).
    GT : livré >= estimation * 80%
    PT : livré >= estimation - (estimation * 80%) + report
    """
    estim = float(p.get('estimation_kg', 0))
    if estim <= 0:
        return True
    quota_gt = float(cfg.get('QUOTA_GT', 0.80))
    if saison == 'GT':
        return float(p.get('volume_livre_gt', 0)) >= estim * quota_gt
    else:
        quota_gt_vol = estim * quota_gt
        plafond_pt = estim - quota_gt_vol + float(p.get('report_gt_vers_pt', 0))
        return float(p.get('volume_livre_pt', 0)) >= plafond_pt

def calculer_score(p, cfg, saison, date_ref):
    w1 = float(cfg.get('SCORE_UTILISATION', 0.50))
    w2 = float(cfg.get('SCORE_VOLUME', 0.30))
    w3 = float(cfg.get('SCORE_DISPO', 0.20))

    estim = float(p.get('estimation_kg', 0))
    livre = float(p.get('volume_livre_gt', 0)) if saison == 'GT' else float(p.get('volume_livre_pt', 0))
    stk = calculer_stock_disponible(p, cfg, saison)

    taux = (livre / estim) if estim > 0 else 1.0
    taux = min(taux, 1.0)

    interv = int(cfg.get('INTERV_JOURS', 14))
    derniere = p.get('_derniere_livraison')
    if derniere:
        d = datetime.strptime(str(derniere)[:10], '%Y-%m-%d').date()
        jours = (date_ref - d).days
    else:
        jours = interv * 3

    score = (1 - taux) * w1 + min(1.0, stk / 1000.0) * w2 + min(1.0, jours / (interv * 3)) * w3
    return round(score, 6), stk

def get_derniere_livraison(code_parcelle):
    rows = sb_get('lignes_repartition', {
        'code_parcelle': f'eq.{code_parcelle}',
        'select': 'date_livraison',
        'order': 'date_livraison.desc',
        'limit': '1'
    })
    if rows and rows[0].get('date_livraison'):
        return rows[0]['date_livraison']
    return None

def date_livraison_optimale(d_debut, d_fin, derniere_livraison_str, interv_jours, densite_hebdo):
    """VBA DateLivOptimale : choisit une date dans [d_debut, d_fin] en favorisant
    les semaines les moins chargées (densite_hebdo), aléatoire sur égalité.
    Respecte l'intervalle minimum depuis la dernière livraison de la parcelle.
    densite_hebdo : dict {numéro_semaine_iso: count} — modifié en place après attribution.
    """
    from datetime import timedelta

    contrainte = d_debut
    if derniere_livraison_str:
        try:
            d_last = datetime.strptime(str(derniere_livraison_str)[:10], '%Y-%m-%d').date()
            d_ideal = d_last + timedelta(days=interv_jours)
            if d_ideal > contrainte:
                contrainte = d_ideal
        except ValueError:
            pass

    if contrainte > d_fin:
        contrainte = d_fin

    # Construire la liste des dates candidates avec leur charge hebdomadaire
    nb_jours = (d_fin - contrainte).days + 1
    if nb_jours <= 0:
        return d_fin

    charges = []
    d = contrainte
    for _ in range(nb_jours):
        sem = d.isocalendar()[1]  # numéro de semaine ISO
        charge = densite_hebdo.get(sem, 0)
        charges.append((charge, d))
        d += timedelta(days=1)

    min_charge = min(c for c, _ in charges)
    candidats = [dt for c, dt in charges if c == min_charge]

    # Tirage au sort parmi les candidats de même charge (VBA: Rnd)
    choix = random.choice(candidats)

    # Incrémenter la densité de la semaine choisie
    sem_choix = choix.isocalendar()[1]
    densite_hebdo[sem_choix] = densite_hebdo.get(sem_choix, 0) + 1

    return choix

@app.route('/api/repartition/simuler', methods=['POST'])
def simuler_repartition():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Non authentifié'}), 401

    data = request.json
    label = data.get('label')
    volume_total = float(data.get('volume_total', 0))
    date_ref_str = data.get('date_ref', date.today().isoformat())
    date_debut_str = data.get('date_debut_livraison', date_ref_str)  # VBA: dDebutLiv
    sections = data.get('sections', [])
    plafond_override = data.get('plafond_override')
    sauter_intervalle = data.get('sauter_intervalle', False)

    if not label or volume_total <= 0:
        return jsonify({'error': 'Label et volume requis'}), 400
    if label not in ('RA', 'FT'):
        return jsonify({'error': 'Label invalide (RA ou FT)'}), 400

    date_ref = datetime.strptime(date_ref_str, '%Y-%m-%d').date()
    date_debut = datetime.strptime(date_debut_str, '%Y-%m-%d').date()
    if date_debut > date_ref:
        date_debut = date_ref
    cfg = get_config()
    saison = cfg.get('SAISON_EN_COURS', 'GT')
    poids_min = float(cfg.get('POIDS_MIN', 25))
    poids_sac = float(cfg.get('POIDS_SAC_KG', 75))
    plafond_base = float(cfg.get('PLAFOND_PARCELLE', 0.30))
    interv_jours = int(cfg.get('INTERV_JOURS', 14))

    plafond = float(plafond_override) if plafond_override else plafond_base

    params = {'actif': 'eq.true', 'select': '*'}
    if label == 'RA':
        params['rainforest_alliance'] = 'eq.true'
    elif label == 'FT':
        params['fairtrade'] = 'eq.true'
    if sections:
        sections_str = ','.join(sections)
        params['section'] = f'in.({sections_str})'

    parcelles = sb_get('parcelles', params)
    if not parcelles:
        return jsonify({'error': 'Aucune parcelle éligible'}), 400

    # FIX #3 — Charge en UNE passe les dernières livraisons (batch par 200 codes)
    # au lieu de N requêtes individuelles (N parcelles). Gain : ~20 min → ~5 s.
    all_codes = [p['code_parcelle'] for p in parcelles]
    dernieres = {}
    BATCH_Q = 200
    for i in range(0, len(all_codes), BATCH_Q):
        batch = all_codes[i:i+BATCH_Q]
        codes_str = ','.join('"' + c.replace('"', '') + '"' for c in batch)
        rows = sb_get('lignes_repartition', {
            'code_parcelle': f'in.({codes_str})',
            'select': 'code_parcelle,date_livraison'
        })
        for r in rows:
            cp = r.get('code_parcelle')
            dl = r.get('date_livraison')
            if not cp:
                continue
            if cp not in dernieres or (dl and dl > (dernieres[cp] or '')):
                dernieres[cp] = dl
    for p in parcelles:
        p['_derniere_livraison'] = dernieres.get(p['code_parcelle'])

    eligibles = []
    exclus_intervalle = []
    exclus_stock = []
    exclus_quota = []

    for p in parcelles:
        # FIX #1 — Vérification quota période (VBA: PCT_GT=80% / PCT_PT=20%)
        if quota_producteur_atteint(p, cfg, saison):
            exclus_quota.append(p['code_parcelle'])
            continue

        stk = calculer_stock_disponible(p, cfg, saison)
        if stk < poids_min:
            exclus_stock.append(p['code_parcelle'])
            continue

        derniere = p.get('_derniere_livraison')
        if derniere and not sauter_intervalle:
            d = datetime.strptime(str(derniere)[:10], '%Y-%m-%d').date()
            jours = (date_ref - d).days
            if jours < interv_jours:
                exclus_intervalle.append({'code': p['code_parcelle'], 'jours_restants': interv_jours - jours})
                continue

        eligibles.append(p)

    if not eligibles:
        return jsonify({
            'error': 'Aucune parcelle disponible',
            'exclus_stock': exclus_stock,
            'exclus_intervalle': exclus_intervalle
        }), 400

    for p in eligibles:
        score, stk = calculer_score(p, cfg, saison, date_ref)
        p['_score'] = score
        p['_stk_dispo'] = stk

    random.shuffle(eligibles)
    eligibles.sort(key=lambda p: p['_score'], reverse=True)

    volume_restant = volume_total
    lignes = []

    # VBA: g_densiteHebdo — densité de livraisons par semaine ISO, pour répartir les dates
    densite_hebdo = {}
    # Pré-charger les livraisons existantes de la saison en cours dans la densité
    try:
        lignes_existantes = sb_get('lignes_repartition', {'select': 'date_livraison'})
        for le in lignes_existantes:
            dl = le.get('date_livraison')
            if dl:
                try:
                    sem = datetime.strptime(str(dl)[:10], '%Y-%m-%d').date().isocalendar()[1]
                    densite_hebdo[sem] = densite_hebdo.get(sem, 0) + 1
                except ValueError:
                    pass
    except Exception:
        pass

    # Seuil au-delà duquel on fractionne en plusieurs livraisons pour plus de réalisme
    MAX_PAR_LIVRAISON_KG = float(cfg.get('MAX_PAR_LIVRAISON_KG', 2500))

    prix_cle = 'PRIX_KG_GT' if saison == 'GT' else 'PRIX_KG_PT'
    prix_kg = float(cfg.get(prix_cle, 0))

    max_prod_kg = float(cfg.get('MAX_PROD_KG', 3000))  # Plafond par producteur (toutes parcelles confondues)

    # Suivi du volume déjà attribué par producteur (pour respecter MAX_PROD_KG au niveau producteur)
    volume_par_producteur = {}

    for p in eligibles:
        if volume_restant < poids_min:
            break

        stk = p['_stk_dispo']
        code_prod = p['code_producteur']

        # Volume déjà attribué à ce producteur dans cette fiche
        deja_attribue = volume_par_producteur.get(code_prod, 0)
        reste_prod = max_prod_kg - deja_attribue
        if reste_prod < poids_min:
            continue  # Ce producteur a déjà atteint son plafond

        # Plafond 30% par parcelle + MAX_PROD par producteur (VBA: PlafondProducteur)
        max_attrib = min(stk, volume_total * plafond, volume_restant, reste_prod)

        if max_attrib < poids_min:
            continue

        nb_sacs_max = math.floor(max_attrib / poids_sac)
        if nb_sacs_max == 0:
            continue

        attrib_total_parcelle = nb_sacs_max * poids_sac
        if attrib_total_parcelle > volume_restant:
            nb_sacs_max = math.floor(volume_restant / poids_sac)
            if nb_sacs_max == 0:
                break
            attrib_total_parcelle = nb_sacs_max * poids_sac

        if attrib_total_parcelle > stk or attrib_total_parcelle < poids_min:
            continue

        # Fractionner en plusieurs livraisons si volume > MAX_PAR_LIVRAISON_KG
        sacs_par_livraison = max(1, math.floor(MAX_PAR_LIVRAISON_KG / poids_sac))
        sacs_restants = nb_sacs_max
        num_livraison = 1

        # VBA DateLivOptimale : date optimale pour cette parcelle
        # Si le producteur a déjà une parcelle attribuée, on décale d'au moins INTERV_JOURS
        # pour éviter deux livraisons identiques le même jour pour le même producteur
        derniere_liv_producteur = p.get('_derniere_livraison')
        if deja_attribue > 0:
            # Chercher la date la plus récente déjà attribuée à ce producteur dans cette fiche
            dates_prod = [
                l['date_livraison'] for l in lignes
                if l['code_producteur'] == code_prod
            ]
            if dates_prod:
                from datetime import timedelta
                date_max_str = max(dates_prod)
                derniere_liv_producteur = date_max_str  # forcer le décalage à partir de cette date
        date_liv_parcelle = date_livraison_optimale(
            date_debut, date_ref,
            derniere_liv_producteur,
            interv_jours, densite_hebdo
        )

        while sacs_restants > 0:
            sacs_cette_livraison = min(sacs_par_livraison, sacs_restants)
            if sacs_cette_livraison == 0:
                break
            vol_livraison = sacs_cette_livraison * poids_sac
            montant_livraison = vol_livraison * prix_kg

            lignes.append({
                'code_parcelle': p['code_parcelle'],
                'code_producteur': p['code_producteur'],
                'nom_producteur': p['nom_producteur'],
                'section': p.get('section', ''),
                'latitude': round(float(p['latitude']), 6) if p.get('latitude') is not None else None,
                'longitude': round(float(p['longitude']), 6) if p.get('longitude') is not None else None,
                'score_calcule': p['_score'],
                'volume_attribue': vol_livraison,
                'nombre_sacs': sacs_cette_livraison,
                'montant': montant_livraison,
                'stk_dispo': stk,
                'num_livraison': num_livraison,
                'date_livraison': date_liv_parcelle.isoformat(),
            })

            sacs_restants -= sacs_cette_livraison
            num_livraison += 1

        volume_restant -= attrib_total_parcelle
        # Comptabiliser au niveau producteur
        volume_par_producteur[code_prod] = volume_par_producteur.get(code_prod, 0) + attrib_total_parcelle

    if not lignes:
        return jsonify({'error': 'Impossible de répartir le volume demandé'}), 400

    # FIX #3 — AbsorberResidu (VBA) : si reliquat >= poids_min, on tente
    # T1 : ajouter un nouveau producteur (premier éligible non encore inclus)
    # T2 : augmenter un producteur déjà dans la fiche (le meilleur score)
    codes_inclus = {l['code_parcelle'] for l in lignes}
    if volume_restant >= poids_min:
        nouveaux = [p for p in eligibles if p['code_parcelle'] not in codes_inclus]
        if nouveaux:
            # T1 : premier producteur disponible non inclus
            p = nouveaux[0]
            stk = p['_stk_dispo']
            max_attrib = min(stk, volume_total * plafond, volume_restant, max_prod_kg)
            nb_sacs_residuel = math.floor(max_attrib / poids_sac)
            if nb_sacs_residuel > 0:
                vol_r = nb_sacs_residuel * poids_sac
                lignes.append({
                    'code_parcelle': p['code_parcelle'],
                    'code_producteur': p['code_producteur'],
                    'nom_producteur': p['nom_producteur'],
                    'section': p.get('section', ''),
                    'latitude': round(float(p['latitude']), 6) if p.get('latitude') is not None else None,
                    'longitude': round(float(p['longitude']), 6) if p.get('longitude') is not None else None,
                    'score_calcule': p['_score'],
                    'volume_attribue': vol_r,
                    'nombre_sacs': nb_sacs_residuel,
                    'montant': vol_r * prix_kg,
                    'stk_dispo': stk,
                    'num_livraison': 1,
                    'absorbe_residu': True,
                })
                volume_restant -= vol_r
        elif volume_restant >= poids_min:
            # T2 : augmenter un producteur déjà inclus (meilleur score en tête)
            for ligne in lignes:
                cp = ligne['code_parcelle']
                p_ref = next((p for p in eligibles if p['code_parcelle'] == cp), None)
                if not p_ref:
                    continue
                stk_restant = p_ref['_stk_dispo'] - ligne['volume_attribue']
                if stk_restant < poids_sac:
                    continue
                sacs_add = math.floor(min(stk_restant, volume_restant) / poids_sac)
                if sacs_add > 0:
                    vol_add = sacs_add * poids_sac
                    ligne['volume_attribue'] += vol_add
                    ligne['nombre_sacs'] += sacs_add
                    ligne['montant'] += vol_add * prix_kg
                    volume_restant -= vol_add
                    break

    # ── AJUSTEMENT FINAL POUR ATTEINDRE LE VOLUME EXACT ───────────────────────
    # Si volume_restant > 0 après AbsorberResidu, deux cas :
    # 1) volume_restant < poids_sac : on ajoute le reliquat en kg brut au dernier
    #    producteur (hors contrainte sac) pour atteindre EXACTEMENT le volume demandé.
    # 2) volume_restant >= poids_sac : les sections sélectionnées n'ont pas assez
    #    de stock éligible → on retourne une erreur détaillée avec suggestions.
    if volume_restant > 0:
        if volume_restant < poids_sac:
            # Cas 1 : reliquat infra-sac — on l'absorbe en kg brut dans la dernière ligne
            if lignes:
                ligne_cible = lignes[-1]
                ligne_cible['volume_attribue'] += volume_restant
                ligne_cible['montant'] += volume_restant * prix_kg
                # nombre_sacs reste inchangé : il s'agit d'un ajout fractionné
                ligne_cible['ajustement_kg_brut'] = volume_restant
                volume_restant = 0
        else:
            # Cas 2 : stock insuffisant dans les sections sélectionnées
            stock_total_eligible = sum(p['_stk_dispo'] for p in eligibles)
            stock_total_exclus = sum(calculer_stock_disponible(p, cfg, saison)
                                     for p in parcelles
                                     if p['code_parcelle'] not in {e['code_parcelle'] for e in eligibles}
                                     and not quota_producteur_atteint(p, cfg, saison))

            sections_selectionnees = set(p.get('section', '') for p in eligibles)
            sections_manquantes_volumes = {}
            for p in parcelles:
                sec = p.get('section', 'Inconnue')
                if sec not in sections_selectionnees:
                    stk = calculer_stock_disponible(p, cfg, saison)
                    if stk >= poids_min and not quota_producteur_atteint(p, cfg, saison):
                        sections_manquantes_volumes[sec] = sections_manquantes_volumes.get(sec, 0) + stk

            suggestions = [
                {'section': s, 'volume_disponible': round(v)}
                for s, v in sorted(sections_manquantes_volumes.items(), key=lambda x: -x[1])
            ]
            return jsonify({
                'error': 'VOLUME_INSUFFISANT',
                'message': (
                    f"Volume insuffisant dans les sections sélectionnées. "
                    f"Demandé : {volume_total:.0f} kg. "
                    f"Affectable : {volume_total - volume_restant:.0f} kg. "
                    f"Manque : {volume_restant:.0f} kg."
                ),
                'volume_demande': volume_total,
                'volume_affectable': volume_total - volume_restant,
                'volume_manquant': volume_restant,
                'sections_supplementaires': suggestions,
            }), 422

    volume_affecte = volume_total - volume_restant
    nb_sacs_total = sum(l['nombre_sacs'] for l in lignes)

    return jsonify({
        'ok': True,
        'label': label,
        'saison': saison,
        'volume_total': volume_total,
        'volume_affecte': volume_affecte,
        'volume_restant': volume_restant,
        'nombre_lignes': len(lignes),
        'nombre_sacs': nb_sacs_total,
        'plafond_applique': plafond,
        'intervalle_saute': sauter_intervalle,
        'lignes': lignes,
        'exclus_stock': exclus_stock,
        'exclus_intervalle': exclus_intervalle,
        'exclus_quota': exclus_quota,
    })

@app.route('/api/repartition/generer', methods=['POST'])
def generer_repartition():
    user = get_current_user()
    if not user or user['profil'] != 'TRACABILITE':
        return jsonify({'error': 'Accès refusé'}), 403

    data = request.json
    label = data.get('label')
    volume_total = float(data.get('volume_total', 0))
    volume_affecte = float(data.get('volume_affecte', 0))
    nb_sacs = int(data.get('nombre_sacs', 0))
    lignes = data.get('lignes', [])
    date_livraison_str = data.get('date_livraison', date.today().isoformat())
    plafond_applique = float(data.get('plafond_applique', 0.30))
    intervalle_saute = bool(data.get('intervalle_saute', False))
    motif_derogation = data.get('motif_derogation', '')
    sections = data.get('sections', [])

    if not lignes:
        return jsonify({'error': 'Aucune ligne de répartition'}), 400

    cfg = get_config()
    saison = cfg.get('SAISON_EN_COURS', 'GT')
    plafond_base = float(cfg.get('PLAFOND_PARCELLE', 0.30))

    # Numérotation séquentielle globale (indépendante de la date)
    existing = sb_get('fiches_repartition', {
        'num_fiche': 'like.FR-%',
        'select': 'num_fiche',
        'order': 'num_fiche.desc',
        'limit': '1'
    })
    if existing:
        last = existing[0]['num_fiche']
        try:
            seq = int(last.split('-')[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    else:
        seq = 1
    num_fiche = f'FR-{seq:03d}'

    fiche = {
        'num_fiche': num_fiche,
        'saison': saison,
        'label': label,
        'volume_total': volume_total,
        'volume_affecte': volume_affecte,
        'nombre_sacs': nb_sacs,
        'plafond_applique': plafond_applique,
        'intervalle_saute': intervalle_saute,
        'nom_agent': user['nom_agent'],
        'sections': ','.join(sections) if sections else '',
        'statut': 'CLOTUREE'
    }
    sb_post('fiches_repartition', fiche)

    # date_livraison_str sert de date de repli si la ligne n'a pas sa propre date (VBA)
    date_livraison_defaut = datetime.strptime(date_livraison_str, '%Y-%m-%d').date().isoformat()

    # Dédupliquer les lignes par code_parcelle (garde la première occurrence)
    seen_parcelles = set()
    lignes_dedup = []
    for l in lignes:
        cp = l.get('code_parcelle')
        if cp and cp not in seen_parcelles:
            seen_parcelles.add(cp)
            lignes_dedup.append(l)
    lignes = lignes_dedup

    for ligne in lignes:
        # Utiliser la date calculée par la simulation (VBA: DateLivOptimale), ou la date de repli
        date_liv = ligne.get('date_livraison') or date_livraison_defaut
        sb_post('lignes_repartition', {
            'num_fiche': num_fiche,
            'code_parcelle': ligne['code_parcelle'],
            'code_producteur': ligne['code_producteur'],
            'nom_producteur': ligne['nom_producteur'],
            'section': ligne.get('section', ''),
            'latitude': ligne.get('latitude'),
            'longitude': ligne.get('longitude'),
            'score_calcule': ligne['score_calcule'],
            'volume_attribue': ligne['volume_attribue'],
            'nombre_sacs': ligne['nombre_sacs'],
            'montant': ligne.get('montant', 0),
            'date_livraison': date_liv,
        })

        parcelles_row = sb_get('parcelles', {'code_parcelle': f'eq.{ligne["code_parcelle"]}', 'select': '*'})
        if parcelles_row:
            p = parcelles_row[0]
            vol = float(ligne['volume_attribue'])
            if saison == 'GT':
                sb_patch('parcelles', {'code_parcelle': ligne['code_parcelle']}, {
                    'volume_livre_gt': float(p.get('volume_livre_gt', 0)) + vol
                })
            else:
                sb_patch('parcelles', {'code_parcelle': ligne['code_parcelle']}, {
                    'volume_livre_pt': float(p.get('volume_livre_pt', 0)) + vol
                })

    if plafond_applique != plafond_base:
        sb_post('derogations', {
            'num_fiche': num_fiche,
            'type_derogation': 'PLAFOND',
            'valeur_avant': str(plafond_base),
            'valeur_apres': str(plafond_applique),
            'nom_agent': user['nom_agent'],
            'motif': motif_derogation or 'Non précisé'
        })

    if intervalle_saute:
        sb_post('derogations', {
            'num_fiche': num_fiche,
            'type_derogation': 'INTERVALLE',
            'valeur_avant': cfg.get('INTERV_JOURS', '14'),
            'valeur_apres': '0',
            'nom_agent': user['nom_agent'],
            'motif': motif_derogation or 'Non précisé'
        })

    journaliser(user['nom_agent'], user['profil'], 'FICHE_GENEREE',
                f'Fiche {num_fiche} - {label} - {volume_affecte} kg - {len(lignes)} parcelles',
                num_fiche)

    return jsonify({'ok': True, 'num_fiche': num_fiche})

# ============================================================
# FICHES
# ============================================================

@app.route('/api/fiches', methods=['GET'])
def get_fiches():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Non authentifié'}), 401

    params = {'select': '*', 'order': 'date_creation.desc', 'limit': '100'}
    label = request.args.get('label')
    saison = request.args.get('saison')
    if label:
        params['label'] = f'eq.{label}'
    if saison:
        params['saison'] = f'eq.{saison}'

    fiches = sb_get('fiches_repartition', params)
    return jsonify(fiches)

@app.route('/api/fiches/<num_fiche>', methods=['GET'])
def get_fiche(num_fiche):
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Non authentifié'}), 401

    fiches = sb_get('fiches_repartition', {'num_fiche': f'eq.{num_fiche}', 'select': '*'})
    if not fiches:
        return jsonify({'error': 'Fiche introuvable'}), 404

    lignes = sb_get('lignes_repartition', {
        'num_fiche': f'eq.{num_fiche}',
        'select': '*',
        'order': 'section.asc,nom_producteur.asc'
    })

    return jsonify({'fiche': fiches[0], 'lignes': lignes})

# ============================================================
# PDF - FICHE DE REPARTITION
# ============================================================

@app.route('/api/fiches/<num_fiche>/pdf', methods=['GET'])
def pdf_fiche_repartition(num_fiche):
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Non authentifié'}), 401

    fiches = sb_get('fiches_repartition', {'num_fiche': f'eq.{num_fiche}', 'select': '*'})
    if not fiches:
        return jsonify({'error': 'Fiche introuvable'}), 404
    fiche = fiches[0]

    lignes = sb_get('lignes_repartition', {
        'num_fiche': f'eq.{num_fiche}',
        'select': '*',
        'order': 'section.asc,score_calcule.desc'
    })

    cfg = get_config()
    coop_nom = cfg.get('COOP_NOM', 'COOPERATIVE')
    campagne = cfg.get('ANNEE_CAMPAGNE', '')

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                            leftMargin=1*cm, rightMargin=1*cm,
                            topMargin=1*cm, bottomMargin=1*cm)

    styles = getSampleStyleSheet()
    elements = []

    label_txt = 'RAINFOREST ALLIANCE' if fiche['label'] == 'RA' else 'FAIRTRADE'
    title = Paragraph(
        f'<b>FICHE D\'ACCOMPAGNEMENT {label_txt}</b>',
        ParagraphStyle('title', parent=styles['Title'], fontSize=14, alignment=TA_CENTER)
    )
    elements.append(title)
    elements.append(Spacer(1, 0.3*cm))

    info_data = [
        [f'N° Fiche: {num_fiche}', f'Saison: {fiche["saison"]}', f'Campagne: {campagne}'],
        [f'Date: {fiche["date_creation"][:10]}', f'Label: {fiche["label"]}', f'Agent: {fiche["nom_agent"]}'],
        [f'Volume total: {fiche["volume_total"]:,.0f} kg', f'Volume affecté: {fiche["volume_affecte"]:,.0f} kg', f'Sacs: {fiche["nombre_sacs"]}'],
    ]
    info_table = Table(info_data, colWidths=[9*cm, 9*cm, 9*cm])
    info_table.setStyle(TableStyle([
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('BACKGROUND', (0,0), (-1,-1), colors.whitesmoke),
        ('PADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 0.4*cm))

    headers = ['#', 'Code Parcelle', 'Producteur', 'Section', 'Score', 'Stock Dispo', 'Volume (kg)', 'Sacs', 'Montant (FCFA)', 'Lat', 'Lon']
    rows = [headers]
    prix_cle = 'PRIX_KG_GT' if fiche['saison'] == 'GT' else 'PRIX_KG_PT'
    prix_kg = float(cfg.get(prix_cle, 0))

    for i, l in enumerate(lignes, 1):
        rows.append([
            str(i),
            l['code_parcelle'],
            l['nom_producteur'],
            l.get('section', ''),
            f'{l.get("score_calcule", 0):.4f}',
            '',
            f'{l["volume_attribue"]:,.0f}',
            str(l['nombre_sacs']),
            f'{l.get("montant", 0):,.0f}',
            f'{l.get("latitude", ""):.6f}' if l.get('latitude') else '',
            f'{l.get("longitude", ""):.6f}' if l.get('longitude') else '',
        ])

    # Utiliser les valeurs officielles de la fiche pour le total (cohérence avec l'en-tête)
    total_vol = fiche['volume_affecte']
    total_sacs = fiche['nombre_sacs']
    total_montant = sum(l.get('montant', 0) for l in lignes)
    rows.append(['', '', 'TOTAL', '', '', '', f'{total_vol:,.0f}', str(total_sacs), f'{total_montant:,.0f}', '', ''])

    col_widths = [0.7*cm, 2.5*cm, 5*cm, 2.5*cm, 1.5*cm, 2*cm, 2*cm, 1.2*cm, 2.5*cm, 1.8*cm, 1.8*cm]
    t = Table(rows, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1a5276')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTSIZE', (0,0), (-1,-1), 7.5),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-1), 0.3, colors.grey),
        ('ROWBACKGROUNDS', (0,1), (-1,-2), [colors.white, colors.HexColor('#eaf4fb')]),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#d6eaf8')),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('PADDING', (0,0), (-1,-1), 3),
        ('ALIGN', (4,0), (-1,-1), 'RIGHT'),
    ]))
    elements.append(t)

    doc.build(elements)
    buffer.seek(0)
    return send_file(buffer, mimetype='application/pdf',
                     as_attachment=True, download_name=f'{num_fiche}.pdf')


@app.route('/api/fiches/<num_fiche>/excel', methods=['GET'])
def export_fiche_excel(num_fiche):
    """Export Excel de la fiche de répartition."""
    user = get_current_user()
    if not user or user['profil'] != 'TRACABILITE':
        return jsonify({'error': 'Accès refusé'}), 403

    fiches = sb_get('fiches_repartition', {'num_fiche': f'eq.{num_fiche}', 'select': '*'})
    if not fiches:
        return jsonify({'error': 'Fiche introuvable'}), 404
    fiche = fiches[0]

    lignes = sb_get('lignes_repartition', {
        'num_fiche': f'eq.{num_fiche}',
        'select': '*',
        'order': 'section.asc,score_calcule.desc'
    })

    cfg = get_config()
    coop_nom = cfg.get('COOP_NOM', 'COOPERATIVE')
    campagne = cfg.get('ANNEE_CAMPAGNE', '')
    prix_cle = 'PRIX_KG_GT' if fiche['saison'] == 'GT' else 'PRIX_KG_PT'
    prix_kg = float(cfg.get(prix_cle, 0))
    label_txt = 'Rainforest Alliance' if fiche['label'] == 'RA' else 'Fairtrade'

    import io as _io
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        return jsonify({'error': 'openpyxl non disponible'}), 500

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f'Fiche {num_fiche}'

    bleu = PatternFill(start_color='1A5276', end_color='1A5276', fill_type='solid')
    bleu_clair = PatternFill(start_color='D6EAF8', end_color='D6EAF8', fill_type='solid')
    or_fill = PatternFill(start_color='C9A84C', end_color='C9A84C', fill_type='solid')
    alt_fill = PatternFill(start_color='EAF4FB', end_color='EAF4FB', fill_type='solid')
    thin = Border(
        left=Side(style='thin', color='AAAAAA'), right=Side(style='thin', color='AAAAAA'),
        top=Side(style='thin', color='AAAAAA'), bottom=Side(style='thin', color='AAAAAA')
    )

    # Titre
    ws.merge_cells('A1:K1')
    ws['A1'] = f'FICHE D\'ACCOMPAGNEMENT {label_txt.upper()} — {coop_nom}'
    ws['A1'].font = Font(name='Arial', bold=True, size=13, color='FFFFFF')
    ws['A1'].fill = bleu
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 32

    # Infos fiche
    infos = [
        (f'N° Fiche: {num_fiche}', f'Saison: {fiche["saison"]}', f'Campagne: {campagne}'),
        (f'Date: {fiche["date_creation"][:10]}', f'Label: {fiche["label"]}', f'Agent: {fiche["nom_agent"]}'),
        (f'Volume total: {fiche["volume_affecte"]:,.0f} kg', f'Sacs: {fiche["nombre_sacs"]}', ''),
    ]
    for row_data in infos:
        ws.append(list(row_data) + [''] * 8)
        r = ws.max_row
        for c in range(1, 12):
            cell = ws.cell(r, c)
            cell.fill = PatternFill(start_color='F2F3F4', end_color='F2F3F4', fill_type='solid')
            cell.font = Font(name='Arial', size=9)
            cell.border = thin
        ws.cell(r, 1).font = Font(name='Arial', size=9, bold=True)

    ws.append([])  # séparateur

    # En-têtes
    headers = ['#', 'Code Parcelle', 'Producteur', 'Section', 'Score', 'Stock Dispo',
               'Volume (kg)', 'Nb Sacs', 'Montant (FCFA)', 'Latitude', 'Longitude']
    ws.append(headers)
    hr = ws.max_row
    for ci, h in enumerate(headers, 1):
        c = ws.cell(hr, ci)
        c.value = h
        c.font = Font(name='Arial', bold=True, size=10, color='FFFFFF')
        c.fill = or_fill
        c.alignment = Alignment(horizontal='center', vertical='center')
        c.border = thin
    ws.row_dimensions[hr].height = 22

    # Données
    for i, l in enumerate(lignes, 1):
        rn = hr + i
        fill = PatternFill(start_color='FFFFFF', end_color='FFFFFF', fill_type='solid') if i % 2 == 1 else alt_fill
        lat = round(float(l['latitude']), 6) if l.get('latitude') else ''
        lon = round(float(l['longitude']), 6) if l.get('longitude') else ''
        row_vals = [i, l['code_parcelle'], l['nom_producteur'], l.get('section', ''),
                    round(float(l.get('score_calcule', 0)), 4), '',
                    float(l['volume_attribue']), int(l['nombre_sacs']),
                    float(l.get('montant', 0)), lat, lon]
        ws.append(row_vals)
        for ci in range(1, 12):
            cell = ws.cell(rn, ci)
            cell.fill = fill
            cell.font = Font(name='Arial', size=9)
            cell.border = thin
            if ci in (7, 9):
                cell.number_format = '#,##0'
                cell.alignment = Alignment(horizontal='right')
            elif ci == 8:
                cell.alignment = Alignment(horizontal='center')
            elif ci in (10, 11):
                cell.number_format = '0.000000'
                cell.alignment = Alignment(horizontal='right')

    # Total
    tr = hr + len(lignes) + 1
    total_montant = sum(float(l.get('montant', 0)) for l in lignes)
    total_data = ['', '', 'TOTAL', '', '', '',
                  float(fiche['volume_affecte']), int(fiche['nombre_sacs']), total_montant, '', '']
    ws.append(total_data)
    for ci in range(1, 12):
        c = ws.cell(tr, ci)
        c.font = Font(name='Arial', bold=True, size=10)
        c.fill = bleu_clair
        c.border = thin
        if ci in (7, 9):
            c.number_format = '#,##0'
            c.alignment = Alignment(horizontal='right')
        elif ci == 8:
            c.alignment = Alignment(horizontal='center')

    # Largeurs
    col_ws = [5, 18, 28, 16, 10, 14, 14, 10, 18, 14, 14]
    for ci, w in enumerate(col_ws, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = w

    out = _io.BytesIO()
    wb.save(out)
    out.seek(0)
    from flask import Response as Resp
    return Resp(out.getvalue(),
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                headers={'Content-Disposition': f'attachment; filename={num_fiche}.xlsx'})


@app.route('/api/fiches/<num_fiche>/accompagnement-excel', methods=['GET'])
def export_accompagnement_excel(num_fiche):
    """Export Excel de la fiche d'accompagnement."""
    user = get_current_user()
    if not user or user['profil'] != 'TRACABILITE':
        return jsonify({'error': 'Accès refusé'}), 403

    fas = sb_get('fiches_accompagnement', {'num_fiche_rep': f'eq.{num_fiche}', 'select': '*'})
    if not fas:
        return jsonify({'error': 'Fiche d\'accompagnement introuvable'}), 404
    fa = fas[0]

    lignes = sb_get('lignes_repartition', {
        'num_fiche': f'eq.{num_fiche}',
        'select': '*',
        'order': 'section.asc,nom_producteur.asc'
    })

    cfg = get_config()
    coop_nom = cfg.get('COOP_NOM', 'COOPERATIVE')
    campagne = cfg.get('ANNEE_CAMPAGNE', '')
    prix_cle = 'PRIX_KG_GT' if fa['saison'] == 'GT' else 'PRIX_KG_PT'
    prix_kg = float(cfg.get(prix_cle, 0))
    label_cert = 'RAINFOREST' if fa['label'] == 'RA' else 'FAIRTRADE'

    import io as _io
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        return jsonify({'error': 'openpyxl non disponible'}), 500

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f'FA-{num_fiche}'

    vert = PatternFill(start_color='196F3D', end_color='196F3D', fill_type='solid')
    vert_clair = PatternFill(start_color='D5F5E3', end_color='D5F5E3', fill_type='solid')
    alt_fill = PatternFill(start_color='EAFAF1', end_color='EAFAF1', fill_type='solid')
    thin = Border(
        left=Side(style='thin', color='AAAAAA'), right=Side(style='thin', color='AAAAAA'),
        top=Side(style='thin', color='AAAAAA'), bottom=Side(style='thin', color='AAAAAA')
    )

    # Titre
    ws.merge_cells('A1:I1')
    ws['A1'] = f'FICHE D\'ACCOMPAGNEMENT DU CACAO CERTIFIÉ {label_cert} — {coop_nom}'
    ws['A1'].font = Font(name='Arial', bold=True, size=13, color='FFFFFF')
    ws['A1'].fill = vert
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 32

    # Transport
    transport = [
        ['N° LOT', fa.get('num_lot', ''), 'Tracteur', fa.get('num_tracteur', ''),
         'Remorque', fa.get('num_remorque', ''), 'Date', date.today().strftime('%d/%m/%Y'), ''],
        ['Connaissement CCC', fa.get('num_connaissement_ccc', ''), 'Chauffeur',
         fa.get('nom_chauffeur', ''), 'Campagne', campagne, 'Agent', fa.get('nom_agent', ''), ''],
        ['Volume total', f'{fa.get("volume_total", 0):,.0f} kg', 'Nombre sacs',
         str(fa.get('nombre_sacs_total', 0)), '', '', '', '', ''],
    ]
    for row_data in transport:
        ws.append(row_data)
        r = ws.max_row
        for ci in range(1, 10):
            cell = ws.cell(r, ci)
            cell.fill = PatternFill(start_color='F9F9F9', end_color='F9F9F9', fill_type='solid')
            cell.font = Font(name='Arial', size=9, bold=(ci % 2 == 1))
            cell.border = thin

    ws.append([])

    # En-têtes
    headers = ['N°', 'Code Plantation', 'Producteur', 'Montant (FCFA)', 'Section',
               'Nombre Sacs', 'Poids Net (kg)', 'Latitude', 'Longitude']
    ws.append(headers)
    hr = ws.max_row
    for ci, h in enumerate(headers, 1):
        c = ws.cell(hr, ci)
        c.value = h
        c.font = Font(name='Arial', bold=True, size=10, color='FFFFFF')
        c.fill = vert
        c.alignment = Alignment(horizontal='center', vertical='center')
        c.border = thin
    ws.row_dimensions[hr].height = 22

    # Données
    for i, l in enumerate(lignes, 1):
        rn = hr + i
        fill = PatternFill(start_color='FFFFFF', end_color='FFFFFF', fill_type='solid') if i % 2 == 1 else alt_fill
        montant = float(l.get('montant', l['volume_attribue'] * prix_kg))
        lat = round(float(l['latitude']), 6) if l.get('latitude') else ''
        lon = round(float(l['longitude']), 6) if l.get('longitude') else ''
        ws.append([i, l['code_parcelle'], l['nom_producteur'], montant,
                   l.get('section', ''), int(l['nombre_sacs']),
                   float(l['volume_attribue']), lat, lon])
        for ci in range(1, 10):
            cell = ws.cell(rn, ci)
            cell.fill = fill
            cell.font = Font(name='Arial', size=9)
            cell.border = thin
            if ci in (4, 7):
                cell.number_format = '#,##0'
                cell.alignment = Alignment(horizontal='right')
            elif ci == 6:
                cell.alignment = Alignment(horizontal='center')
            elif ci in (8, 9):
                cell.number_format = '0.000000'
                cell.alignment = Alignment(horizontal='right')

    # Total
    tr = hr + len(lignes) + 1
    total_montant = sum(float(l.get('montant', l['volume_attribue'] * prix_kg)) for l in lignes)
    total_vol = float(fa.get('volume_total', 0))
    total_sacs = int(fa.get('nombre_sacs_total', 0))
    ws.append(['', '', 'TOTAL', total_montant, '', total_sacs, total_vol, '', ''])
    for ci in range(1, 10):
        c = ws.cell(tr, ci)
        c.font = Font(name='Arial', bold=True, size=10)
        c.fill = vert_clair
        c.border = thin
        if ci in (4, 7):
            c.number_format = '#,##0'
            c.alignment = Alignment(horizontal='right')
        elif ci == 6:
            c.alignment = Alignment(horizontal='center')

    # Largeurs
    col_ws = [5, 20, 30, 18, 16, 12, 16, 14, 14]
    for ci, w in enumerate(col_ws, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = w

    out = _io.BytesIO()
    wb.save(out)
    out.seek(0)
    from flask import Response as Resp
    return Resp(out.getvalue(),
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                headers={'Content-Disposition': f'attachment; filename=FA-{num_fiche}.xlsx'})


# ============================================================
# FICHE D'ACCOMPAGNEMENT
# ============================================================

@app.route('/api/fiches/<num_fiche>/accompagnement', methods=['POST'])
def creer_accompagnement(num_fiche):
    user = get_current_user()
    if not user or user['profil'] != 'TRACABILITE':
        return jsonify({'error': 'Accès refusé'}), 403

    fiches = sb_get('fiches_repartition', {'num_fiche': f'eq.{num_fiche}', 'select': '*'})
    if not fiches:
        return jsonify({'error': 'Fiche introuvable'}), 404
    fiche = fiches[0]

    cfg = get_config()
    data = request.json

    fa = {
        'num_fiche_rep': num_fiche,
        'label': fiche['label'],
        'saison': fiche['saison'],
        'campagne': cfg.get('ANNEE_CAMPAGNE', ''),
        'num_tracteur': data.get('num_tracteur', ''),
        'num_remorque': data.get('num_remorque', ''),
        'nom_chauffeur': data.get('nom_chauffeur', ''),
        'num_connaissement_ccc': data.get('num_connaissement_ccc', ''),
        'num_lot': num_fiche,
        'volume_total': fiche['volume_affecte'],
        'nombre_sacs_total': fiche['nombre_sacs'],
        'nom_agent': user['nom_agent'],
    }
    result = sb_post('fiches_accompagnement', fa)
    journaliser(user['nom_agent'], user['profil'], 'FA_CREEE',
                f'FA pour fiche {num_fiche}', num_fiche)

    return jsonify({'ok': True, 'id': result[0]['id'] if result else None})

@app.route('/api/accompagnement/<int:fa_id>/pdf', methods=['GET'])
def pdf_accompagnement(fa_id):
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Non authentifié'}), 401

    fas = sb_get('fiches_accompagnement', {'id': f'eq.{fa_id}', 'select': '*'})
    if not fas:
        return jsonify({'error': 'Fiche accompagnement introuvable'}), 404
    fa = fas[0]

    fiches = sb_get('fiches_repartition', {'num_fiche': f'eq.{fa["num_fiche_rep"]}', 'select': '*'})
    if not fiches:
        return jsonify({'error': 'Fiche répartition introuvable'}), 404
    fiche = fiches[0]

    lignes = sb_get('lignes_repartition', {
        'num_fiche': f'eq.{fiche["num_fiche"]}',
        'select': '*',
        'order': 'section.asc,nom_producteur.asc'
    })

    cfg = get_config()
    coop_nom = cfg.get('COOP_NOM', 'SFED COOP-CA')
    coop_adresse = cfg.get('COOP_ADRESSE', '')
    coop_tel = cfg.get('COOP_TELEPHONE', '')
    campagne = cfg.get('ANNEE_CAMPAGNE', '')
    prix_cle = 'PRIX_KG_GT' if fa['saison'] == 'GT' else 'PRIX_KG_PT'
    prix_kg = float(cfg.get(prix_cle, 0))

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)
    styles = getSampleStyleSheet()
    elements = []

    label_cert = 'RAINFOREST' if fa['label'] == 'RA' else 'FAIRTRADE'
    header_data = [
        [
            Paragraph(f'<b>{coop_nom}</b>', ParagraphStyle('h', fontSize=11, alignment=TA_LEFT)),
            Paragraph(f'<b>FICHE D\'ACCOMPAGNEMENT DU CACAO CERTIFIÉ {label_cert}</b>',
                      ParagraphStyle('t', fontSize=13, alignment=TA_CENTER)),
            Paragraph(f'Date: {date.today().strftime("%d/%m/%Y")}\nCampagne: {campagne}',
                      ParagraphStyle('r', fontSize=9, alignment=TA_RIGHT))
        ]
    ]
    h_table = Table(header_data, colWidths=[6*cm, 13*cm, 6*cm])
    h_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ]))
    elements.append(h_table)
    elements.append(Spacer(1, 0.3*cm))

    transport_data = [
        ['N° LOT:', fa.get('num_lot', ''), 'Tracteur:', fa.get('num_tracteur', ''), 'Remorque:', fa.get('num_remorque', '')],
        ['Connaissement CCC N°:', fa.get('num_connaissement_ccc', ''), 'Chauffeur:', fa.get('nom_chauffeur', ''), '', ''],
        ['Volume total:', f'{fa.get("volume_total", 0):,.0f} kg', 'Nombre sacs:', str(fa.get('nombre_sacs_total', 0)), '', ''],
    ]
    t_transport = Table(transport_data, colWidths=[3.5*cm, 5*cm, 2.5*cm, 5*cm, 2.5*cm, 5*cm])
    t_transport.setStyle(TableStyle([
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTNAME', (2,0), (2,-1), 'Helvetica-Bold'),
        ('FONTNAME', (4,0), (4,-1), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('BACKGROUND', (0,0), (-1,-1), colors.whitesmoke),
        ('PADDING', (0,0), (-1,-1), 5),
    ]))
    elements.append(t_transport)
    elements.append(Spacer(1, 0.5*cm))

    headers = ['N°', 'Code Plantation', 'Producteur', 'Montant (FCFA)', 'Section', 'Nombre Sacs', 'Poids Net (kg)', 'Latitude', 'Longitude']
    rows = [headers]

    for i, l in enumerate(lignes, 1):
        montant = l.get('montant', l['volume_attribue'] * prix_kg)
        rows.append([
            str(i),
            l['code_parcelle'],
            l['nom_producteur'],
            f'{montant:,.0f}',
            l.get('section', ''),
            str(l['nombre_sacs']),
            f'{l["volume_attribue"]:,.0f}',
            f'{l.get("latitude", ""):.6f}' if l.get('latitude') else '',
            f'{l.get("longitude", ""):.6f}' if l.get('longitude') else '',
        ])

    total_vol = sum(l['volume_attribue'] for l in lignes)
    total_sacs = sum(l['nombre_sacs'] for l in lignes)
    total_montant = sum(l.get('montant', l['volume_attribue'] * prix_kg) for l in lignes)
    rows.append(['', '', 'TOTAL', f'{total_montant:,.0f}', '', str(total_sacs), f'{total_vol:,.0f}', '', ''])

    col_widths = [0.7*cm, 2.8*cm, 5.5*cm, 3*cm, 2.5*cm, 2*cm, 2.5*cm, 2.2*cm, 2.2*cm]
    t = Table(rows, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#196f3d')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('GRID', (0,0), (-1,-1), 0.3, colors.grey),
        ('ROWBACKGROUNDS', (0,1), (-1,-2), [colors.white, colors.HexColor('#eafaf1')]),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#d5f5e3')),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('ALIGN', (3,0), (-1,-1), 'RIGHT'),
        ('PADDING', (0,0), (-1,-1), 3),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 0.5*cm))

    sig_data = [['Agent de traçabilité:', '', 'Chauffeur:', '', 'Réceptionné par:']]
    sig_table = Table(sig_data, colWidths=[4*cm, 7*cm, 3*cm, 7*cm, 4.5*cm])
    sig_table.setStyle(TableStyle([
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('FONTNAME', (0,0), (0,0), 'Helvetica-Bold'),
        ('FONTNAME', (2,0), (2,0), 'Helvetica-Bold'),
        ('FONTNAME', (4,0), (4,0), 'Helvetica-Bold'),
        ('LINEBELOW', (1,0), (1,0), 0.5, colors.black),
        ('LINEBELOW', (3,0), (3,0), 0.5, colors.black),
    ]))
    elements.append(sig_table)

    doc.build(elements)
    buffer.seek(0)
    return send_file(buffer, mimetype='application/pdf',
                     as_attachment=True,
                     download_name=f'FA-{fa["num_fiche_rep"]}.pdf')

# ============================================================
# SUIVI LIVRAISONS PAR PRODUCTEUR
# ============================================================

@app.route('/api/reinitialiser-test', methods=['POST'])
def reinitialiser_test():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Non authentifié'}), 401

    cfg = get_config()
    saison = cfg.get('SAISON_EN_COURS', 'GT')

    try:
        # Récupérer les fiches de la saison en cours
        fiches = sb_get('fiches_repartition', {'saison': f'eq.{saison}', 'select': 'num_fiche'})
        num_fiches = [f['num_fiche'] for f in fiches if f.get('num_fiche')]

        lignes_supprimees = 0
        fiches_supprimees = 0

        # Supprimer les lignes de répartition pour chaque fiche
        for nf in num_fiches:
            r = requests.delete(
                f"{SUPABASE_URL}/rest/v1/lignes_repartition?num_fiche=eq.{nf}",
                headers=sb_headers()
            )
            if r.status_code in (200, 204):
                lignes_supprimees += 1

        # Supprimer les fiches de la saison
        r2 = requests.delete(
            f"{SUPABASE_URL}/rest/v1/fiches_repartition?saison=eq.{saison}",
            headers=sb_headers()
        )
        fiches_supprimees = len(num_fiches)

        return jsonify({
            'message': f'{fiches_supprimees} fiche(s) et leurs lignes supprimées. Saison {saison} réinitialisée pour test.',
            'fiches': fiches_supprimees,
            'saison': saison
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/suivi-livraisons', methods=['GET'])
def suivi_livraisons():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Non authentifié'}), 401

    cfg = get_config()
    saison = cfg.get('SAISON_EN_COURS', 'GT')

    # Récupère toutes les lignes de répartition
    lignes = sb_get('lignes_repartition', {
        'select': 'code_producteur,nom_producteur,section,code_parcelle,volume_attribue,nombre_sacs,montant,date_livraison,num_fiche',
        'order': 'date_livraison.desc'
    })

    # Enrichit avec label/saison depuis fiches_repartition
    fiches_map = {}
    fiches_list = sb_get('fiches_repartition', {'select': 'num_fiche,label,saison'})
    for f in fiches_list:
        fiches_map[f['num_fiche']] = f

    # Agrège par producteur
    par_producteur = {}
    for l in lignes:
        cp = l.get('code_producteur', '')
        if not cp:
            continue
        if cp not in par_producteur:
            par_producteur[cp] = {
                'code_producteur': cp,
                'nom_producteur': l.get('nom_producteur', ''),
                'section': l.get('section', ''),
                'total_volume': 0,
                'total_montant': 0,
                'nb_livraisons': 0,
                'derniere_livraison': None,
                'livraisons': []
            }
        rec = par_producteur[cp]
        rec['total_volume'] += float(l.get('volume_attribue', 0) or 0)
        rec['total_montant'] += float(l.get('montant', 0) or 0)
        rec['nb_livraisons'] += 1
        dl = l.get('date_livraison')
        if dl and (not rec['derniere_livraison'] or dl > rec['derniere_livraison']):
            rec['derniere_livraison'] = dl
        num_fiche_val = l.get('num_fiche') or l.get('num_fiche_rep', '')
        fiche_info = fiches_map.get(num_fiche_val, {})
        rec['livraisons'].append({
            'code_parcelle': l.get('code_parcelle'),
            'volume_attribue': l.get('volume_attribue'),
            'nombre_sacs': l.get('nombre_sacs'),
            'montant': l.get('montant'),
            'date_livraison': dl,
            'num_fiche': num_fiche_val,
            'label': fiche_info.get('label', ''),
            'saison': fiche_info.get('saison', ''),
        })

    # Tri par nom producteur
    result = sorted(par_producteur.values(), key=lambda x: x['nom_producteur'])
    return jsonify(result)


@app.route('/api/suivi-livraisons/export-excel', methods=['GET'])
def suivi_livraisons_excel():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Non authentifié'}), 401

    import io
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        return jsonify({'error': 'openpyxl non disponible'}), 500

    cfg = get_config()
    saison = cfg.get('SAISON_EN_COURS', 'GT')
    coop_nom = cfg.get('COOP_NOM', 'COOPERATIVE')

    lignes = sb_get('lignes_repartition', {
        'select': 'code_producteur,nom_producteur,section,code_parcelle,volume_attribue,nombre_sacs,montant,date_livraison,num_fiche',
        'order': 'date_livraison.desc'
    })

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f'Suivi Livraisons {saison}'

    # Styles
    vert_fonce = PatternFill(start_color='1B4332', end_color='1B4332', fill_type='solid')
    vert_clair = PatternFill(start_color='D8F3DC', end_color='D8F3DC', fill_type='solid')
    or_fill = PatternFill(start_color='C9A84C', end_color='C9A84C', fill_type='solid')
    blanc_fill = PatternFill(start_color='FFFFFF', end_color='FFFFFF', fill_type='solid')
    gris_fill = PatternFill(start_color='F0F4F2', end_color='F0F4F2', fill_type='solid')
    thin = Border(
        left=Side(style='thin', color='B0C4BB'),
        right=Side(style='thin', color='B0C4BB'),
        top=Side(style='thin', color='B0C4BB'),
        bottom=Side(style='thin', color='B0C4BB')
    )

    # Titre
    ws.merge_cells('A1:I1')
    ws['A1'] = f'{coop_nom} — Suivi des Livraisons — Saison {saison}'
    ws['A1'].font = Font(name='Arial', bold=True, size=14, color='FFFFFF')
    ws['A1'].fill = vert_fonce
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 30

    ws.merge_cells('A2:I2')
    from datetime import date
    ws['A2'] = f'Exporté le {date.today().strftime("%d/%m/%Y")} — {len(lignes)} ligne(s)'
    ws['A2'].font = Font(name='Arial', size=10, color='2D6A4F')
    ws['A2'].fill = vert_clair
    ws['A2'].alignment = Alignment(horizontal='center')

    # En-têtes colonnes
    headers = ['Code Producteur', 'Nom Producteur', 'Section', 'Code Parcelle',
               'N° Fiche', 'Date Livraison', 'Volume (kg)', 'Nb Sacs', 'Montant (FCFA)']
    ws.append([])  # ligne 3 vide
    ws.append(headers)
    header_row = 4
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col_idx)
        cell.font = Font(name='Arial', bold=True, size=10, color='FFFFFF')
        cell.fill = or_fill
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = thin

    ws.row_dimensions[header_row].height = 22

    # Données
    for i, l in enumerate(lignes):
        row_num = header_row + 1 + i
        fill = blanc_fill if i % 2 == 0 else gris_fill
        vals = [
            l.get('code_producteur', ''),
            l.get('nom_producteur', ''),
            l.get('section', ''),
            l.get('code_parcelle', ''),
            l.get('num_fiche', ''),
            l.get('date_livraison', ''),
            float(l.get('volume_attribue', 0) or 0),
            int(l.get('nombre_sacs', 0) or 0),
            float(l.get('montant', 0) or 0),
        ]
        ws.append(vals)
        for col_idx in range(1, 10):
            cell = ws.cell(row=row_num, column=col_idx)
            cell.fill = fill
            cell.border = thin
            cell.font = Font(name='Arial', size=9)
            if col_idx in (7, 9):
                cell.number_format = '#,##0'
                cell.alignment = Alignment(horizontal='right')
            elif col_idx == 8:
                cell.alignment = Alignment(horizontal='center')

    # Ligne totaux
    last_row = header_row + 1 + len(lignes)
    ws.cell(last_row, 1, 'TOTAL').font = Font(name='Arial', bold=True, size=10)
    ws.cell(last_row, 1).fill = vert_clair
    total_vol = sum(float(l.get('volume_attribue', 0) or 0) for l in lignes)
    total_sacs = sum(int(l.get('nombre_sacs', 0) or 0) for l in lignes)
    total_mont = sum(float(l.get('montant', 0) or 0) for l in lignes)
    for col, val in [(7, total_vol), (8, total_sacs), (9, total_mont)]:
        c = ws.cell(last_row, col, val)
        c.font = Font(name='Arial', bold=True, size=10)
        c.fill = vert_clair
        c.border = thin
        c.number_format = '#,##0'
        c.alignment = Alignment(horizontal='right')

    # Largeurs colonnes
    col_widths = [18, 28, 16, 20, 14, 16, 14, 10, 18]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    from flask import Response
    return Response(
        output.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename=suivi_livraisons_{saison}.xlsx'}
    )

# ============================================================
# ALERTES STOCK CRITIQUE
# ============================================================

@app.route('/api/alertes', methods=['GET'])
def get_alertes():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Non authentifié'}), 401

    cfg = get_config()
    saison = cfg.get('SAISON_EN_COURS', 'GT')
    seuil_critique = 0.10

    parcelles = sb_get('parcelles', {'actif': 'eq.true', 'select': '*'})
    alertes = []

    for p in parcelles:
        stk = calculer_stock_disponible(p, cfg, saison)
        estim = float(p.get('estimation_kg', 0))
        if estim > 0 and stk / estim < seuil_critique:
            alertes.append({
                'code_parcelle': p['code_parcelle'],
                'nom_producteur': p['nom_producteur'],
                'section': p.get('section', ''),
                'estimation_kg': estim,
                'stock_disponible': stk,
                'taux': round(stk / estim * 100, 1),
            })

    alertes.sort(key=lambda a: a['taux'])
    return jsonify(alertes)

# ============================================================
# CLOTURE GT → PT
# ============================================================

@app.route('/api/cloture-gt', methods=['POST'])
def cloture_gt():
    user = get_current_user()
    if not user or user['profil'] != 'DIRECTEUR':
        return jsonify({'error': 'Seul le directeur peut clôturer la GT'}), 403

    data = request.json
    description = data.get('description', '').strip()
    if not description:
        return jsonify({'error': 'Description de l\'action requise'}), 400

    cfg = get_config()
    saison_en_cours = cfg.get('SAISON_EN_COURS', 'GT')
    if saison_en_cours != 'GT':
        return jsonify({'error': 'La saison en cours n\'est pas GT'}), 400

    date_debut_pt_str = cfg.get('DATE_DEBUT_PT', '')
    if date_debut_pt_str:
        date_debut_pt = datetime.strptime(date_debut_pt_str, '%Y-%m-%d').date()
        if date.today() < date_debut_pt:
            return jsonify({
                'error': f'La petite traite ne commence que le {date_debut_pt.strftime("%d/%m/%Y")}. Clôture impossible avant cette date.'
            }), 400

    parcelles = sb_get('parcelles', {'actif': 'eq.true', 'select': '*'})
    quota_gt = float(cfg.get('QUOTA_GT', 0.80))
    nb_parcelles = 0
    volume_reporte = 0

    for p in parcelles:
        estim = float(p.get('estimation_kg', 0))
        livre_gt = float(p.get('volume_livre_gt', 0))
        plafond_gt = estim * quota_gt
        residu = max(0, plafond_gt - livre_gt)

        if residu > 0:
            ancien_report = float(p.get('report_gt_vers_pt', 0))
            sb_patch('parcelles', {'code_parcelle': p['code_parcelle']}, {
                'report_gt_vers_pt': ancien_report + residu
            })
            volume_reporte += residu
            nb_parcelles += 1

    sb_patch('config', {'cle': 'SAISON_EN_COURS'}, {'valeur': 'PT'})

    sb_post('clotures_saison', {
        'saison_cloturee': 'GT',
        'campagne': cfg.get('ANNEE_CAMPAGNE', ''),
        'nom_agent': user['nom_agent'],
        'description': description,
        'nb_parcelles': nb_parcelles,
        'volume_reporte': volume_reporte,
    })

    journaliser(user['nom_agent'], user['profil'], 'CLOTURE_GT',
                f'GT clôturée. {nb_parcelles} parcelles, {volume_reporte:.0f} kg reportés sur PT. {description}')

    return jsonify({
        'ok': True,
        'nb_parcelles': nb_parcelles,
        'volume_reporte': volume_reporte,
        'message': f'Grande traite clôturée. {volume_reporte:.0f} kg reportés sur {nb_parcelles} parcelles.'
    })

# ============================================================
# CHARGEMENT AMI
# ============================================================

@app.route('/api/chargement-ami', methods=['GET'])
def get_chargement_ami():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Non authentifié'}), 401
    return jsonify({'message': 'Module chargement ami — à compléter selon logique métier existante'})

# ============================================================
# DIRECTEUR - CONNEXIONS & STATS
# ============================================================

@app.route('/api/directeur/connexions', methods=['GET'])
def directeur_connexions():
    user = get_current_user()
    if not user or user['profil'] != 'DIRECTEUR':
        return jsonify({'error': 'Accès refusé'}), 403

    sessions = sb_get('sessions', {
        'select': '*',
        'order': 'date_connexion.desc',
        'limit': '200'
    })
    return jsonify(sessions)

@app.route('/api/directeur/stats', methods=['GET'])
def directeur_stats():
    user = get_current_user()
    if not user or user['profil'] != 'DIRECTEUR':
        return jsonify({'error': 'Accès refusé'}), 403

    cfg = get_config()
    saison = cfg.get('SAISON_EN_COURS', 'GT')

    fiches = sb_get('fiches_repartition', {'select': 'label,saison,volume_affecte,nombre_sacs,date_creation'})
    parcelles = sb_get('parcelles', {'actif': 'eq.true', 'select': 'code_producteur,estimation_kg,volume_livre_gt,volume_livre_pt,section,rainforest_alliance,fairtrade'})
    journal = sb_get('journal', {'select': '*', 'order': 'date_heure.desc', 'limit': '50'})

    # Comptage producteurs distincts
    producteurs_distincts = set(p.get('code_producteur', '') for p in parcelles if p.get('code_producteur'))

    stats = {
        'nb_fiches_ra': sum(1 for f in fiches if f['label'] == 'RA'),
        'nb_fiches_ft': sum(1 for f in fiches if f['label'] == 'FT'),
        'volume_ra': sum(f['volume_affecte'] for f in fiches if f['label'] == 'RA'),
        'volume_ft': sum(f['volume_affecte'] for f in fiches if f['label'] == 'FT'),
        'nb_parcelles': len(parcelles),
        'nb_producteurs': len(producteurs_distincts),
        'nb_parcelles_ra': sum(1 for p in parcelles if p.get('rainforest_alliance')),
        'nb_parcelles_ft': sum(1 for p in parcelles if p.get('fairtrade')),
        'estimation_totale': sum(p.get('estimation_kg', 0) for p in parcelles),
        'livre_gt': sum(p.get('volume_livre_gt', 0) for p in parcelles),
        'livre_pt': sum(p.get('volume_livre_pt', 0) for p in parcelles),
        'saison_en_cours': saison,
        'prix_kg_gt': float(cfg.get('PRIX_KG_GT', 0)),
        'prix_kg_pt': float(cfg.get('PRIX_KG_PT', 0)),
    }

    sections = {}
    for p in parcelles:
        s = p.get('section', 'N/A')
        if s not in sections:
            sections[s] = {'estimation': 0, 'livre': 0}
        sections[s]['estimation'] += p.get('estimation_kg', 0)
        sections[s]['livre'] += p.get('volume_livre_gt' if saison == 'GT' else 'volume_livre_pt', 0)

    return jsonify({
        'stats': stats,
        'par_section': sections,
        'journal': journal
    })

@app.route('/api/directeur/rapport-sections', methods=['GET'])
def rapport_sections():
    user = get_current_user()
    if not user or user['profil'] != 'DIRECTEUR':
        return jsonify({'error': 'Accès refusé'}), 403

    cfg = get_config()
    saison = request.args.get('saison', cfg.get('SAISON_EN_COURS', 'GT'))

    fiches_saison = sb_get('fiches_repartition', {'saison': f'eq.{saison}', 'select': 'num_fiche'})
    num_fiches = [f['num_fiche'] for f in fiches_saison]

    rapport = {}
    for nf in num_fiches:
        lignes = sb_get('lignes_repartition', {'num_fiche': f'eq.{nf}', 'select': '*'})
        for l in lignes:
            s = l.get('section', 'N/A')
            label_fiche = sb_get('fiches_repartition', {'num_fiche': f'eq.{nf}', 'select': 'label'})
            label = label_fiche[0]['label'] if label_fiche else '?'
            key = f'{s}_{label}'
            if key not in rapport:
                rapport[key] = {'section': s, 'label': label, 'volume': 0, 'sacs': 0, 'nb_parcelles': 0}
            rapport[key]['volume'] += l['volume_attribue']
            rapport[key]['sacs'] += l['nombre_sacs']
            rapport[key]['nb_parcelles'] += 1

    return jsonify(list(rapport.values()))

# ============================================================
# GESTION CODES D'ACCES
# ============================================================

@app.route('/api/codes-acces', methods=['GET'])
def get_codes_acces():
    user = get_current_user()
    if not user or user['profil'] != 'DIRECTEUR':
        return jsonify({'error': 'Accès refusé'}), 403
    users = sb_get('utilisateurs', {'select': 'id,profil,actif'})
    return jsonify(users)

@app.route('/api/codes-acces', methods=['POST'])
def creer_code_acces():
    user = get_current_user()
    if not user or user['profil'] != 'DIRECTEUR':
        return jsonify({'error': 'Accès refusé'}), 403

    data = request.json
    code = data.get('code_acces', '').strip()
    profil = data.get('profil', '').upper()

    if not code or profil not in ('DIRECTEUR', 'TRACABILITE'):
        return jsonify({'error': 'Code et profil valides requis'}), 400

    code_hash = hash_code(code)
    sb_post('utilisateurs', {'code_acces': code_hash, 'profil': profil})
    journaliser(user['nom_agent'], user['profil'], 'CODE_CREE', f'Profil: {profil}')
    return jsonify({'ok': True})

@app.route('/api/codes-acces/<int:uid>/actif', methods=['PATCH'])
def toggle_code_acces(uid):
    user = get_current_user()
    if not user or user['profil'] != 'DIRECTEUR':
        return jsonify({'error': 'Accès refusé'}), 403

    data = request.json
    actif = bool(data.get('actif'))
    sb_patch('utilisateurs', {'id': str(uid)}, {'actif': actif})
    return jsonify({'ok': True})

# ============================================================
# PAGE PRINCIPALE
# ============================================================

@app.route('/')
def index():
    user = get_current_user()
    if not user:
        return app.send_static_file('index.html')
    return app.send_static_file('app.html')

@app.route('/login')
def login_page():
    return app.send_static_file('index.html')

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'version': 'v4'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)