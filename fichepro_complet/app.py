"""
FichePro Manager - Serveur Flask
Sessions persistantes via Supabase — données disponibles après redémarrage
"""
import os, io, json, random
from pathlib import Path
from datetime import date, datetime, timedelta
from flask import (Flask, render_template, request, jsonify,
                   session, redirect, send_file, make_response)
from engine import FicheEngine
from license_manager import (verify_license, is_licensed, get_license_info,
                              peut_generer_fiche, incrementer_fiches_demo, is_demo)
from export_excel import generer_export_excel, generer_nom_fichier
from supabase_db import (
    charger_producteurs, sauvegarder_producteurs,
    sauvegarder_fiche, charger_fiches,
    creer_session, lire_session, supprimer_session
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fichepro2026")

DATA_DIR    = Path("data")
CONFIG_FILE = DATA_DIR / "config.json"
DATA_DIR.mkdir(exist_ok=True)

# Moteurs en mémoire — un par licence
_engines = {}

def precharger_depuis_supabase():
    """
    Au démarrage du serveur, charge tous les producteurs depuis Supabase
    groupés par license_key. Ainsi les données sont disponibles
    immédiatement sans attendre que l'utilisateur se connecte.
    """
    try:
        from supabase_db import _get
        # Charger tous les producteurs toutes licences confondues
        tous = []
        offset = 0
        while True:
            rows = _get("producers", {
                "select": "license_key",
                "limit" : "1000",
                "offset": str(offset),
                "order" : "license_key.asc"
            })
            if not rows: break
            tous.extend(rows)
            if len(rows) < 1000: break
            offset += 1000
        
        # Récupérer les clés uniques
        keys = list(set(r["license_key"] for r in tous if r.get("license_key")))
        print(f"[STARTUP] {len(keys)} licence(s) trouvée(s) dans Supabase: {keys}")
        
        # Charger le moteur pour chaque licence
        for key in keys:
            _engines[key] = _init_engine(key)
            nb = len(_engines[key].base_parcelles)
            print(f"[STARTUP] Licence {key} : {nb} parcelles chargées")
            
    except Exception as e:
        print(f"[STARTUP] Erreur préchargement: {e}")

# ============================================================
# CONFIG
# ============================================================
def get_config():
    if CONFIG_FILE.exists():
        try: return json.loads(CONFIG_FILE.read_text())
        except: pass
    return {}

def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))

# ============================================================
# SESSION PERSISTANTE
# ============================================================
COOKIE_NAME = "fp_session"

def get_session_id():
    """Lit le session_id depuis le cookie navigateur"""
    return request.cookies.get(COOKIE_NAME, "")

def get_license_key():
    """Récupère la clé de licence depuis le header X-License-Key.
    Envoyé par le JavaScript (localStorage) à chaque requête.
    Fonctionne avec tous les workers — pas de session, pas de cookie.
    """
    key = request.headers.get("X-License-Key", "").strip().upper()
    return key if key else ""

def set_session(response, license_key: str) -> str:
    """Crée une session Supabase et pose le cookie navigateur"""
    sid = creer_session(license_key)
    # Cookie sans expiration — persiste jusqu'à déconnexion manuelle
    response.set_cookie(
        COOKIE_NAME, sid,
        httponly=True,
        samesite="Lax",
        secure=False,     # Compatible HTTP et HTTPS
        max_age=365*24*3600  # 1 an maximum
    )
    session["license_key"] = license_key
    return sid

def clear_session(response):
    """Supprime la session"""
    sid = get_session_id()
    if sid:
        supprimer_session(sid)
    response.delete_cookie(COOKIE_NAME)
    session.clear()

# ============================================================
# MOTEUR
# ============================================================
def get_engine():
    """Retourne le moteur pour la licence active.
    Charge depuis Supabase si nécessaire — ne dépend pas de la mémoire inter-workers.
    """
    license_key = get_license_key()
    
    # Moteur en mémoire avec données → l'utiliser directement
    if license_key and license_key in _engines and _engines[license_key].base_parcelles:
        return _engines[license_key]
    
    # Charger depuis Supabase si clé disponible
    if license_key:
        _engines[license_key] = _init_engine(license_key)
        if _engines[license_key].base_parcelles:
            return _engines[license_key]
    
    # Dernier recours : lire le cookie et interroger Supabase directement
    sid = get_session_id()
    if sid:
        from supabase_db import lire_session
        key = lire_session(sid)
        if key and key not in _engines:
            _engines[key] = _init_engine(key)
        if key and _engines.get(key) and _engines[key].base_parcelles:
            session["license_key"] = key
            return _engines[key]
    
    return None

def _init_engine(license_key: str) -> FicheEngine:
    """
    Initialise le moteur depuis Supabase.
    Recalcule les stocks en déduisant toutes les fiches déjà générées.
    """
    engine = FicheEngine("")
    prods  = charger_producteurs(license_key)

    if prods:
        engine.base_parcelles = []
        engine.dict_lignes    = {}
        parc_index            = {}

        for i, p in enumerate(prods):
            est = int(p.get("estim", 0))
            bp  = {
                "code"  : p.get("code", ""),
                "parc"  : p.get("parcelle", ""),
                "nom"   : p.get("nom", ""),
                "sect"  : p.get("section", ""),
                "estim" : est,
                "stk_gt": int(est * 0.8),
                "stk_pt": int(est * 0.2),
                "liv_gt": 0,
                "liv_pt": 0,
                "lon"   : p.get("lon"),
                "lat"   : p.get("lat")
            }
            engine.base_parcelles.append(bp)
            engine.dict_lignes[bp["code"]] = i
            parc_index[bp["parc"]] = i

        # Déduire les volumes des fiches déjà générées
        try:
            fiches = charger_fiches(license_key)
            for fiche in fiches:
                lignes_json = fiche.get("lignes", "[]")
                try:
                    lignes = json.loads(lignes_json) if isinstance(lignes_json, str) else (lignes_json or [])
                except:
                    lignes = []
                periode = (fiche.get("periode") or "").lower()
                est_gt  = "grande" in periode or "gt" in periode
                for ligne in lignes:
                    parc  = ligne.get("parc", "")
                    poids = int(ligne.get("poids", 0))
                    if parc in parc_index and poids > 0:
                        bp = engine.base_parcelles[parc_index[parc]]
                        if est_gt:
                            bp["liv_gt"] += poids
                            bp["stk_gt"]  = max(0, bp["stk_gt"] - poids)
                        else:
                            bp["liv_pt"] += poids
                            bp["stk_pt"]  = max(0, bp["stk_pt"] - poids)
        except Exception as e:
            engine._log(f"Erreur recalcul stocks: {e}", "warn")

    cfg = get_config()
    engine.calendrier = cfg.get("calendrier", "NOUVEAU")
    return engine

def reload_engine(license_key: str) -> FicheEngine:
    """Recharge le moteur depuis Supabase"""
    if license_key in _engines:
        del _engines[license_key]
    _engines[license_key] = _init_engine(license_key)
    return _engines[license_key]

# ============================================================
# VÉRIFICATION LICENCE
# ============================================================
def check_licensed():
    """Vérifie si la session est valide et la licence active.
    Si le header X-License-Key est présent et valide → accès autorisé.
    """
    key = get_license_key()
    if not key:
        return False, None
    # Valider la clé directement sans dépendre de license.json
    from license_manager import ADMIN_CODES, DEMO_UNIQUE, DEMO_PREFIX
    if key in ADMIN_CODES:
        return True, key
    if key == DEMO_UNIQUE or (key.startswith(DEMO_PREFIX) and len(key) == 12):
        return True, key
    # Code standard WIL...HAY
    if key.startswith("WIL") and key.endswith("HAY") and len(key) == 17:
        return True, key
    return False, None

# ============================================================
# ROUTES PRINCIPALES
# ============================================================
@app.route("/")
def index():
    # Si session valide → aller directement sur /app
    key = get_license_key()
    if key:
        return redirect("/app")
    return render_template("index.html")

@app.route("/app")
def app_main():
    # La vérification est faite côté JavaScript (localStorage + fetchAPI)
    return render_template("app.html")

@app.route("/logout")
def logout():
    resp = make_response(redirect("/"))
    clear_session(resp)
    return resp

@app.route("/api/logout", methods=["POST"])
def api_logout():
    resp = make_response(jsonify({"succes": True}))
    clear_session(resp)
    return resp

# ============================================================
# LICENCE
# ============================================================
@app.route("/api/licence/verify", methods=["POST"])
def api_verify_licence():
    data = request.json or {}
    key  = data.get("key", "").strip()
    if not key:
        return jsonify({"valid": False, "message": "Cle vide."})

    result = verify_license(key)
    if not result.get("valid"):
        return jsonify(result)

    lk = key.upper()
    # Créer la session persistante
    resp = make_response(jsonify(result))
    set_session(resp, lk)
    # Précharger les données
    _engines[lk] = _init_engine(lk)
    return resp

@app.route("/api/licence/status")
def api_licence_status():
    key = get_license_key()
    info = get_license_info()
    if not info.get("key") and key:
        info = {"key": key, "expire": "31/12/2099", "admin": True}
    return jsonify({"licensed": bool(key), "info": info})

@app.route("/api/licence/renouveler", methods=["POST"])
def api_renouveler_licence():
    """Renouvellement avec nouveau code — conserve l'historique"""
    data    = request.json or {}
    new_key = data.get("key", "").strip()
    if not new_key:
        return jsonify({"valid": False, "message": "Cle vide."})

    result = verify_license(new_key)
    if not result.get("valid"):
        return jsonify(result)

    old_key = get_license_key()
    new_lk  = new_key.upper()

    # Migrer les données de l'ancienne clé vers la nouvelle
    if old_key and old_key != new_lk:
        try:
            # Les fiches et producteurs restent accessibles via l'ancienne clé
            # La nouvelle clé part avec un registre vierge
            pass
        except:
            pass

    resp = make_response(jsonify({**result, "message": "Licence renouvelee. Importez votre nouveau registre RA."}))
    set_session(resp, new_lk)
    _engines[new_lk] = FicheEngine("")  # Moteur vierge pour la nouvelle période
    return resp

# ============================================================
# IMPORT REGISTRE RA
# ============================================================
@app.route("/api/import/ra", methods=["POST"])
def api_import_ra():
    if "file" not in request.files:
        return jsonify({"erreur": "Aucun fichier recu."})
    f = request.files["file"]
    if not f.filename.endswith((".xlsx", ".xls", ".xlsm")):
        return jsonify({"erreur": "Format non supporte. Utilisez .xlsx"})

    license_key = get_license_key()
    if not license_key:
        return jsonify({"erreur": "Session expiree. Reconnectez-vous."})

    # Fichier Excel vierge si nécessaire
    cfg     = get_config()
    wb_path = cfg.get("wb_path", "")
    if not wb_path or not Path(wb_path).exists():
        try:
            import openpyxl
            wb_new = openpyxl.Workbook()
            ws     = wb_new.active
            ws.title = "Base Parcelles"
            wb_new.create_sheet("Suivi Livraisons")
            wb_path = str(DATA_DIR / "fichepro_auto.xlsx")
            wb_new.save(wb_path)
            cfg["wb_path"] = wb_path
            save_config(cfg)
        except Exception as e:
            return jsonify({"erreur": f"Erreur: {e}"})

    engine   = _engines.get(license_key) or _init_engine(license_key)
    tmp_path = str(DATA_DIR / "registre_ra_import.xlsx")
    file_bytes = f.read()
    with open(tmp_path, "wb") as fw:
        fw.write(file_bytes)

    result = engine.importer_registre_ra(tmp_path)
    try:
        os.unlink(tmp_path)
    except:
        pass

    if result.get("succes"):
        _engines[license_key] = engine
        sauvegarder_producteurs(engine.base_parcelles, license_key)
        result["nb_prod_moteur"] = len(engine.base_parcelles)
        result["stats"]          = engine.stats_dashboard()

    return jsonify(result)

# ============================================================
# DASHBOARD ET STATS
# ============================================================
@app.route("/api/dashboard")
def api_dashboard():
    engine = get_engine()
    if not engine:
        return jsonify({"erreur": "Non connecte."})
    cfg = get_config()
    engine.calendrier = cfg.get("calendrier", "NOUVEAU")
    return jsonify(engine.stats_dashboard())

@app.route("/api/config/status")
def api_config_status():
    engine = get_engine()
    cfg    = get_config()
    return jsonify({
        "has_file"  : True,
        "calendrier": cfg.get("calendrier", "NOUVEAU"),
        "stats"     : engine.stats_dashboard() if engine else {}
    })

@app.route("/api/producteurs")
def api_producteurs():
    engine = get_engine()
    if not engine or not engine.base_parcelles:
        return jsonify([])
    cfg = get_config()
    engine.calendrier = cfg.get("calendrier", "NOUVEAU")
    periode, _, _ = engine.periode_traite(date.today())
    liste = []
    for bp in engine.base_parcelles:
        stk   = engine.stock_prod(bp, periode)
        est   = bp["estim"]
        liv   = bp["liv_gt"] + bp["liv_pt"]
        pct   = round(liv / est * 100, 1) if est > 0 else 0
        score = round(engine.calculer_score(bp, periode), 3)
        liste.append({
            "code" : bp["code"], "nom": bp["nom"], "sect": bp["sect"],
            "estim": est, "stk": stk, "liv": liv, "pct": pct, "score": score
        })
    liste.sort(key=lambda x: x["score"], reverse=True)
    return jsonify(liste)

@app.route("/api/sections")
def api_sections():
    engine = get_engine()
    if not engine or not engine.base_parcelles:
        return jsonify([])
    cfg = get_config()
    engine.calendrier = cfg.get("calendrier", "NOUVEAU")
    periode, _, _ = engine.periode_traite(date.today())
    sections = {}
    for bp in engine.base_parcelles:
        s   = bp["sect"]
        stk = engine.stock_prod(bp, periode)
        if s not in sections:
            sections[s] = {"nom": s, "vol": 0, "nb": 0}
        sections[s]["vol"] += stk
        sections[s]["nb"]  += 1
    return jsonify(sorted(sections.values(), key=lambda x: x["vol"], reverse=True))

# ============================================================
# CALENDRIER
# ============================================================
@app.route("/api/calendrier/set", methods=["POST"])
def api_set_calendrier():
    data = request.json or {}
    cal  = data.get("calendrier", "")
    if cal not in ("NOUVEAU", "ANCIEN"):
        return jsonify({"erreur": "Invalide."})
    cfg = get_config()
    cfg["calendrier"] = cal
    save_config(cfg)
    engine = get_engine()
    if engine:
        engine.calendrier = cal
    return jsonify({"succes": True, "calendrier": cal})

# ============================================================
# GENERATION FICHE
# ============================================================
@app.route("/api/fiche/generer", methods=["POST"])
def api_generer_fiche():
    engine = get_engine()
    if not engine or not engine.base_parcelles:
        return jsonify({"erreur": "Importez d'abord votre registre RA."})
    data = request.json or {}
    try:
        poids_total = int(data.get("poids_total", 0))
        d_ref       = date.fromisoformat(data.get("d_ref", ""))
        d_debut     = date.fromisoformat(data.get("d_debut", ""))
        sections    = data.get("sections", [])
        prix_kg     = float(data.get("prix_kg", 0))
        calendrier  = data.get("calendrier", "NOUVEAU")
    except Exception as e:
        return jsonify({"erreur": f"Parametres invalides: {e}"})

    if poids_total <= 0:  return jsonify({"erreur": "Poids invalide."})
    if not sections:      return jsonify({"erreur": "Selectionnez au moins une section."})
    if prix_kg <= 0:      return jsonify({"erreur": "Prix invalide."})
    if d_debut > d_ref:   return jsonify({"erreur": "Date debut > date limite."})

    # Vérifier si la licence demo peut encore générer
    peut, msg_erreur = peut_generer_fiche()
    if not peut:
        return jsonify({"erreur": msg_erreur})

    engine.calendrier = calendrier
    engine.actualiser_stocks()
    result = engine.generer_fiche(poids_total, d_ref, d_debut, sections, prix_kg)

    if result.get("succes"):
        license_key = get_license_key()
        if is_demo():
            # DEMO : compteur incrémenté mais rien sauvegardé dans Supabase
            incrementer_fiches_demo()
            result["demo_info"] = "Demonstration utilisee. Contactez Wilfried VABOU au +225 01 02 02 50 25 pour activer une licence complete."
        else:
            # STANDARD / ADMIN : tout sauvegardé dans Supabase
            sauvegarder_fiche(result, license_key)
            sauvegarder_producteurs(engine.base_parcelles, license_key)

    return jsonify(result)

@app.route("/api/fiche/continuer", methods=["POST"])
def api_continuer_fiche():
    engine = get_engine()
    if not engine:
        return jsonify({"erreur": "Moteur non initialise."})
    data = request.json or {}
    try:
        palier      = float(data.get("palier", 0))
        etat        = data.get("etat", {})
        periode     = data.get("periode", "")
        d_debut     = date.fromisoformat(data.get("d_debut", ""))
        d_ref       = date.fromisoformat(data.get("d_ref", ""))
        prix_kg     = float(data.get("prix_kg", 0))
        poids_total = int(data.get("poids_total", 0))
    except Exception as e:
        return jsonify({"erreur": f"Parametres: {e}"})

    result = engine.continuer_apres_autorisation(
        palier, etat, periode, d_debut, d_ref, prix_kg, poids_total)
    if result.get("succes"):
        license_key = get_license_key()
        sauvegarder_fiche(result, license_key)
        sauvegarder_producteurs(engine.base_parcelles, license_key)
    return jsonify(result)

# ============================================================
# EXPORTS
# ============================================================
@app.route("/api/export/excel", methods=["POST"])
def api_export_excel():
    data       = request.json or {}
    fiche_data = data.get("fiche", {})
    if not fiche_data:
        return jsonify({"erreur": "Aucune fiche."})
    try:
        excel_bytes = generer_export_excel(fiche_data)
        nom         = generer_nom_fichier(fiche_data)
        return send_file(io.BytesIO(excel_bytes), as_attachment=True,
                        download_name=nom,
                        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        return jsonify({"erreur": f"Erreur export: {e}"})

@app.route("/api/export/pdf", methods=["POST"])
def api_export_pdf():
    return jsonify({"erreur": "Export PDF bientot disponible."})

@app.route("/api/fiches/<fiche_id>/excel")
def api_telecharger_fiche(fiche_id):
    license_key = get_license_key()
    fiches      = charger_fiches(license_key)
    fiche       = next((f for f in fiches if f.get("fiche_id") == fiche_id), None)
    if not fiche:
        return jsonify({"erreur": "Fiche introuvable."})
    try:
        lignes     = json.loads(fiche.get("lignes", "[]"))
        fiche_data = {**fiche, "lignes_fiche": lignes}
        excel_bytes = generer_export_excel(fiche_data)
        nom         = generer_nom_fichier(fiche_data)
        return send_file(io.BytesIO(excel_bytes), as_attachment=True,
                        download_name=nom,
                        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        return jsonify({"erreur": f"Erreur: {e}"})

# ============================================================
# FICHES ET JOURNAL
# ============================================================
@app.route("/api/fiches")
def api_fiches():
    license_key = get_license_key()
    return jsonify(charger_fiches(license_key))

@app.route("/api/log")
def api_log():
    engine = get_engine()
    return jsonify(engine.log[-50:] if engine else [])

@app.route("/api/log/clear", methods=["POST"])
def api_log_clear():
    engine = get_engine()
    if engine:
        engine.log = []
    return jsonify({"succes": True})

# Précharger au démarrage — données disponibles immédiatement
precharger_depuis_supabase()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
