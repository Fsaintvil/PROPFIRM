#!/usr/bin/env python3
"""
QUICK START GUIDE - ENHANCED ULTIMATE TRADING ROBOT
Guide de démarrage rapide pour le robot de trading optimisé

UTILISATION:
1. python scripts/quick_start.py
2. Le système configure et démarre automatiquement
3. Trading selon règles FTMO (lundi 00:05, ordres/930s,
   fermeture vendredi 22:00)
"""

import sys
import subprocess
from pathlib import Path
import json
from datetime import datetime


def main():
    """Guide de démarrage rapide"""
    print("🚀 QUICK START - ENHANCED ULTIMATE TRADING ROBOT")
    print("=" * 55)
    print("Guide de démarrage automatique")

    # Vérifier structure
    scripts_dir = Path("scripts")
    if not scripts_dir.exists():
        print("❌ Répertoire scripts/ manquant")
        return

    robot_script = scripts_dir / "enhanced_ultimate_trading_robot.py"
    deployment_script = scripts_dir / "auto_deployment_system.py"

    if not robot_script.exists():
        print(f"❌ {robot_script} manquant")
        return

    if not deployment_script.exists():
        print(f"❌ {deployment_script} manquant")
        return

    print("✅ Scripts trouvés")

    # Créer configuration rapide
    quick_config = {
        "version": "2.0",
            "created": datetime.now().isoformat(),
                "mode": "auto_trading",
                "deployment": "automated",
                "schedule": {
            "monday_start": "00:05 Europe/Prague",
                "order_interval": "930 seconds",
                    "friday_close": "22:00 Europe/Prague",
                    },
                    "systems": {
            "portfolio_optimizer": True,
                "regime_detection": True,
                    "risk_management": True,
                    "automated_deployment": True,
                    },
                    "performance_target": {
            "sharpe_ratio": 1.651,
                "win_rate": "55%+",
                    "max_drawdown": "12%",
                    },
                    }

    # Sauvegarder config
    Path("control").mkdir(exist_ok=True)
    with open("control/quick_start_config.json", "w") as f:
        json.dump(quick_config, f, indent=2)

    print("📋 CONFIGURATION:")
    print("  🤖 Robot optimisé (faiblesses corrigées)")
    print("  ⏰ Déploiement automatique FTMO")
    print("  📊 Focus Portfolio Optimizer (Sharpe 1.651)")
    print("  🛡️  Gestion risques intégrée")

    print("\n🎯 OPTIONS DÉMARRAGE:")
    print("1. Démarrage automatique (recommandé)")
    print("2. Démarrage manuel du robot")
    print("3. Test configuration seulement")

    try:
        choice = input("\nChoisir option (1-3): ").strip()
    except KeyboardInterrupt:
        print("\n\n👋 Annulé")
        return

    if choice == "1":
        print("\n🚀 DÉMARRAGE AUTOMATIQUE...")
        # Lancer système de déploiement
        try:
            subprocess.run(
                [sys.executable, str(deployment_script)], check=True
            )
        except subprocess.CalledProcessError as e:
            print(f"❌ Erreur déploiement: {e}")

    elif choice == "2":
        print("\n🎯 DÉMARRAGE MANUEL...")
        # Lancer robot directement
        try:
            subprocess.run([sys.executable, str(robot_script)], check=True)
        except subprocess.CalledProcessError as e:
            print(f"❌ Erreur robot: {e}")

    elif choice == "3":
        print("\n🔍 TEST CONFIGURATION...")
        print("✅ Configuration créée:")
        print("  📄 control/quick_start_config.json")
        print("  🤖 Robot: scripts/enhanced_ultimate_trading_robot.py")
        print("  🚀 Déployeur: scripts/auto_deployment_system.py")
        print("\nPour démarrer manuellement:")
        print(f"  python {robot_script}")
        print("Ou avec déploiement automatique:")
        print(f"  python {deployment_script}")

    else:
        print("❌ Option invalide")


if __name__ == "__main__":
    main()
