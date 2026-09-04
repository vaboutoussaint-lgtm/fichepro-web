"""
FichePro Manager - Generateur de licences (usage admin uniquement)
Identique au generateur VBA GenererLicence avec suivi dans feuille Excel.
Lancez : python admin_licences.py
"""
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

# Ajouter le dossier parent au path
import sys
sys.path.insert(0, os.path.dirname(__file__))

from license_manager import generer_code_licence, verifier_licence

SUIVI_FILE = Path("data/suivi_licences.json")

def charger_suivi() -> list:
    if not SUIVI_FILE.exists():
        return []
    try:
        return json.loads(SUIVI_FILE.read_text())
    except:
        return []

def sauvegarder_suivi(suivi: list):
    SUIVI_FILE.parent.mkdir(exist_ok=True)
    SUIVI_FILE.write_text(json.dumps(suivi, ensure_ascii=False, indent=2))

def enregistrer_licence(code: str, date_act: date, jours: int,
                         client: str, email: str, tel: str, commentaires: str):
    suivi = charger_suivi()
    date_exp = date_act + timedelta(days=jours)
    suivi.append({
        "date_generation": date.today().strftime("%d/%m/%Y"),
        "date_activation": date_act.strftime("%d/%m/%Y"),
        "duree"          : jours,
        "date_expiration": date_exp.strftime("%d/%m/%Y"),
        "code"           : code,
        "client"         : client,
        "email"          : email,
        "tel"            : tel,
        "statut"         : "Genere",
        "commentaires"   : commentaires
    })
    sauvegarder_suivi(suivi)

def afficher_suivi():
    suivi = charger_suivi()
    if not suivi:
        print("\n  Aucune licence generee pour l'instant.")
        return
    print(f"\n{'='*80}")
    print(f"  SUIVI DES LICENCES ({len(suivi)} total)")
    print(f"{'='*80}")
    print(f"{'Client':<20} {'Code':<20} {'Activation':<12} {'Expiration':<12} {'Statut'}")
    print(f"{'-'*80}")
    today = date.today()
    for lic in suivi:
        try:
            exp = datetime.strptime(lic['date_expiration'], "%d/%m/%Y").date()
            if today > exp:
                statut = "EXPIRE"
            elif (exp - today).days <= 15:
                statut = "BIENTOT"
            else:
                statut = "Valide"
        except:
            statut = lic.get('statut', '?')
        print(f"{lic.get('client',''):<20} {lic.get('code',''):<20} "
              f"{lic.get('date_activation',''):<12} {lic.get('date_expiration',''):<12} {statut}")

def generer_nouvelle():
    print("\n" + "="*50)
    client = input("  Nom du client : ").strip()
    if not client:
        print("  Annule.")
        return
    email  = input("  Email         : ").strip()
    tel    = input("  Telephone     : ").strip()
    date_str = input(f"  Date activation (JJ/MM/AAAA) [Entree = aujourd'hui] : ").strip()
    if not date_str:
        date_act = date.today()
    else:
        try:
            date_act = datetime.strptime(date_str, "%d/%m/%Y").date()
        except:
            print("  Date invalide.")
            return

    jours_str = input("  Duree (jours) [Entree = 365] : ").strip()
    jours = 365 if not jours_str else int(jours_str) if jours_str.isdigit() else 0
    if not (1 <= jours <= 999):
        print("  Duree invalide.")
        return

    commentaires = input("  Commentaires  : ").strip()
    code = generer_code_licence(date_act, jours)
    date_exp = date_act + timedelta(days=jours)

    enregistrer_licence(code, date_act, jours, client, email, tel, commentaires)

    print(f"\n{'='*50}")
    print(f"  LICENCE GENEREE AVEC SUCCES")
    print(f"{'='*50}")
    print(f"  Client     : {client}")
    print(f"  Code       : {code}")
    print(f"  Activation : {date_act.strftime('%d/%m/%Y')}")
    print(f"  Expiration : {date_exp.strftime('%d/%m/%Y')} ({jours} jours)")
    print(f"{'='*50}")

def verifier_cle():
    code = input("\n  Cle a verifier : ").strip().upper()
    date_str = input("  Date activation du client (JJ/MM/AAAA) : ").strip()
    try:
        date_act = datetime.strptime(date_str, "%d/%m/%Y").date()
    except:
        print("  Date invalide.")
        return
    valide, date_exp, msg = verifier_licence(code, date_act)
    print(f"\n  Resultat : {'VALIDE' if valide else 'INVALIDE'}")
    print(f"  Message  : {msg}")

def menu():
    os.system('cls' if os.name == 'nt' else 'clear')
    print("="*50)
    print("  FichePro Manager — Generateur de licences")
    print("  Wilfried VABOU · +225 01 02 02 50 25")
    print("="*50)
    suivi = charger_suivi()
    print(f"  Licences generees : {len(suivi)}")
    print()
    print("  1. Generer une licence 365 jours (1 an)")
    print("  2. Generer une licence 30 jours  (essai)")
    print("  3. Generer une licence personnalisee")
    print("  4. Voir le suivi complet")
    print("  5. Verifier une cle existante")
    print("  6. Quitter")
    print()
    return input("  Votre choix : ").strip()

if __name__ == "__main__":
    import random
    while True:
        choix = menu()
        if choix == "1":
            # Raccourci 365 jours
            client = input("\n  Nom du client : ").strip()
            if client:
                email = input("  Email : ").strip()
                tel   = input("  Tel   : ").strip()
                code  = generer_code_licence(date.today(), 365)
                date_exp = date.today() + timedelta(days=365)
                enregistrer_licence(code, date.today(), 365, client, email, tel, "")
                print(f"\n  Code : {code}")
                print(f"  Expire : {date_exp.strftime('%d/%m/%Y')}")
            input("\n  Entree pour continuer...")
        elif choix == "2":
            client = input("\n  Nom du client : ").strip()
            if client:
                email = input("  Email : ").strip()
                tel   = input("  Tel   : ").strip()
                code  = generer_code_licence(date.today(), 30)
                date_exp = date.today() + timedelta(days=30)
                enregistrer_licence(code, date.today(), 30, client, email, tel, "Essai")
                print(f"\n  Code : {code}")
                print(f"  Expire : {date_exp.strftime('%d/%m/%Y')}")
            input("\n  Entree pour continuer...")
        elif choix == "3":
            generer_nouvelle()
            input("\n  Entree pour continuer...")
        elif choix == "4":
            afficher_suivi()
            input("\n  Entree pour continuer...")
        elif choix == "5":
            verifier_cle()
            input("\n  Entree pour continuer...")
        elif choix == "6":
            print("\n  Au revoir.")
            break
