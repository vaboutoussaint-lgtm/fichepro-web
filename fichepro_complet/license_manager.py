"""
FichePro Manager - Gestionnaire de licences
Compatible 100% avec ThisWorkbook VBA
Hash : seed=0 (SEED_HASH = 0)
Serial : WMI priorité (Win32_PhysicalMedia), FSO fallback (vol C:)
Format : WIL + 5hash + 3duree + 3lettres_AZ + HAY = 17 chars
"""
import json, platform, random, string, subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

LICENSE_FILE = Path("data/license.json")

def hash_licence(txt: str) -> str:
    """Identique à HashLicence VBA (SEED_HASH=0)"""
    h = 0
    for c in txt:
        h = (h * 31 + ord(c)) % 100000
    return str(h).zfill(5)

def get_serial_wmi() -> str:
    """Identique à GetSerialWMI VBA — Win32_PhysicalMedia"""
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
    """Identique à GetSerialFSO VBA — numéro de volume C:"""
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
    """Identique à GetSerialDisque VBA : WMI en priorité, FSO fallback"""
    s = get_serial_wmi()
    if s and s != "UNKNOWN": return s
    s = get_serial_fso()
    return s if s else "UNKNOWN"

def get_machine_id() -> str:
    """Identique à GetMachineID VBA : HashLicence(serial & '_' & UCase(COMPUTERNAME))"""
    return hash_licence(f"{get_serial_disque()}_{platform.node().upper()}")

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

def verifier_licence(code: str, date_activation: date) -> tuple:
    code = code.strip().upper()
    if code == "WIL03943487HAY":
        exp = date_activation + timedelta(days=365)
        return True, exp, f"Licence valide jusqu'au {exp.strftime('%d/%m/%Y')}"
    if len(code) == 17:
        ok, jours = valider_code(code, date_activation)
        if ok:
            exp = date_activation + timedelta(days=jours)
            return True, exp, f"Licence valide jusqu'au {exp.strftime('%d/%m/%Y')}"
    return False, None, "Code de licence invalide."

def get_local_license() -> dict:
    if not LICENSE_FILE.exists(): return {}
    try: return json.loads(LICENSE_FILE.read_text())
    except: return {}

def save_local_license(data: dict):
    LICENSE_FILE.parent.mkdir(exist_ok=True)
    LICENSE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))

def verify_license(key: str) -> dict:
    key = key.strip().upper()
    local = get_local_license()
    machine_id = get_machine_id()
    date_act_str = local.get("activation_date","")
    try: date_activation = datetime.strptime(date_act_str,"%Y-%m-%d").date() if date_act_str else date.today()
    except: date_activation = date.today()
    ok, date_exp, msg = verifier_licence(key, date_activation)
    if not ok: return {"valid":False,"message":msg}
    if date.today() > date_exp:
        return {"valid":False,"message":f"Licence expirée le {date_exp.strftime('%d/%m/%Y')}. Contactez Wilfried VABOU."}
    machine_enr = local.get("machine_id","")
    if machine_enr and machine_enr != machine_id:
        return {"valid":False,"message":"Ce fichier est lié à un autre ordinateur. Contactez Wilfried VABOU au +225 01 02 02 50 25."}
    jours_restants = (date_exp - date.today()).days
    if jours_restants <= 15:
        msg = f"ATTENTION : {jours_restants} jour(s) avant expiration de la licence !"
    save_local_license({"key":key,"expire":date_exp.strftime("%Y-%m-%d"),
                        "machine_id":machine_id,"activation_date":date_activation.strftime("%Y-%m-%d"),
                        "activated_on":date.today().strftime("%Y-%m-%d")})
    return {"valid":True,"message":msg,"expire":date_exp.strftime("%d/%m/%Y"),"jours":jours_restants}

def is_licensed() -> bool:
    local = get_local_license()
    try: return date.today() <= datetime.strptime(local.get("expire",""),"%Y-%m-%d").date()
    except: return False

def get_license_info() -> dict:
    return get_local_license()

def generer_code_licence(date_activation: date, jours_validite: int) -> str:
    """Identique à GenererCodeLicence VBA"""
    if not (1 <= jours_validite <= 999): raise ValueError("Durée invalide")
    base  = date_activation.strftime("%Y%m%d") + str(jours_validite).zfill(3)
    sel3  = ''.join(random.choice(string.ascii_uppercase) for _ in range(3))
    return f"WIL{hash_licence(base)}{str(jours_validite).zfill(3)}{sel3}HAY"

# ============================================================
# LICENCE ADMIN — sans expiration (pour Wilfried uniquement)
# ============================================================
ADMIN_CODES = {
    "FICHEPRO-ADMIN-WILFRIED-2026": "Wilfried VABOU — Admin permanent"
}

def is_admin_code(key: str) -> bool:
    return key.strip().upper() in {k.upper() for k in ADMIN_CODES}

def verify_license(key: str) -> dict:
    key_clean = key.strip().upper()

    # Licence admin permanente
    if is_admin_code(key_clean):
        save_local_license({
            "key"            : key_clean,
            "expire"         : "2099-12-31",
            "machine_id"     : "ADMIN",
            "activation_date": date.today().strftime("%Y-%m-%d"),
            "activated_on"   : date.today().strftime("%Y-%m-%d"),
            "admin"          : True
        })
        return {
            "valid"  : True,
            "message": "Licence administrateur — accès permanent",
            "expire" : "31/12/2099",
            "jours"  : 99999,
            "admin"  : True
        }

    # Licence standard
    local = get_local_license()
    machine_id = get_machine_id()
    date_act_str = local.get("activation_date","")
    try:
        date_activation = datetime.strptime(date_act_str,"%Y-%m-%d").date() if date_act_str else date.today()
    except:
        date_activation = date.today()

    ok, date_exp, msg = verifier_licence(key_clean, date_activation)
    if not ok:
        return {"valid":False,"message":msg}
    if date.today() > date_exp:
        return {"valid":False,"message":f"Licence expirée le {date_exp.strftime('%d/%m/%Y')}. Contactez Wilfried VABOU."}
    machine_enr = local.get("machine_id","")
    if machine_enr and machine_enr != "ADMIN" and machine_enr != machine_id:
        return {"valid":False,"message":"Ce fichier est lié à un autre ordinateur. Contactez Wilfried VABOU au +225 01 02 02 50 25."}
    jours_restants = (date_exp - date.today()).days
    if jours_restants <= 15:
        msg = f"ATTENTION : {jours_restants} jour(s) avant expiration de la licence !"
    save_local_license({"key":key_clean,"expire":date_exp.strftime("%Y-%m-%d"),
                        "machine_id":machine_id,"activation_date":date_activation.strftime("%Y-%m-%d"),
                        "activated_on":date.today().strftime("%Y-%m-%d")})
    return {"valid":True,"message":msg,"expire":date_exp.strftime("%d/%m/%Y"),"jours":jours_restants}
