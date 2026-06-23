#!/usr/bin/env python3
"""
Backtest Institutionnel — CLI Principal.

Point d'entrée unique pour le backtest multi-actifs 12+ ans.

Usage :
    # Backtest simple (1 symbole, 1 stratégie)
    python scripts/bt_run.py --symbol EURUSD --strategy mom20x3 --tf H1

    # Backtest multi-symboles
    python scripts/bt_run.py --symbols EURUSD,GBPUSD,XAUUSD --strategy mom20x3 --tf H1

    # Backtest toutes les stratégies
    python scripts/bt_run.py --symbol EURUSD --strategy all --tf H4

    # Walk-Forward
    python scripts/bt_run.py --symbol EURUSD --strategy mom20x3 --tf H1 --walk-forward

    # Monte Carlo
    python scripts/bt_run.py --symbol EURUSD --strategy mom20x3 --tf H1 --monte-carlo

    # Stress Tests
    python scripts/bt_run.py --symbol EURUSD --strategy mom20x3 --tf H1 --stress-test

    # FTMO Challenge (par symbole)
    python scripts/bt_run.py --symbol EURUSD --strategy mom20x3 --tf H1 --ftmo

    # FTMO Challenge (portefeuille multi-symboles)
    python scripts/bt_run.py --group all --strategy mom20x3 --tf H1 --ftmo-portfolio

    # Rapport complet (tous les tests + charts)
    python scripts/bt_run.py --symbol EURUSD --strategy mom20x3 --tf H1 --full-report

    # Export JSON
    python scripts/bt_run.py --symbol EURUSD --strategy mom20x3 --tf H1 --export-json report.json

    # Liste des symboles disponibles
    python scripts/bt_run.py --list-symbols

Styles de coûts :
    --costs realistic   : Spread historique + commission + swap + slippage stochastique
    --costs raw         : Aucun coût (backtest idéal)
    --costs conservative: Spread ×3, commission max, slippage élevé
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None  # --config nécessite PyYAML

# Ajouter la racine du projet au path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from engine_simple.backtest_core import (
    BacktestEngine,
    BacktestConfig,
    DataLoader,
    MetricsCalculator,
    ReportGenerator,
    ChartGenerator,
    CostModel,
    FTMOChallengeSimulator,
    FTMOConfig,
    FTMOPortfolioSimulator,
)
from engine_simple.backtest_core.strategies import (
    MOM20x3,
    TrendFollowing,
    Breakout,
    MeanReversion,
)
from engine_simple.backtest_core.robustness import (
    WalkForwardAnalyzer,
    MonteCarloSimulator,
    StressTester,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bt_run")

# ─── Symboles disponibles (6 meilleurs MOM20x3 uniquement) ───────────────

SYMBOLS_MAJOR_FX = [
    "EURUSD",
    "USDCAD",
]
SYMBOLS_MINOR_FX = [
    "EURJPY",
    "GBPJPY",
]
SYMBOLS_METALS = [
    "XAUUSD",
]
SYMBOLS_CRYPTO = [
    "BTCUSD",
]
SYMBOLS_INDICES = []
SYMBOLS_ENERGY = []

ALL_SYMBOLS = SYMBOLS_MAJOR_FX + SYMBOLS_MINOR_FX + SYMBOLS_METALS + SYMBOLS_CRYPTO + SYMBOLS_INDICES + SYMBOLS_ENERGY

# ─── Mapping stratégies ──────────────────────────────────────────────────

STRATEGY_MAP = {
    "mom20x3": MOM20x3,
    "trend_following": TrendFollowing,
    "breakout": Breakout,
    "mean_reversion": MeanReversion,
}


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest Institutionnel Multi-Actifs 12+ ans",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python scripts/bt_run.py --symbol EURUSD --strategy mom20x3 --tf H1
  python scripts/bt_run.py --symbols EURUSD,GBPUSD --strategy all --tf H1 --full-report
  python scripts/bt_run.py --symbol XAUUSD --strategy trend_following --tf H4 --ftmo
  python scripts/bt_run.py --symbol BTCUSD --strategy breakout --tf H1 --walk-forward
        """,
    )

    # Symboles
    sym_group = parser.add_argument_group("Symboles")
    sym_group.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Symbole unique (ex: EURUSD)",
    )
    sym_group.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Symboles séparés par des virgules (ex: EURUSD,GBPUSD,XAUUSD)",
    )
    sym_group.add_argument(
        "--list-symbols",
        action="store_true",
        help="Lister les symboles disponibles",
    )
    sym_group.add_argument(
        "--group",
        type=str,
        default=None,
        choices=["major_fx", "minor_fx", "metals", "crypto", "all"],
        help="Groupe de symboles prédéfini",
    )

    # Stratégie
    strat_group = parser.add_argument_group("Stratégie")
    strat_group.add_argument(
        "--strategy",
        type=str,
        default="mom20x3",
        choices=["mom20x3", "trend_following", "breakout", "mean_reversion", "all"],
        help="Stratégie à tester (défaut: mom20x3)",
    )

    # Timeframe
    tf_group = parser.add_argument_group("Timeframe")
    tf_group.add_argument(
        "--tf",
        "--timeframe",
        type=str,
        default="H1",
        choices=["M15", "M5", "M1", "H1", "H4", "D1"],
        help="Timeframe (défaut: H1)",
    )
    tf_group.add_argument(
        "--start",
        type=str,
        default=None,
        help="Date de début (YYYY-MM-DD)",
    )
    tf_group.add_argument(
        "--end",
        type=str,
        default=None,
        help="Date de fin (YYYY-MM-DD)",
    )

    # Coûts
    cost_group = parser.add_argument_group("Coûts")
    cost_group.add_argument(
        "--costs",
        type=str,
        default="realistic",
        choices=["realistic", "raw", "conservative"],
        help="Modèle de coûts (défaut: realistic)",
    )
    cost_group.add_argument(
        "--capital",
        type=float,
        default=200_000.0,
        help="Capital initial (défaut: 200000)",
    )
    cost_group.add_argument(
        "--risk",
        type=float,
        default=0.0044,
        help="Risque par trade en %% du capital (défaut: 0.0044)",
    )

    # Tests de robustesse
    test_group = parser.add_argument_group("Tests de Robustesse")
    test_group.add_argument(
        "--walk-forward",
        action="store_true",
        help="Exécuter Walk-Forward Analysis",
    )
    test_group.add_argument(
        "--wf-splits",
        type=int,
        default=5,
        help="Nombre de folds pour walk-forward (défaut: 5)",
    )
    test_group.add_argument(
        "--monte-carlo",
        action="store_true",
        help="Exécuter Monte Carlo Simulation",
    )
    test_group.add_argument(
        "--mc-simulations",
        type=int,
        default=1000,
        help="Nombre de simulations Monte Carlo (défaut: 1000)",
    )
    test_group.add_argument(
        "--stress-test",
        action="store_true",
        help="Exécuter Stress Tests",
    )
    test_group.add_argument(
        "--stress-sample-bars",
        type=int,
        default=None,
        help="Nombre de barres pour stress tests (défaut: toutes, recommandé: 5000 pour vitesse)",
    )
    test_group.add_argument(
        "--stress-scenarios",
        type=str,
        default=None,
        help="Scénarios à exécuter, séparés par virgule (ex: CRASH-2008,COVID-2020)",
    )
    test_group.add_argument(
        "--ftmo",
        action="store_true",
        help="Simuler le challenge FTMO (par symbole)",
    )
    test_group.add_argument(
        "--ftmo-portfolio",
        action="store_true",
        help="Simuler le challenge FTMO sur le portefeuille multi-symboles combiné",
    )

    # Configuration
    cfg_group = parser.add_argument_group("Configuration")
    cfg_group.add_argument(
        "--config",
        type=str,
        default=None,
        help="Charger config depuis un fichier YAML (ex: config/backtest.yaml)",
    )

    # Reporting
    report_group = parser.add_argument_group("Reporting")
    report_group.add_argument(
        "--full-report",
        action="store_true",
        help="Générer le rapport complet (charts + PDF + HTML)",
    )
    report_group.add_argument(
        "--output-dir",
        type=str,
        default="backtest/results",
        help="Dossier de sortie (défaut: backtest/results)",
    )
    report_group.add_argument(
        "--export-json",
        type=str,
        default=None,
        help="Exporter les métriques en JSON",
    )
    report_group.add_argument(
        "--export-csv",
        action="store_true",
        help="Exporter les trades en CSV",
    )
    report_group.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Logs détaillés",
    )

    return parser.parse_args()


# ═══════════════════════════════════════════════════════════════════════════
# Fonctions principales
# ═══════════════════════════════════════════════════════════════════════════


def resolve_symbols(args) -> list[str]:
    """Résout la liste des symboles à partir des arguments CLI."""
    if args.list_symbols:
        return []

    symbols = []

    if args.group:
        group_map = {
            "major_fx": SYMBOLS_MAJOR_FX,
            "minor_fx": SYMBOLS_MINOR_FX,
            "metals": SYMBOLS_METALS,
            "crypto": SYMBOLS_CRYPTO,
            "all": ALL_SYMBOLS,
        }
        symbols = group_map.get(args.group, [])
        logger.info(f"Groupe '{args.group}': {len(symbols)} symboles")

    if args.symbol:
        symbols.append(args.symbol.upper())

    if args.symbols:
        for s in args.symbols.split(","):
            s = s.strip().upper()
            if s:
                symbols.append(s)

    # Dédupliquer tout en gardant l'ordre
    seen = set()
    unique = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            unique.append(s)

    return unique


def build_costs_config(args) -> dict:
    """Construit la configuration des coûts selon le mode."""
    if args.costs == "raw":
        return {
            "spread_multiplier": 0.0,
            "commission_multiplier": 0.0,
            "swap_enabled": False,
            "slippage_enabled": False,
        }
    elif args.costs == "conservative":
        return {
            "spread_multiplier": 3.0,
            "commission_multiplier": 2.0,
            "swap_enabled": True,
            "slippage_enabled": True,
            "slippage_mean_mult": 3.0,
            "slippage_std_mult": 3.0,
        }
    else:  # realistic
        return {
            "spread_multiplier": 1.0,
            "commission_multiplier": 1.0,
            "swap_enabled": True,
            "slippage_enabled": True,
            "slippage_mean_mult": 1.0,
            "slippage_std_mult": 1.0,
        }


def load_config_from_yaml(args) -> argparse.Namespace:
    """Charge les paramètres depuis un fichier YAML et surcharge args."""
    if not args.config:
        return args

    config_path = Path(args.config)
    if not config_path.exists():
        logger.warning(f"Fichier config introuvable: {config_path}")
        return args

    if yaml is None:
        logger.warning("PyYAML non installé, impossible de charger la config")
        return args

    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    logger.info(f"Configuration chargée depuis {config_path}")

    # Capital
    capital = cfg.get("capital", {})
    if capital.get("initial_balance") and args.capital == 200_000.0:
        args.capital = float(capital["initial_balance"])
    if capital.get("risk_per_trade") and args.risk == 0.0044:
        args.risk = float(capital["risk_per_trade"])

    # Symboles — si --config est utilisé sans --symbol/--group explicite, on prend TOUS les symboles du YAML
    symbols_cfg = cfg.get("symbols", {})
    if not args.symbol and not args.symbols and not args.group:
        # Vérifier si le YAML définit des groupes → utiliser "all"
        has_groups = any(k in symbols_cfg for k in ("major_fx", "minor_fx", "metals", "crypto", "indices", "energy"))
        if has_groups:
            args.group = "all"
            logger.info("Config YAML: tous les groupes de symboles activés")

    # Timeframe par défaut
    if symbols_cfg.get("default_timeframe") and args.tf == "H1":
        args.tf = symbols_cfg["default_timeframe"]

    # Dates
    if symbols_cfg.get("start_date") and not args.start:
        args.start = str(symbols_cfg["start_date"])
    if symbols_cfg.get("end_date") and not args.end:
        args.end = str(symbols_cfg["end_date"])

    # Stratégie
    strat_cfg = cfg.get("strategy", {})
    if strat_cfg.get("name") and args.strategy == "mom20x3":
        args.strategy = strat_cfg["name"]

    # Coûts
    costs_cfg = cfg.get("costs", {})
    if costs_cfg.get("mode") and args.costs == "realistic":
        args.costs = costs_cfg["mode"]

    # Reporting
    report_cfg = cfg.get("reporting", {})
    if report_cfg.get("output_dir") and args.output_dir == "backtest/results":
        args.output_dir = report_cfg["output_dir"]
    if report_cfg.get("full_report") and not args.full_report:
        args.full_report = True
    if report_cfg.get("export_json") and not args.export_json:
        args.export_json = report_cfg["export_json"]
    if report_cfg.get("export_csv") and not args.export_csv:
        args.export_csv = True

    # Walk-Forward
    rob_cfg = cfg.get("robustness", {})
    wf_cfg = rob_cfg.get("walk_forward", {})
    if wf_cfg.get("n_splits") and args.wf_splits == 5:
        args.wf_splits = int(wf_cfg["n_splits"])

    # Monte Carlo
    mc_cfg = rob_cfg.get("monte_carlo", {})
    if mc_cfg.get("n_simulations") and args.mc_simulations == 1000:
        args.mc_simulations = int(mc_cfg["n_simulations"])

    # Stress Tests
    st_cfg = rob_cfg.get("stress_tests", {})
    if st_cfg.get("sample_bars") and not args.stress_sample_bars:
        args.stress_sample_bars = int(st_cfg["sample_bars"])
    if st_cfg.get("scenarios") and not args.stress_scenarios:
        args.stress_scenarios = ",".join(st_cfg["scenarios"])

    logger.debug(f"Config YAML appliquée: start={args.start}, end={args.end}, costs={args.costs}")
    return args


def create_strategies(strategy_name: str):
    """Crée les instances de stratégies demandées."""
    if strategy_name == "all":
        return {name: cls() for name, cls in STRATEGY_MAP.items()}
    elif strategy_name in STRATEGY_MAP:
        return {strategy_name: STRATEGY_MAP[strategy_name]()}
    else:
        logger.error(f"Stratégie inconnue: {strategy_name}")
        sys.exit(1)


def run_backtest(
    symbol: str,
    strategy,
    data,
    timeframe: str,
    config: BacktestConfig,
    dl: DataLoader,
) -> object:
    """Exécute un backtest simple pour un symbole."""
    # Ajouter les indicateurs
    if "adx_14" not in data.columns:
        data = dl.add_indicators(data)

    # Créer le moteur
    engine = BacktestEngine(config)

    # Exécuter
    logger.info(f"Backtest {symbol} {timeframe}...")
    t0 = time.time()
    result = engine.run(symbol=symbol, strategy=strategy, data=data, timeframe=timeframe)
    elapsed = time.time() - t0

    logger.info(
        f"Terminé en {elapsed:.1f}s: {result.total_trades} trades, "
        f"WR {result.win_rate:.1f}%, PnL ${result.net_profit:.2f}"
    )

    return result


def run_robustness_tests(
    engine: BacktestEngine,
    strategy,
    data,
    symbol: str,
    timeframe: str,
    args,
) -> dict:
    """Exécute les tests de robustesse demandés."""
    results = {}

    if args.walk_forward:
        logger.info(f"Walk-Forward Analysis ({args.wf_splits} folds)...")
        wf = WalkForwardAnalyzer(
            n_splits=args.wf_splits,
            verbose=args.verbose,
        )
        wf_result = wf.run(engine, strategy, data, symbol, timeframe)
        results["walk_forward"] = wf_result
        print(wf.summary(wf_result))

    if args.monte_carlo:
        logger.info(f"Monte Carlo Simulation ({args.mc_simulations}x)...")
        mc = MonteCarloSimulator(
            n_simulations=args.mc_simulations,
            verbose=args.verbose,
        )
        # D'abord un backtest normal pour avoir des trades
        bt_result = engine.run(symbol, strategy, data, timeframe)
        mc_result = mc.run(bt_result.trades, args.capital)
        results["monte_carlo"] = mc_result
        print(mc.summary(mc_result))

    if args.stress_test:
        # Résoudre les scénarios
        stress_scenarios = None
        if args.stress_scenarios:
            stress_scenarios = [s.strip() for s in args.stress_scenarios.split(",")]

        n_scenarios = len(stress_scenarios) if stress_scenarios else 4
        logger.info(f"Stress Tests ({n_scenarios} scénarios, sample_bars={args.stress_sample_bars or 'toutes'})...")
        st = StressTester(
            scenarios=stress_scenarios,
            verbose=args.verbose,
            sample_bars=args.stress_sample_bars,
        )
        t0 = time.time()
        stress_result = st.run(engine, strategy, data, symbol, timeframe)
        elapsed = time.time() - t0
        results["stress_test"] = stress_result
        logger.info(f"Stress tests terminés en {elapsed:.0f}s")
        print(st.summary(stress_result))

    if args.ftmo:
        logger.info("Simulation FTMO Challenge...")
        ftmo = FTMOChallengeSimulator(
            FTMOConfig(account_size=args.capital),
            verbose=args.verbose,
        )
        bt_result = engine.run(symbol, strategy, data, timeframe)
        if bt_result.trades:
            ftmo_verdict = ftmo.evaluate(
                bt_result.trades,
                bt_result.equity_curve,
                bt_result.dates,
                args.capital,
            )
            results["ftmo"] = ftmo_verdict
            print(ftmo.summary(ftmo_verdict))

    return results


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════


def main():
    args = parse_args()

    # Charger la config YAML si demandée
    if args.config:
        args = load_config_from_yaml(args)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # ─── Lister les symboles ──────────────────────────────────────────
    if args.list_symbols:
        print("\n📊 Symboles disponibles pour le backtest :\n")
        for group_name, group_symbols in [
            ("Forex Majeurs", SYMBOLS_MAJOR_FX),
            ("Forex Mineurs", SYMBOLS_MINOR_FX),
            ("Métaux", SYMBOLS_METALS),
            ("Cryptos", SYMBOLS_CRYPTO),
            ("Indices", SYMBOLS_INDICES),
            ("Énergies", SYMBOLS_ENERGY),
        ]:
            print(f"  {group_name}: {', '.join(group_symbols)}")
        print(f"\n  Total: {len(ALL_SYMBOLS)} symboles")
        print()
        return

    # ─── Résoudre les symboles ─────────────────────────────────────────
    symbols = resolve_symbols(args)
    if not symbols:
        logger.error("Aucun symbole spécifié. Utilisez --symbol, --symbols, --group ou --list-symbols")
        sys.exit(1)

    logger.info(f"Symboles: {', '.join(symbols)}")
    logger.info(f"Timeframe: {args.tf} | Stratégie: {args.strategy} | Coûts: {args.costs}")

    # ─── Initialisation ────────────────────────────────────────────────
    dl = DataLoader()
    costs_config = build_costs_config(args)

    bt_config = BacktestConfig(
        initial_balance=args.capital,
        risk_per_trade=args.risk,
        costs_config=costs_config,
    )

    strategies = create_strategies(args.strategy)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report_gen = ReportGenerator()
    chart_gen = ChartGenerator(output_dir=output_dir)

    all_results = {}

    # ─── Boucle symboles × stratégies ──────────────────────────────────
    for symbol in symbols:
        for strat_name, strategy in strategies.items():
            key = f"{symbol}_{args.tf}_{strat_name}"

            try:
                # Charger les données
                data = dl.load(
                    symbol=symbol,
                    timeframe=args.tf,
                    start=args.start,
                    end=args.end,
                )
                if data.empty:
                    logger.warning(f"Pas de données pour {symbol} {args.tf}")
                    continue

                # Nettoyer
                data = dl.clean(data, symbol=symbol)

                # Ajouter indicateurs
                data = dl.add_indicators(data)

                # Backtest simple
                result = run_backtest(symbol, strategy, data, args.tf, bt_config, dl)
                all_results[key] = result

                # Afficher le rapport
                print("\n" + report_gen.format_report(result, symbol, args.tf, strat_name))

                # Tests de robustesse
                engine = BacktestEngine(bt_config)
                robustness = run_robustness_tests(engine, strategy, data, symbol, args.tf, args)
                if robustness:
                    all_results[f"{key}_robustness"] = robustness

            except FileNotFoundError as e:
                logger.warning(f"Données manquantes pour {symbol} {args.tf}: {e}")
                continue
            except Exception as e:
                logger.error(f"Erreur sur {symbol} {args.tf}: {e}", exc_info=args.verbose)
                continue

    # ─── FTMO Portfolio (multi-symboles combinés) ─────────────────────
    if args.ftmo_portfolio and len(all_results) >= 1:
        logger.info("=" * 60)
        logger.info("SIMULATION FTMO PORTFOLIO MULTI-SYMBOLES")
        logger.info("=" * 60)

        # Regrouper les résultats par stratégie
        portfolio_groups = {}
        for key, result in all_results.items():
            if hasattr(result, "trades"):
                parts = key.split("_")
                if len(parts) >= 3:
                    strat = parts[-1]
                else:
                    strat = "mom20x3"
                if strat not in portfolio_groups:
                    portfolio_groups[strat] = {}
                portfolio_groups[strat][parts[0]] = result

        for strat_name, sym_results in portfolio_groups.items():
            logger.info(f"  Portfolio {strat_name}: {len(sym_results)} symboles")

            ftmo_portfolio = FTMOPortfolioSimulator(
                config=FTMOConfig(account_size=args.capital),
                verbose=args.verbose,
            )
            verdict = ftmo_portfolio.evaluate_portfolio(
                symbol_results=sym_results,
                balance=args.capital,
            )
            print("\n" + FTMOPortfolioSimulator.portfolio_summary(verdict, sym_results))

            # Stocker dans all_results pour l'export
            all_results[f"FTMO_PORTFOLIO_{strat_name}"] = {
                "ftmo_verdict": verdict,
                "metrics": {
                    "n": verdict.total_trades,
                    "win_rate": verdict.win_rate,
                    "net_profit": verdict.total_pnl,
                    "profit_factor": verdict.profit_factor,
                    "max_dd_pct": verdict.max_dd_pct,
                    "return_pct": verdict.total_pnl_pct,
                },
            }

    # ─── Rapport comparatif multi-symboles ──────────────────────────────
    if len(all_results) > 1:
        # Extraire les métriques pour la comparaison
        comparison = {}
        for key, result in all_results.items():
            if hasattr(result, "metrics"):
                comparison[key] = result.metrics
            elif isinstance(result, dict) and "error" not in result:
                comparison[key] = result

        if comparison:
            print("\n" + report_gen.format_comparison(comparison, "Comparaison Multi-Symboles"))

    # ─── Exports ───────────────────────────────────────────────────────
    for key, result in all_results.items():
        if args.export_json and hasattr(result, "metrics"):
            json_path = Path(args.export_json)
            if len(all_results) > 1:
                json_path = json_path.parent / f"{json_path.stem}_{key}{json_path.suffix}"
            report_gen.export_json(result, str(json_path))

        if args.export_csv and hasattr(result, "trades"):
            csv_path = output_dir / f"trades_{key}.csv"
            report_gen.export_csv(result.trades, str(csv_path))

        if args.full_report and hasattr(result, "equity_curve"):
            charts = chart_gen.full_report(result, key)
            if charts:
                logger.info(f"Rapport visuel: {len(charts)} fichiers dans {output_dir}")

    # ─── Résumé exécutif ───────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  RÉSUMÉ EXÉCUTIF")
    print("=" * 60)
    for key, result in all_results.items():
        if hasattr(result, "metrics"):
            print(f"  {report_gen.executive_summary(result)}")
    print("=" * 60)

    logger.info("✅ Backtest terminé")


if __name__ == "__main__":
    main()
