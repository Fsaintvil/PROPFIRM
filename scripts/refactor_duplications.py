#!/usr/bin/env python3
"""
Refactoring Script — Duplications, conflits et code mort
=========================================================
Usage:
    python scripts/refactor_duplications.py --dry-run   # Voir les changements sans appliquer
    python scripts/refactor_duplications.py --apply      # Appliquer les changements
    python scripts/refactor_duplications.py --revert     # Revert les changements (TODO)
"""

import os
import sys
import shutil
import argparse
import json
from pathlib import Path

BACKUP_DIR = Path(".refactor_backups")
PROJECT_ROOT = Path(__file__).parent.parent


def backup_file(path):
    """Sauvegarde un fichier avant modification."""
    rel = os.path.relpath(path, PROJECT_ROOT)
    backup = BACKUP_DIR / rel
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup)
    return backup


def restore_backup(path):
    """Restaure un fichier depuis sa sauvegarde."""
    rel = os.path.relpath(path, PROJECT_ROOT)
    backup = BACKUP_DIR / rel
    if backup.exists():
        shutil.copy2(backup, path)
        return True
    return False


def remove_risk_per_trade_from_strategy(dry_run=True):
    """🔴 Fix #1: Supprime risk_per_trade redondant de strategy.py SYMBOL_CONFIG.

    Garde les overrides explicites (ex: ETHUSD risk_per_trade=0.001).
    Supprime les valeurs par défaut (0.004) qui dupliquent le YAML.
    """
    path = PROJECT_ROOT / "engine_simple" / "strategy.py"
    with open(path) as f:
        lines = f.readlines()

    changes = []
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Cherche les lignes "risk_per_trade": 0.004, dans un contexte SYMBOL_CONFIG
        stripped = line.strip()
        if ('"risk_per_trade"' in stripped or "'risk_per_trade'" in stripped) and "0.004" in stripped:
            # Vérifie si c'est dans DEFAULT_SYMBOL_CONFIG ou un symbole standard
            # On garde seulement les risk_per_trade différents de 0.004 (ex: 0.001 pour ETHUSD)
            prev_context = "".join(lines[max(0, i - 5) : i]).lower()
            if "default" in prev_context or True:  # Default or any standard symbol
                # Check if this is a special case (not 0.004)
                if "0.001" in stripped or "0.002" in stripped or "0.003" in stripped:
                    new_lines.append(line)
                    changes.append(f"  GARDÉ (special): {stripped}")
                else:
                    changes.append(f"  SUPPRIMÉ: {stripped}")
                    # Skip this line
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)
        i += 1

    if dry_run:
        print(f"\n🔴 Fix #1: strategy.py — {len(changes)} changements")
        for c in changes:
            print(c)
        return None
    else:
        backup_file(path)
        with open(path, "w") as f:
            f.writelines(new_lines)
        print(f"✅ strategy.py: {len(changes)} risk_per_trade supprimés")
        return path


def fix_portfolio_controller(dry_run=True):
    """🔴 Fix #2: Supprime MAX_POSITIONS_TOTAL hardcodé dans portfolio_controller.py.

    Remplace par l'import depuis config_simple.
    """
    path = PROJECT_ROOT / "engine_simple" / "portfolio_controller.py"
    with open(path) as f:
        content = f.read()

    changes = []

    # 1. Remplacer la constante hardcodée
    old_const = "MAX_POSITIONS_TOTAL = 18  # 🔧 ×1.5 (3 Juillet): 12→18 (11 symboles actifs)"
    new_const = "from config_simple import MAX_POSITIONS as MAX_POSITIONS_TOTAL"
    if old_const in content:
        content = content.replace(old_const, new_const)
        changes.append("MAX_POSITIONS_TOTAL → import depuis config_simple")

    old_const2 = "MAX_POSITIONS_PER_SYMBOL = 6  # 🔧 ×1.5: 4→6 (plus de marge par symbole)"
    new_const2 = "from config_simple import MAX_POSITIONS_PER_SYMBOL"
    if old_const2 in content:
        content = content.replace(old_const2, new_const2)
        changes.append("MAX_POSITIONS_PER_SYMBOL → import depuis config_simple")

    old_const3 = "MAX_POSITIONS_PER_DIRECTION = 9  # 🔧 ×1.5: 6→9"
    new_const3 = "MAX_POSITIONS_PER_DIRECTION = MAX_POSITIONS_TOTAL // 2  # Dynamique depuis config_simple"
    if old_const3 in content:
        content = content.replace(old_const3, new_const3)
        changes.append("MAX_POSITIONS_PER_DIRECTION → dynamique")

    if dry_run:
        print(f"\n🔴 Fix #2: portfolio_controller.py — {len(changes)} changements")
        for c in changes:
            print(f"  {c}")
        return None
    else:
        if changes:
            backup_file(path)
            with open(path, "w") as f:
                f.write(content)
        print(f"✅ portfolio_controller.py: {len(changes)} changements")
        return path


def fix_nas100_to_us100(dry_run=True):
    """🔴 Fix #3: Remplace NAS100.cash → US100.cash dans les scripts de backtest."""
    patterns = {
        "scripts/backtest_with_costs.py": "NAS100.cash",
        "scripts/backtest_universe.py": "NAS100.cash",
        "scripts/backtest_volume_indicators.py": "NAS100.cash",
        "scripts/walk_forward_16y.py": "NAS100.cash",
    }

    changes = []
    for rel_path, old_name in patterns.items():
        path = PROJECT_ROOT / rel_path
        if not path.exists():
            continue
        with open(path) as f:
            content = f.read()

        # Chercher dans get_pip_info et ailleurs
        if "NAS100.cash" in content or "nas100" in content.lower():
            count = content.count("NAS100.cash")
            if dry_run:
                changes.append(f"  {rel_path}: {count} occurrences de NAS100.cash")
            else:
                backup_file(path)
                content = content.replace("NAS100.cash", "US100.cash")
                with open(path, "w") as f:
                    f.write(content)
                changes.append(f"  {rel_path}: {count} occurrences corrigées")

    if dry_run:
        print(f"\n🔴 Fix #3: NAS100.cash → US100.cash — {len(changes)} fichiers")
        for c in changes:
            print(c)
        return None
    else:
        print(f"✅ NAS100.cash→US100.cash: {len(changes)} fichiers")
        return True


def create_backtest_utils(dry_run=True):
    """🟡 Fix #4-6: Crée engine_simple/backtest_utils.py avec les fonctions partagées.

    Extrait:
    - get_pip_info(symbol)
    - get_pip_value_per_lot(symbol)
    - get_contract_size(symbol)
    - SimTrade class
    - precalc_atr_and_adx()
    - THRESHOLD / TRAILING_LEVELS constants
    - compute_metrics()
    """
    utils_path = PROJECT_ROOT / "engine_simple" / "backtest_utils.py"

    content = '''"""
Backtest Utils — Fonctions partagées entre les scripts de backtest.
Centralise le code dupliqué : SimTrade, get_pip_info, THRESHOLD, etc.

Usage:
    from engine_simple.backtest_utils import SimTrade, get_pip_info, compute_metrics
"""

import numpy as np

# ═══════════════════════════════════════════════════════════════════════════════
# THRESHOLD CONSTANTS — SOURCE UNIQUE (Juillet 2026)
# Synchronisé avec engine_simple/strategy.py et config/default.yaml
# ═══════════════════════════════════════════════════════════════════════════════
THRESHOLD_TRENDING = 2.5
THRESHOLD_RANGING = 2.0
THRESHOLD_MAX = 3.0   # ⚠️ Doit correspondre à strategy.py (3.0, pas 2.5)
THRESHOLD_MIN = 1.5

SL_ATR_TRENDING = 2.0
TP_ATR_TRENDING = 5.0
SL_ATR_RANGING = 1.5
TP_ATR_RANGING = 4.0

# ═══════════════════════════════════════════════════════════════════════════════
# TRAILING LEVELS — SOURCE UNIQUE
# ═══════════════════════════════════════════════════════════════════════════════
TRAILING_LEVELS = {
    "RANGING": [(1.0, 0.50), (2.0, 0.35), (3.0, 0.20), (5.0, 0.10)],
    "TREND_UP": [(1.0, 0.80), (2.0, 0.50), (3.0, 0.30), (5.0, 0.15)],
    "TREND_DOWN": [(1.0, 0.80), (2.0, 0.50), (3.0, 0.30), (5.0, 0.15)],
    "HIGH_VOL": [(1.0, 1.00), (2.0, 0.70), (3.0, 0.50), (5.0, 0.25)],
    "LOW_VOL": [(1.0, 0.40), (2.0, 0.25), (3.0, 0.15), (5.0, 0.08)],
}

# ═══════════════════════════════════════════════════════════════════════════════
# SYMBOL METADATA
# ═══════════════════════════════════════════════════════════════════════════════

def get_pip_info(symbol):
    """Retourne (pip_size, pip_value) pour un symbole."""
    if symbol in ("XAUUSD", "XAGUSD"):
        return 0.01, 1.0
    if symbol in ("US500.cash", "JP225.cash", "US30.cash", "US100.cash"):
        return 0.01, 1.0
    if symbol in ("USOIL.cash", "UKOIL.cash"):
        return 0.01, 1.0
    if symbol in ("BTCUSD", "ETHUSD", "SOLUSD", "LNKUSD", "BNBUSD"):
        return 0.01, 1.0
    if symbol in ("NATGAS.cash", "GER40.cash", "UK100.cash"):
        return 0.01, 1.0
    return 0.0001, 10.0


def get_pip_value_per_lot(symbol):
    """Retourne la valeur en $ d'un pip pour 1 lot standard."""
    _, pv = get_pip_info(symbol)
    return pv


def get_contract_size(symbol):
    """Retourne la taille d'un contrat standard."""
    if symbol in ("XAUUSD", "XAGUSD"):
        return 100
    if symbol in ("US500.cash", "JP225.cash", "US30.cash", "US100.cash",
                  "GER40.cash", "UK100.cash"):
        return 1
    if symbol in ("USOIL.cash", "UKOIL.cash", "NATGAS.cash"):
        return 100
    if symbol in ("BTCUSD", "ETHUSD", "SOLUSD", "LNKUSD", "BNBUSD"):
        return 1
    return 100_000


# ═══════════════════════════════════════════════════════════════════════════════
# PRECALCULATE INDICATORS
# ═══════════════════════════════════════════════════════════════════════════════

def precalc_atr_and_adx(high, low, close, period=14):
    """Pré-calcule ATR et ADX pour toute la série.
    
    Returns:
        tuple: (atr_arr, adx_arr, pos_di, neg_di) — tableaux numpy
    """
    n = len(close)
    if n < period * 2:
        return np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    
    # True Range
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1])
        )
    )
    
    # ATR (SMA of TR)
    atr_arr = np.full(n, np.nan)
    for i in range(period, n):
        atr_arr[i] = np.mean(tr[i - period:i])
    
    # ADX with +DI/-DI
    up_move = high[1:] - high[:-1]
    down_move = low[:-1] - low[1:]
    pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    
    tr_smoothed = np.full(n, np.nan)
    pos_smoothed = np.full(n, np.nan)
    neg_smoothed = np.full(n, np.nan)
    
    for i in range(period, n):
        tr_smoothed[i] = np.mean(tr[i - period:i])
        pos_smoothed[i] = np.mean(pos_dm[i - period:i])
        neg_smoothed[i] = np.mean(neg_dm[i - period:i])
    
    pos_di = 100 * pos_smoothed / np.maximum(tr_smoothed, 1e-10)
    neg_di = 100 * neg_smoothed / np.maximum(tr_smoothed, 1e-10)
    
    dx = np.abs(pos_di - neg_di) / np.maximum(pos_di + neg_di, 1e-10) * 100
    adx_arr = np.full(n, np.nan)
    for i in range(period * 2, n):
        adx_arr[i] = np.mean(dx[i - period:i])
    
    return atr_arr, adx_arr, pos_di, neg_di


# ═══════════════════════════════════════════════════════════════════════════════
# SIMTRADE CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class SimTrade:
    """Classe de trading simulé pour backtest — version unifiée (Juillet 2026).
    
    Supporte :
    - Coûts (spread, slippage, commission)
    - Trailing stop
    - Partial TP
    """
    __slots__ = (
        "symbol", "timeframe", "action", "entry", "sl", "tp",
        "atr_val", "regime", "open_bar", "open_time", "direction",
        "closed", "result", "profit_usd", "profit_pct",
        "peak_price", "trailing_sl", "partial_closed",
        "bars_held", "close_time", "close_price", "lot",
        "_pip_size", "_pip_value", "_contract_size",
        "cost_pips", "commission_usd", "spread_cost_pips",
        "spread_from_data", "profit_usd_cost", "profit_pct_cost",
    )

    def __init__(self, symbol, timeframe, action, entry, sl, tp,
                 atr_val, regime, bar_idx, bar_time, balance,
                 spread_pts=None):
        self.symbol = symbol
        self.timeframe = timeframe
        self.action = action
        self.entry = entry
        self.sl = sl
        self.tp = tp
        self.atr_val = atr_val
        self.regime = regime
        self.open_bar = bar_idx
        self.open_time = bar_time
        self.direction = 0 if action == "BUY" else 1
        self.closed = False
        self.result = None
        self.profit_usd = 0.0
        self.profit_pct = 0.0
        self.profit_usd_cost = 0.0
        self.profit_pct_cost = 0.0
        self.peak_price = entry
        self.trailing_sl = sl
        self.partial_closed = False
        self.bars_held = 0
        self.close_time = None
        self.close_price = entry
        self.lot = 0.01
        self.cost_pips = 0.0
        self.commission_usd = 0.0
        self.spread_cost_pips = 0.0
        self.spread_from_data = spread_pts if (spread_pts is not None and spread_pts > 0) else None
        self._pip_size, self._pip_value = get_pip_info(symbol)
        self._contract_size = get_contract_size(symbol)

    def get_pip_info(self):
        return self._pip_size, self._pip_value


def compute_metrics(closed_trades, use_cost=False):
    """Calcule les métriques de performance pour une liste de trades fermés.
    
    Args:
        closed_trades: Liste de SimTrade ou dicts avec 'profit_usd'/'profit_usd_cost'
        use_cost: Si True, utilise profit_usd_cost (avec coûts)
    
    Returns:
        dict avec win_rate, profit_factor, total_pnl, max_drawdown, etc.
    """
    if not closed_trades:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "total_pnl": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
        }
    
    # Extraire les PnL selon la source
    pnls = []
    for t in closed_trades:
        if hasattr(t, 'profit_usd_cost') and use_cost:
            pnls.append(t.profit_usd_cost)
        elif hasattr(t, 'profit_usd'):
            pnls.append(t.profit_usd)
        elif hasattr(t, 'profit_pct'):
            pnls.append(t.profit_pct)
        elif isinstance(t, dict):
            pnls.append(t.get('profit_usd_cost' if use_cost else 'profit_usd', 0))
        else:
            pnls.append(0)
    
    pnls = np.array(pnls)
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    
    total_pnl = float(pnls.sum())
    win_rate = float(len(wins) / len(pnls)) if len(pnls) > 0 else 0
    profit_factor = float(abs(wins.sum() / max(abs(losses.sum()), 1e-10))) if len(losses) > 0 else float('inf')
    
    # Drawdown (simple peak-to-trough)
    cumsum = np.cumsum(pnls)
    peak = np.maximum.accumulate(cumsum)
    dd = cumsum - peak
    max_dd_pct = float(abs(min(dd)) / max(abs(peak[-1]), 1)) if len(dd) > 0 else 0
    
    return {
        "total_trades": len(pnls),
        "win_rate": round(win_rate * 100, 1),
        "profit_factor": round(profit_factor, 2),
        "total_pnl": round(total_pnl, 2),
        "max_drawdown_pct": round(max_dd_pct * 100, 1),
        "avg_win": round(float(wins.mean()), 2) if len(wins) > 0 else 0,
        "avg_loss": round(float(losses.mean()), 2) if len(losses) > 0 else 0,
        "total_wins": len(wins),
        "total_losses": len(losses),
    }


if __name__ == "__main__":
    # Test rapide
    pip, pv = get_pip_info("EURUSD")
    print(f"EURUSD: pip={pip}, pip_value={pv}")
    pip, pv = get_pip_info("XAUUSD")
    print(f"XAUUSD: pip={pip}, pip_value={pv}")
    pip, pv = get_pip_info("US100.cash")
    print(f"US100.cash: pip={pip}, pip_value={pv}")
    print("✅ backtest_utils.py loaded successfully")
'''

    if dry_run:
        print(f"\n🟡 Fix #4-6: Créer engine_simple/backtest_utils.py ({len(content.split(chr(10)))} lignes)")
        if utils_path.exists():
            print("  ⚠️ Le fichier existe déjà !")
        else:
            print("  ✅ Nouveau fichier à créer")
        return None
    else:
        if not utils_path.exists():
            with open(utils_path, "w") as f:
                f.write(content)
            print(f"✅ backtest_utils.py créé ({len(content.split(chr(10)))} lignes)")
        else:
            print(f"⏭️ backtest_utils.py existe déjà, skip")
        return utils_path


def run_tests():
    """Exécute les tests après refactoring."""
    print("\n🧪 Exécution des tests...")
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=line"], capture_output=True, text=True, timeout=120
    )
    print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
    if result.returncode != 0:
        print(f"❌ {result.stderr[-500:]}" if result.stderr else "")
        return False
    print("✅ Tous les tests passent")
    return True


def main():
    parser = argparse.ArgumentParser(description="Refactoring des duplications")
    parser.add_argument("--dry-run", action="store_true", help="Simulation uniquement")
    parser.add_argument("--apply", action="store_true", help="Appliquer les changements")
    parser.add_argument("--backtest-utils", action="store_true", help="Créer backtest_utils.py seulement")
    parser.add_argument("--run-tests", action="store_true", help="Exécuter les tests après")
    args = parser.parse_args()

    if not args.dry_run and not args.apply and not args.backtest_utils:
        print("Usage: python scripts/refactor_duplications.py --dry-run")
        print("       python scripts/refactor_duplications.py --apply")
        print("       python scripts/refactor_duplications.py --backtest-utils")
        return

    is_dry = args.dry_run or args.backtest_utils

    if is_dry:
        print("\n" + "=" * 60)
        print("  🔍 DRY RUN — Aucun changement appliqué")
        print("=" * 60)

    # Phase 1: 🔴 Critiques
    remove_risk_per_trade_from_strategy(dry_run=is_dry)
    fix_portfolio_controller(dry_run=is_dry)
    fix_nas100_to_us100(dry_run=is_dry)

    # Phase 2: 🟡 Factorisation
    create_backtest_utils(dry_run=(not args.backtest_utils and not args.apply))

    if is_dry:
        print("\n" + "=" * 60)
        print("  DRY RUN terminé. Pour appliquer: --apply")
        print("=" * 60)
        return

    if args.apply or args.backtest_utils:
        # Phase 1: Appliquer
        remove_risk_per_trade_from_strategy(dry_run=False)
        fix_portfolio_controller(dry_run=False)
        fix_nas100_to_us100(dry_run=False)

        # Phase 2
        create_backtest_utils(dry_run=False)

        # Tests
        if args.run_tests:
            run_tests()

    print("\n✅ Refactoring terminé")


if __name__ == "__main__":
    main()
