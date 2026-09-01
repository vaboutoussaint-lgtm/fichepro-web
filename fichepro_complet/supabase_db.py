"""
FichePro Manager - Connexion Supabase
Isolation complète par licence — chaque client voit uniquement ses données
"""
import os, json, requests
from datetime import date

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

def _headers():
    return {
        "apikey"       : SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type" : "application/json",
        "Prefer"       : "return=representation"
    }

def _get(table, params=None):
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}",
                         headers=_headers(), params=params, timeout=15)
        return r.json() if r.status_code == 200 else []
    except:
        return []

def _upsert(table, data, on_conflict):
    try:
        headers = {**_headers(), "Prefer": f"resolution=merge-duplicates,return=minimal"}
        url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
        if isinstance(data, list):
            # Batch de 500
            for i in range(0, len(data), 500):
                r = requests.post(url, headers=headers, json=data[i:i+500], timeout=30)
                if r.status_code not in (200, 201, 204):
                    return False
            return True
        else:
            r = requests.post(url, headers=headers, json=data, timeout=10)
            return r.status_code in (200, 201, 204)
    except:
        return False

def _post(table, data):
    try:
        r = requests.post(f"{SUPABASE_URL}/rest/v1/{table}",
                          headers=_headers(), json=data, timeout=10)
        if r.status_code in (200, 201):
            res = r.json()
            return res[0] if isinstance(res, list) else res
        return {}
    except:
        return {}

# ============================================================
# PRODUCTEURS — isolés par licence
# ============================================================
def charger_producteurs(license_key: str) -> list:
    """Charge uniquement les producteurs de cette licence"""
    tous = []
    offset = 0
    while True:
        rows = _get("producers", {
            "select"     : "*",
            "license_key": f"eq.{license_key}",
            "limit"      : "1000",
            "offset"     : str(offset),
            "order"      : "id.asc"
        })
        if not rows:
            break
        tous.extend(rows)
        if len(rows) < 1000:
            break
        offset += 1000
    return tous

def sauvegarder_producteurs(producteurs: list, license_key: str) -> dict:
    """Sauvegarde les producteurs liés à cette licence"""
    if not producteurs:
        return {"erreur": "Liste vide"}
    rows = []
    for p in producteurs:
        rows.append({
            "license_key": license_key,
            "code"       : p.get("code", ""),
            "parcelle"   : p.get("parc", p.get("parcelle", "")),
            "nom"        : p.get("nom", ""),
            "section"    : p.get("sect", p.get("section", "")),
            "estim"      : int(p.get("estim", 0)),
            "stk_gt"     : int(p.get("stk_gt", 0)),
            "stk_pt"     : int(p.get("stk_pt", 0)),
            "liv_gt"     : int(p.get("liv_gt", 0)),
            "liv_pt"     : int(p.get("liv_pt", 0)),
            "lon"        : float(p["lon"]) if p.get("lon") else None,
            "lat"        : float(p["lat"]) if p.get("lat") else None
        })
    ok = _upsert("producers", rows, "license_key,parcelle")
    return {"succes": ok, "total": len(rows)}

# ============================================================
# FICHES — isolées par licence
# ============================================================
def sauvegarder_fiche(fiche_data: dict, license_key: str) -> dict:
    row = {
        "license_key"   : license_key,
        "fiche_id"      : fiche_data.get("fiche_id", ""),
        "periode"       : fiche_data.get("periode", ""),
        "poids_total"   : int(fiche_data.get("poids_total", 0)),
        "nb_producteurs": int(fiche_data.get("nb_producteurs", 0)),
        "nb_sacs"       : int(fiche_data.get("nb_sacs", 0)),
        "montant"       : int(fiche_data.get("montant", 0)),
        "prix_kg"       : float(fiche_data.get("prix_kg", 0)),
        "lignes"        : json.dumps(fiche_data.get("lignes_fiche", [])),
        "date_generation": date.today().isoformat()
    }
    return _post("fiches", row)

def charger_fiches(license_key: str) -> list:
    return _get("fiches", {
        "select"     : "*",
        "license_key": f"eq.{license_key}",
        "order"      : "date_generation.desc",
        "limit"      : "100"
    })

def tester_connexion() -> bool:
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/producers",
                         headers=_headers(), params={"select":"code","limit":"1"}, timeout=5)
        return r.status_code == 200
    except:
        return False
