"""Analyse détaillée des trades : SL/TP/ATR/per symbol"""
import sys
import warnings
from datetime import datetime, timedelta

import MetaTrader5 as mt5
import pandas as pd

sys.path.insert(0, '.')
warnings.filterwarnings('ignore')

mt5.initialize()
now = datetime.now()

# --- 1. Get ATR reference for each symbol ---
rates = {}
for sym in ['EURUSD','GBPUSD','GBPJPY','USDCHF','USDCAD','NZDUSD','XAUUSD','ETHUSD','USOIL.cash']:
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 50)
    if r is not None and len(r) > 20:
        df = pd.DataFrame(r)
        df['atr'] = df['high'] - df['low']
        rates[sym] = df['atr'].iloc[-20:].mean()
    else:
        rates[sym] = 0

# --- 2. Open positions ---
pos = mt5.positions_get()
ours = [p for p in pos if p.magic == 999001] if pos else []
print(f'=== OPEN POSITIONS ({len(ours)}) ===')
cols = ["Symbol", "Dir", "Lot", "Entry", "Price", "SL", "TP"]
cols2 = ["Profit", "ATRpts", "SLatr", "TPatr", "PctDD"]
header = " ".join(f"{c:>{12 if i==0 else 5 if i<3 else 10 if i<7 else 8}}"
                  for i, c in enumerate(cols + cols2))
print(header)
for p in ours:
    sym = p.symbol
    atr = rates.get(sym, 0)
    pt = p.price_open
    sl_dist = abs(p.price_open - p.sl) / (atr if atr > 0 else 1)
    tp_dist = abs(p.price_open - p.tp) / (atr if atr > 0 else 1)
    dd_pct = p.profit / 199276.14 * 100
    bid = mt5.symbol_info_tick(sym).bid if p.type == 0 else mt5.symbol_info_tick(sym).ask
    dir_lbl = "BUY" if p.type == 0 else "SELL"
    print(f'{sym:>12} {dir_lbl:>5} {p.volume:>5.2f} {p.price_open:>10.1f} {bid:>10.1f} '
          f'{p.sl:>10.1f} {p.tp:>10.1f} {p.profit:>8.1f} {atr:>8.1f} '
          f'{sl_dist:>6.1f} {tp_dist:>6.1f} {dd_pct:>8.2f}%')

# --- 3. Closed trades ---
print()
print('=== CLOSED POSITIONS (RR analysis) ===')
deals = mt5.history_deals_get(now - timedelta(days=2), now)
our_deals = [d for d in deals if d.magic == 999001] if deals else []
df = pd.DataFrame([{
    'ticket': d.ticket, 'pos_id': d.position_id, 'symbol': d.symbol,
    'type': d.type, 'volume': d.volume, 'price': d.price,
    'profit': d.profit, 'time': datetime.fromtimestamp(d.time),
    'comment': d.comment
} for d in our_deals])

by_pos = df.groupby('pos_id')
results = []
for pid, grp in by_pos:
    total_pnl = grp['profit'].sum()
    if total_pnl == 0:
        continue
    entries = grp[grp['profit'] == 0]
    exits = grp[grp['profit'] != 0]
    if len(entries) == 0 or len(exits) == 0:
        continue
    symbol = grp['symbol'].iloc[0]
    entry_price = entries['price'].mean()
    exit_price = exits['price'].mean()
    direction = 'BUY' if entries.iloc[0]['type'] == 0 else 'SELL'
    atr_ref = rates.get(symbol, 1)

    move = (exit_price - entry_price) / atr_ref if atr_ref > 0 else 0
    if direction == 'SELL':
        move = -move

    max_profit = exits['profit'].max()
    max_loss = exits['profit'].min()

    if total_pnl < 0 and len(exits) == 1:
        reason = 'SL_HIT'
    elif total_pnl > 0 and len(exits) == 1:
        reason = 'TP_HIT'
    elif len(exits) > 1:
        reason = 'PARTIAL'
    else:
        reason = 'OTHER'

    results.append({
        'pos_id': pid, 'symbol': symbol, 'dir': direction,
        'entry': round(entry_price, 2), 'exit': round(exit_price, 2),
        'P&L': round(total_pnl, 1), 'maxWin': round(max_profit, 1),
        'maxLoss': round(max_loss, 1), 'move_atr': round(move, 2),
        'reason': reason, 'n_exits': len(exits)
    })

rdf = pd.DataFrame(results)
if len(rdf) == 0:
    print('No closed trades')
    mt5.shutdown()
    exit()

rdf['type'] = rdf['P&L'].apply(lambda x: 'WIN' if x > 0 else 'LOSS')

# Per symbol summary
h2_cols = ["Symbol", "Total", "Wins", "Loss", "WR", "P&L",
           "AvgMoveATR", "AvgWin", "AvgLoss", "RRwins"]
h2_widths = [12, 5, 5, 5, 5, 8, 11, 8, 8, 6]
h2 = " ".join(f"{c:>{w}}" for c, w in zip(h2_cols, h2_widths, strict=False))
print(h2)
for sym, grp in rdf.groupby('symbol'):
    w = grp[grp['P&L'] > 0]
    lo_ = grp[grp['P&L'] < 0]
    avg_win = w['P&L'].mean() if len(w) > 0 else 0
    avg_loss = lo_['P&L'].mean() if len(lo_) > 0 else 0
    rr = abs(avg_win / avg_loss) if avg_loss != 0 else 99
    wr_pct = len(w) / len(grp) * 100
    print(f'{sym:>12} {len(grp):>5} {len(w):>5} {len(lo_):>5} {wr_pct:>4.0f}% '
          f'{grp["P&L"].sum():>8.1f} {grp["move_atr"].mean():>11.2f} '
          f'{avg_win:>8.1f} {avg_loss:>8.1f} {rr:>6.1f}')

print()
wins_count = len(rdf[rdf["P&L"] > 0])
wr_pct = wins_count / len(rdf) * 100
print(f'=== TOTAL: {len(rdf)} trades | WR {wins_count}/{len(rdf)} = {wr_pct:.1f}% '
      f'| P&L {rdf["P&L"].sum():.1f} ===')

# --- 4. Detailed loss analysis ---
losses = rdf[rdf['P&L'] < 0]
print()
print(f'=== PERTES ({len(losses)}) : DETAIL ===')
for _, row in losses.iterrows():
    print(f'{row["symbol"]:>12} {row["dir"]:>5} P&L={row["P&L"]:>7.1f} '
          f'move={row["move_atr"]:>+.2f}ATR {row["reason"]:>8} exits={row["n_exits"]}')

# Win/loss ATR comparison
wins2 = rdf[rdf['P&L'] > 0]
print()
print('=== SL/TP DISTANCE ANALYSIS ===')
print(f'WIN avg move: {wins2["move_atr"].mean():.2f} ATR ({len(wins2)} trades)')
print(f'LOSS avg move: {losses["move_atr"].mean():.2f} ATR ({len(losses)} trades)')
ratio_msg = (
    f'Ratio win/loss move: {abs(wins2["move_atr"].mean() / losses["move_atr"].mean()):.2f}'
    if len(losses) > 0 else 'No losses'
)
print(ratio_msg)

# Actual RR ratio
print(f'Avg win P&L: {wins2["P&L"].mean():.1f}' if len(wins2) > 0 else '')
print(f'Avg loss P&L: {losses["P&L"].mean():.1f}' if len(losses) > 0 else '')
rr_msg = (
    f'RR ratio (avg win / avg loss): {abs(wins2["P&L"].mean() / losses["P&L"].mean()):.2f}'
    if len(wins2) > 0 and len(losses) > 0 else ''
)
print(rr_msg)

mt5.shutdown()
