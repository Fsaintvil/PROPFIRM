"""
analyse_definitive.py — Analyse risque FTMO $200K (chiffres réels)
"""
import random
from collections import defaultdict

BALANCE = 199385
EQUITY = 199175
FLOATING = -210
DD_CURRENT = 210
PEAK = 199385
HISTORICAL_TRADES = 951
HISTORICAL_PNL = 2083
WR = 0.605
RISK_PER_TRADE = 800  # 0.4% * 200K
RR = 2.0
PROFIT_TARGET = 20000
MAX_DD = 20000
DAILY_LIMIT = 4000

# Positions actuelles
POSITIONS = {
    "EURUSD": {"count": 3, "fl": -63, "group": "G1"},
    "AUDUSD": {"count": 2, "fl": -4, "group": "G1"},
    "ETHUSD": {"count": 2, "fl": 12, "group": "G5"},
    "XAUUSD": {"count": 2, "fl": -137, "group": "G4"},
    "NZDUSD": {"count": 2, "fl": -18, "group": "G3"},
    "USDCAD": {"count": 3, "fl": -4, "group": "G2"},
    "GBPJPY": {"count": 2, "fl": 3, "group": "G2"},
    "USDJPY": {"count": 2, "fl": -70, "group": "G2"},
}

# Stats historiques par symbole (depuis ReportHistory)
SYM_STATS = {
    "EURUSD": {"trades": 47, "pnl": 114.34, "wr": 0.49, "avg_win": 12.0, "avg_loss": -14.5},
    "GBPUSD": {"trades": 176, "pnl": 505.19, "wr": 0.56, "avg_win": 13.0, "avg_loss": -14.0},
    "USDJPY": {"trades": 44, "pnl": -67.14, "wr": 0.32, "avg_win": 11.0, "avg_loss": -14.0},
    "USDCAD": {"trades": 532, "pnl": 1622.50, "wr": 0.69, "avg_win": 13.0, "avg_loss": -14.5},
    "USDCHF": {"trades": 41, "pnl": 82.52, "wr": 0.54, "avg_win": 12.5, "avg_loss": -14.0},
    "AUDUSD": {"trades": 47, "pnl": -95.06, "wr": 0.55, "avg_win": 12.0, "avg_loss": -14.0},
    "NZDUSD": {"trades": 39, "pnl": -1.47, "wr": 0.38, "avg_win": 12.0, "avg_loss": -13.5},
    "EURJPY": {"trades": 2, "pnl": -77.39, "wr": 0.0, "avg_win": 0, "avg_loss": -38.7},
    "ETHUSD": {"trades": 22, "pnl": -0.65, "wr": 0.32, "avg_win": 11.0, "avg_loss": -14.0},
    "USOIL.cash": {"trades": 1, "pnl": 0.60, "wr": 1.0, "avg_win": 0.6, "avg_loss": 0},
}
SYM_STATS["XAUUSD"] = {"trades": 0, "pnl": 0, "wr": 0.5, "avg_win": 15, "avg_loss": -15}

print("=" * 60)
print("  ANALYSE DE RISQUE DÉFINITIVE")
print("=" * 60)

# ─── 1. SANTÉ DU COMPTE ───
print("\n  ── État du compte ──")
print(f"  Balance: ${BALANCE:,}  |  Equity: ${EQUITY:,}  |  Floating: ${FLOATING:,}")
print(f"  DD actuel: ${DD_CURRENT} (0.1%)  |  Peak: ${PEAK:,}")
print(f"  PnL historique: +${HISTORICAL_PNL:,}  |  WR: {WR:.0%}")
print(f"  Variabilité journalière estimée: ±${RISK_PER_TRADE * 20 * (WR * RR - (1-WR)):.0f}")

# ─── 2. ANALYSE CORRÉLATION ───
print("\n  ── Corrélation positions ──")
groups = defaultdict(list)
for sym, d in POSITIONS.items():
    groups[d["group"]].append((sym, d["count"], d["fl"]))
for g, items in groups.items():
    total_pos = sum(i[1] for i in items)
    total_fl = sum(i[2] for i in items)
    syms = ', '.join(f'{s}({c})' for s,c,fl in items)
    flag = " ⚠ VIOLATION" if total_pos > 4 else ""
    print(f"  {g}: {total_pos} positions ({syms}) fl={total_fl:+}{flag}")

# ─── 3. EXPOSITION AU RISQUE ───
print("\n  ── Exposition ──")
total_risk = sum(POSITIONS[s]["count"] for s in POSITIONS) * RISK_PER_TRADE
print(f"  Risque par trade: ${RISK_PER_TRADE}")
print(f"  Risque total (si tout SL): ${total_risk:,}")
print(f"  Risque / capital: {total_risk/BALANCE*100:.1f}%")
print(f"  Marge FTMO restante: ${MAX_DD - DD_CURRENT:,}")
print(f"  Trades consécutifs pour ruine: {(MAX_DD - DD_CURRENT) / RISK_PER_TRADE:.0f}")

# ─── 4. PIRES SYMBOLES ───
print("\n  ── Symbols à risque ──")
for s in sorted(SYM_STATS, key=lambda x: SYM_STATS[x].get("wr", 0.5)):
    d = SYM_STATS[s]
    if d["trades"] < 5:
        continue
    expectancy = d["wr"] * 2 - (1 - d["wr"])  # RR=2
    print(f"  {s:>12s}: {d['trades']:4d}t WR={d['wr']:.0%} expectancy={expectancy:+.2f}R "
          f"contrib={d['pnl']:+.0f}")
    if d["wr"] < 0.40:
        print("           ⚠ WR < 40% → risque élevé")
    if d["trades"] < 20:
        print("           ⚠ Échantillon insuffisant")

# ─── 5. MONTE CARLO ───
print("\n  ── Monte Carlo (500 trades, 5000 simulations) ──")
for label, wr, rr_val, risk_mult in [
    ("Actuel (60.5% WR, RR=2.0)", 0.605, 2.0, 1.0),
    ("Sans ETHUSD (61.5% WR)", 0.615, 2.0, 1.0),
    ("Seuils bas (55% WR)", 0.55, 2.0, 1.0),
    ("Marché neutre (50% WR)", 0.50, 2.0, 1.0),
    ("Crise (40% WR)", 0.40, 2.0, 1.0),
    ("ETHUSD+XAUUSD réduit (60% WR)", 0.60, 2.0, 0.85),
]:
    rpt = RISK_PER_TRADE * risk_mult
    avg_win = rpt * rr_val
    avg_loss = rpt
    ruined, hit_target = 0, 0
    final_pnls, max_dds = [], []
    for _ in range(5000):
        cap = BALANCE
        peak = BALANCE
        local_ruin = False
        target = False
        for _ in range(500):
            if random.random() < wr:
                cap += avg_win
            else:
                cap -= avg_loss
            if cap > peak:
                peak = cap
            if peak - cap > MAX_DD:
                local_ruin = True
                break
            if cap - BALANCE >= PROFIT_TARGET:
                target = True
                break
        final_pnls.append(cap - BALANCE)
        max_dds.append(peak - min(cap, peak))
        if local_ruin:
            ruined += 1
        if target:
            hit_target += 1
    print(f"  {label:35s}: ruin={ruined/50:.1f}% target={hit_target/50:.1f}% "
          f"avg_PnL=${sum(final_pnls)/5000:.0f}")

# ─── 6. BUDGET JOURNALIER ───
print("\n  ── Budget journalier FTMO ──")
daily_risk_budget = DAILY_LIMIT / RISK_PER_TRADE
daily_target = 20000 / 30  # 30 jours
expected_daily = RISK_PER_TRADE * 20 * (WR * RR - (1-WR))
print(f"  Daily loss max: ${DAILY_LIMIT:,} (2.0%)")
print(f"  Trades max à -${RISK_PER_TRADE}: {daily_risk_budget:.0f}")
print(f"  Target journalier: ${daily_target:.0f}")
print(f"  Espérance journalière: ${expected_daily:.0f}")
if expected_daily > 0:
    print(f"  Jours estimés pour target: ${PROFIT_TARGET/expected_daily:.0f}")
else:
    print("  Jours: N/A (espérance négative)")

# ─── 7. VERDICT ───
print(f"\n{'='*60}")
print("  VERDICT")
print(f"{'='*60}")

risks = []
if SYM_STATS["ETHUSD"]["wr"] < 0.35:
    risks.append("ETHUSD WR=32% sur 22 trades")
if SYM_STATS["USDJPY"]["wr"] < 0.35:
    risks.append("USDJPY WR=32% sur 44 trades")
if SYM_STATS["NZDUSD"]["wr"] < 0.40:
    risks.append("NZDUSD WR=38% sur 39 trades")
if len(POSITIONS) >= 8:
    risks.append(f"{len(POSITIONS)} symboles en position simultanément")
if total_risk > 10000:
    risks.append(f"Risque total exposé ${total_risk:,} > $10K")

if not risks:
    print("  ✅ Profil de risque acceptable")
else:
    print(f"  ⚠ {len(risks)} point(s) de vigilance :")
    for r in risks:
        print(f"    • {r}")

print("")
print(f"  Monte Carlo (WR actuel): ruin={ruined/50:.1f}% target={hit_target/50:.1f}%")
if ruined/50 < 1:
    print("  → Risque de ruine négligeable si le WR tient")
else:
    print("  → Risque non négligeable")
print("  Le vrai risque: WR qui chute à 50% ou moins sur les 500 prochains trades")
