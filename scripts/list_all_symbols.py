"""List ALL configured symbols."""
import yaml

with open("config/default.yaml", "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

limits = cfg.get("symbol_limits", {})

# Group by category
forex_majors = ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "AUDUSD", "NZDUSD", "USDCHF", "EURGBP"]
forex_cross = ["EURJPY", "GBPJPY", "AUDJPY"]
crypto = ["BTCUSD", "ETHUSD", "SOLUSD", "BNBUSD"]
indices = ["US500.cash", "US30.cash", "US100.cash", "JP225.cash", "GER40.cash", "UK100.cash"]
commodities = ["XAUUSD", "XAGUSD", "USOIL.cash", "UKOIL.cash", "NATGAS.cash"]

categories = [
    ("FOREX MAJORS", forex_majors),
    ("FOREX CROSSES", forex_cross),
    ("CRYPTO", crypto),
    ("INDICES", indices),
    ("COMMODITIES", commodities),
]

# Count active (risk_mult > 0)
active = 0
inactive = 0

for cat_name, syms in categories:
    cat_active = 0
    rows = []
    for s in syms:
        if s not in limits:
            continue
        lim = limits[s]
        lot = lim.get("max_lot", "?")
        ms = lim.get("min_score", "?")
        risk = lim.get("risk_mult", 1.0)
        session = lim.get("preferred_hours", "24/7")
        if isinstance(session, list):
            session = f"[{session[0]}-{session[-1]}]"
        is_active = float(risk) > 0
        if is_active:
            cat_active += 1
            active += 1
        else:
            inactive += 1
        rows.append((s, lot, ms, risk, session, is_active))
    
    print(f"\n--- {cat_name} ({cat_active}/{len(rows)} actifs) ---")
    for s, lot, ms, risk, session, is_active in rows:
        status = "ACTIVE" if is_active else "MORT"
        print(f"  {s:<16} max_lot={lot:<5} min_score={ms:<5} risk={risk:<5} session={session:<10} {status}")

print(f"\n{'='*50}")
print(f"TOTAL: {len(limits)} symboles configures")
print(f"  ACTIFS (risk > 0): {active}")
print(f"  MORTS (risk = 0):  {inactive}")
