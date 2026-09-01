"""
FichePro Manager - Gestionnaire de licences
Compatible 100% avec ThisWorkbook VBA (SEED_HASH=0)
"""
import json, platform, random, string, subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

LICENSE_FILE = Path("data/license.json")

# Codes admin illimités — pas d'expiration, pas de verrouillage machine
ADMIN_CODES = {
    "WIL03943487HAY",           # Code legacy Wilfried — ADMIN illimité
    "FICHEPRO-ADMIN-WILFRIED-2026"  # Code admin explicite
}

# ============================================================
# HASH — identique à HashLicence VBA (SEED_HASH=0)
# ============================================================
def hash_licence(txt: str) -> str:
    h = 0
    for c in txt:
        h = (h * 31 + ord(c)) % 100000
    return str(h).zfill(5)

# ============================================================
# SERIAL DISQUE — WMI priorité, FSO fallback
# ============================================================
def get_serial_wmi() -> str:
    try:
        r = subprocess.run(["wmic","diskdrive","get","SerialNumber"],
                           capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            l = line.strip()
            if l and l.lower() != "serialnumber":
                return l
    except: pass
    return ""

def get_serial_fso() -> str:
    try:
        r = subprocess.run(["cmd","/c","vol","C:"],
                           capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            if "serie" in line.lower() or "serial" in line.lower():
                parts = line.split()
                if parts: return parts[-1].replace("-","")
    except: pass
    return ""

def get_serial_disque() -> str:
    s = get_serial_wmi()
    if s and s != "UNKNOWN": return s
    s = get_serial_fso()
    return s if s else "UNKNOWN"

def get_machine_id() -> str:
    return hash_licence(f"{get_serial_disque()}_{platform.node().upper()}")

# ============================================================
# VALIDATION CODE STANDARD
# ============================================================
def valider_code(code: str, date_activation: date) -> tuple:
    if len(code) != 17: return False, 0
    if not code.startswith("WIL") or not code.endswith("HAY"): return False, 0
    hash_code = code[3:8]
    duree_str = code[8:11]
    if not duree_str.isdigit(): return False, 0
    jours = int(duree_str)
    if jours <= 0: return False, 0
    base = date_activation.strftime("%Y%m%d") + str(jours).zfill(3)
    return hash_code == hash_licence(base), jours

# ============================================================
# STOCKAGE LOCAL
# ============================================================
def get_local_license() -> dict:
    if not LICENSE_FILE.exists(): return {}
    try: return json.loads(LICENSE_FILE.read_text())
    except: return {}

def save_local_license(data: dict):
    LICENSE_FILE.parent.mkdir(exist_ok=True)
    LICENSE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))

# ============================================================
# VÉRIFICATION PRINCIPALE
# ============================================================
def verify_license(key: str) -> dict:
    key = key.strip().upper()

    # ── Licence ADMIN illimitée ──
    if key in ADMIN_CODES:
        save_local_license({
            "key"            : key,
            "expire"         : "2099-12-31",
            "machine_id"     : "ADMIN",
            "activation_date": date.today().strftime("%Y-%m-%d"),
            "activated_on"   : date.today().strftime("%Y-%m-%d"),
            "admin"          : True
        })
        return {
            "valid"  : True,
            "message": "Licence administrateur illimitée",
            "expire" : "31/12/2099",
            "jours"  : 99999,
            "admin"  : True
        }

    # ── Licence standard ──
    local = get_local_license()
    machine_id = get_machine_id()
    date_act_str = local.get("activation_date", "")
    try:
        date_activation = datetime.strptime(date_act_str, "%Y-%m-%d").date() if date_act_str else date.today()
    except:
        date_activation = date.today()

    ok, jours = valider_code(key, date_activation)
    if not ok:
        return {"valid": False, "message": "Code de licence invalide."}

    date_exp = date_activation + timedelta(days=jours)
    if date.today() > date_exp:
        return {"valid": False, "message": f"Licence expirée le {date_exp.strftime('%d/%m/%Y')}. Contactez Wilfried VABOU."}

    machine_enr = local.get("machine_id", "")
    if machine_enr and machine_enr != "ADMIN" and machine_enr != machine_id:
        return {"valid": False, "message": "Ce fichier est lié à un autre ordinateur. Contactez Wilfried VABOU."}

    jours_restants = (date_exp - date.today()).days
    msg = f"Licence valide jusqu'au {date_exp.strftime('%d/%m/%Y')}"
    if jours_restants <= 15:
        msg = f"ATTENTION : {jours_restants} jour(s) avant expiration !"

    save_local_license({
        "key"            : key,
        "expire"         : date_exp.strftime("%Y-%m-%d"),
        "machine_id"     : machine_id,
        "activation_date": date_activation.strftime("%Y-%m-%d"),
        "activated_on"   : date.today().strftime("%Y-%m-%d")
    })
    return {
        "valid"  : True,
        "message": msg,
        "expire" : date_exp.strftime("%d/%m/%Y"),
        "jours"  : jours_restants
    }

def is_licensed() -> bool:
    local = get_local_license()
    # Admin toujours valide
    if local.get("admin"): return True
    if local.get("machine_id") == "ADMIN": return True
    try:
        return date.today() <= datetime.strptime(local.get("expire",""), "%Y-%m-%d").date()
    except:
        return False

def get_license_info() -> dict:
    local = get_local_license()
    # Formater la date d'expiration pour l'affichage
    if local.get("expire"):
        try:
            exp = datetime.strptime(local["expire"], "%Y-%m-%d").date()
            local["expire"] = exp.strftime("%d/%m/%Y")
        except: pass
    return local

def generer_code_licence(date_activation: date, jours_validite: int) -> str:
    if not (1 <= jours_validite <= 999): raise ValueError("Durée invalide")
    base = date_activation.strftime("%Y%m%d") + str(jours_validite).zfill(3)
    sel3 = ''.join(random.choice(string.ascii_uppercase) for _ in range(3))
    return f"WIL{hash_licence(base)}{str(jours_validite).zfill(3)}{sel3}HAY"
