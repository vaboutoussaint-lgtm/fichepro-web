"""
FichePro Manager — Serveur Flask principal
Lance l'interface web et expose les API pour le moteur Excel
"""
import os
import io
import sys
import json
import threading
import webbrowser
from pathlib import Path
from datetime import date, datetime
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file

from engine import FicheEngine
from license_manager import verify_license, is_licensed, get_license_info
from export_excel import generer_export_excel, generer_nom_fichier
from supabase_db import (
    charger_producteurs, sauvegarder_producteurs, sauvegarder_fiche,
    charger_fiches, nb_producteurs, tester_connexion
)

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Chemins de données
DATA_DIR    = Path("data")
CONFIG_FILE = DATA_DIR / "config.json"
DATA_DIR.mkdir(exist_ok=True)

# Instance moteur globale
_engine: FicheEngine = None

def get_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except:
            pass
    return {}

def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))

def get_engine() -> FicheEngine:
    """
    Charge le moteur. Si Supabase contient des producteurs, 
    les charge directement en mémoire sans fichier Excel.
    """
    global _engine
    cfg = get_config()
    wb_path = cfg.get("wb_path", "")
    
    if _engine is None:
        # Créer un moteur minimal même sans fichier Excel
        _engine = FicheEngine(wb_path or "")
        
        # Charger les producteurs depuis Supabase
        prods = charger_producteurs()
        if prods:
            _engine.base_parcelles = []
            _engine.dict_lignes = {}
            for i, p in enumerate(prods):
                bp = {
                    "code"   : p.get("code", ""),
                    "parc"   : p.get("parcelle", ""),
                    "nom"    : p.get("nom", ""),
                    "sect"   : p.get("section", ""),
                    "estim"  : int(p.get("estim", 0)),
                    "stk_gt" : int(p.get("stk_gt", 0)),
                    "stk_pt" : int(p.get("stk_pt", 0)),
                    "liv_gt" : int(p.get("liv_gt", 0)),
                    "liv_pt" : int(p.get("liv_pt", 0)),
                    "lon"    : p.get("lon"),
                    "lat"    : p.get("lat")
                }
                _engine.base_parcelles.append(bp)
                _engine.dict_lignes[bp["code"]] = i
            _engine._log(f"{len(prods)} producteurs chargés depuis Supabase")
        
        # Charger le fichier Excel si disponible
        if wb_path and Path(wb_path).exists():
            _engine.charger_workbook()
            if not prods:
                _engine.init_dict_lignes()
                _engine.precharger_memoire()
                _engine.init_compteur_fiches()
        
        cal = cfg.get("calendrier", "")
        if cal:
            _engine.calendrier = cal
    
    return _engine

# ============================================================
#  ROUTES PRINCIPALES
# ============================================================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/app")
def app_main():
    """La vérification de licence lit le fichier data/license.json (persistant).
    La session n'est pas nécessaire — la licence est stockée sur disque."""
    if not is_licensed():
        return redirect("/")
    return render_template("app.html")

# ============================================================
#  API LICENCE
# ============================================================
@app.route("/api/licence/verify", methods=["POST"])
def api_verify_licence():
    data = request.json or {}
    key  = data.get("key", "").strip()
    if not key:
        return jsonify({"valid": False, "message": "Clé vide."})
    result = verify_license(key)
    return jsonify(result)

@app.route("/api/licence/status")
def api_licence_status():
    info = get_license_info()
    licensed = is_licensed()
    return jsonify({"licensed": licensed, "info": info})

# ============================================================
#  API CONFIGURATION FICHIER EXCEL
# ============================================================
@app.route("/api/config/fichier", methods=["POST"])
def api_set_fichier():
    if "file" not in request.files:
        return jsonify({"erreur": "Aucun fichier reçu."})
    f = request.files["file"]
    if not f.filename.endswith((".xlsm", ".xlsx")):
        return jsonify({"erreur": "Format non supporté. Utilisez .xlsm ou .xlsx."})
    dest = DATA_DIR / f.filename
    f.save(str(dest))
    cfg = get_config()
    cfg["wb_path"] = str(dest)
    save_config(cfg)
    global _engine
    _engine = None
    engine = get_engine()
    if engine is None:
        return jsonify({"erreur": "Impossible de charger le fichier Excel."})
    # Détecter si la Base Parcelles est vide => signaler que l'import RA est nécessaire
    nb_prod = len(engine.base_parcelles)
    stats = engine.stats_dashboard()
    return jsonify({
        "succes"       : True,
        "fichier"      : f.filename,
        "stats"        : stats,
        "nb_producteurs": nb_prod,
        "besoin_import_ra": nb_prod == 0
    })

@app.route("/api/config/status")
def api_config_status():
    cfg = get_config()
    wb_path = cfg.get("wb_path", "")
    has_file = bool(wb_path and Path(wb_path).exists())
    result = {"has_file": has_file, "calendrier": cfg.get("calendrier", "")}
    if has_file:
        engine = get_engine()
        if engine:
            result["stats"] = engine.stats_dashboard()
    return jsonify(result)

# ============================================================
#  API CALENDRIER
# ============================================================
@app.route("/api/calendrier/set", methods=["POST"])
def api_set_calendrier():
    data = request.json or {}
    cal  = data.get("calendrier", "")
    if cal not in ("NOUVEAU", "ANCIEN"):
        return jsonify({"erreur": "Calendrier invalide."})
    cfg = get_config()
    cfg["calendrier"] = cal
    save_config(cfg)
    engine = get_engine()
    if engine:
        engine.calendrier = cal
    return jsonify({"succes": True, "calendrier": cal})

@app.route("/api/calendrier/reset", methods=["POST"])
def api_reset_calendrier():
    cfg = get_config()
    cfg.pop("calendrier", None)
    save_config(cfg)
    engine = get_engine()
    if engine:
        engine.calendrier = ""
    return jsonify({"succes": True})

# ============================================================
#  API IMPORT REGISTRE RAINFOREST ALLIANCE
# ============================================================
@app.route("/api/import/ra", methods=["POST"])
def api_import_ra():
    """Import du registre RA — crée automatiquement le fichier FichePro si absent"""
    global _engine
    if "file" not in request.files:
        return jsonify({"erreur": "Aucun fichier reçu."})
    f = request.files["file"]
    if not f.filename.endswith((".xlsx", ".xls", ".xlsm")):
        return jsonify({"erreur": "Format non supporté. Utilisez le fichier Excel RA (.xlsx)."})

    # Si pas de fichier FichePro configuré, en créer un vierge automatiquement
    cfg = get_config()
    wb_path = cfg.get("wb_path", "")
    if not wb_path or not Path(wb_path).exists():
        # Créer un fichier Excel vierge avec les feuilles nécessaires
        try:
            import openpyxl
            wb_new = openpyxl.Workbook()
            # Feuille Base Parcelles
            ws_bp = wb_new.active
            ws_bp.title = "Base Parcelles"
            headers_bp = ["Code producteur","Parcelle","Producteur","Section",
                         "","","","","Estimation","","STK_GT","STK_PT","LIV_GT","LIV_PT","Longitude","Latitude"]
            for i, h in enumerate(headers_bp, 1):
                ws_bp.cell(1, i, h)
            # Feuille Suivi Livraisons
            ws_sl = wb_new.create_sheet("Suivi Livraisons")
            headers_sl = ["Code","Poids","Date","ID Fiche","Parcelle","Section","Periode","Prix","Montant"]
            for i, h in enumerate(headers_sl, 1):
                ws_sl.cell(1, i, h)
            wb_path = str(DATA_DIR / "fichepro_auto.xlsx")
            wb_new.save(wb_path)
            cfg["wb_path"] = wb_path
            save_config(cfg)
            global _engine
            _engine = None
        except Exception as e:
            return jsonify({"erreur": f"Impossible de créer le fichier de base : {e}"})

    engine = get_engine()
    if engine is None:
        return jsonify({"erreur": "Erreur lors du chargement du moteur."})

    tmp = DATA_DIR / ("ra_" + f.filename)
    f.save(str(tmp))
    result = engine.importer_registre_ra(str(tmp))
    tmp.unlink(missing_ok=True)
    if result.get("succes"):
        # Sauvegarder dans Supabase
        sb_result = sauvegarder_producteurs(engine.base_parcelles)
        result["supabase"] = sb_result.get("succes", False)
        # Forcer rechargement du moteur depuis Supabase
        _engine = None
        engine2 = get_engine()
        result["stats"] = engine2.stats_dashboard() if engine2 else {}
        result["nb_prod_moteur"] = len(engine2.base_parcelles) if engine2 else 0
    return jsonify(result)

# ============================================================
#  API DASHBOARD / STATS
# ============================================================
@app.route("/api/dashboard")
def api_dashboard():
    engine = get_engine()
    if engine is None:
        return jsonify({"erreur": "Fichier non configuré."})
    cfg     = get_config()
    cal     = cfg.get("calendrier", "")
    if cal:
        engine.calendrier = cal
    stats = engine.stats_dashboard()
    return jsonify(stats)

@app.route("/api/producteurs")
def api_producteurs():
    engine = get_engine()
    if engine is None:
        return jsonify([])
    cfg = get_config()
    cal = cfg.get("calendrier", "NOUVEAU")
    engine.calendrier = cal
    periode, _, _ = engine.periode_traite(date.today())
    liste = []
    for bp in engine.base_parcelles:
        stk  = engine.stock_prod(bp, periode)
        est  = bp["estim"]
        liv  = bp["liv_gt"] + bp["liv_pt"]
        pct  = round(liv / est * 100, 1) if est > 0 else 0
        score = round(engine.calculer_score(bp, periode), 3)
        liste.append({
            "code" : bp["code"],
            "nom"  : bp["nom"],
            "sect" : bp["sect"],
            "estim": est,
            "stk"  : stk,
            "liv"  : liv,
            "pct"  : pct,
            "score": score
        })
    liste.sort(key=lambda x: x["score"], reverse=True)
    return jsonify(liste)

@app.route("/api/sections")
def api_sections():
    global _engine
    # Toujours recharger depuis Supabase pour avoir les données fraîches
    _engine = None
    engine = get_engine()
    if engine is None or not engine.base_parcelles:
        return jsonify([])
    cfg = get_config()
    cal = cfg.get("calendrier", "NOUVEAU")
    engine.calendrier = cal
    periode, _, _ = engine.periode_traite(date.today())
    sections = {}
    for bp in engine.base_parcelles:
        s = bp["sect"]
        stk = engine.stock_prod(bp, periode)
        if s not in sections:
            sections[s] = {"nom": s, "vol": 0, "nb": 0}
        sections[s]["vol"] += stk
        sections[s]["nb"]  += 1
    return jsonify(sorted(sections.values(), key=lambda x: x["vol"], reverse=True))

# ============================================================
#  API GENERATION FICHE
# ============================================================
@app.route("/api/fiche/generer", methods=["POST"])
def api_generer_fiche():
    engine = get_engine()
    if engine is None:
        return jsonify({"erreur": "Fichier non configuré."})
    data = request.json or {}
    try:
        poids_total = int(data.get("poids_total", 0))
        d_ref       = date.fromisoformat(data.get("d_ref", ""))
        d_debut     = date.fromisoformat(data.get("d_debut", ""))
        sections    = data.get("sections", [])
        prix_kg     = float(data.get("prix_kg", 0))
        calendrier  = data.get("calendrier", "NOUVEAU")
    except Exception as e:
        return jsonify({"erreur": f"Paramètres invalides : {e}"})

    if poids_total <= 0:
        return jsonify({"erreur": "Le poids demandé doit être supérieur à 0."})
    if not sections:
        return jsonify({"erreur": "Sélectionnez au moins une section."})
    if prix_kg <= 0:
        return jsonify({"erreur": "Le prix au kg doit être supérieur à 0."})
    if d_debut > d_ref:
        return jsonify({"erreur": "La date de début doit être antérieure ou égale à la date limite."})

    engine.calendrier = calendrier
    # Actualiser les stocks avant génération
    engine.actualiser_stocks()
    result = engine.generer_fiche(poids_total, d_ref, d_debut, sections, prix_kg)
    if "succes" in result and result["succes"]:
        # Sauvegarder la fiche dans Supabase
        sauvegarder_fiche(result)
        # Mettre à jour les stocks dans Supabase
        sauvegarder_producteurs(engine.base_parcelles)
    return jsonify(result)

@app.route("/api/fiche/continuer", methods=["POST"])
def api_continuer_fiche():
    """Reprend la génération après autorisation de dépassement de limite"""
    engine = get_engine()
    if engine is None:
        return jsonify({"erreur": "Fichier non configuré."})
    data = request.json or {}
    try:
        palier     = float(data.get("palier", 0))
        etat       = data.get("etat", {})
        periode    = data.get("periode", "")
        d_debut    = date.fromisoformat(data.get("d_debut", ""))
        d_ref      = date.fromisoformat(data.get("d_ref", ""))
        prix_kg    = float(data.get("prix_kg", 0))
        poids_total= int(data.get("poids_total", 0))
    except Exception as e:
        return jsonify({"erreur": f"Paramètres invalides : {e}"})
    result = engine.continuer_apres_autorisation(
        palier, etat, periode, d_debut, d_ref, prix_kg, poids_total
    )
    if "succes" in result and result["succes"]:
        engine.sauvegarder_fiche(result)
    return jsonify(result)

# ============================================================
#  API EXPORT PDF
# ============================================================
@app.route("/api/export/excel", methods=["POST"])
def api_export_excel():
    data = request.json or {}
    fiche_data = data.get("fiche", {})
    if not fiche_data:
        return jsonify({"erreur": "Aucune fiche à exporter."})
    try:
        excel_bytes = generer_export_excel(fiche_data)
        nom_fichier = generer_nom_fichier(fiche_data)
        return send_file(
            io.BytesIO(excel_bytes),
            as_attachment=True,
            download_name=nom_fichier,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        return jsonify({"erreur": f"Erreur export Excel : {e}"})

@app.route("/api/export/pdf", methods=["POST"])
def api_export_pdf():
    """Export PDF — à implémenter avec ReportLab"""
    data = request.json or {}
    fiche_data = data.get("fiche", {})
    if not fiche_data:
        return jsonify({"erreur": "Aucune fiche à exporter."})
    return jsonify({"erreur": "Export PDF en cours de développement. Utilisez l'export Excel."})

# ============================================================
#  API LOG EN TEMPS REEL
# ============================================================
@app.route("/api/fiches")
def api_fiches():
    """Historique des fiches générées depuis Supabase"""
    fiches = charger_fiches()
    return jsonify(fiches)

@app.route("/api/log")
def api_log():
    engine = get_engine()
    if engine is None:
        return jsonify([])
    logs = engine.log[-50:]  # 50 dernières entrées
    return jsonify(logs)

@app.route("/api/log/clear", methods=["POST"])
def api_log_clear():
    engine = get_engine()
    if engine:
        engine.log = []
    return jsonify({"succes": True})

# ============================================================
#  API DECONNEXION
# ============================================================
@app.route("/api/logout", methods=["POST"])
def api_logout():
    """Déconnecte l'utilisateur en effaçant la session Flask.
    Les données (fichier Excel, config, licence) sont conservées sur disque."""
    session.clear()
    return jsonify({"succes": True})

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ============================================================
#  LANCEMENT
# ============================================================
def open_browser():
    import time
    time.sleep(1.2)
    webbrowser.open("http://localhost:5000")

if __name__ == "__main__":
    # Ouvrir le navigateur automatiquement au démarrage
    t = threading.Thread(target=open_browser, daemon=True)
    t.start()
    print("=" * 50)
    print("  FichePro Manager — Démarrage...")
    print("  Interface : http://localhost:5000")
    print("  Appuyez sur Ctrl+C pour arrêter")
    print("=" * 50)
    app.run(host="127.0.0.1", port=5000, debug=False)
