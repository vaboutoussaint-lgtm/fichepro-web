"""
FichePro Manager - Connexion Supabase
Stockage permanent des producteurs, fiches et licences
"""
import json
import requests
from datetime import date, datetime
from typing import Optional

import os
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

HEADERS = {
    "apikey"       : SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type" : "application/json",
    "Prefer"       : "return=representation"
}

# ============================================================
# UTILITAIRES
# ============================================================
def _get(table: str, params: dict = None) -> list:
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    r = requests.get(url, headers=HEADERS, params=params, timeout=10)
    if r.status_code == 200:
        return r.json()
    return []

def _post(table: str, data) -> dict:
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    r = requests.post(url, headers=HEADERS, json=data, timeout=10)
    if r.status_code in (200, 201):
        result = r.json()
        return result[0] if isinstance(result, list) else result
    return {"erreur": r.text}

def _patch(table: str, filters: dict, data: dict) -> bool:
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    params = {k: f"eq.{v}" for k, v in filters.items()}
    r = requests.patch(url, headers=HEADERS, params=params, json=data, timeout=10)
    return r.status_code in (200, 204)

def _delete(table: str, filters: dict) -> bool:
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    params = {k: f"eq.{v}" for k, v in filters.items()}
    r = requests.delete(url, headers=HEADERS, params=params, timeout=10)
    return r.status_code in (200, 204)

def _upsert(table: str, data, on_conflict: str = "") -> bool:
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {**HEADERS, "Prefer": f"resolution=merge-duplicates,return=minimal"}
    if on_conflict:
        url += f"?on_conflict={on_conflict}"
    if isinstance(data, list):
        r = requests.post(url, headers=headers, json=data, timeout=30)
    else:
        r = requests.post(url, headers=headers, json=data, timeout=10)
    return r.status_code in (200, 201, 204)

# ============================================================
# PRODUCTEURS — table "producers"
# Colonnes : code, parcelle, nom, section, estim, stk_gt, stk_pt,
#            liv_gt, liv_pt, lon, lat
# ============================================================
def sauvegarder_producteurs(producteurs: list) -> dict:
    """
    Sauvegarde tous les producteurs dans Supabase.
    Upsert sur la colonne 'parcelle' (unique par parcelle).
    """
    if not producteurs:
        return {"erreur": "Liste vide"}
    try:
        # Préparer les données pour Supabase
        rows = []
        for p in producteurs:
            rows.append({
                "code"   : p.get("code", ""),
                "parcelle": p.get("parc", p.get("parcelle", "")),
                "nom"    : p.get("nom", ""),
                "section": p.get("sect", p.get("section", "")),
                "estim"  : int(p.get("estim", 0)),
                "stk_gt" : int(p.get("stk_gt", p.get("stk_gt", 0))),
                "stk_pt" : int(p.get("stk_pt", p.get("stk_pt", 0))),
                "liv_gt" : int(p.get("liv_gt", 0)),
                "liv_pt" : int(p.get("liv_pt", 0)),
                "lon"    : float(p["lon"]) if p.get("lon") else None,
                "lat"    : float(p["lat"]) if p.get("lat") else None
            })
        # Upsert par batch de 500
        total = 0
        for i in range(0, len(rows), 500):
            batch = rows[i:i+500]
            ok = _upsert("producers", batch, on_conflict="parcelle")
            if ok:
                total += len(batch)
        return {"succes": True, "total": total}
    except Exception as e:
        return {"erreur": str(e)}

def charger_producteurs() -> list:
    """Charge tous les producteurs depuis Supabase"""
    try:
        rows = _get("producers", {"select": "*", "limit": "10000"})
        return rows
    except:
        return []

def maj_stock_producteur(parcelle: str, stk_gt: int, stk_pt: int,
                          liv_gt: int, liv_pt: int) -> bool:
    """Met à jour les stocks d'une parcelle après génération de fiche"""
    return _patch("producers", {"parcelle": parcelle}, {
        "stk_gt": stk_gt,
        "stk_pt": stk_pt,
        "liv_gt": liv_gt,
        "liv_pt": liv_pt
    })

def nb_producteurs() -> int:
    """Nombre de producteurs en base"""
    try:
        rows = _get("producers", {"select": "code", "limit": "1"})
        # Supabase count
        url = f"{SUPABASE_URL}/rest/v1/producers"
        headers = {**HEADERS, "Prefer": "count=exact"}
        r = requests.get(url, headers=headers, params={"select": "code"}, timeout=5)
        count_header = r.headers.get("Content-Range", "")
        if "/" in count_header:
            return int(count_header.split("/")[1])
        return len(charger_producteurs())
    except:
        return 0

# ============================================================
# FICHES — table "fiches"
# ============================================================
def sauvegarder_fiche(fiche_data: dict) -> dict:
    """Sauvegarde une fiche générée dans Supabase"""
    try:
        row = {
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
    except Exception as e:
        return {"erreur": str(e)}

def charger_fiches() -> list:
    """Charge l'historique des fiches"""
    try:
        return _get("fiches", {
            "select": "*",
            "order" : "date_generation.desc",
            "limit" : "100"
        })
    except:
        return []

# ============================================================
# LICENCES — table "licenses"
# ============================================================
def verifier_licence_supabase(key: str) -> Optional[dict]:
    """Vérifie si une licence existe dans Supabase"""
    try:
        rows = _get("licenses", {
            "select"     : "*",
            "license_key": f"eq.{key}"
        })
        return rows[0] if rows else None
    except:
        return None

def enregistrer_licence(key: str, client: str, expiry: str) -> dict:
    """Enregistre une licence dans Supabase"""
    return _post("licenses", {
        "license_key" : key,
        "client_name" : client,
        "expiry_date" : expiry
    })

# ============================================================
# TEST DE CONNEXION
# ============================================================
def tester_connexion() -> bool:
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/producers",
            headers=HEADERS,
            params={"select": "code", "limit": "1"},
            timeout=5
        )
        return r.status_code == 200
    except:
        return False

if __name__ == "__main__":
    print("Test connexion Supabase:", tester_connexion())
    print("Nb producteurs:", nb_producteurs())
