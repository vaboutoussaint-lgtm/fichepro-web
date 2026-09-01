"""
FichePro Manager — Serveur Flask
Isolation complète par licence — données séparées par client
"""
import os, io, json, random
from pathlib import Path
from datetime import date
from flask import Flask, render_template, request, jsonify, session, redirect, send_file
from engine import FicheEngine
from license_manager import verify_license, is_licensed, get_license_info
from export_excel import generer_export_excel, generer_nom_fichier
from supabase_db import (charger_producteurs, sauvegarder_producteurs,
                         sauvegarder_fiche, charger_fiches)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fichepro2026")

DATA_DIR    = Path("data")
CONFIG_FILE = DATA_DIR / "config.json"
DATA_DIR.mkdir(exist_ok=True)

# Moteurs par licence — isolation complète
_engines = {}

def get_config():
    if CONFIG_FILE.exists():
        try: return json.loads(CONFIG_FILE.read_text())
        except: pass
    return {}

def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))

def get_license_key():
    """Récupère la clé de licence active depuis le fichier local"""
    info = get_license_info()
    return info.get("key", "DEFAULT").upper()

def get_engine():
    """Retourne le moteur isolé pour la licence active"""
    license_key = get_license_key()
    if license_key not in _engines:
        _engines[license_key] = _init_engine(license_key)
    return _engines[license_key]

def _init_engine(license_key):
    """Initialise un moteur depuis Supabase pour une licence donnée"""
    engine = FicheEngine("")
    prods = charger_producteurs(license_key)
    if prods:
        engine.base_parcelles = []
        engine.dict_lignes = {}
        for i, p in enumerate(prods):
            bp = {
                "code"  : p.get("code", ""),
                "parc"  : p.get("parcelle", ""),
                "nom"   : p.get("nom", ""),
                "sect"  : p.get("section", ""),
                "estim" : int(p.get("estim", 0)),
                "stk_gt": int(p.get("stk_gt", 0)),
                "stk_pt": int(p.get("stk_pt", 0)),
                "liv_gt": int(p.get("liv_gt", 0)),
                "liv_pt": int(p.get("liv_pt", 0)),
                "lon"   : p.get("lon"),
                "lat"   : p.get("lat")
            }
            engine.base_parcelles.append(bp)
            engine.dict_lignes[bp["code"]] = i
    cfg = get_config()
    engine.calendrier = cfg.get("calendrier", "NOUVEAU")
    return engine

def reload_engine():
    """Recharge le moteur pour la licence active depuis Supabase"""
    license_key = get_license_key()
    _engines[license_key] = _init_engine(license_key)
    return _engines[license_key]

# ============================================================
# ROUTES
# ============================================================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/app")
def app_main():
    if not is_licensed(): return redirect("/")
    return render_template("app.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"succes": True})

# ============================================================
# LICENCE
# ============================================================
@app.route("/api/licence/verify", methods=["POST"])
def api_verify_licence():
    data = request.json or {}
    key = data.get("key", "").strip()
    if not key: return jsonify({"valid": False, "message": "Cle vide."})
    result = verify_license(key)
    if result.get("valid"):
        # Précharger les données de ce client
        lk = key.upper()
        _engines[lk] = _init_engine(lk)
    return jsonify(result)

@app.route("/api/licence/status")
def api_licence_status():
    return jsonify({"licensed": is_licensed(), "info": get_license_info()})

# ============================================================
# IMPORT RA
# ============================================================
@app.route("/api/import/ra", methods=["POST"])
def api_import_ra():
    if "file" not in request.files:
        return jsonify({"erreur": "Aucun fichier recu."})
    f = request.files["file"]
    if not f.filename.endswith((".xlsx", ".xls", ".xlsm")):
        return jsonify({"erreur": "Format non supporte."})

    license_key = get_license_key()

    # Créer fichier Excel vierge si nécessaire
    cfg = get_config()
    wb_path = cfg.get("wb_path", "")
    if not wb_path or not Path(wb_path).exists():
        try:
            import openpyxl
            wb_new = openpyxl.Workbook()
            ws = wb_new.active
            ws.title = "Base Parcelles"
            wb_new.create_sheet("Suivi Livraisons")
            wb_path = str(DATA_DIR / "fichepro_auto.xlsx")
            wb_new.save(wb_path)
            cfg["wb_path"] = wb_path
            save_config(cfg)
        except Exception as e:
            return jsonify({"erreur": f"Erreur: {e}"})

    engine = get_engine()
    # Sauvegarder avec nom simple sans caractères spéciaux
    tmp_path = str(DATA_DIR / "registre_ra_import.xlsx")
    f.save(tmp_path)
    result = engine.importer_registre_ra(tmp_path)
    try:
        os.unlink(tmp_path)
    except:
        pass

    if result.get("succes"):
        # Sauvegarder avec la clé licence
        sb = sauvegarder_producteurs(engine.base_parcelles, license_key)
        result["supabase"] = sb.get("succes", False)
        # Recharger
        engine2 = reload_engine()
        result["nb_prod_moteur"] = len(engine2.base_parcelles)
        result["stats"] = engine2.stats_dashboard()

    return jsonify(result)

# ============================================================
# DASHBOARD ET STATS
# ============================================================
@app.route("/api/dashboard")
def api_dashboard():
    engine = get_engine()
    cfg = get_config()
    engine.calendrier = cfg.get("calendrier", "NOUVEAU")
    return jsonify(engine.stats_dashboard())

@app.route("/api/config/status")
def api_config_status():
    engine = get_engine()
    cfg = get_config()
    return jsonify({
        "has_file"  : True,
        "calendrier": cfg.get("calendrier", "NOUVEAU"),
        "stats"     : engine.stats_dashboard() if engine else {}
    })

@app.route("/api/producteurs")
def api_producteurs():
    engine = get_engine()
    if not engine or not engine.base_parcelles: return jsonify([])
    cfg = get_config()
    engine.calendrier = cfg.get("calendrier", "NOUVEAU")
    periode, _, _ = engine.periode_traite(date.today())
    liste = []
    for bp in engine.base_parcelles:
        stk   = engine.stock_prod(bp, periode)
        est   = bp["estim"]
        liv   = bp["liv_gt"] + bp["liv_pt"]
        pct   = round(liv/est*100, 1) if est > 0 else 0
        score = round(engine.calculer_score(bp, periode), 3)
        liste.append({"code": bp["code"], "nom": bp["nom"], "sect": bp["sect"],
                      "estim": est, "stk": stk, "liv": liv, "pct": pct, "score": score})
    liste.sort(key=lambda x: x["score"], reverse=True)
    return jsonify(liste)

@app.route("/api/sections")
def api_sections():
    engine = get_engine()
    if not engine or not engine.base_parcelles: return jsonify([])
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
    cal = data.get("calendrier", "")
    if cal not in ("NOUVEAU", "ANCIEN"):
        return jsonify({"erreur": "Invalide."})
    cfg = get_config()
    cfg["calendrier"] = cal
    save_config(cfg)
    engine = get_engine()
    if engine: engine.calendrier = cal
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

    if poids_total <= 0: return jsonify({"erreur": "Poids invalide."})
    if not sections:     return jsonify({"erreur": "Selectionnez au moins une section."})
    if prix_kg <= 0:     return jsonify({"erreur": "Prix invalide."})
    if d_debut > d_ref:  return jsonify({"erreur": "Date debut > date limite."})

    engine.calendrier = calendrier
    engine.actualiser_stocks()
    result = engine.generer_fiche(poids_total, d_ref, d_debut, sections, prix_kg)

    if result.get("succes"):
        license_key = get_license_key()
        sauvegarder_fiche(result, license_key)
        sauvegarder_producteurs(engine.base_parcelles, license_key)

    return jsonify(result)

@app.route("/api/fiche/continuer", methods=["POST"])
def api_continuer_fiche():
    engine = get_engine()
    if not engine: return jsonify({"erreur": "Moteur non initialise."})
    data = request.json or {}
    try:
        palier=float(data.get("palier",0)); etat=data.get("etat",{})
        periode=data.get("periode","")
        d_debut=date.fromisoformat(data.get("d_debut",""))
        d_ref=date.fromisoformat(data.get("d_ref",""))
        prix_kg=float(data.get("prix_kg",0))
        poids_total=int(data.get("poids_total",0))
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
    data = request.json or {}
    fiche_data = data.get("fiche", {})
    if not fiche_data: return jsonify({"erreur": "Aucune fiche."})
    try:
        excel_bytes = generer_export_excel(fiche_data)
        nom = generer_nom_fichier(fiche_data)
        return send_file(io.BytesIO(excel_bytes), as_attachment=True, download_name=nom,
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        return jsonify({"erreur": f"Erreur export: {e}"})

@app.route("/api/export/pdf", methods=["POST"])
def api_export_pdf():
    return jsonify({"erreur": "Export PDF bientot disponible."})

# Télécharger une fiche historique par son ID
@app.route("/api/fiches/<fiche_id>/excel")
def api_telecharger_fiche(fiche_id):
    license_key = get_license_key()
    fiches = charger_fiches(license_key)
    fiche = next((f for f in fiches if f.get("fiche_id") == fiche_id), None)
    if not fiche: return jsonify({"erreur": "Fiche introuvable."})
    try:
        lignes = json.loads(fiche.get("lignes", "[]"))
        fiche_data = {**fiche, "lignes_fiche": lignes}
        excel_bytes = generer_export_excel(fiche_data)
        nom = generer_nom_fichier(fiche_data)
        return send_file(io.BytesIO(excel_bytes), as_attachment=True, download_name=nom,
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
    if engine: engine.log = []
    return jsonify({"succes": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
