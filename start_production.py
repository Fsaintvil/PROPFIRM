#!/usr/bin/env python3
"""
LANCEUR PRODUCTION SÉCURISÉ - PROPFIRM Trading Robot
Mode production avec health checks complets, logs structurés,
verrou anti multi-instance et recovery automatique.
"""

import sys
import os
import logging
import importlib
from typing import List
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime
import argparse

# Configuration des chemins pour importer le moteur depuis scripts/
if 'scripts' not in sys.path:
    sys.path.append('scripts')


def setup_logger(level: str = "INFO") -> logging.Logger:
    """Configure un logger pour le lanceur avec rotation de fichiers.

    Args:
        level: Niveau de log (DEBUG, INFO, WARNING, ERROR)
    Returns:
        Logger configuré
    """
    Path("logs").mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("production_launcher")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Éviter doublons
    if logger.handlers:
        logger.handlers.clear()

    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Fichier avec rotation
    file_handler = RotatingFileHandler(
        f"logs/launcher_{datetime.now().strftime('%Y%m%d')}.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.addHandler(file_handler)

    # Console
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.addHandler(console)

    return logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Lance la production PROPFIRM avec contrôles avancés"
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default=(
            "BTCUSD, ETHUSD, XAUUSD, USDCAD, AUDNZD, EURJPY, "
            "GBPCHF, NZDJPY, EURUSD, EURAUD, US500.cash, JP225.cash"
        ),
        help="Liste des symboles, séparés par des virgules",
    )
    parser.add_argument(
        "--lots", type=str, default="0.01",
        help="Taille des lots: valeur unique (ex: 0.01) ou mapping ex: EURUSD=0.02,XAUUSD=0.01"
    )
    parser.add_argument(
        "--risk", type=float, default=0.02,
        help="Risque max par trade (ex: 0.02 pour 2%)"
    )
    parser.add_argument(
        "--interval", type=int, default=None,
        help="Intervalle de trading en secondes (override)"
    )
    parser.add_argument(
        "--threshold", type=float, default=None,
        help="Seuil de confiance (override)"
    )
    parser.add_argument(
        "--smoke", type=int, default=None,
        help="Mode smoke: nombre de cycles max (définit ENGINE_MAX_CYCLES)"
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Ne pas demander de confirmation (mode non interactif)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="N'exécute pas le trading, fait seulement les health checks"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Ignore le verrou/arrêt d'urgence et force le lancement"
    )
    parser.add_argument(
        "--log-level", type=str, default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Niveau de logs du lanceur"
    )
    parser.add_argument(
        "--lock-file", type=str, default=str(Path("control/production.lock")),
        help="Chemin du fichier de verrouillage"
    )
    parser.add_argument(
        "--auto", action="store_true",
        help="Configure automatiquement symboles/lots/flags/threshold"
    )
    return parser.parse_args()


def parse_lots(lots_arg: str, symbols: List[str]) -> dict:
    """Parse l'argument --lots en dictionnaire par symbole."""
    try:
        # Valeur unique
        val = float(lots_arg)
        return {s: val for s in symbols}
    except Exception:
        pass

    result = {}
    for item in lots_arg.split(","):
        if not item:
            continue
        if "=" in item:
            k, v = item.split("=", 1)
            try:
                result[k.strip()] = float(v)
            except Exception:
                continue
    # Compléter manquants avec 0.01
    for s in symbols:
        if s not in result:
            result[s] = 0.01
    return result


def create_lock(lock_path: Path, logger: logging.Logger, force: bool = False) -> bool:
    """Crée un lockfile pour empêcher plusieurs instances."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists() and not force:
        try:
            # Détection d'obsolescence simple (>24h)
            mtime = datetime.fromtimestamp(lock_path.stat().st_mtime)
            if (datetime.now() - mtime).total_seconds() > 86400:
                logger.warning(
                    "Lockfile ancien détecté (>24h) — "
                    "utilisez --force pour l'écraser"
                )
            else:
                logger.error(
                    "Une production est déjà en cours (lock: "
                    f"{lock_path}) — utilisez --force pour forcer"
                )
            return False
        except Exception:
            logger.error(f"Lockfile présent: {lock_path}")
            return False

    try:
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write(f"timestamp={datetime.now().isoformat()}\n")
            f.write(f"pid={os.getpid()}\n")
        logger.debug(f"Lockfile créé: {lock_path}")
        return True
    except Exception as e:
        logger.error(f"Impossible de créer le lockfile: {e}")
        return False


def remove_lock(lock_path: Path, logger: logging.Logger):
    try:
        if lock_path.exists():
            lock_path.unlink()
            logger.debug("Lockfile supprimé")
    except Exception as e:
        logger.warning(f"Impossible de supprimer le lockfile: {e}")


def main():
    args = parse_args()
    logger = setup_logger(args.log_level)
    exit_code = 0

    # Enforce running from PowerShell (pwsh / Windows PowerShell)
    try:
        # Variables d'environnement typiques de PowerShell
        ps_env_present = any(
            k in os.environ for k in ("PSModulePath", "PSExecutionPolicyPreference")
        )
    except Exception:
        ps_env_present = False

    if not ps_env_present:
        # Not running inside a PowerShell session (pwsh/powershell)
        msg = (
            "This script must be launched from PowerShell (pwsh.exe or powershell.exe).\n"
            "Please open a PowerShell terminal and run the command there."
        )
        # Print to console (logger may not be configured early in some runs)
        print(msg)
        return 6

    logger.info("🚀 PROPFIRM - LANCEMENT PRODUCTION")
    logger.info("=" * 50)
    logger.info(f"📅 Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Créer lockfile
    lock_path = Path(args.lock_file)
    if not create_lock(lock_path, logger, force=args.force):
        return 2

    # Mode smoke
    if args.smoke is not None:
        os.environ["ENGINE_MAX_CYCLES"] = str(int(args.smoke))
        # Temps de sommeil réduit pour tests rapides
        os.environ.setdefault("ENGINE_SMOKE_SLEEP", "2")

    try:
        # 1. Import du moteur optimisé
        logger.info("1️⃣ Chargement du moteur de trading…")
        # Import dynamique robuste (évite les erreurs d'analyse statique)
        try:
            LiveTradingEngine = (
                importlib.import_module("live_trading_engine")
                .LiveTradingEngine
            )
        except Exception:
            LiveTradingEngine = (
                importlib.import_module("scripts.live_trading_engine")
                .LiveTradingEngine
            )
        logger.info("✅ Moteur chargé avec améliorations")

        # 2. Instanciation avec configuration fournie/optimale
        if args.auto:
            try:
                from tools.auto_configurator import (
                    suggest_production_config, apply_env_flags
                )
                auto_cfg = suggest_production_config()
                apply_env_flags(auto_cfg.get("flags", {}))
                symbols = auto_cfg.get("symbols", [])
                lot_sizes = auto_cfg.get("lot_sizes", {})
                # Overrides proposés
                engine_interval_override = auto_cfg.get("interval_seconds")
                engine_threshold_override = auto_cfg.get("threshold")
                logger.info("🧪 AUTO-CONFIG active: %s", auto_cfg)
            except Exception as _ac_err:
                logger.warning("Auto-config indisponible: %s", _ac_err)
                symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
                lot_sizes = parse_lots(args.lots, symbols)
                engine_interval_override = None
                engine_threshold_override = None
        else:
            symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
            lot_sizes = parse_lots(args.lots, symbols)
            engine_interval_override = None
            engine_threshold_override = None
        logger.info("2️⃣ Initialisation avec configuration…")
        engine = LiveTradingEngine(
            symbols=symbols,
            lot_sizes=lot_sizes,
            max_risk_per_trade=args.risk,
        )

        # Overrides non-invasifs
    # Intervalle: priorité aux arguments, sinon configuration auto,
    # puis variable d'environnement canonique TRADING_INTERVAL
    # (compatibilité: TRADE_INTERVAL_SECONDS est déprécié)
        if args.interval is not None:
            engine.trading_interval = int(args.interval)
        elif engine_interval_override is not None:
            engine.trading_interval = int(engine_interval_override)
        else:
            env_interval = os.environ.get("TRADING_INTERVAL")
            if not env_interval:
                # compat: ancien nom (déprécié)
                env_interval = os.environ.get("TRADE_INTERVAL_SECONDS")
                if env_interval:
                    logger.warning(
                        "TRADE_INTERVAL_SECONDS déprécié — utilisez TRADING_INTERVAL"
                    )
            if env_interval:
                try:
                    engine.trading_interval = int(float(env_interval))
                except Exception:
                    logger.warning("Intervalle environnement invalide: %s", env_interval)

        if args.threshold is not None:
            try:
                val = float(args.threshold)
                engine.confidence_threshold = val
                # Garder cohérent avec les métriques
                if isinstance(engine.performance_metrics, dict):
                    engine.performance_metrics["optimal_threshold"] = val
            except Exception:
                pass
        elif engine_threshold_override is not None:
            try:
                val = float(engine_threshold_override)
                engine.confidence_threshold = val
                if isinstance(engine.performance_metrics, dict):
                    engine.performance_metrics["optimal_threshold"] = val
            except Exception:
                pass

        # 3. Résumé préflight
        logger.info("\n🔎 RÉSUMÉ PRÉFLIGHT")
        logger.info(f"  • Symboles: {engine.symbols}")
        logger.info(f"  • Lots: {engine.lot_sizes}")
        logger.info(f"  • Intervalle: {engine.trading_interval}s")
        logger.info(f"  • Seuil: {engine.confidence_threshold}")
        logger.info(f"  • Risk/trade: {args.risk:.2%}")

        # 3.b Vérification arrêt d'urgence
        emergency_active = False
        try:
            emergency_active = engine.check_emergency_stop()
        except Exception:
            emergency_active = False

        if emergency_active and not args.force:
            logger.error(
                "🚨 Arrêt d'urgence actif – démarrage bloqué "
                "(utilisez --force pour passer outre)"
            )
            remove_lock(lock_path, logger)
            return 3

        # 4. Health checks (dry-run possible)
        logger.info("\n🩺 HEALTH CHECKS…")
        health_ok = engine.production_health_check()
        if not health_ok:
            logger.warning("Health checks partiels/échoués")
        else:
            logger.info("✅ Système prêt pour la production")

        if args.dry_run:
            logger.info("--dry-run: arrêt après vérifications, aucun trade exécuté")
            remove_lock(lock_path, logger)
            return 0 if health_ok else 4

        # 5. Confirmation utilisateur (sécurisée)
        if not args.yes:
            print()  # newline pour la question
            print("⚠️ MODE LIVE - Trading réel activé")
            print("Tapez 'PROD' pour confirmer le démarrage en production")
            try:
                response = input("▶️ Confirmation: ")
            except EOFError:
                response = ""
            if response.strip().upper() not in {"PROD", "YES", "OUI"}:
                logger.info("⏹️ Démarrage annulé par l'utilisateur")
                remove_lock(lock_path, logger)
                return 0
        else:
            logger.info("--yes détecté: confirmation ignorée")

        # 6. Démarrage production
        logger.info("\n🚦 DÉMARRAGE PRODUCTION…")
        logger.info("📊 Monitoring actif - logs/ (launcher & moteur)")
        logger.info("⏹️ Arrêt: Ctrl+C")
        logger.info("-" * 50)

        # Optional: initialize an external AIManager if available and attach to engine
        try:
            try:
                from ai_init import AIManager  # type: ignore[import]
            except Exception:
                from scripts.ai_init import AIManager  # type: ignore[import]

            logger.info("🧠 Initialisation de l'AI Manager externe...")
            ai = AIManager()
            # Informational logs if available
            try:
                n_meta = len(getattr(ai, 'meta', {}).get('model_ensemble', []))
            except Exception:
                try:
                    n_meta = len(getattr(ai, 'meta').model_ensemble)
                except Exception:
                    n_meta = 0
            logger.info(f"✅ AIManager initialisé (meta models={n_meta})")

            # Attach to engine where possible (non-invasive)
            try:
                setattr(engine, 'ai_manager', ai)
                # compatibility helpers
                if hasattr(ai, 'meta'):
                    setattr(engine, 'meta_learning', getattr(ai, 'meta'))
                if hasattr(ai, 'rl'):
                    setattr(engine, 'rl_agent', getattr(ai, 'rl'))
                if hasattr(ai, 'portfolio'):
                    setattr(engine, 'portfolio_optimizer', getattr(ai, 'portfolio'))
            except Exception:
                logger.debug('Impossible d attacher AIManager à l engine (non critique)')
        except Exception as e:
            logger.warning(f"AIManager non disponible ou échec init: {e}")

        success = engine.start_production()

        if success:
            logger.info("\n✅ Session de trading terminée")
            exit_code = 0
        else:
            logger.error("\n❌ Échec du démarrage/production")
            exit_code = 5

    except KeyboardInterrupt:
        logger.info("\n⏹️ Arrêt demandé par l'utilisateur")
        logger.info("💾 Sauvegarde de la session…")
        try:
            # Méthode canonique du moteur (si disponible)
            if 'engine' in locals():
                engine.save_trading_session()
                logger.info("✅ Session sauvegardée")
        except Exception:
            logger.warning("Impossible de sauvegarder la session")
        exit_code = 0

    except Exception as e:
        logger.error(f"\n❌ Erreur critique: {e}")
        import traceback
        traceback.print_exc()
        exit_code = 1

    finally:
        # Nettoyage lockfile
        remove_lock(lock_path, logger)
        logger.info("\n🏁 Arrêt du système de trading")
        return exit_code


if __name__ == "__main__":
    sys.exit(main())
