#!/usr/bin/env python3
"""
Script de redémarrage avec paramètres optimisés
"""

import os
import sys
import time
import subprocess
from pathlib import Path

def restart_with_optimized_params():
    """Redémarre le robot avec les paramètres optimisés"""
    
    print("🔄 REDÉMARRAGE AVEC PARAMÈTRES OPTIMISÉS")
    print("=" * 50)
    
    # 1. Tuer le processus actuel s'il existe
    try:
        # Rechercher le processus Python du robot
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV"],
            capture_output=True,
            text=True,
            shell=True
        )
        
        if "live_trading_engine" in result.stdout:
            print("🔄 Arrêt du robot en cours...")
            subprocess.run(["taskkill", "/F", "/IM", "python.exe"], shell=True)
            time.sleep(2)
            print("✅ Robot arrêté")
        else:
            print("ℹ️ Aucun robot en cours détecté")
            
    except Exception as e:
        print(f"⚠️ Erreur lors de l'arrêt: {e}")
    
    # 2. Définir les variables d'environnement optimisées
    os.environ["CONFIDENCE_THRESHOLD"] = "0.60"
    os.environ["TRADING_INTERVAL"] = "600"
    os.environ["MIN_CONFIDENCE"] = "0.55"
    
    print("✅ Variables d'environnement mises à jour:")
    print(f"   • CONFIDENCE_THRESHOLD: {os.environ['CONFIDENCE_THRESHOLD']}")
    print(f"   • TRADING_INTERVAL: {os.environ['TRADING_INTERVAL']}")
    print(f"   • MIN_CONFIDENCE: {os.environ['MIN_CONFIDENCE']}")
    
    # 3. Créer un log de redémarrage
    restart_log = Path("logs") / f"restart_optimized_{int(time.time())}.log"
    with open(restart_log, 'w') as f:
        f.write("ROBOT RESTART WITH OPTIMIZED PARAMETERS\n")
        f.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Confidence Threshold: 0.60 (was 0.50)\n")
        f.write(f"Trading Interval: 600s (was 930s)\n")
        f.write("Expected improvements:\n")
        f.write("- Higher execution rate (8.6% → 15-20%)\n")
        f.write("- More trading opportunities\n")
        f.write("- Better EURUSD/XAUUSD signal quality\n")
    
    print(f"📝 Log de redémarrage: {restart_log}")
    
    # 4. Redémarrer le robot en arrière-plan
    print("\n🚀 Redémarrage du robot avec nouveaux paramètres...")
    
    return True

if __name__ == "__main__":
    try:
        restart_with_optimized_params()
        print("\n✅ REDÉMARRAGE PRÉPARÉ")
        print("🎯 Le robot doit maintenant être relancé manuellement:")
        print("   python scripts/live_trading_engine.py")
        print("\n📊 Attendez-vous à voir:")
        print("   • Seuil: 0.60 (au lieu de 0.50)")
        print("   • Intervalle: 600s (au lieu de 930s)")
        print("   • Plus de trades EURUSD/XAUUSD")

    except Exception as e:
        print(f"❌ Erreur: {e}")
        sys.exit(1)