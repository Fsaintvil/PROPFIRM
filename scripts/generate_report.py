#!/usr/bin/env python3
"""Generate professional consolidated report from live trade data."""

import json
import csv
from datetime import datetime
from collections import defaultdict
from pathlib import Path

RUNTIME = Path("runtime")

# ── Load data ────────────────────────────────────────────────────────────────

with open(RUNTIME / "performance_history.json") as f:
    perf = json.load(f)

with open(RUNTIME / "robot_state.json") as f:
    state = json.load(f)

with open(RUNTIME / "trades_log.csv") as f:
    reader = csv.DictReader(f)
    trades_detail = list(reader)

with open(RUNTIME / "ftmo_report.json") as f:
    ftmo = json.load(f)

recent_trades = perf.get("recent_trades", [])
rolling = perf.get("rolling", {})
daily = perf.get("daily", {})
trade_history = state.get("trade_history", [])
daily_pnl = state.get("daily_pnl_by_date", {})

# ── Analyse par symbole ──────────────────────────────────────────────────────

symbols_data = defaultdict(
    lambda: {
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "pnl": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "max_win": 0.0,
        "max_loss": 0.0,
        "regimes": defaultdict(int),
        "directions": defaultdict(int),
    }
)

for t in recent_trades:
    sym = t.get("symbol", "?")
    profit = t.get("profit", 0)
    sd = symbols_data[sym]
    sd["trades"] += 1
    sd["pnl"] += profit
    if profit > 0:
        sd["wins"] += 1
        sd["gross_profit"] += profit
        sd["max_win"] = max(sd["max_win"], profit)
    else:
        sd["losses"] += 1
        sd["gross_loss"] += abs(profit)
        sd["max_loss"] = min(sd["max_loss"], profit)
    regime = t.get("regime", "?")
    sd["regimes"][regime] += 1
    direction = t.get("direction", "?")
    sd["directions"][direction] += 1

# ── Analyse quotidienne ──────────────────────────────────────────────────────

daily_analysis = {}
for date_str, pnl in sorted(daily_pnl.items()):
    day_trades = [
        t for t in recent_trades if str(t.get("ts", ""))[:10] == date_str or str(t.get("time", ""))[:10] == date_str
    ]
    wins = sum(1 for t in day_trades if t.get("profit", 0) > 0)
    total = len(day_trades)
    daily_analysis[date_str] = {
        "trades": total,
        "wins": wins,
        "wr": round(wins / total * 100, 1) if total else 0,
        "pnl": round(pnl, 2),
        "avg_trade": round(pnl / total, 2) if total else 0,
    }

# ── Rolling windows ──────────────────────────────────────────────────────────

rolling_data = {}
for key in ["last_20", "last_50", "last_100"]:
    r = rolling.get(key, {})
    if r.get("trades"):
        rolling_data[key] = {
            "trades": r["trades"],
            "wr": round(r.get("win_rate", 0) * 100, 1),
            "pnl": round(r.get("pnl", 0), 2),
            "pf": round(r.get("profit_factor", 0), 2),
        }

# ── Rapport structuré ────────────────────────────────────────────────────────

symbol_summary = []
for sym in ["XAUUSD", "EURUSD", "BTCUSD", "ETHUSD"]:
    sd = symbols_data.get(sym, {})
    if not sd.get("trades"):
        continue
    wr = round(sd["wins"] / sd["trades"] * 100, 1) if sd["trades"] else 0
    pf = round(sd["gross_profit"] / sd["gross_loss"], 2) if sd["gross_loss"] else float("inf")
    avg_win = round(sd["gross_profit"] / sd["wins"], 2) if sd["wins"] else 0
    avg_loss = round(sd["gross_loss"] / sd["losses"], 2) if sd["losses"] else 0
    symbol_summary.append(
        {
            "symbol": sym,
            "trades": sd["trades"],
            "wins": sd["wins"],
            "losses": sd["losses"],
            "win_rate": wr,
            "pnl": round(sd["pnl"], 2),
            "profit_factor": pf if pf != float("inf") else "N/A",
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "max_win": round(sd["max_win"], 2),
            "max_loss": round(sd["max_loss"], 2),
            "best_regime": max(sd["regimes"], key=sd["regimes"].get) if sd["regimes"] else "?",
            "direction_ratio": f"{sd['directions'].get('SELL', 0)}S/{sd['directions'].get('BUY', 0)}B",
        }
    )

report = {
    "generated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    "ftmo_challenge": {
        "balance": ftmo.get("balance"),
        "equity": ftmo.get("equity"),
        "pnl": round(ftmo.get("pnl", 0), 2),
        "profit_progress": ftmo.get("profit_progress"),
        "profit_remaining": ftmo.get("profit_remaining"),
        "dd_from_peak": ftmo.get("dd_from_peak"),
        "trading_days": ftmo.get("trading_days"),
        "days_remaining": ftmo.get("days_remaining"),
        "total_trades": ftmo.get("total_trades"),
        "win_rate": ftmo.get("win_rate"),
        "consistency_violated": ftmo.get("consistency_violated"),
        "best_day_pct": ftmo.get("best_day_pct"),
        "consecutive_losses": ftmo.get("consecutive_losses"),
        "status": ftmo.get("status"),
    },
    "symbol_performance": symbol_summary,
    "daily_breakdown": daily_analysis,
    "rolling_windows": rolling_data,
    "totals": {
        "total_trades": len(recent_trades),
        "total_pnl": round(sum(t.get("profit", 0) for t in recent_trades), 2),
        "total_wins": sum(1 for t in recent_trades if t.get("profit", 0) > 0),
        "total_losses": sum(1 for t in recent_trades if t.get("profit", 0) <= 0),
        "global_wr": round(sum(1 for t in recent_trades if t.get("profit", 0) > 0) / len(recent_trades) * 100, 1)
        if recent_trades
        else 0,
        "best_day": "?",
        "worst_day": "?",
    },
}

if daily_analysis:
    report["totals"]["best_day"] = max(daily_analysis.items(), key=lambda x: x[1]["pnl"])[0]
    report["totals"]["worst_day"] = min(daily_analysis.items(), key=lambda x: x[1]["pnl"])[0]

# ── Export JSON ──────────────────────────────────────────────────────────────

with open(RUNTIME / "live_report.json", "w") as f:
    json.dump(report, f, indent=2)
print("✅ Rapport exporté : runtime/live_report.json")

# ── Export CSV consolidé ─────────────────────────────────────────────────────

csv_path = RUNTIME / "trades_consolidated_live.csv"
with open(csv_path, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["timestamp", "symbol", "direction", "profit", "regime", "source"])
    for t in recent_trades:
        writer.writerow(
            [
                t.get("ts", t.get("time", "")),
                t.get("symbol", ""),
                t.get("direction", ""),
                round(t.get("profit", 0), 2),
                t.get("regime", ""),
                "LIVE",
            ]
        )
    # Ajouter les trades historiques du robot_state si non présents
    existing = {(t.get("symbol"), round(t.get("profit", 0), 2)) for t in recent_trades}
    for t in trade_history:
        key = (t.get("symbol"), round(t.get("profit", 0), 2))
        if key not in existing:
            writer.writerow(
                [
                    t.get("time", ""),
                    t.get("symbol", ""),
                    "",
                    round(t.get("profit", 0), 2),
                    "",
                    "STATE",
                ]
            )
print(f"✅ CSV exporté : {csv_path} ({sum(1 for _ in open(csv_path)) - 1} lignes)")

# ── Print summary ────────────────────────────────────────────────────────────

print()
print("=" * 60)
print("RAPPORT DE PERFORMANCE — TRADES LIVE")
print("=" * 60)
print()
print(f"Trades analysés:       {report['totals']['total_trades']}")
print(f"PnL total:             ${report['totals']['total_pnl']:+,.2f}")
print(f"Win Rate global:       {report['totals']['global_wr']}%")
print(f"Wins/Losses:           {report['totals']['total_wins']}W / {report['totals']['total_losses']}L")
print(f"Meilleur jour:         {report['totals']['best_day']}")
print(f"Pire jour:             {report['totals']['worst_day']}")
print()
print("── Performance par Symbole ──")
print(f"{'Symbole':8} {'Trades':6} {'WR':6} {'PnL':>10} {'PF':6} {'Avg Win':8} {'Avg Loss':8}")
print("-" * 56)
for s in report["symbol_performance"]:
    print(
        f"{s['symbol']:8} {s['trades']:6} {s['win_rate']:5.1f}% ${s['pnl']:>+7.2f} "
        f"{s['profit_factor'] if s['profit_factor'] != 'N/A' else 'N/A':>6} "
        f"${s['avg_win']:>6.2f} ${s['avg_loss']:>7.2f}"
    )
print()
print("── Rolling Windows ──")
for k, v in rolling_data.items():
    print(f"{k:12}: {v['trades']}T  WR={v['wr']}%  PnL=${v['pnl']:+.1f}  PF={v['pf']}")
print()
print("── Breakdown Quotidien ──")
for day, d in sorted(daily_analysis.items()):
    print(f"{day}: {d['trades']}T  WR={d['wr']}%  PnL=${d['pnl']:+.2f}  Avg=${d['avg_trade']:+.2f}")
print()
print("── Challenge FTMO ──")
c = report["ftmo_challenge"]
print(f"Balance: ${c['balance']:,.2f}  |  Equity: ${c['equity']:,.2f}")
print(f"PnL: ${c['pnl']:+,.2f} ({c['profit_progress']})  |  Restant: {c['profit_remaining']}")
print(
    f"DD Peak: {c['dd_from_peak']}  |  Consistency: {'❌ VIOLATED' if c['consistency_violated'] else '✅ OK'} ({c['best_day_pct']})"
)
print(
    f"Jours: {c['trading_days']}/{c['days_remaining'] + c['trading_days']}  |  Trades: {c['total_trades']}  |  WR: {c['win_rate']}"
)
print("=" * 60)
