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


################################################################################
# FROM: scripts\auto_improve_bot.py
################################################################################
"""Auto-improve the bot using LightGBM grid search with time-series CV.

Saves best config, CV results, retrained model and backtest under
`artifacts/auto_improve/`.
"""
from __future__ import annotations

import json
from pathlib import Path

# itertools not needed yet
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import accuracy_score


def time_series_cv_scores(X, y, params, num_boost_round=50, n_splits=5):
    n = len(X)
    fold_size = n // (n_splits + 1)
    scores = []
    for i in range(1, n_splits + 1):
        train_end = fold_size * i
        val_start = train_end
        val_end = min(train_end + fold_size, n)
        if val_start >= val_end:
            continue
        X_train = X.iloc[:train_end]
        y_train = y[:train_end]
        X_val = X.iloc[val_start:val_end]
        y_val = y[val_start:val_end]
        dtrain = lgb.Dataset(X_train.values, label=y_train)
        model = lgb.train(params, dtrain, num_boost_round=num_boost_round)
        preds = model.predict(X_val.values)
        pred_labels = (preds > 0.5).astype(int)
        acc = float(accuracy_score(y_val, pred_labels))
        scores.append(acc)
    return scores


def run_grid_search(horizons, grid, num_boost_round=50):
    base = Path.cwd()
    data_path = base / "data" / "features_sample.csv"
    df = pd.read_csv(data_path, parse_dates=[0], index_col=0)
    results = []
    for horizon in horizons:
        print("Grid search horizon", horizon)
        df_local = df.copy()
        if "label" not in df_local.columns:
            df_local["label"] = (
                df_local["close"].shift(-horizon) > df_local["close"]
            ).astype(int)
            df_local = df_local.dropna()
        X = df_local.drop(columns=["label"]).ffill().fillna(0)
        y = df_local["label"].values
        for params in grid:
            lgb_params = {
                "objective": "binary",
                "metric": "binary_logloss",
                "verbose": -1,
                "num_leaves": params["num_leaves"],
                "learning_rate": params["learning_rate"],
            }
            scores = time_series_cv_scores(
                X, y, lgb_params, num_boost_round=num_boost_round
            )
            mean_score = float(np.mean(scores)) if scores else None
            std_score = float(np.std(scores)) if scores else None
            r = {
                "horizon": horizon,
                "params": params,
                "mean_accuracy": mean_score,
                "std_accuracy": std_score,
                "scores": scores,
            }
            results.append(r)
    return results


def retrain_and_backtest(best_item):
    base = Path.cwd()
    data_path = base / "data" / "features_sample.csv"
    df = pd.read_csv(data_path, parse_dates=[0], index_col=0)
    horizon = best_item["horizon"]
    params = best_item["params"]
    if "label" not in df.columns:
        df["label"] = (df["close"].shift(-horizon) > df["close"]).astype(int)
        df = df.dropna()
    X = df.drop(columns=["label"]).ffill().fillna(0)
    y = df["label"].values
    dtrain = lgb.Dataset(X.values, label=y)
    lgb_params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "verbose": -1,
        "num_leaves": params["num_leaves"],
        "learning_rate": params["learning_rate"],
    }
    model = lgb.train(lgb_params, dtrain, num_boost_round=100)
    art = base / "artifacts" / "auto_improve"
    art.mkdir(parents=True, exist_ok=True)
    model.save_model(str(art / "best_lightgbm.txt"))
    # run backtest using existing script with conservative params
    import subprocess

    subprocess.run(
        [
            "python",
            "scripts/backtest_poc.py",
            "--transaction-cost",
            "0.0001",
            "--slippage",
            "0.0002",
            "--max-position-size",
            "0.1",
            "--stop-loss",
            "0.02",
            "--take-profit",
            "0.04",
        ],
        check=True,
    )
    # copy backtest report
    bt = base / "artifacts" / "backtest_report.json"
    if bt.exists():
        art.joinpath("backtest_report.json").write_text(
            bt.read_text(), encoding="utf-8"
        )
    return art


def main():
    horizons = [1, 5, 15]
    # small grid
    grid = [
        {"num_leaves": 15, "learning_rate": 0.1},
        {"num_leaves": 31, "learning_rate": 0.05},
        {"num_leaves": 63, "learning_rate": 0.01},
    ]
    results = run_grid_search(horizons, grid, num_boost_round=50)
    out = Path.cwd() / "artifacts" / "auto_improve"
    out.mkdir(parents=True, exist_ok=True)
    (out / "grid_results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    # pick best by mean_accuracy
    valid = [r for r in results if r["mean_accuracy"] is not None]
    best = max(valid, key=lambda r: r["mean_accuracy"]) if valid else None
    (out / "best.json").write_text(
        json.dumps(best, indent=2), encoding="utf-8"
    )
    if best:
        art = retrain_and_backtest(best)
        print("Auto-improve finished. Artifacts in", art)
    else:
        print("No valid results from grid search.")


if __name__ == "__main__":
    main()


################################################################################
# FROM: scripts\auto_improve_grid_large.py
################################################################################
"""Larger grid search for LightGBM using time-series CV.

Runs an expanded grid across several LightGBM hyperparameters, selects
the best config by mean CV accuracy, retrains on all data and runs the
protected backtest. Results are saved under artifacts/auto_improve/.
"""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import accuracy_score


def time_series_cv_scores(X, y, params, num_boost_round=100, n_splits=5):
    n = len(X)
    fold_size = n // (n_splits + 1)
    scores = []
    for i in range(1, n_splits + 1):
        train_end = fold_size * i
        val_start = train_end
        val_end = min(train_end + fold_size, n)
        if val_start >= val_end:
            continue
        X_train = X.iloc[:train_end]
        y_train = y[:train_end]
        X_val = X.iloc[val_start:val_end]
        y_val = y[val_start:val_end]
        dtrain = lgb.Dataset(X_train.values, label=y_train)
        model = lgb.train(params, dtrain, num_boost_round=num_boost_round)
        preds = model.predict(X_val.values)
        pred_labels = (preds > 0.5).astype(int)
        acc = float(accuracy_score(y_val, pred_labels))
        scores.append(acc)
    return scores


def run_grid_search(horizons, grid, num_boost_round=100):
    base = Path.cwd()
    data_path = base / "data" / "features_sample.csv"
    df = pd.read_csv(data_path, parse_dates=[0], index_col=0)
    results = []
    for horizon in horizons:
        print("Large grid search horizon", horizon)
        df_local = df.copy()
        if "label" not in df_local.columns:
            df_local["label"] = (
                df_local["close"].shift(-horizon) > df_local["close"]
            ).astype(int)
            df_local = df_local.dropna()
        X = df_local.drop(columns=["label"]).ffill().fillna(0)
        y = df_local["label"].values
        for params in grid:
            lgb_params = {
                "objective": "binary",
                "metric": "binary_logloss",
                "verbose": -1,
                "num_leaves": params["num_leaves"],
                "learning_rate": params["learning_rate"],
                "max_depth": params.get("max_depth", -1),
                "min_data_in_leaf": params.get("min_data_in_leaf", 20),
            }
            scores = time_series_cv_scores(
                X, y, lgb_params, num_boost_round=num_boost_round
            )
            mean_score = float(np.mean(scores)) if scores else None
            std_score = float(np.std(scores)) if scores else None
            r = {
                "horizon": horizon,
                "params": params,
                "mean_accuracy": mean_score,
                "std_accuracy": std_score,
                "scores": scores,
            }
            results.append(r)
    return results


def retrain_and_backtest(best_item):
    base = Path.cwd()
    data_path = base / "data" / "features_sample.csv"
    df = pd.read_csv(data_path, parse_dates=[0], index_col=0)
    horizon = best_item["horizon"]
    params = best_item["params"]
    if "label" not in df.columns:
        df["label"] = (df["close"].shift(-horizon) > df["close"]).astype(int)
        df = df.dropna()
    X = df.drop(columns=["label"]).ffill().fillna(0)
    y = df["label"].values
    dtrain = lgb.Dataset(X.values, label=y)
    lgb_params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "verbose": -1,
        "num_leaves": params["num_leaves"],
        "learning_rate": params["learning_rate"],
        "max_depth": params.get("max_depth", -1),
        "min_data_in_leaf": params.get("min_data_in_leaf", 20),
    }
    model = lgb.train(lgb_params, dtrain, num_boost_round=200)
    art = base / "artifacts" / "auto_improve"
    art.mkdir(parents=True, exist_ok=True)
    model.save_model(str(art / "best_lightgbm_large.txt"))
    # run backtest with conservative protections
    import subprocess

    subprocess.run(
        [
            "python",
            "scripts/backtest_poc.py",
            "--transaction-cost",
            "0.0001",
            "--slippage",
            "0.0002",
            "--max-position-size",
            "0.1",
            "--stop-loss",
            "0.02",
            "--take-profit",
            "0.04",
        ],
        check=True,
    )
    bt = base / "artifacts" / "backtest_report.json"
    if bt.exists():
        content = bt.read_text()
        (art / "backtest_report_large.json").write_text(
            content, encoding="utf-8"
        )
    return art


def main():
    horizons = [1, 5, 15]
    # larger grid but kept modest to keep runtime reasonable
    grid = []
    for num_leaves in (31, 63, 127):
        for lr in (0.1, 0.05, 0.01):
            for max_depth in (-1, 6):
                for min_leaf in (5, 20):
                    grid.append(
                        {
                            "num_leaves": num_leaves,
                            "learning_rate": lr,
                            "max_depth": max_depth,
                            "min_data_in_leaf": min_leaf,
                        }
                    )

    results = run_grid_search(horizons, grid, num_boost_round=100)
    out = Path.cwd() / "artifacts" / "auto_improve"
    out.mkdir(parents=True, exist_ok=True)
    grid_file = out / "grid_results_large.json"
    grid_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
    valid = [r for r in results if r["mean_accuracy"] is not None]
    best = max(valid, key=lambda r: r["mean_accuracy"]) if valid else None
    best_file = out / "best_large.json"
    best_file.write_text(json.dumps(best, indent=2), encoding="utf-8")
    if best:
        art = retrain_and_backtest(best)
        print("Large auto-improve finished. Artifacts in", art)
    else:
        print("No valid results from large grid search.")


if __name__ == "__main__":
    main()


################################################################################
# FROM: scripts\auto_retry_close.py
################################################################################
#!/usr/bin/env python3
"""
Auto retry loop to close positions:
- Calls `scripts/close_current_positions_verified.py` periodically
- Stops when remaining_positions == 0 or when duration elapsed
- Writes per-iteration logs to artifacts/live_trading/auto_retry_<timestamp>.log
Usage example:
  python scripts/auto_retry_close.py --interval 5 --duration 120
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

OUT_DIR = Path("artifacts") / "live_trading"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def run_once(iter_idx: int, out_prefix: Path) -> dict:
    """Run the verified close script once and capture summary info."""
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    log_file = out_prefix / f"auto_retry_{ts}_{iter_idx}.log"
    cmd = [sys.executable, "scripts/close_current_positions_verified.py"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    # write stdout/stderr for inspection
    log_file.write_text(
        f"# CMD: {' '.join(cmd)}\n# TIMESTAMP: {ts}\n\n--- STDOUT ---\n{res.stdout}\n\n--- STDERR ---\n{res.stderr}\n",
        encoding="utf-8",
    )

    summary = {"timestamp": ts, "returncode": res.returncode, "log": str(log_file)}

    # try to read the output JSON produced by the called script
    out_json = out_prefix / "close_after_diagnostics.json"
    if out_json.exists():
        try:
            data = json.loads(out_json.read_text(encoding="utf-8"))
            summary["remaining_positions"] = data.get("remaining_positions")
            summary["records"] = len(data.get("records", []))
        except Exception as e:
            summary["json_read_error"] = str(e)
    else:
        summary["note"] = "output json not found"

    # append a concise summary file
    summary_file = out_prefix / "auto_retry_summary.json"
    all_summaries = []
    if summary_file.exists():
        try:
            all_summaries = json.loads(summary_file.read_text(encoding="utf-8"))
        except Exception:
            all_summaries = []
    all_summaries.append(summary)
    summary_file.write_text(json.dumps(all_summaries, indent=2), encoding="utf-8")

    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Interval between attempts in minutes",
    )
    ap.add_argument(
        "--duration", type=float, default=120.0, help="Total duration in minutes"
    )
    ap.add_argument("--max-iterations", type=int, default=9999, help="Cap iterations")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Run one quick iteration only for testing",
    )
    args = ap.parse_args()

    interval_s = int(args.interval * 60)
    duration_s = int(args.duration * 60)
    end_time = datetime.utcnow() + timedelta(seconds=duration_s)

    out_prefix = OUT_DIR

    iter_idx = 0
    print(
        f"Starting auto-retry close loop: interval={args.interval}min duration={args.duration}min"
    )
    try:
        while datetime.utcnow() < end_time and iter_idx < args.max_iterations:
            iter_idx += 1
            print(f"Iteration {iter_idx} at {datetime.utcnow().isoformat()}Z")
            summary = run_once(iter_idx, out_prefix)
            print(
                " ->",
                summary.get("remaining_positions"),
                "remaining_positions, returncode=",
                summary.get("returncode"),
            )

            if summary.get("remaining_positions") == 0:
                print("All positions closed; exiting loop.")
                break

            if args.dry_run:
                print("Dry-run: stopping after one iteration.")
                break

            # sleep until next iteration
            now = datetime.utcnow()
            if now + timedelta(seconds=interval_s) > end_time:
                # final iteration will be next; adjust sleep to not overshoot
                sleep_s = max(0, int((end_time - now).total_seconds()))
            else:
                sleep_s = interval_s
            print(f"Sleeping {sleep_s} seconds until next attempt...")
            time.sleep(sleep_s)

    except KeyboardInterrupt:
        print("Interrupted by user; exiting.")


if __name__ == "__main__":
    main()


# End of merged preview
