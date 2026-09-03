"""
FichePro Manager - Gestionnaire de licences
3 types de licences :
- ADMIN    : illimitée, pas de verrouillage machine (Wilfried)
- DEMO     : 1 seule fiche, libre sur tous les PC
- STANDARD : durée fixe, verrouillée sur 1 PC
"""
import json, platform, subprocess, random, string
from datetime import date, datetime, timedelta
from pathlib import Path

LICENSE_FILE = Path("data/license.json")

# ============================================================
# CODES ADMIN ILLIMITÉS
# ============================================================
ADMIN_CODES = {
    "WIL03943487HAY",
    "FICHEPRO-ADMIN-WILFRIED-2026"
}

# ============================================================
# PRÉFIXES DEMO
# Format : DEMO + 8 caractères aléatoires
# ============================================================
DEMO_PREFIX = "DEMO"

# Code démo unique universel
DEMO_UNIQUE = "DEMO2026CAPR"

# ============================================================
# HASH — identique VBA (SEED=0)
# ============================================================
def hash_licence(txt: str) -> str:
    h = 0
    for c in txt:
        h = (h * 31 + ord(c)) % 100000
    return str(h).zfill(5)

# ============================================================
# MACHINE ID
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

def get_machine_id() -> str:
    serial = get_serial_wmi() or platform.node().upper()
    return hash_licence(f"{serial}_{platform.node().upper()}")

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
# VALIDATION CODE STANDARD (format WIL...HAY)
# ============================================================
def valider_code_standard(code: str, date_activation: date) -> tuple:
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
# VÉRIFICATION PRINCIPALE
# ============================================================
def verify_license(key: str) -> dict:
    key = key.strip().upper()

    # ── ADMIN illimité ──
    if key in ADMIN_CODES:
        save_local_license({
            "key"    : key,
            "expire" : "2099-12-31",
            "type"   : "ADMIN",
            "admin"  : True
        })
        return {
            "valid"  : True,
            "type"   : "ADMIN",
            "message": "Licence administrateur illimitee",
            "expire" : "31/12/2099",
            "jours"  : 99999,
            "admin"  : True
        }

    # ── DEMO — 1 fiche maximum, libre sur tous les PC ──
    if key == DEMO_UNIQUE or (key.startswith(DEMO_PREFIX) and len(key) == 12):
        local = get_local_license()
        # Vérifier si cette démo a déjà généré une fiche
        fiches_generees = local.get("demo_fiches", 0)
        if local.get("key") == key and fiches_generees >= 1:
            return {
                "valid"  : False,
                "type"   : "DEMO",
                "message": "Licence de demonstration expiree apres 1 fiche. Contactez Wilfried VABOU au +225 01 02 02 50 25 pour activer une licence complete.",
                "expire" : ""
            }
        save_local_license({
            "key"         : key,
            "type"        : "DEMO",
            "demo_fiches" : fiches_generees,
            "machine_id"  : "DEMO_LIBRE"
        })
        return {
            "valid"      : True,
            "type"       : "DEMO",
            "message"    : "Licence demonstration - 1 fiche autorisee",
            "expire"     : "Demonstration",
            "jours"      : 1,
            "demo"       : True,
            "fiches_restantes": 1 - fiches_generees
        }

    # ── STANDARD — durée fixe, verrouillée sur 1 PC ──
    local = get_local_license()
    machine_id = get_machine_id()
    date_act_str = local.get("activation_date", "")
    try:
        date_activation = datetime.strptime(date_act_str, "%Y-%m-%d").date() if date_act_str else date.today()
    except:
        date_activation = date.today()

    ok, jours = valider_code_standard(key, date_activation)
    if not ok:
        return {"valid": False, "message": "Code de licence invalide. Verifiez le code ou contactez Wilfried VABOU."}

    date_exp = date_activation + timedelta(days=jours)
    if date.today() > date_exp:
        return {
            "valid"  : False,
            "type"   : "EXPIRE",
            "message": f"Licence expiree le {date_exp.strftime('%d/%m/%Y')}. Contactez Wilfried VABOU pour renouveler.",
            "expire" : date_exp.strftime("%d/%m/%Y"),
            "renouvellement_requis": True
        }

    machine_enr = local.get("machine_id", "")
    if machine_enr and machine_enr not in ("DEMO_LIBRE", "ADMIN") and machine_enr != machine_id:
        return {
            "valid"  : False,
            "message": "Ce code est lie a un autre ordinateur. Contactez Wilfried VABOU au +225 01 02 02 50 25."
        }

    jours_restants = (date_exp - date.today()).days
    msg = f"Licence valide jusqu'au {date_exp.strftime('%d/%m/%Y')}"
    if jours_restants <= 15:
        msg = f"ATTENTION : {jours_restants} jour(s) avant expiration !"

    save_local_license({
        "key"            : key,
        "type"           : "STANDARD",
        "expire"         : date_exp.strftime("%Y-%m-%d"),
        "machine_id"     : machine_id,
        "activation_date": date_activation.strftime("%Y-%m-%d")
    })
    return {
        "valid"  : True,
        "type"   : "STANDARD",
        "message": msg,
        "expire" : date_exp.strftime("%d/%m/%Y"),
        "jours"  : jours_restants
    }

# ============================================================
# VÉRIFICATION ACCÈS
# ============================================================
def is_licensed() -> bool:
    local = get_local_license()
    if local.get("admin"): return True
    if local.get("type") == "DEMO": return True
    try:
        return date.today() <= datetime.strptime(local.get("expire",""), "%Y-%m-%d").date()
    except:
        return False

def is_demo() -> bool:
    return get_local_license().get("type") == "DEMO"

def incrementer_fiches_demo():
    """Appelé après chaque génération de fiche en mode demo"""
    local = get_local_license()
    if local.get("type") == "DEMO":
        local["demo_fiches"] = local.get("demo_fiches", 0) + 1
        save_local_license(local)
        return local["demo_fiches"]
    return 0

def peut_generer_fiche() -> tuple:
    """Retourne (peut_generer, message_erreur)"""
    local = get_local_license()
    if local.get("admin"): return True, ""
    if local.get("type") == "DEMO":
        fiches = local.get("demo_fiches", 0)
        if fiches >= 1:
            return False, "Licence demonstration limitee a 1 fiche. Contactez Wilfried VABOU pour activer une licence complete."
        return True, ""
    return True, ""

def get_license_info() -> dict:
    local = get_local_license()
    if local.get("expire") and "-" in str(local.get("expire","")):
        try:
            exp = datetime.strptime(local["expire"], "%Y-%m-%d").date()
            local["expire"] = exp.strftime("%d/%m/%Y")
        except: pass
    return local

# ============================================================
# GÉNÉRATION DE CODES
# ============================================================
def generer_code_demo() -> str:
    """Génère un code demo : DEMO + 8 caractères alphanumériques"""
    chars = string.ascii_uppercase + string.digits
    suffix = ''.join(random.choice(chars) for _ in range(8))
    return f"DEMO{suffix}"

def generer_code_standard(date_activation: date, jours_validite: int) -> str:
    """Génère un code standard WIL...HAY"""
    if not (1 <= jours_validite <= 999):
        raise ValueError("Duree invalide (1-999 jours)")
    base  = date_activation.strftime("%Y%m%d") + str(jours_validite).zfill(3)
    sel3  = ''.join(random.choice(string.ascii_uppercase) for _ in range(3))
    return f"WIL{hash_licence(base)}{str(jours_validite).zfill(3)}{sel3}HAY"
