# Merged preview for prefix: auto
# Generated from 4 files

################################################################################
# FROM: scripts\auto_deployment_system.py
################################################################################
#!/usr/bin/env python3
"""
AUTOMATED DEPLOYMENT SYSTEM - ENHANCED ULTIMATE TRADING ROBOT
Système de déploiement automatique avec règles temporelles FTMO

FONCTIONNALITÉS DÉPLOYÉES:
✅ Timing FTMO automatique (UTC+1/UTC+2)
✅ Démarrage lundi 5 minutes après ouverture
✅ Envoi ordres toutes les 930 secondes
✅ Fermeture automatique vendredi 30min avant clôture
✅ Gestion complète du cycle de trading
✅ Monitoring risques en temps réel
"""

import subprocess
import sys
import json
import time
import logging
from datetime import datetime
import pytz
from pathlib import Path


class AutoDeploymentSystem:
    """Système de déploiement automatique du robot optimisé"""

    def __init__(self):
        self.robot_script = "enhanced_ultimate_trading_robot.py"
        self.deployment_config = self.get_deployment_config()
        self.ftmo_tz = pytz.timezone("Europe/Prague")

        self.setup_logging()
        self.setup_deployment_directories()

    def get_deployment_config(self):
        """Configuration du déploiement automatique"""
        return {
            "robot_script": "enhanced_ultimate_trading_robot.py",
            "auto_start": True,
            "timezone": "Europe/Prague",  # FTMO timezone
            "schedule": {
                "monday_start_delay_minutes": 5,  # 5min après ouverture
                "order_interval_seconds": 930,  # 15.5 minutes
                "friday_close_advance_minutes": 30,  # 30min avant fermeture
            },
            "monitoring": {
                "health_check_interval": 60,  # 1 minute
                "restart_on_failure": True,
                "max_restart_attempts": 3,
                "log_rotation_days": 7,
            },
            "safety": {
                "require_market_hours": True,
                "emergency_stop_enabled": True,
                "risk_limit_monitoring": True,
            },
        }

    def setup_logging(self):
        """Configuration logging déploiement"""
        log_dir = Path("logs/deployment")
        log_dir.mkdir(parents=True, exist_ok=True)

        log_file = (
            log_dir / f"deployment_{datetime.now().strftime('%Y%m%d')}.log"
        )

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
        )
        self.logger = logging.getLogger(__name__)

    def setup_deployment_directories(self):
        """Créer structure répertoires déploiement"""
        dirs = [
            "logs/deployment",
            "control/deployment",
            "artifacts/deployment",
            "data/deployment",
        ]
        for dir_path in dirs:
            Path(dir_path).mkdir(parents=True, exist_ok=True)

    def check_deployment_prerequisites(self):
        """Vérifier prérequis déploiement"""
        print("\n🔍 VÉRIFICATION PRÉREQUIS DÉPLOIEMENT")
        print("=" * 45)

        checks = []

        # 1. Vérifier script robot
        robot_path = Path(f"scripts/{self.robot_script}")
        if robot_path.exists():
            checks.append(("Script robot", True, f"✅ {robot_path}"))
        else:
            checks.append(("Script robot", False, f"❌ {robot_path} manquant"))

        # 2. Vérifier Python et packages
        try:
            # Vérifier packages critiques sans les importer globalement
            __import__('pandas')
            __import__('numpy')
            __import__('pytz')

            checks.append(
                ("Packages Python", True, "✅ Packages critiques installés")
            )
        except ImportError as e:
            checks.append(
                ("Packages Python", False, f"❌ Package manquant: {e}")
            )

        # 3. Vérifier fuseau horaire
        try:
            ftmo_time = datetime.now(self.ftmo_tz)
            checks.append(
                (
                    "Fuseau FTMO",
                    True,
                    f"✅ {ftmo_time.strftime('%Y-%m-%d %H:%M:%S %Z')}",
                )
            )
        except Exception as e:
            checks.append(("Fuseau FTMO", False, f"❌ Erreur timezone: {e}"))

        # 4. Vérifier permissions fichiers
        try:
            test_file = Path("control/deployment/test_permissions.tmp")
            test_file.write_text("test")
            test_file.unlink()
            checks.append(("Permissions", True, "✅ Écriture autorisée"))
        except Exception as e:
            checks.append(("Permissions", False, f"❌ Erreur permissions: {e}"))

        # Afficher résultats
        for check_name, status, message in checks:
            print(f"  {message}")

        all_passed = all(status for _, status, _ in checks)

        if all_passed:
            print("\n✅ Tous les prérequis sont satisfaits")
        else:
            print("\n❌ Certains prérequis ne sont pas satisfaits")

        return all_passed

    def get_ftmo_market_schedule(self):
        """Obtenir planning marché FTMO"""
        now_ftmo = datetime.now(self.ftmo_tz)

        # Marché forex FTMO: Lundi 00:00 - Vendredi 23:00
        sched_config = self.deployment_config['schedule']

        monday_delay = sched_config['monday_start_delay_minutes']
        order_interval = sched_config['order_interval_seconds']
        friday_advance = sched_config['friday_close_advance_minutes']

        schedule_info = {
            "current_time": now_ftmo,
            "timezone": "Europe/Prague (FTMO)",
            "market_hours": {
                "monday_open": "00:00",
                "friday_close": "23:00",
                "weekend": "Fermé",
            },
            "robot_schedule": {
                "monday_start": f"00:{monday_delay:02d}",
                "order_interval": f"{order_interval}s",
                "friday_stop": f"22:{60-friday_advance:02d}",
            },
        }

        return schedule_info

    def is_deployment_time(self):
        """Vérifier si c'est le moment de déployer"""
        now_ftmo = datetime.now(self.ftmo_tz)
        weekday = now_ftmo.weekday()
        hour = now_ftmo.hour
        minute = now_ftmo.minute

        # Déploiement possible pendant heures de marché
        if weekday > 4:  # Weekend
            return False, "Marché fermé (weekend)"

        # Vendredi après 22:30
        if weekday == 4 and (hour > 22 or (hour == 22 and minute > 30)):
            return False, "Marché fermé (vendredi soir)"

        # Lundi avant 00:05
        if weekday == 0 and (hour == 0 and minute < 5):
            return False, "Attente ouverture marché (lundi)"

        return True, "Horaires de marché"

    def deploy_robot(self):
        """Déployer le robot avec configuration automatique"""
        print("\n🚀 DÉPLOIEMENT ROBOT OPTIMISÉ")
        print("=" * 35)

        try:
            # Vérifier timing
            can_deploy, reason = self.is_deployment_time()
            if not can_deploy:
                print(f"⏳ Déploiement différé: {reason}")
                return False

            # Obtenir informations timing
            schedule_info = self.get_ftmo_market_schedule()
            current_time = schedule_info['current_time']
            ftmo_time_str = current_time.strftime('%Y-%m-%d %H:%M:%S')
            print(f"🕐 Heure FTMO: {ftmo_time_str}")

            # Construire commande déploiement
            robot_path = Path(f"scripts/{self.robot_script}")
            if not robot_path.exists():
                print(f"❌ Script robot non trouvé: {robot_path}")
                return False

            # Lancer le robot en arrière-plan
            cmd = [sys.executable, str(robot_path)]

            print(f"🎯 Lancement: {' '.join(cmd)}")

            # Créer fichier de statut
            deployment_status = {
                "deployed_at": datetime.now().isoformat(),
                "ftmo_time": schedule_info["current_time"].isoformat(),
                "robot_script": str(robot_path),
                "deployment_config": self.deployment_config,
                "schedule_info": schedule_info,
                "process_id": None,
                "status": "deploying",
            }

            # Sauvegarder statut
            status_file = Path("control/deployment/deployment_status.json")
            with open(status_file, "w") as f:
                json.dump(deployment_status, f, indent=2)

            # Lancer le processus
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=Path.cwd(),
            )

            # Attendre quelques secondes pour vérifier démarrage
            time.sleep(3)

            if process.poll() is None:  # Processus toujours en cours
                # Mettre à jour statut
                deployment_status["process_id"] = process.pid
                deployment_status["status"] = "running"

                with open(status_file, "w") as f:
                    json.dump(deployment_status, f, indent=2)

                print("✅ Robot déployé avec succès")
                print(f"📊 PID: {process.pid}")
                print(f"🎯 Statut: {status_file}")

                self.logger.info(f"Robot déployé - PID: {process.pid}")

                # Démarrer monitoring
                if self.deployment_config["monitoring"]["restart_on_failure"]:
                    self.start_monitoring(process, deployment_status)

                return True
            else:
                # Erreur démarrage
                stdout, stderr = process.communicate()
                print("❌ Erreur démarrage robot:")
                print(f"STDOUT: {stdout}")
                print(f"STDERR: {stderr}")

                deployment_status["status"] = "failed"
                deployment_status["error"] = stderr

                with open(status_file, "w") as f:
                    json.dump(deployment_status, f, indent=2)

                return False

        except Exception as e:
            print(f"❌ Erreur déploiement: {e}")
            self.logger.error(f"Erreur déploiement: {e}")
            return False

    def start_monitoring(self, process, deployment_status):
        """Démarrer monitoring du robot"""
        print("\n🛡️  DÉMARRAGE MONITORING")
        print("=" * 25)

        check_interval = self.deployment_config["monitoring"][
            "health_check_interval"
        ]

        def monitoring_loop():
            restart_attempts = 0
            max_attempts = self.deployment_config["monitoring"][
                "max_restart_attempts"
            ]

            while restart_attempts < max_attempts:
                time.sleep(check_interval)

                # Vérifier si processus toujours en vie
                if process.poll() is not None:
                    self.logger.warning(
                        f"Robot arrêté - Code: {process.returncode}"
                    )

                    # Tentative redémarrage
                    if self.deployment_config["monitoring"][
                        "restart_on_failure"
                    ]:
                        restart_attempts += 1
                        msg = f"Redémarrage {restart_attempts}/{max_attempts}"
                        self.logger.info(msg)

                        # Relancer le robot
                        if self.deploy_robot():
                            restart_attempts = 0  # Reset compteur si succès
                            break
                    else:
                        break
                else:
                    # Robot toujours en vie
                    self.update_monitoring_status(process.pid)

            if restart_attempts >= max_attempts:
                self.logger.error("Nombre max de redémarrages atteint")

        # Lancer monitoring en arrière-plan
        import threading

        monitor_thread = threading.Thread(target=monitoring_loop, daemon=True)
        monitor_thread.start()

        print("✅ Monitoring démarré")
        print(f"  🔄 Vérification toutes les {check_interval}s")
        print("  🛠️  Redémarrage automatique activé")

    def update_monitoring_status(self, pid):
        """Mettre à jour statut monitoring"""
        status_file = Path("control/deployment/monitoring_status.json")

        monitoring_status = {
            "last_check": datetime.now().isoformat(),
            "robot_pid": pid,
            "status": "healthy",
            "checks_completed": getattr(self, "checks_completed", 0) + 1,
        }

        self.checks_completed = monitoring_status["checks_completed"]

        with open(status_file, "w") as f:
            json.dump(monitoring_status, f, indent=2)

    def show_deployment_summary(self):
        """Afficher résumé déploiement"""
        print("\n📋 RÉSUMÉ DÉPLOIEMENT")
        print("=" * 25)

        schedule_info = self.get_ftmo_market_schedule()

        print(f"🤖 Robot: {self.robot_script}")
        current_time = schedule_info['current_time']
        ftmo_time_str = current_time.strftime('%Y-%m-%d %H:%M:%S')
        print(f"🕐 Heure FTMO: {ftmo_time_str}")
        print("🎯 Planning automatique:")

        robot_schedule = schedule_info['robot_schedule']
        print(f"  📅 Démarrage lundi: {robot_schedule['monday_start']}")
        print(f"  🔄 Interval ordres: {robot_schedule['order_interval']}")
        print(f"  📅 Arrêt vendredi: {robot_schedule['friday_stop']}")

        can_deploy, reason = self.is_deployment_time()
        status_icon = "🟢" if can_deploy else "🟡"
        print(f"{status_icon} Statut: {reason}")

    def get_deployment_status(self):
        """Obtenir statut déploiement actuel"""
        status_file = Path("control/deployment/deployment_status.json")

        if status_file.exists():
            with open(status_file, "r") as f:
                return json.load(f)

        return None

    def stop_robot(self):
        """Arrêter le robot déployé"""
        print("\n🛑 ARRÊT ROBOT")
        print("=" * 15)

        status = self.get_deployment_status()
        if not status or not status.get("process_id"):
            print("❌ Aucun robot en cours")
            return False

        try:
            import psutil

            process = psutil.Process(status["process_id"])
            process.terminate()

            # Attendre arrêt propre
            process.wait(timeout=10)

            print("✅ Robot arrêté")

            # Mettre à jour statut
            status["status"] = "stopped"
            status["stopped_at"] = datetime.now().isoformat()

            status_file = Path("control/deployment/deployment_status.json")
            with open(status_file, "w") as f:
                json.dump(status, f, indent=2)

            return True

        except Exception as e:
            print(f"❌ Erreur arrêt: {e}")
            return False


def main():
    """Interface principale déploiement"""
    print("🚀 AUTOMATED DEPLOYMENT SYSTEM")
    print("Enhanced Ultimate Trading Robot v2.0")
    print("=" * 50)

    try:
        deployment_system = AutoDeploymentSystem()

        # Afficher résumé
        deployment_system.show_deployment_summary()

        # Vérifier prérequis
        if not deployment_system.check_deployment_prerequisites():
            print("\n❌ Prérequis non satisfaits - Arrêt déploiement")
            return

        # Choix action
        print("\n🎯 ACTIONS DISPONIBLES:")
        print("1. Déployer robot automatiquement")
        print("2. Vérifier statut déploiement")
        print("3. Arrêter robot")
        print("4. Monitoring seulement")

        try:
            choice = input("\nChoisir action (1-4): ").strip()
        except KeyboardInterrupt:
            print("\n\n👋 Annulé par utilisateur")
            return

        if choice == "1":
            print("\n🚀 DÉPLOIEMENT AUTOMATIQUE...")
            success = deployment_system.deploy_robot()
            if success:
                print("\n✅ Déploiement réussi!")
                print("Le robot fonctionne selon les règles FTMO")
            else:
                print("\n❌ Échec déploiement")

        elif choice == "2":
            status = deployment_system.get_deployment_status()
            if status:
                print(f"\n📊 Statut: {status['status']}")
                print(f"🕐 Déployé: {status['deployed_at']}")
                if status.get("process_id"):
                    print(f"📊 PID: {status['process_id']}")
            else:
                print("\n❌ Aucun déploiement actuel")

        elif choice == "3":
            deployment_system.stop_robot()

        elif choice == "4":
            print("\n🛡️  Mode monitoring seulement")
            print("Appuyer Ctrl+C pour arrêter")
            try:
                while True:
                    time.sleep(60)
                    print(f"🔄 Check {datetime.now().strftime('%H:%M:%S')}")
            except KeyboardInterrupt:
                print("\n🛑 Monitoring arrêté")

        else:
            print("❌ Choix invalide")

    except Exception as e:
        print(f"❌ Erreur système: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
