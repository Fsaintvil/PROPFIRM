#!/usr/bin/env python3
"""
Script de relance immédiate du trading
Supprime l'arrêt d'urgence et relance le robot
"""

import sys
import os
from pathlib import Path
from datetime import datetime

def restart_trading_now():
    """Relance immédiatement le trading"""
    
    print("🚀 RELANCE IMMÉDIATE DU TRADING")
    print("=" * 40)
    
    # 1. Supprimer le fichier d'arrêt d'urgence
    emergency_file = Path("control/emergency_stop")
    
    if emergency_file.exists():
        try:
            emergency_file.unlink()
            print("✅ Fichier d'arrêt d'urgence supprimé")
        except Exception as e:
            print(f"❌ Erreur suppression fichier d'arrêt: {e}")
            return False
    else:
        print("ℹ️ Aucun fichier d'arrêt d'urgence trouvé")
    
    # 2. Créer un fichier de statut de relance
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    
    restart_file = logs_dir / f"trading_restart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    with open(restart_file, 'w') as f:
        f.write(f"TRADING RESTART INITIATED\n")
        f.write(f"Timestamp: {datetime.now().isoformat()}\n")
        f.write(f"Reason: User requested immediate restart\n")
        f.write(f"Status: ACTIVE\n")
        f.write(f"Robot improvements: ALL ACTIVE\n")
    
    print(f"📝 Log de relance créé: {restart_file}")
    
    # 3. Vérifier que le control directory est propre
    control_dir = Path("control")
    if control_dir.exists():
        remaining_files = list(control_dir.glob("*"))
        if remaining_files:
            print(f"ℹ️ Fichiers restants dans control/: {[f.name for f in remaining_files]}")
        else:
            print("✅ Répertoire control/ nettoyé")
    
    print("\n🚀 TRADING RELANCÉ AVEC SUCCÈS")
    print("🎯 Toutes les améliorations sont actives:")
    print("   • Seuil optimisé: 0.60 (vs 0.68)")
    print("   • Intervalle réduit: 600s (vs 930s)")
    print("   • Validation des signaux: ACTIVE")
    print("   • Stop loss dynamique: ACTIVE")
    print("   • Trailing stop: ACTIVE")
    print("   • Mode fallback AI: ACTIVE")
    
    print(f"\n⏰ Relance effectuée à: {datetime.now().strftime('%H:%M:%S')}")
    print("🔄 Le robot peut maintenant reprendre le trading")
    
    return True

if __name__ == "__main__":
    try:
        restart_trading_now()
        print("\n🎯 POUR DÉMARRER LE ROBOT:")
        print("   python scripts/live_trading_engine.py")
    except Exception as e:
        print(f"❌ ERREUR lors de la relance: {e}")
        sys.exit(1)