"""
FichePro v4 - Backend Flask complet
Coopérative CAPRESSA - Gestion répartition cacao certifié RA/FT
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
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
CORS(app, supports_credentials=True)

# ============================================================
# CONFIGURATION SUPABASE
# ============================================================
SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')

def sb_headers():
    return {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=representation'
    }

def sb_get(table, params=None):
    url = f'{SUPABASE_URL}/rest/v1/{table}'
    r = requests.get(url, headers=sb_headers(), params=params)
    if r.status_code in (200, 206):
        return r.json()
    return []

def sb_post(table, data):
    url = f'{SUPABASE_URL}/rest/v1/{table}'
    r = requests.post(url, headers=sb_headers(), json=data)
    if r.status_code in (200, 201):
        return r.json()
    return None

def sb_patch(table, filters, data):
    url = f'{SUPABASE_URL}/rest/v1/{table}'
    params = {k: f'eq.{v}' for k, v in filters.items()}
    r = requests.patch(url, headers=sb_headers(), params=params, json=data)
    return r.status_code in (200, 204)

def sb_delete(table, filters):
    url = f'{SUPABASE_URL}/rest/v1/{table}'
    params = {k: f'eq.{v}' for k, v in filters.items()}
    r = requests.delete(url, headers=sb_headers(), params=params)
    return r.status_code in (200, 204)

def get_config():
    """Charge toute la configuration en dict."""
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
    """Décorateur: vérifie session active et profil."""
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

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    code = data.get('code_acces', '').strip()
    nom = data.get('nom_agent', '').strip()
    if not code or not nom:
        return jsonify({'error': 'Code d\'accès et nom requis'}), 400

    code_hash = hash_code(code)
    users = sb_get('utilisateurs', {'code_acces': f'eq.{code_hash}', 'actif': 'eq.true', 'select': '*'})
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
    journaliser(nom, user['profil'], 'CONNEXION', f'Connexion réussie')

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
    if not user or user['profil'] != 'DIRECTEUR':
        return jsonify({'error': 'Accès refusé'}), 403
    data = request.json
    for cle, valeur in data.items():
        sb_patch('config', {'cle': cle}, {'valeur': str(valeur)})
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
            """Retourne une liste de dicts avec clés normalisées (MAJUSCULES_UNDERSCORE)."""
            if filename.endswith('.csv'):
                content = f.read().decode('utf-8-sig')
                # Détecter le séparateur
                sep = ';' if content.count(';') > content.count(',') else ','
                reader = csv.DictReader(io.StringIO(content), delimiter=sep)
                rows = list(reader)
                # Normaliser les clés
                return [{k.strip().upper().replace(' ', '_'): v for k, v in r.items()} for r in rows]
            elif filename.endswith(('.xls', '.xlsx')):
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
            return jsonify({'error': 'Format non supporté (CSV ou Excel requis)'}), 400

        if not rows:
            return jsonify({'error': 'Fichier vide'}), 400

        # Colonnes disponibles
        all_cols = list(rows[0].keys())

        # Colonnes obligatoires
        required = ['CODE_PARCELLE', 'CODE_PRODUCTEUR', 'NOM_PRENOMS', 'ESTIMATION_KG']
        missing = [c for c in required if c not in all_cols]
        if missing:
            return jsonify({'error': f'Colonnes manquantes: {", ".join(missing)}'}), 400

        # Colonnes RA/FT — chercher variantes
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

        nb_insere = 0
        nb_maj = 0
        errors = []

        for row in rows:
            try:
                code_parcelle = str(row.get('CODE_PARCELLE') or '').strip()
                if not code_parcelle or code_parcelle.lower() in ('none', 'nan', ''):
                    continue

                lat = row.get('LATITUDE')
                lon = row.get('LONGITUDE')
                parcelle = {
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
                }

                # Vérifier si existe
                existing = sb_get('parcelles', {'code_parcelle': f'eq.{code_parcelle}', 'select': 'code_parcelle'})
                if existing:
                    # Mise à jour (ne touche pas aux volumes livrés)
                    sb_patch('parcelles', {'code_parcelle': code_parcelle}, {
                        'code_producteur': parcelle['code_producteur'],
                        'nom_producteur': parcelle['nom_producteur'],
                        'section': parcelle['section'],
                        'village': parcelle['village'],
                        'superficie_ha': parcelle['superficie_ha'],
                        'estimation_kg': parcelle['estimation_kg'],
                        'latitude': parcelle['latitude'],
                        'longitude': parcelle['longitude'],
                        'rainforest_alliance': parcelle['rainforest_alliance'],
                        'fairtrade': parcelle['fairtrade'],
                        'actif': True,
                    })
                    nb_maj += 1
                else:
                    sb_post('parcelles', parcelle)
                    nb_insere += 1
            except Exception as e:
                errors.append(str(e))

        journaliser(user['nom_agent'], user['profil'], 'IMPORT_REGISTRE',
                    f'{nb_insere} insérées, {nb_maj} mises à jour')

        return jsonify({
            'ok': True,
            'insere': nb_insere,
            'maj': nb_maj,
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

    label = request.args.get('label')  # RA, FT, ou vide
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
    """
    Calcule le stock disponible pour une parcelle selon la saison.
    GT: 80% de l'estimation - volume_livre_gt - reserve_gt
    PT: estimation - volume_livre_gt - volume_livre_pt - reserve_gt - reserve_pt + report_gt_vers_pt
    """
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
    else:  # PT
        # Le quota PT = 100% de l'estimation totale - ce qui est en GT
        # + résidus GT reportés
        quota_gt_vol = estim * quota_gt
        dispo_pt_base = estim - quota_gt_vol  # ~20%
        # On ajoute les résidus GT reportés
        dispo = dispo_pt_base + report - livre_pt - reserve_pt

    return max(0, dispo)

def calculer_score(p, cfg, saison, date_ref):
    """
    Score = (1-taux)*0.5 + min(1, stk/1000)*0.3 + min(1, jours/(14×3))*0.2
    """
    w1 = float(cfg.get('SCORE_UTILISATION', 0.50))
    w2 = float(cfg.get('SCORE_VOLUME', 0.30))
    w3 = float(cfg.get('SCORE_DISPO', 0.20))

    estim = float(p.get('estimation_kg', 0))
    livre = float(p.get('volume_livre_gt', 0)) if saison == 'GT' else float(p.get('volume_livre_pt', 0))
    stk = calculer_stock_disponible(p, cfg, saison)

    # Taux d'utilisation
    taux = (livre / estim) if estim > 0 else 1.0
    taux = min(taux, 1.0)

    # Jours depuis dernière livraison
    interv = int(cfg.get('INTERV_JOURS', 14))
    derniere = p.get('_derniere_livraison')
    if derniere:
        d = datetime.strptime(str(derniere)[:10], '%Y-%m-%d').date()
        jours = (date_ref - d).days
    else:
        jours = interv * 3  # Jamais livré = max score dispo

    score = (1 - taux) * w1 + min(1.0, stk / 1000.0) * w2 + min(1.0, jours / (interv * 3)) * w3
    return round(score, 6), stk

def get_derniere_livraison(code_parcelle):
    """Dernière date de livraison pour une parcelle."""
    rows = sb_get('lignes_repartition', {
        'code_parcelle': f'eq.{code_parcelle}',
        'select': 'date_livraison',
        'order': 'date_livraison.desc',
        'limit': '1'
    })
    if rows and rows[0].get('date_livraison'):
        return rows[0]['date_livraison']
    return None

@app.route('/api/repartition/simuler', methods=['POST'])
def simuler_repartition():
    """
    Simule la répartition sans enregistrer.
    Retourne la liste des producteurs sélectionnés avec leurs volumes et scores.
    """
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Non authentifié'}), 401

    data = request.json
    label = data.get('label')  # RA ou FT
    volume_total = float(data.get('volume_total', 0))
    date_ref_str = data.get('date_ref', date.today().isoformat())
    sections = data.get('sections', [])  # Filtrer par section(s)
    plafond_override = data.get('plafond_override')  # Dérogation plafond
    sauter_intervalle = data.get('sauter_intervalle', False)  # Dérogation intervalle

    if not label or volume_total <= 0:
        return jsonify({'error': 'Label et volume requis'}), 400
    if label not in ('RA', 'FT'):
        return jsonify({'error': 'Label invalide (RA ou FT)'}), 400

    date_ref = datetime.strptime(date_ref_str, '%Y-%m-%d').date()
    cfg = get_config()
    saison = cfg.get('SAISON_EN_COURS', 'GT')
    poids_min = float(cfg.get('POIDS_MIN', 25))
    poids_sac = float(cfg.get('POIDS_SAC_KG', 75))
    plafond_base = float(cfg.get('PLAFOND_PARCELLE', 0.30))
    interv_jours = int(cfg.get('INTERV_JOURS', 14))

    plafond = float(plafond_override) if plafond_override else plafond_base

    # 1. Charger parcelles éligibles selon label
    params = {'actif': 'eq.true', 'select': '*'}
    if label == 'RA':
        params['rainforest_alliance'] = 'eq.true'
    elif label == 'FT':
        params['fairtrade'] = 'eq.true'
    if sections:
        params['section'] = f'in.("{",".join(sections)}")'

    parcelles = sb_get('parcelles', params)
    if not parcelles:
        return jsonify({'error': 'Aucune parcelle éligible'}), 400

    # 2. Enrichir chaque parcelle avec dernière livraison
    for p in parcelles:
        p['_derniere_livraison'] = get_derniere_livraison(p['code_parcelle'])

    # 3. Filtrer: stock > POIDS_MIN + contrainte 14j (sauf dérogation)
    eligibles = []
    exclus_intervalle = []
    exclus_stock = []

    for p in parcelles:
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

    # 4. Calculer score pour chaque éligible
    for p in eligibles:
        score, stk = calculer_score(p, cfg, saison, date_ref)
        p['_score'] = score
        p['_stk_dispo'] = stk

    # 5. Trier par score décroissant, ex-aequo mélangés aléatoirement
    # Regrouper par score arrondi à 4 décimales
    random.shuffle(eligibles)
    eligibles.sort(key=lambda p: p['_score'], reverse=True)

    # 6. Répartir le volume selon l'algorithme
    volume_restant = volume_total
    lignes = []

    for p in eligibles:
        if volume_restant < poids_min:
            break

        stk = p['_stk_dispo']
        max_plafond = min(stk, volume_total * plafond)
        attrib = min(stk, max_plafond, volume_restant)
        attrib = max(attrib, poids_min)

        if attrib < poids_min or attrib > volume_restant:
            continue

        # Arrondir au sac entier
        nb_sacs = math.floor(attrib / poids_sac)
        if nb_sacs == 0:
            nb_sacs = 1
        attrib_final = nb_sacs * poids_sac

        if attrib_final > volume_restant:
            nb_sacs = math.floor(volume_restant / poids_sac)
            if nb_sacs == 0:
                break
            attrib_final = nb_sacs * poids_sac

        if attrib_final > stk or attrib_final < poids_min:
            continue

        # Prix
        prix_cle = 'PRIX_KG_GT' if saison == 'GT' else 'PRIX_KG_PT'
        prix_kg = float(cfg.get(prix_cle, 0))
        montant = attrib_final * prix_kg

        lignes.append({
            'code_parcelle': p['code_parcelle'],
            'code_producteur': p['code_producteur'],
            'nom_producteur': p['nom_producteur'],
            'section': p.get('section', ''),
            'latitude': p.get('latitude'),
            'longitude': p.get('longitude'),
            'score_calcule': p['_score'],
            'volume_attribue': attrib_final,
            'nombre_sacs': nb_sacs,
            'montant': montant,
            'stk_dispo': stk,
        })

        volume_restant -= attrib_final

    if not lignes:
        return jsonify({'error': 'Impossible de répartir le volume demandé'}), 400

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
    })

@app.route('/api/repartition/generer', methods=['POST'])
def generer_repartition():
    """
    Génère et enregistre définitivement une fiche de répartition.
    Met à jour les réserves des parcelles.
    """
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

    # Générer numéro de fiche: FR-YYYYMMDD-NNN
    today_str = date.today().strftime('%Y%m%d')
    prefix = f'FR-{today_str}-'
    existing = sb_get('fiches_repartition', {
        'num_fiche': f'like.{prefix}%',
        'select': 'num_fiche',
        'order': 'num_fiche.desc',
        'limit': '1'
    })
    if existing:
        last = existing[0]['num_fiche']
        seq = int(last.split('-')[-1]) + 1
    else:
        seq = 1
    num_fiche = f'{prefix}{seq:03d}'

    # Enregistrer fiche
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

    # Enregistrer lignes + mettre à jour réserves
    date_livraison = datetime.strptime(date_livraison_str, '%Y-%m-%d').date().isoformat()

    for ligne in lignes:
        # Insérer ligne
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
            'date_livraison': date_livraison,
        })

        # Mettre à jour volume livré (pas réserve — c'est "livré en caisse")
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

    # Enregistrer dérogations si nécessaire
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
# FICHES (liste, détail)
# ============================================================

@app.route('/api/fiches', methods=['GET'])
def get_fiches():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Non authentifié'}), 401

    params = {
        'select': '*',
        'order': 'date_creation.desc',
        'limit': '100'
    }
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

    # Titre
    label_txt = 'RAINFOREST ALLIANCE' if fiche['label'] == 'RA' else 'FAIRTRADE'
    title = Paragraph(
        f'<b>FICHE DE RÉPARTITION {label_txt}</b>',
        ParagraphStyle('title', parent=styles['Title'], fontSize=14, alignment=TA_CENTER)
    )
    elements.append(title)
    elements.append(Spacer(1, 0.3*cm))

    # Infos fiche
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

    # Tableau lignes
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
            '',  # stock dispo pas stocké en ligne
            f'{l["volume_attribue"]:,.0f}',
            str(l['nombre_sacs']),
            f'{l.get("montant", 0):,.0f}',
            f'{l.get("latitude", ""):.4f}' if l.get('latitude') else '',
            f'{l.get("longitude", ""):.4f}' if l.get('longitude') else '',
        ])

    # Ligne totaux
    total_vol = sum(l['volume_attribue'] for l in lignes)
    total_sacs = sum(l['nombre_sacs'] for l in lignes)
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

    # En-tête coop
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

    # Infos transport
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

    # Tableau producteurs
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
            f'{l.get("latitude", ""):.4f}' if l.get('latitude') else '',
            f'{l.get("longitude", ""):.4f}' if l.get('longitude') else '',
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

    # Signatures
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
# ALERTES STOCK CRITIQUE
# ============================================================

@app.route('/api/alertes', methods=['GET'])
def get_alertes():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Non authentifié'}), 401

    cfg = get_config()
    saison = cfg.get('SAISON_EN_COURS', 'GT')
    seuil_critique = 0.10  # <10%

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
    """
    Irréversible — reporte les résidus GT vers PT.
    Vérifie que la date de début PT est atteinte.
    """
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

    # Vérifier date début PT
    date_debut_pt_str = cfg.get('DATE_DEBUT_PT', '')
    if date_debut_pt_str:
        date_debut_pt = datetime.strptime(date_debut_pt_str, '%Y-%m-%d').date()
        if date.today() < date_debut_pt:
            return jsonify({
                'error': f'La petite traite ne commence que le {date_debut_pt.strftime("%d/%m/%Y")}. Clôture impossible avant cette date.'
            }), 400

    # Calculer résidus pour chaque parcelle
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
            # Reporter sur PT
            ancien_report = float(p.get('report_gt_vers_pt', 0))
            sb_patch('parcelles', {'code_parcelle': p['code_parcelle']}, {
                'report_gt_vers_pt': ancien_report + residu
            })
            volume_reporte += residu
            nb_parcelles += 1

    # Changer saison → PT
    sb_patch('config', {'cle': 'SAISON_EN_COURS'}, {'valeur': 'PT'})

    # Enregistrer clôture
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
    # Placeholder — logique existante à brancher
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

    # Fiches
    fiches = sb_get('fiches_repartition', {'select': 'label,saison,volume_affecte,nombre_sacs,date_creation'})
    # Parcelles
    parcelles = sb_get('parcelles', {'actif': 'eq.true', 'select': 'estimation_kg,volume_livre_gt,volume_livre_pt,section,rainforest_alliance,fairtrade'})
    # Journal (dernières 50)
    journal = sb_get('journal', {'select': '*', 'order': 'date_heure.desc', 'limit': '50'})

    # Stats par label
    stats = {
        'nb_fiches_ra': sum(1 for f in fiches if f['label'] == 'RA'),
        'nb_fiches_ft': sum(1 for f in fiches if f['label'] == 'FT'),
        'volume_ra': sum(f['volume_affecte'] for f in fiches if f['label'] == 'RA'),
        'volume_ft': sum(f['volume_affecte'] for f in fiches if f['label'] == 'FT'),
        'nb_parcelles': len(parcelles),
        'nb_parcelles_ra': sum(1 for p in parcelles if p.get('rainforest_alliance')),
        'nb_parcelles_ft': sum(1 for p in parcelles if p.get('fairtrade')),
        'estimation_totale': sum(p.get('estimation_kg', 0) for p in parcelles),
        'livre_gt': sum(p.get('volume_livre_gt', 0) for p in parcelles),
        'livre_pt': sum(p.get('volume_livre_pt', 0) for p in parcelles),
        'saison_en_cours': saison,
    }

    # Par section
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
    """Rapport de distribution par section pour le directeur."""
    user = get_current_user()
    if not user or user['profil'] != 'DIRECTEUR':
        return jsonify({'error': 'Accès refusé'}), 403

    cfg = get_config()
    saison = request.args.get('saison', cfg.get('SAISON_EN_COURS', 'GT'))

    # Lignes de répartition avec info section
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
# GESTION CODES D'ACCES (directeur)
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
    result = sb_post('utilisateurs', {'code_acces': code_hash, 'profil': profil})
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
    return app.send_static_file('app.html')

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'version': 'v4'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
