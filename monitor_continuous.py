#!/usr/bin/env python3
"""
BOUCLE DE MONITORING CONTINU - Rapports toutes les 15 minutes
"""
import subprocess
import time
from datetime import datetime


def run_monitoring():
    """Lancer le monitoring en boucle toutes les 15 minutes"""
    cycle = 1
    print("\n" + "="*120)
    print(f"🤖 DÉMARRAGE DU MONITORING CONTINU - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("Rapports toutes les 15 minutes")
    print("="*120)

    while True:
        try:
            # Afficher le rapport
            subprocess.run(["python", "report_continuous.py", str(cycle)])

            # Attendre 15 minutes (900 secondes)
            print(f"\n⏳ Prochaine mise à jour dans 15 minutes ({cycle+1}e rapport)...")
            print(f"   Heure: {datetime.now().strftime('%H:%M:%S')}")

            time.sleep(900)  # 15 minutes
            cycle += 1

        except KeyboardInterrupt:
            print("\n\n✋ Monitoring arrêté par l'utilisateur")
            break
        except Exception as e:
            print(f"\n⚠️  Erreur: {e}")
            time.sleep(60)  # Attendre 1 minute avant de réessayer

if __name__ == "__main__":
    run_monitoring()
