"""
analyse_risque.py — Analyse approfondie du robot FTMO
  - Monte Carlo (risque de ruine)
  - Corrélation positions actuelles
  - Scénario worst-case drawdown
  - Contribution par symbole
  - VaR (Value at Risk)
"""

import random
import re
from collections import defaultdict

DB_PATH = "runtime/trading_journal.db"
LOG_FILE = "logs/simple_robot.log"

HISTORICAL_WR = {
    "EURUSD": 0.49, "GBPUSD": 0.56, "USDJPY": 0.32, "USDCAD": 0.69,
    "USDCHF": 0.54, "AUDUSD": 0.55, "NZDUSD": 0.38, "EURJPY": 0.00,
    "GBPJPY": 0.55, "XAUUSD": 0.50, "ETHUSD": 0.32, "USOIL.cash": 1.00,
}
HISTORICAL_TRADES = {
    "EURUSD": 47, "GBPUSD": 176, "USDJPY": 44, "USDCAD": 532, "USDCHF": 41,
    "AUDUSD": 47, "NZDUSD": 39, "EURJPY": 2, "XAUUSD": 1, "ETHUSD": 22,
    "USOIL.cash": 1,
}

CORRELATION_GROUPS = [
    ["EURUSD", "GBPUSD", "AUDUSD", "EURJPY"],
    ["USDJPY", "USDCAD", "USDCHF", "GBPJPY"],
    ["NZDUSD"], ["XAUUSD"], ["ETHUSD"], ["USOIL.cash"],
]

INITIAL_BALANCE = 200000
PROFIT_TARGET = 20000
MAX_DD = 20000
DAILY_LIMIT = 4000
RISK_PER_TRADE = 0.004
RR = 2.0  # Average RR

def get_positions():
    # Parse the latest position info from the robot log
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return [], []

    # Find last positions line
    for line in reversed(lines):
        if "Positions:" in line and "Pert" in line:
            # Extract from individual position lines
            continue
        if "Positions:" in line and "Par symbole" in line:
            continue
        if "Positions:" in line:
            break

    # Extract position details from individual lines
    positions = []
    pending = []
    for line in reversed(lines):
        m = re.search(r'PEND\s+(\w+)\s+(BUY|SELL)', line)
        if m:
            pending.append((m.group(1), m.group(2), 0, 0, 0, 0, 0, 0))
        m = re.search(r'(\w+):\s+([-+\d.]+)\s+USD', line)
        if m and m.group(1) not in ["TREND_UP","TREND_DOWN","RANGING","HIGH_VOL","LOW_VOL"]:
            positions.append((m.group(1), "?", 0, 0, 0, 0, float(m.group(2)), 0))

    if not positions:
        # Fallback: parse the summary line
        for line in reversed(lines):
            m = re.search(r'Positions:\s+(.+?)\s+→\s+Total:\s+([-+\d.]+)', line)
            if m:
                parts = m.group(1).split('|')
                for p in parts:
                    mm = re.match(r'(\w+)=([-+\d.]+)', p.strip())
                    if mm:
                        positions.append((mm.group(1), "?", 0, 0, 0, 0, float(mm.group(2)), 0))
                break

    return positions, pending

def monte_carlo_ruin(wr, rr, n_trades=1000, n_sim=10000, capital=200000, risk_pct=0.004):
    """Simule n_sim séquences de n_trades trades"""
    risk_per_trade = capital * risk_pct
    avg_win = risk_per_trade * rr
    avg_loss = risk_per_trade

    results = {"final_capital": [], "max_dd": [], "ruined": 0, "hit_target": 0}
    for _ in range(n_sim):
        cap = capital
        peak = capital
        ruined = False
        hit_target = False
        for _ in range(n_trades):
            if random.random() < wr:
                cap += avg_win
            else:
                cap -= avg_loss
            if cap > peak:
                peak = cap
            dd = peak - cap
            if dd > MAX_DD:
                ruined = True
                break
            if cap - capital >= PROFIT_TARGET:
                hit_target = True
                break
        results["final_capital"].append(cap - capital)
        results["max_dd"].append(peak - min(cap, peak))
        if ruined:
            results["ruined"] += 1
        if hit_target:
            results["hit_target"] += 1
    return {
        "avg_pnl": sum(results["final_capital"])/n_sim,
        "avg_max_dd": sum(results["max_dd"])/n_sim,
        "ruin_prob": results["ruined"]/n_sim*100,
        "target_prob": results["hit_target"]/n_sim*100,
        "best": max(results["final_capital"]),
        "worst": min(results["final_capital"]),
    }

def worst_case_current_positions(positions):
    """Calcule le pire drawdown possible - estime par la perte max si chaque pos va au SL"""
    if not positions:
        return 0, []
    total_fl = sum(p[-3] for p in positions)
    # Si on connait le SL, on peut calculer, sinon on estime à 3x le risque actuel
    # Le risque par trade = 0.4% × $200K = $800, SL à ~3×ATR
    risk_per = 800
    worst = total_fl - risk_per * len(positions)
    return worst, [(p[0], risk_per) for p in positions]

def correlation_analysis(positions):
    """Analyse la corrélation des positions actuelles"""
    groups = defaultdict(list)
    for pos in positions:
        sym = pos[0]
        profit = pos[-3]
        direction = pos[1]
        for g in CORRELATION_GROUPS:
            if sym in g:
                groups[tuple(g)].append((sym, direction, profit))
    print("\n  ── Analyse corrélation ──")
    violations = 0
    for g, pos_list in groups.items():
        dirs = defaultdict(list)
        for sym, d, p in pos_list:
            dirs[d].append((sym, p))
        print(f"  Groupe {list(g)}:")
        for d, items in dirs.items():
            print(f"    {d}: {len(items)} positions → {', '.join(f'{s}({p:+.0f})' for s,p in items)}")
            if len(items) > 2:
                print(f"    ⚠ VIOLATION: {len(items)} positions {d} dans ce groupe (max 2)")
                violations += 1
    return violations

def symbol_contribution():
    """Analyse contribution de chaque symbole au PnL total"""
    print("\n  ── Contribution par symbole (historique) ──")
    sym_contrib = {}
    for s in sorted(HISTORICAL_WR, key=lambda x: -HISTORICAL_TRADES.get(x, 0)):
        t = HISTORICAL_TRADES.get(s, 0)
        wr = HISTORICAL_WR.get(s, 0.5)
        if t < 5:
            print(f"  {s:>12s}: {t:4d}t WR={wr:.0%} ⚠ échantillon insuffisant")
            continue
        expectancy = wr * RR - (1-wr) * 1
        contrib = expectancy * t
        sym_contrib[s] = contrib
        print(f"  {s:>12s}: {t:4d}t WR={wr:.0%} expectancy={expectancy:+.2f}R contribution={contrib:+.0f}R")
    return sym_contrib

def main():
    print("=" * 60)
    print("  ANALYSE DE RISQUE FTMO CHALLENGE $200K")
    print("=" * 60)

    # ─── 1. Positions actuelles ───
    positions, pending = get_positions()
    print(f"\n  Positions ouvertes: {len(positions)}")
    print(f"  Ordres en attente: {len(pending)}")
    for sym, d, v, e, sl, _tp, p, _t in positions:
        sl_dist = abs(e - sl) if sl else 0
        print(f"    {sym:>12s} {d:5s} vol={v:.2f} entry={e:.5f} SL={sl:.5f}({sl_dist:.5f}) PnL={p:+.0f}")
    for sym, d, v, e, sl, _tp, p, _t in pending:
        print(f"    PEND {sym:>12s} {d:5s} vol={v:.2f} entry={e:.5f}")

    total_fl = sum(p[-3] for p in positions) if positions else 0

    # ─── 2. Corrélation ───
    correlation_analysis(positions)

    # ─── 3. Worst-case drawdown ───
    worst_dd, details = worst_case_current_positions(positions)
    print("\n  ── Scénario worst-case ──")
    print(f"    Floating actuel: {total_fl:+.0f}")
    print(f"    Si tout va au SL: -${worst_dd:.0f}")
    print(f"    Pire total: -${total_fl + worst_dd:.0f}")
    print(f"    % du capital: {(total_fl+worst_dd)/INITIAL_BALANCE*100:.2f}%")
    print(f"    FTMO max DD: {MAX_DD:.0f}")
    print(f"    Marge restante: ${MAX_DD - max(0, total_fl+worst_dd):.0f}")

    # ─── 4. Monte Carlo ───
    print("\n  ── Monte Carlo (wr=60.5%, RR=2.0, 1000 trades, 10000 sims) ──")
    mc = monte_carlo_ruin(0.605, RR, n_trades=500, n_sim=10000)
    print(f"    PnL moyen: ${mc['avg_pnl']:.0f}")
    print(f"    Ruin rate: {mc['ruin_prob']:.1f}%")
    print(f"    Atteinte target (+$20K): {mc['target_prob']:.1f}%")
    print(f"    Meilleur cas: ${mc['best']:.0f}")
    print(f"    Pire cas: ${mc['worst']:.0f}")

    # ─── 5. Par symbole ───
    symbol_contribution()

    # ─── 6. Risk budget ───
    print("\n  ── Budget risque ──")
    risk_per = INITIAL_BALANCE * RISK_PER_TRADE
    total_risk = risk_per * len(positions) if positions else 0
    print(f"    Risque par trade: ${risk_per:.0f} ({RISK_PER_TRADE*100:.1f}%)")
    print(f"    Risque total exposé: ${total_risk:.0f} ({total_risk/INITIAL_BALANCE*100:.1f}%)")
    print(f"    Jours pour target à $500/j: {PROFIT_TARGET/500:.0f}j")

    # ─── 7. Scenario analysis ───
    print("\n  ── Scénarios ──")
    for label, wr, rr_val, _desc in [
        ("Réel (60.5% WR, RR=2.0)", 0.605, 2.0, "Performance actuelle"),
        ("ETHUSD supprimé (61.5% WR)", 0.615, 2.0, "Sans le symbole qui perd"),
        ("Seuils réduits (55% WR)", 0.55, 2.0, "WR plus bas mais + de trades"),
        ("Marché défavorable (50% WR)", 0.50, 2.0, "WR aléatoire"),
        ("Crise (40% WR)", 0.40, 2.0, "Pire scénario réaliste"),
    ]:
        mc2 = monte_carlo_ruin(wr, rr_val, n_trades=500, n_sim=5000)
        print(f"    {label}: risk_ruin={mc2['ruin_prob']:.1f}% "
              f"target={mc2['target_prob']:.1f}% avg=${mc2['avg_pnl']:.0f}")

    # ─── 8. Daily risk budget ───
    print("\n  ── Budget journalier FTMO ──")
    print(f"    Daily loss max: ${DAILY_LIMIT:.0f} (2%)")
    print(f"    Pertes consécutives max: {DAILY_LIMIT/RISK_PER_TRADE:.0f} trades à -${RISK_PER_TRADE:.0f}")

if __name__ == "__main__":
    main()
