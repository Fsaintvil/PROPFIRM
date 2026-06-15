"""
Backtest Production Rapide — 3 actifs calibrés

Version optimisée pour des résultats rapides.
Focus sur: WR, PnL, DD, PF par actif.

Usage:
    python scripts/backtest_quick.py
"""
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine_simple.indicators import atr, adx, ema
from engine_simple.strategy import _get_symbol_config

# ── Configuration ──
INITIAL_BALANCE = 200_000.0
RISK_PER_TRADE = 0.004
MIN_BARS = 60
MAX_BARS = 10000  # Dernières 10K barres pour rapidité
TIMEOUT_BARS = 120

PRODUCTION_SYMBOLS = ["XAUUSD", "BTCUSD", "ETHUSD"]


def get_pip_info(symbol):
    if symbol in ('XAUUSD', 'XAGUSD'):
        return 0.01, 1.0
    elif symbol in ('US500.cash', 'JP225.cash'):
        return 0.01, 1.0
    elif symbol in ('BTCUSD', 'ETHUSD'):
        return 0.01, 1.0
    return 0.0001, 10.0


def run_quick_backtest(symbol, close, high, low):
    """Backtest rapide sans trailing complexe."""
    sym_cfg = _get_symbol_config(symbol)
    period = sym_cfg["momentum_period"]
    pip_size, pip_value = get_pip_info(symbol)
    
    n = len(close)
    start_idx = max(MIN_BARS, n - MAX_BARS)
    
    trades = []
    balance = INITIAL_BALANCE
    peak_balance = INITIAL_BALANCE
    max_dd = 0
    
    for i in range(start_idx, n):
        if i < period + 14:
            continue
        
        # ATR
        atr_val = atr(high[i-20:i+1], low[i-20:i+1], close[i-20:i+1], 14)
        if atr_val is None or len(atr_val) == 0:
            continue
        current_atr = float(atr_val[-1])
        if current_atr <= 0:
            continue
        
        # ADX — utiliser une fenêtre plus petite pour la vitesse
        adx_arr, plus_di, minus_di = adx(high[i-50:i+1], low[i-50:i+1], close[i-50:i+1], 14)
        
        # Momentum
        mom = close[i] - close[i - period]
        mom_abs = abs(mom)
        
        # Threshold
        is_trending = adx_arr >= 25
        thresh = sym_cfg['threshold_trending'] if is_trending else sym_cfg['threshold_ranging']
        thresh = max(1.5, min(2.5, thresh))
        threshold_value = thresh * current_atr
        
        if mom_abs < threshold_value:
            continue
        
        # Direction
        if mom > 0:
            action = 'BUY'
            if plus_di <= minus_di * 0.8:
                continue
        else:
            action = 'SELL'
            if minus_di <= plus_di * 0.8:
                continue
        
        # Pullback
        ema20_arr = ema(close[:i+1], 20)
        if len(ema20_arr) > 0 and not np.isnan(ema20_arr[-1]):
            ema20_val = float(ema20_arr[-1])
            if ema20_val > 0:
                pullback_dist = (close[i] - ema20_val) / ema20_val * 100
                atr_mult = sym_cfg['pullback_band_trending'] if is_trending else sym_cfg['pullback_band_ranging']
                pullback_band = (atr_mult * current_atr) / ema20_val * 100
                pullback_band = max(0.05, min(1.0, pullback_band))
                if abs(pullback_dist) >= pullback_band:
                    continue
        
        # SL/TP
        if is_trending:
            sl_dist = sym_cfg['sl_atr_trending'] * current_atr
            tp_dist = sym_cfg['tp_atr_trending'] * current_atr
        else:
            sl_dist = sym_cfg['sl_atr_ranging'] * current_atr
            tp_dist = sym_cfg['tp_atr_ranging'] * current_atr
        
        entry = close[i]
        if action == 'BUY':
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            sl = entry + sl_dist
            tp = entry - tp_dist
        
        # Simuler le trade (find next SL/TP hit)
        result = None
        exit_price = entry
        for j in range(i+1, min(i+TIMEOUT_BARS, n)):
            if action == 'BUY':
                if low[j] <= sl:
                    result = 'SL'
                    exit_price = sl
                    break
                elif high[j] >= tp:
                    result = 'TP'
                    exit_price = tp
                    break
            else:
                if high[j] >= sl:
                    result = 'SL'
                    exit_price = sl
                    break
                elif low[j] <= tp:
                    result = 'TP'
                    exit_price = tp
                    break
        
        if result is None:
            # Timeout
            result = 'TIMEOUT'
            exit_price = close[min(i+TIMEOUT_BARS, n-1)]
        
        # PnL
        if action == 'BUY':
            pips = (exit_price - entry) / pip_size
        else:
            pips = (entry - exit_price) / pip_size
        
        # Lot sizing
        risk_usd = balance * RISK_PER_TRADE
        risk_in_pips = sl_dist / pip_size
        lot = risk_usd / (risk_in_pips * pip_value) if risk_in_pips > 0 else 0.01
        lot = max(0.01, min(1.0, lot))
        
        profit_usd = pips * lot * pip_value
        balance += profit_usd
        
        # Drawdown
        if balance > peak_balance:
            peak_balance = balance
        dd = (peak_balance - balance) / peak_balance
        if dd > max_dd:
            max_dd = dd
        
        trades.append({
            'bar': i,
            'action': action,
            'entry': entry,
            'exit': exit_price,
            'result': result,
            'profit_usd': profit_usd,
            'lot': lot,
        })
    
    return trades, balance, max_dd


def print_stats(sym, trades, final_balance, max_dd):
    """Affiche les statistiques."""
    if not trades:
        print(f"\n  {sym}: Aucun trade")
        return
    
    wins = [t for t in trades if t['profit_usd'] > 0]
    losses = [t for t in trades if t['profit_usd'] <= 0]
    
    total_pnl = sum(t['profit_usd'] for t in trades)
    win_rate = len(wins) / len(trades) * 100
    
    gross_profit = sum(t['profit_usd'] for t in wins) if wins else 0
    gross_loss = abs(sum(t['profit_usd'] for t in losses)) if losses else 1
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
    
    avg_win = gross_profit / len(wins) if wins else 0
    avg_loss = gross_loss / len(losses) if losses else 0
    
    # Count by result
    sl_count = sum(1 for t in trades if t['result'] == 'SL')
    tp_count = sum(1 for t in trades if t['result'] == 'TP')
    timeout_count = sum(1 for t in trades if t['result'] == 'TIMEOUT')
    
    print(f"\n{'='*60}")
    print(f"  {sym}")
    print(f"{'='*60}")
    print(f"  Trades:        {len(trades)}")
    print(f"  Win Rate:      {win_rate:.1f}%")
    print(f"  PnL Total:     ${total_pnl:,.2f}")
    print(f"  Final Balance: ${final_balance:,.2f}")
    print(f"  Profit Factor: {profit_factor:.2f}")
    print(f"  Max Drawdown:  {max_dd*100:.1f}%")
    print(f"  Avg Win:       ${avg_win:,.2f}")
    print(f"  Avg Loss:      ${avg_loss:,.2f}")
    print(f"  SL: {sl_count} | TP: {tp_count} | TIMEOUT: {timeout_count}")
    print(f"  Expectancy:    ${total_pnl/len(trades):,.2f}")


def main():
    print("="*70)
    print("  BACKTEST RAPIDE — Configuration Production")
    print("  XAUUSD | BTCUSD | US500.cash")
    print("="*70)
    
    # Load data
    data_dir = Path("data/historical")
    all_stats = []
    total_trades = []
    
    for sym in PRODUCTION_SYMBOLS:
        parquet_path = data_dir / f"{sym}_H1.parquet"
        if not parquet_path.exists():
            print(f"\n  {sym}: Données non trouvées")
            continue
        
        print(f"\n  Chargement {sym}...")
        df = pd.read_parquet(parquet_path)
        close = df['close'].values.astype(float)
        high = df['high'].values.astype(float)
        low = df['low'].values.astype(float)
        
        print(f"  {len(close)} barres chargées")
        print(f"  Backtest en cours...")
        
        start_time = time.time()
        trades, final_balance, max_dd = run_quick_backtest(sym, close, high, low)
        elapsed = time.time() - start_time
        
        print(f"  Terminé en {elapsed:.1f}s")
        
        print_stats(sym, trades, final_balance, max_dd)
        
        total_trades.extend(trades)
        if trades:
            wins = sum(1 for t in trades if t['profit_usd'] > 0)
            all_stats.append({
                'symbol': sym,
                'trades': len(trades),
                'win_rate': wins/len(trades)*100,
                'pnl': sum(t['profit_usd'] for t in trades),
                'dd': max_dd*100,
            })
    
    # Global stats
    if total_trades:
        total_pnl = sum(t['profit_usd'] for t in total_trades)
        total_wins = sum(1 for t in total_trades if t['profit_usd'] > 0)
        global_wr = total_wins / len(total_trades) * 100
        
        print(f"\n{'='*60}")
        print(f"  RÉSUMÉ GLOBAL")
        print(f"{'='*60}")
        print(f"  Total Trades:  {len(total_trades)}")
        print(f"  Global WR:     {global_wr:.1f}%")
        print(f"  Total PnL:     ${total_pnl:,.2f}")
    
    # Verdict
    print(f"\n{'='*60}")
    print(f"  VERDICT PRODUCTION")
    print(f"{'='*60}")
    
    ready = True
    for stats in all_stats:
        wr = stats['win_rate']
        dd = stats['dd']
        pnl = stats['pnl']
        
        status = "OK" if (wr > 55 and dd < 15) else "ATTENTION"
        if status == "ATTENTION":
            ready = False
        
        print(f"\n  {stats['symbol']}: {status}")
        print(f"    WR: {wr:.1f}% | DD: {dd:.1f}% | PnL: ${pnl:,.2f}")
    
    if ready:
        print(f"\n  TOUS LES ACTIFS VALIDES — Pret pour production")
    else:
        print(f"\n  CERTAINS ACTIFS NECESSITENT UNE ATTENTION")
    
    print(f"\n{'='*70}")


if __name__ == "__main__":
    main()
