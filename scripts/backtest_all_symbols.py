"""
Backtest MOM20x3 sur données H1 CSV (sans MT5) — tous les symboles disponibles.

Utilise les données H1 de runtime/market_h1_2026/ et la stratégie
engine_simple/strategy.py::mom20x3_signal() pour un backtest propre,
indépendant de MT5.

Usage:
    python scripts/backtest_all_symbols.py
    python scripts/backtest_all_symbols.py --csv runtime/trades_backtest.csv  # exporter les trades
"""
import argparse
import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine_simple.indicators import atr
from engine_simple.strategy import mom20x3_signal


# ── Configuration du backtest ──
INITIAL_BALANCE = 200_000.0
RISK_PER_TRADE = 0.0044      # 0.44% du capital par trade
MIN_BARS = 60                 # bars minimum pour warmup
MAX_SPREAD_PIPS = 5           # pips max pour entrer
BE_BUFFER_ATR = 0.80          # buffer pour break-even après partial TP


class SimTrade:
    """Simule un trade unique avec SL/TP ATR, trailing, partial TP."""

    def __init__(self, symbol: str, action: str, entry: float, sl: float, tp: float,
                 atr_val: float, regime: str, bar_idx: int, bar_time, balance: float):
        self.symbol = symbol
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
        self.result = None          # "SL", "TP", "TIMEOUT"
        self.profit_pct = 0.0
        self.profit_usd = 0.0
        self.peak_price = entry
        self.trailing_sl = sl
        self.partial_closed = False
        self.bars_held = 0
        self.close_time = None
        self.close_price = entry

        # Déterminer pip_size et pip_value selon le type d'instrument
        if symbol in ('XAUUSD', 'XAGUSD'):
            pip_size = 0.01          # gold: $0.01 par tick
            pip_value_per_lot = 1.0  # 1 lot = 100 oz, $1 par $0.01
        elif symbol in ('US500.cash', 'JP225.cash', 'US30.cash', 'NAS100.cash'):
            pip_size = 0.01
            pip_value_per_lot = 1.0
        elif symbol in ('USOIL.cash', 'UKOIL.cash', 'BTCUSD', 'ETHUSD'):
            pip_size = 0.01
            pip_value_per_lot = 1.0
        else:  # Forex standard
            pip_size = 0.0001
            pip_value_per_lot = 10.0  # 1 lot = $10/pip

        price_dist = abs(entry - sl)
        if price_dist > 0:
            risk_usd = balance * RISK_PER_TRADE
            risk_in_pips = price_dist / pip_size
            self.lot = risk_usd / (risk_in_pips * pip_value_per_lot) if risk_in_pips > 0 else 0.01
        else:
            self.lot = 0.01

        self.lot = max(0.01, min(1.0, self.lot))
        self._pip_size = pip_size
        self.usd_per_pip = self.lot * pip_value_per_lot

    def check_sl_tp(self, high: float, low: float, close: float, bar_idx: int, bar_time):
        if self.closed:
            return
        if self.direction == 0:  # BUY
            if low <= self.trailing_sl:
                self.closed = True
                self.close_price = self.trailing_sl
                self.result = "SL"
            elif high >= self.tp:
                self.closed = True
                self.close_price = self.tp
                self.result = "TP"
        else:  # SELL
            if high >= self.trailing_sl:
                self.closed = True
                self.close_price = self.trailing_sl
                self.result = "SL"
            elif low <= self.tp:
                self.closed = True
                self.close_price = self.tp
                self.result = "TP"

        if self.closed:
            self.close_time = bar_time
            self.bars_held = bar_idx - self.open_bar
            if self.direction == 0:
                self.profit_pct = (self.close_price - self.entry) / self.entry
            else:
                self.profit_pct = (self.entry - self.close_price) / self.entry
            pips = (self.close_price - self.entry) * (1 if self.direction == 0 else -1) / self._pip_size
            self.profit_usd = pips * self.usd_per_pip

    def update_peak(self, high: float, low: float, close: float):
        if self.closed:
            return
        if self.direction == 0:  # BUY
            self.peak_price = max(self.peak_price, high)
        else:  # SELL
            self.peak_price = min(self.peak_price, low)

    def update_trailing(self, atr_val: float):
        """Trailing stop ATR adaptatif par régime."""
        if self.closed or atr_val <= 0:
            return

        if self.direction == 0:
            profit_atr = (self.peak_price - self.entry) / atr_val
        else:
            profit_atr = (self.entry - self.peak_price) / atr_val

        if profit_atr <= 1.0:
            return  # pas de trailing avant 1.0×ATR

        # Niveaux de trailing par régime
        levels = {
            "RANGING": [(1.0, 0.50), (2.0, 0.35), (3.0, 0.20), (5.0, 0.10)],
            "TREND_UP": [(1.0, 0.80), (2.0, 0.50), (3.0, 0.30), (5.0, 0.15)],
            "TREND_DOWN": [(1.0, 0.80), (2.0, 0.50), (3.0, 0.30), (5.0, 0.15)],
            "HIGH_VOL": [(1.0, 1.00), (2.0, 0.70), (3.0, 0.50), (5.0, 0.25)],
            "LOW_VOL": [(1.0, 0.40), (2.0, 0.25), (3.0, 0.15), (5.0, 0.08)],
        }
        lvls = levels.get(self.regime, levels["RANGING"])
        trail_dist = lvls[-1][1]
        for thresh, dist in reversed(lvls):
            if profit_atr > thresh:
                trail_dist = dist
                break

        trail_distance = trail_dist * atr_val
        if self.direction == 0:  # BUY
            new_sl = self.peak_price - trail_distance
            if new_sl > self.trailing_sl:
                self.trailing_sl = new_sl
        else:  # SELL
            new_sl = self.peak_price + trail_distance
            if new_sl < self.trailing_sl:
                self.trailing_sl = new_sl

    def check_partial_tp(self, atr_val: float):
        """Partial TP à 60% du TP → set BE buffer."""
        if self.closed or self.partial_closed or atr_val <= 0:
            return

        if self.direction == 0:
            progress = (self.peak_price - self.entry) / max(self.tp - self.entry, 1e-10)
        else:
            progress = (self.entry - self.peak_price) / max(self.entry - self.tp, 1e-10)

        if progress < 0.60:
            return

        self.partial_closed = True
        be_buffer = BE_BUFFER_ATR * atr_val
        if self.direction == 0:
            be_sl = self.entry + be_buffer
            if be_sl > self.trailing_sl:
                self.trailing_sl = be_sl
        else:
            be_sl = self.entry - be_buffer
            if be_sl < self.trailing_sl:
                self.trailing_sl = be_sl

    def to_dict(self):
        return {
            "symbol": self.symbol,
            "action": self.action,
            "regime": self.regime,
            "entry": round(self.entry, 5),
            "sl": round(self.sl, 5),
            "tp": round(self.tp, 5),
            "close_price": round(self.close_price, 5),
            "result": self.result,
            "profit_usd": round(self.profit_usd, 2),
            "profit_pct": round(self.profit_pct * 100, 3),
            "bars_held": self.bars_held,
            "partial_tp": self.partial_closed,
            "lot": round(self.lot, 4),
        }


def load_h1_csv(filepath: str) -> dict | None:
    """Charge un fichier H1 CSV et retourne un dict avec arrays numpy."""
    try:
        data = np.genfromtxt(filepath, delimiter=',', skip_header=1,
                             dtype=[('time', 'U19'), ('open', 'f8'), ('high', 'f8'),
                                    ('low', 'f8'), ('close', 'f8'), ('volume', 'f8'),
                                    ('spread', 'f8'), ('real_vol', 'f8')],
                             missing_values='', filling_values=0)
        if len(data) < MIN_BARS:
            return None
        # Parser les timestamps
        times = [datetime.strptime(str(t).strip(), '%Y-%m-%d %H:%M:%S') for t in data['time']]
        return {
            "time": np.array(times),
            "open": data['open'],
            "high": data['high'],
            "low": data['low'],
            "close": data['close'],
            "spread": data['spread'],
        }
    except Exception as e:
        print(f"    Erreur chargement {filepath}: {e}")
        return None


def run_backtest_on_symbol(symbol: str, data: dict, balance: float = INITIAL_BALANCE) -> list[SimTrade]:
    """Exécute le backtest MOM20x3 sur un symbole."""
    close = data['close']
    high = data['high']
    low = data['low']
    times = data['time']
    n = len(close)

    all_trades: list[SimTrade] = []
    open_trades: list[SimTrade] = []

    for i in range(MIN_BARS, n):
        # 1. Mettre à jour les trades ouverts
        still_open = []
        for t in open_trades:
            t.update_peak(high[i], low[i], close[i])

            # Calcul ATR pour ce trade
            atr_val = atr(high[max(0, i-20):i+1], low[max(0, i-20):i+1],
                          close[max(0, i-20):i+1], 14)
            atr_v = float(atr_val[-1]) if atr_val is not None and len(atr_val) > 0 else t.atr_val

            t.check_partial_tp(atr_v)
            t.check_sl_tp(high[i], low[i], close[i], i, times[i])
            t.update_trailing(atr_v)

            if not t.closed:
                # Timeout: fermer après 120 barres H1 (5 jours)
                if i - t.open_bar > 120:
                    t.closed = True
                    t.close_price = close[i]
                    t.close_time = times[i]
                    t.result = "TIMEOUT"
                    t.bars_held = i - t.open_bar
                    if t.direction == 0:
                        t.profit_pct = (t.close_price - t.entry) / t.entry
                    else:
                        t.profit_pct = (t.entry - t.close_price) / t.entry
                    pips = (t.close_price - t.entry) * (1 if t.direction == 0 else -1) / t._pip_size
                    t.profit_usd = pips * t.usd_per_pip

            if not t.closed:
                still_open.append(t)

        open_trades = still_open

        # 2. Générer un signal MOM20x3
        signal = mom20x3_signal(close[:i+1], high[:i+1], low[:i+1])

        if signal is None:
            continue

        # 3. Vérifier si on a déjà un trade ouvert sur ce signal directionnel
        same_direction = [t for t in open_trades if t.action == signal['action']]
        if same_direction:
            continue  # déjà exposé dans cette direction

        # 4. Calculer les prix SL/TP
        entry = close[i]
        atr_v = signal['atr']
        if atr_v <= 0:
            continue

        # Vérifier le spread
        if data['spread'][i] > MAX_SPREAD_PIPS * 10:  # spread en pts
            continue

        sl_dist = signal['sl_atr'] * atr_v
        tp_dist = signal['tp_atr'] * atr_v

        if signal['action'] == "BUY":
            sl_price = entry - sl_dist
            tp_price = entry + tp_dist
        else:
            sl_price = entry + sl_dist
            tp_price = entry - tp_dist

        # Vérifier RR min
        rr = tp_dist / sl_dist if sl_dist > 0 else 0
        if rr < 2.0:
            continue

        # 5. Créer le trade
        regime = signal.get('_regime', "RANGING")
        trade = SimTrade(symbol, signal['action'], entry, sl_price, tp_price,
                         atr_v, regime, i, times[i], balance)
        all_trades.append(trade)
        open_trades.append(trade)

    return all_trades


def print_results(all_results: dict):
    """Affiche les résultats par symbole."""
    print()
    print("=" * 78)
    print("  BACKTEST MOM20x3 — TOUS SYMBOLES (H1, Jan-Mai 2026)")
    print(f"  {datetime.utcnow().strftime('%d %B %Y %H:%M UTC')}")
    print("=" * 78)

    headers = ["Symbole", "Trades", "WR", "PnL", "PF", "Avg$", "RR", "DD Max", "Edge"]
    print(f"\n  {headers[0]:10s} {headers[1]:>7s} {headers[2]:>7s} {headers[3]:>10s} "
          f"{headers[4]:>6s} {headers[5]:>7s} {headers[6]:>5s} {headers[7]:>7s} {headers[8]:>6s}")
    print(f"  {'-'*72}")

    # Trier par PnL décroissant (ignorer les erreurs)
    sorted_symbols = sorted(
        [(s, r) for s, r in all_results.items() if 'total_pnl' in r],
        key=lambda x: x[1]['total_pnl'], reverse=True
    )

    for sym, res in sorted_symbols:
        n = res['n']
        wr = res['win_rate']
        pnl = res['total_pnl']
        pf = res['profit_factor']
        avg_win = res['avg_win_usd']
        avg_rr = res['avg_rr']
        dd = res['max_drawdown_pct']
        sig = res['significant']

        edge_str = "✅" if sig and wr > 50 else "⚠️" if sig else "❌"
        print(f"  {sym:10s} {n:7d} {wr:>6.1f}% {pnl:>+9.2f} "
              f"{pf:>5.2f} {avg_win:>+6.1f} {avg_rr:>4.2f} {dd:>5.1f}% {edge_str:>6s}")

    print(f"  {'-'*72}")

    # Totaux (ignorer les erreurs)
    valid_results = {s: r for s, r in all_results.items() if 'total_pnl' in r}
    total_trades = sum(r['n'] for r in valid_results.values())
    total_pnl = sum(r['total_pnl'] for r in valid_results.values())
    total_wins = sum(r['wins'] for r in valid_results.values())
    total_wr = total_wins / max(total_trades, 1) * 100
    print(f"  {'TOTAL':10s} {total_trades:7d} {total_wr:>6.1f}% {total_pnl:>+9.2f}")
    print()


def compute_results(all_trades: dict[str, list[SimTrade]]) -> dict:
    """Calcule les métriques par symbole."""
    results = {}
    for sym, trades in all_trades.items():
        n = len(trades)
        if n == 0:
            results[sym] = {"n": 0, "error": "Aucun trade"}
            continue

        wins = [t for t in trades if t.profit_usd > 0]
        losses = [t for t in trades if t.profit_usd <= 0]
        n_wins = len(wins)
        n_losses = len(losses)
        wr = n_wins / n * 100 if n > 0 else 0
        total_pnl = sum(t.profit_usd for t in trades)
        gross_profit = sum(max(0, t.profit_usd) for t in trades)
        gross_loss = abs(sum(min(0, t.profit_usd) for t in trades))
        pf = gross_profit / gross_loss if gross_loss > 0 else float('inf') if gross_profit > 0 else 0

        avg_win_usd = sum(t.profit_usd for t in wins) / n_wins if n_wins > 0 else 0
        avg_loss_usd = abs(sum(t.profit_usd for t in losses)) / n_losses if n_losses > 0 else 0
        avg_rr = abs(avg_win_usd / avg_loss_usd) if avg_loss_usd > 0 else 0

        # Max drawdown (% du capital initial)
        peak = INITIAL_BALANCE
        dd_max = 0
        balance = INITIAL_BALANCE
        for t in trades:
            balance += t.profit_usd
            if balance > peak:
                peak = balance
            dd = (peak - balance) / peak * 100
            dd_max = max(dd_max, dd)

        # Test de significativité (binomial)
        from math import sqrt, erf
        if n >= 5:
            z = (wr/100 - 0.5) / sqrt(0.5 * 0.5 / n)
            p = 2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2))))
        else:
            p = 1.0

        results[sym] = {
            "n": n,
            "wins": n_wins,
            "losses": n_losses,
            "win_rate": round(wr, 1),
            "total_pnl": round(total_pnl, 2),
            "profit_factor": round(pf, 2),
            "avg_win_usd": round(avg_win_usd, 2),
            "avg_loss_usd": round(avg_loss_usd, 2),
            "avg_rr": round(avg_rr, 2),
            "max_drawdown_pct": round(dd_max, 1),
            "p_value": round(p, 4),
            "significant": p < 0.05,
        }
    return results


def main():
    parser = argparse.ArgumentParser(description="Backtest MOM20x3 sur H1 CSV")
    parser.add_argument("--csv", type=str, default=None, help="Export des trades vers CSV")
    args = parser.parse_args()

    data_dir = Path("runtime/market_h1_2026")
    if not data_dir.exists():
        print(f"❌ Dossier {data_dir} introuvable. Lancez d'abord download_h1_2026.py")
        sys.exit(1)

    csv_files = sorted(data_dir.glob("*_H1.csv"))
    print(f"📂 {len(csv_files)} fichiers H1 trouvés dans {data_dir}")

    all_trades = {}
    all_results = {}

    for fpath in csv_files:
        symbol = fpath.stem.replace("_H1", "")
        print(f"\n  ⏳ {symbol}... ", end="", flush=True)

        data = load_h1_csv(str(fpath))
        if data is None:
            print("❌ pas assez de données")
            continue

        trades = run_backtest_on_symbol(symbol, data)
        n = len(trades)
        closed = [t for t in trades if t.closed]

        if n == 0:
            print(f"✅ 0 signaux")
            all_trades[symbol] = []
            continue

        wins = len([t for t in closed if t.profit_usd > 0])
        pnl = sum(t.profit_usd for t in closed)
        wr = wins / max(len(closed), 1) * 100
        print(f"✅ {len(closed)} trades, {wr:.0f}% WR, ${pnl:+.0f}")

        all_trades[symbol] = closed

    # Métriques
    all_results = compute_results(all_trades)
    print_results(all_results)

    # Export CSV
    if args.csv:
        with open(args.csv, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["symbol", "action", "regime", "entry", "sl", "tp",
                           "close_price", "result", "profit_usd", "bars_held",
                           "partial_tp", "lot"])
            for sym, trades in sorted(all_trades.items()):
                for t in trades:
                    d = t.to_dict()
                    writer.writerow([d[k] for k in ["symbol","action","regime","entry",
                                                     "sl","tp","close_price","result",
                                                     "profit_usd","bars_held",
                                                     "partial_tp","lot"]])
        print(f"\n📄 Trades exportés vers {args.csv}")

    # Recommandations
    print("\n" + "=" * 78)
    print("  RECOMMANDATIONS — Symboles à (ré)activer")
    print("=" * 78)
    for sym, res in sorted(all_results.items(), key=lambda x: x[1].get('total_pnl', 0), reverse=True):
        if "error" in res:
            continue
        n = res['n']
        wr = res['win_rate']
        pnl = res['total_pnl']
        pf = res['profit_factor']
        sig = res['significant']
        dd = res['max_drawdown_pct']

        # Critères de recommandation
        if n < 5:
            rec = "🟡 Pas assez de trades"
        elif sig and wr > 55 and pf > 1.3 and dd < 15:
            rec = "🟢 ACTIVER"
        elif sig and wr > 50 and pf > 1.1:
            rec = "🟡 Surveiller"
        elif pnl < 0 and wr < 45:
            rec = "🔴 ÉVITER"
        else:
            rec = "🟡 Neutre"

        print(f"  {sym:10s} | {n:4d} trades | WR={wr:5.1f}% | PnL=${pnl:>+8.2f} | "
              f"PF={pf:.2f} | DD={dd:.1f}% | {rec}")

    return all_results


if __name__ == "__main__":
    main()
