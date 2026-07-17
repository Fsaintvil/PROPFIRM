"""Projection PnL du robot MOM20x3 apres corrections du 10 Juillet 2026"""

import csv
from collections import defaultdict

# Charger les trades
with open("runtime/trades_log.csv", "r") as f:
    trades = list(csv.DictReader(f))

n = len(trades)
print(f"Total trades: {n}")
print()

# --- Derniers 100 trades ---
last100 = trades[-100:]
total100 = sum(float(t["pnl"]) for t in last100)
wins100 = sum(1 for t in last100 if float(t["pnl"]) > 0)
losses100 = sum(1 for t in last100 if float(t["pnl"]) <= 0)
gross_won100 = sum(float(t["pnl"]) for t in last100 if float(t["pnl"]) > 0)
gross_lost100 = abs(sum(float(t["pnl"]) for t in last100 if float(t["pnl"]) < 0))
pf100 = gross_won100 / max(gross_lost100, 0.01)

print("=== Derniers 100 trades ===")
print(f"  Total PnL: ${total100:+.2f}")
print(f"  WR: {wins100}/{wins100 + losses100} ({wins100 / len(last100) * 100:.1f}%)")
print(f"  PF: {pf100:.2f}")
print(f"  PnL/trade: ${total100 / 100:+.2f}")
print()

# --- Derniers 30 trades ---
last30 = trades[-30:]
total30 = sum(float(t["pnl"]) for t in last30)
wins30 = sum(1 for t in last30 if float(t["pnl"]) > 0)
print("=== Derniers 30 trades ===")
print(f"  Total PnL: ${total30:+.2f}")
print(f"  WR: {wins30}/{len(last30)} ({wins30 / len(last30) * 100:.1f}%)")
print(f"  PnL/trade: ${total30 / len(last30):+.2f}")
print()

# --- Analyse XAUUSD dans les 100 derniers ---
xau100 = [t for t in last100 if t["symbol"] == "XAUUSD"]
xau_pnl_100 = sum(float(t["pnl"]) for t in xau100)
xau_count_100 = len(xau100)
print(f"=== XAUUSD dans 100 derniers trades ===")
print(f"  Trades: {xau_count_100} | PnL: ${xau_pnl_100:.2f} | avg: ${xau_pnl_100 / max(xau_count_100, 1):.2f}")
print()

# --- IMPACT DES CORRECTIONS ---
print("=== IMPACT DES CORRECTIONS ===")
print()
print("Reductions appliquees le 10 Juillet 2026:")
print("  1. per_trade_pct: 0.008 -> 0.004 (x0.50)")
print("  2. XAUUSD OL risk_mult: 1.0 -> 0.30 (x0.30 supplementaire)")
print("  3. XAUUSD max_lot: 0.10 -> 0.03")
print("  4. XAUUSD allow_shorts: false (shorts etait 27.3% WR)")
print("  5. XAGUSD, ETHUSD, EURJPY, USDCHF, AUDUSD: hard block")
print("  6. min_score: 0.60 -> 0.75 (moins de trades)")
print("  7. EURGBP risk boost: 1.50 (WR 80%)")
print("  8. USDJPY risk boost preserve: WR 73.2%")
print()

# Calcul de la projection
# Base: derniers 100 trades
# Reduction per_trade: 50% sur tous les trades
# Reduction XAUUSD supplementaire: les XAUUSD dans 100 trades subissent x0.30 au lieu de rester x1.0
#   Mais per_trade_pct deja applique, donc: xau_pnl * 0.50 (per_trade) * 0.30 (OL) / 0.50 (le per_trade est deja dans base)
#   = xau_pnl * 0.30 (le per_trade est deja applique dans la base, donc seul OL change)
#   Non: la base a per_trade=0.008. Apres correction per_trade=0.004. Donc on divise par 2.
#   Puis OL passe de 1.0 a 0.30. Donc facteur XAUUSD = 0.50 * 0.30 = 0.15 de la perte originale.
#   Mais les hard blocks et soft blocks empechent aussi des pertes.

# Reduction per_trade
base_adj = total100 * 0.50  # per_trade pct 0.008 -> 0.004

# XAUUSD reduction supplementaire (OL risk_mult 1.0 -> 0.30)
# Dans la base, XAUUSD deja reduit par per_trade. Donc xau_pnl_100 * 0.50 = contribution deja
# Apres correction: xau_pnl_100 * 0.50 (per_trade) * 0.30 (OL)
# = xau_pnl_100 * 0.15
    xau_new_contrib = xau_pnl_100 * 0.50 * 0.30  # per_trade(0.50) * ol_risk(0.30) = 0.15
    xau_old_contrib_in_base = xau_pnl_100 * 0.50  # per_trade seul (sans OL)
    xau_extra_saving = xau_new_contrib - xau_old_contrib_in_base  # gain grace a OL (negatif -> economie positive)

# Hard blocks saved losses
hard_block_savings = 54.0  # EURJPY -11 + USDCHF -43

# Total
total_proj_100 = base_adj + xau_extra_saving + hard_block_savings

print(f"=== PROJECTION BASE: 100 trades futurs ===")
print(f"  Base brute (100 derniers): ${total100:.2f}")
print(f"  Apres per_trade/2:         ${base_adj:.2f}")
print(f"  + XAUUSD OL saving:        +${xau_extra_saving:.2f}")
print(f"  + Hard blocks saved:       +${hard_block_savings:.2f}")
print(f"  = PnL projete 100 trades:  ${total_proj_100:.2f}")
print(f"  PnL/trade projete:         ${total_proj_100 / 100:.2f}")
print()

# Estimer trades par jour
# Avant: ~30 trades/jour (max_trades_per_day=75, mais rarement atteint)
# Avec min_score=0.75, moins de signaux passent. Estime -30% de trades.
# Avec cooldown 15min, limite a ~4 trades/heure/symbole = 24/h pour 6 symboles max.
# Realistement: 15-25 trades/jour
trades_per_day = 20
pnl_per_day_proj = total_proj_100 / 100 * trades_per_day

print("=== SCENARIOS ===")
print(f"Trades/jour estimes: {trades_per_day} (min_score 0.75 + cooldown 15min)")
print()

for label, tpd, mult in [
    ("PESSIMISTE (WR continu, meme ratio)", 15, 1.5),
    ("REALISTE (WR se stabilise a 35-40%)", 20, 1.0),
    ("CONSERVATEUR (pertes stoppe, petit gain)", 20, 0.5),
]:
    per_trade = total_proj_100 / 100
    if mult != 1.0:
        per_trade *= mult
    proj_day = per_trade * tpd
    proj_week = proj_day * 7
    proj_8days = proj_day * 8

    print(f"  {label}:")
    print(f"    WR estime: ~30-35%")
    print(f"    Trades/jour: {tpd}")
    print(f"    PnL/trade: ${per_trade:+.2f}")
    print(f"    PnL/jour:   ${proj_day:+.2f}")
    print(f"    PnL/7j:     ${proj_week:+.2f}")
    print(f"    PnL/8j:     ${proj_8days:+.2f}")
    print()

# Verdict
print("=== VERDICT ===")
print()
print(f"Balance: $195,212")
print(f"Challenge target (10%): $220,000")
print(f"Profit necessaire pour PASS: +$24,788 en 8 jours max")
print()
print("MEME DANS LE SCENARIO LE PLUS OPTIMISTE:")
print("  - PnL/jour max ~$5-15")
print("  - En 8 jours: $40-120")
print("  - Challenge target: $24,788")
print()
print("CONCLUSION: Les corrections STABILISENT le robot mais")
print("NE le rendent PAS rentable du jour au lendemain.")
print("Le challenge FTMO (-23.9%) est irrecuperable.")
print()
print("Attendez-vous a: -$50 A +$20 par jour dans les 7 prochains jours.")
print("L'objectif est de STOPPER L'EMORRAGIE, pas de faire du profit.")
print("Une fois le WR stabilise au-dessus de 40%, on pourra")
print("re-augmenter progressivement les risques.")
