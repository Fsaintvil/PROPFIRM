#!/usr/bin/env python3
"""
Monitor Impact — Surveillance de l'impact des fixes du 1 Sept 2026.

Compare la performance AVANT (26/08→31/08) et APRÈS (01/09→) les changements :
- SOLUSD trailing relâché (lock 2.00, trail 1.20)
- XAUUSD time-stop loss 3h, profit 10h
- BTCUSD profit time-stop 15h
- JP225 adx_thresh 18
- Forex time_stop loss 3h
- Cooldown reset

Usage:
    python scripts/monitor_impact.py              # Rapport complet
    python scripts/monitor_impact.py --daily      # Juste aujourd'hui
    python scripts/monitor_impact.py --snapshot   # Sauvegarder baseline
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# ── Paths ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
STATE_FILE = PROJECT_ROOT / "runtime" / "robot_state.json"
SNAPSHOT_DIR = PROJECT_ROOT / "runtime" / "impact_snapshots"
BASELINE_DATE = "2026-09-01"  # Date des fixes


def load_state() -> dict:
    """Charge le robot_state.json."""
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_live_trades(state: dict) -> list[dict]:
    """Retourne les trades live (non-historiques)."""
    return [t for t in state.get("trade_history", []) if not t.get("historical", False)]


def trades_by_day(trades: list[dict]) -> dict[str, list[dict]]:
    """Groupe les trades par jour (YYYY-MM-DD)."""
    days: dict[str, list[dict]] = {}
    for t in trades:
        day = t["time"].split(" ")[0]
        days.setdefault(day, []).append(t)
    return days


def day_stats(day_trades: list[dict]) -> dict:
    """Calcule les stats d'une journée."""
    if not day_trades:
        return {"count": 0, "wins": 0, "losses": 0, "wr": 0, "pnl": 0, "avg_win": 0, "avg_loss": 0, "pf": 0}

    wins = [t for t in day_trades if t["profit"] > 0]
    losses = [t for t in day_trades if t["profit"] <= 0]
    total_win = sum(t["profit"] for t in wins)
    total_loss = abs(sum(t["profit"] for t in losses))

    return {
        "count": len(day_trades),
        "wins": len(wins),
        "losses": len(losses),
        "wr": len(wins) / len(day_trades) * 100 if day_trades else 0,
        "pnl": sum(t["profit"] for t in day_trades),
        "avg_win": total_win / len(wins) if wins else 0,
        "avg_loss": -total_loss / len(losses) if losses else 0,
        "pf": total_win / total_loss if total_loss > 0 else float("inf"),
    }


def symbol_stats(trades: list[dict]) -> dict[str, dict]:
    """Stats par symbole."""
    by_sym: dict[str, list[dict]] = {}
    for t in trades:
        by_sym.setdefault(t["symbol"], []).append(t)
    return {s: day_stats(ts) for s, ts in by_sym.items()}


def print_header(title: str) -> None:
    print(f"\n{'═' * 70}")
    print(f"  {title}")
    print(f"{'═' * 70}")


def print_day_row(day: str, stats: dict, emoji: str = "") -> None:
    pnl = stats["pnl"]
    icon = "🟢" if pnl > 0 else ("🔴" if pnl < 0 else "⚪")
    pf_str = f"{stats['pf']:.2f}" if stats['pf'] != float('inf') else "∞"
    print(
        f"  {icon} {day} | {stats['count']:3d} trades | "
        f"W:{stats['wins']:2d} L:{stats['losses']:2d} | "
        f"WR {stats['wr']:5.1f}% | PF {pf_str:>5s} | "
        f"PnL ${pnl:>+8.2f} {emoji}"
    )


def print_symbol_row(sym: str, stats: dict) -> None:
    pnl = stats["pnl"]
    icon = "🟢" if pnl > 0 else ("🔴" if pnl < 0 else "⚪")
    pf_str = f"{stats['pf']:.2f}" if stats['pf'] != float('inf') else "∞"
    print(
        f"  {icon} {sym:12s} | {stats['count']:3d}T | "
        f"WR {stats['wr']:5.1f}% | PF {pf_str:>5s} | "
        f"PnL ${pnl:>+8.2f}"
    )


def compare_periods(before: list[dict], after: list[dict]) -> None:
    """Compare les deux périodes."""
    b = day_stats(before)
    a = day_stats(after)

    print(f"\n  {'Métrique':20s} {'AVANT (26-31/08)':>20s} {'APRÈS (01/09+)':>20s} {'Δ':>12s}")
    print(f"  {'─' * 72}")

    metrics = [
        ("Trades", "count", "{:.0f}"),
        ("Win Rate %", "wr", "{:.1f}%"),
        ("Profit Factor", "pf", "{:.2f}"),
        ("PnL Total", "pnl", "${:+.2f}"),
        ("Avg Win", "avg_win", "${:+.2f}"),
        ("Avg Loss", "avg_loss", "${:+.2f}"),
    ]

    for label, key, fmt in metrics:
        bv = b[key] if key != "pf" or b[key] != float("inf") else 99.99
        av = a[key] if key != "pf" or a[key] != float("inf") else 99.99
        delta = av - bv if isinstance(av, (int, float)) and isinstance(bv, (int, float)) else 0
        if key == "wr":
            delta_str = f"{delta:+.1f}pt"
        elif key == "pnl":
            delta_str = f"${delta:+.0f}"
        else:
            delta_str = f"{delta:+.1f}"
        print(f"  {label:20s} {fmt.format(bv):>20s} {fmt.format(av):>20s} {delta_str:>12s}")


def detect_anomalies(after_trades: list[dict]) -> list[str]:
    """Détecte les anomalies dans la période post-fix."""
    alerts = []
    days = trades_by_day(after_trades)

    for day, trades in sorted(days.items()):
        s = day_stats(trades)

        # Alert: WR < 20% sur 5+ trades
        if s["count"] >= 5 and s["wr"] < 20:
            alerts.append(f"⚠️  {day}: WR {s['wr']:.1f}% sur {s['count']} trades — signal dégradé?")

        # Alert: 5+ pertes consécutives
        streak = 0
        for t in trades:
            if t["profit"] <= 0:
                streak += 1
                if streak >= 5:
                    alerts.append(f"🔴 {day}: {streak} pertes consécutives — circuit breaker?")
                    break
            else:
                streak = 0

        # Alert: PnL < -$50 en un jour
        if s["pnl"] < -50:
            alerts.append(f"🔴 {day}: PnL ${s['pnl']:.2f} — journée catastrophique")

    return alerts


def save_snapshot(state: dict, label: str = "daily") -> Path:
    """Sauvegarde un snapshot de l'état."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SNAPSHOT_DIR / f"snapshot_{label}_{ts}.json"

    trades = get_live_trades(state)
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "consecutive_losses": state.get("consecutive_losses", 0),
        "total_trades": len(trades),
        "stats": day_stats(trades),
        "by_symbol": symbol_stats(trades),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, default=str)
    print(f"  📸 Snapshot sauvegardé: {path}")
    return path


def main() -> None:
    args = sys.argv[1:]

    state = load_state()
    trades = get_live_trades(state)
    days = trades_by_day(trades)

    if "--snapshot" in args:
        save_snapshot(state, "manual")
        return

    # ── Séparation AVANT / APRÈS ──
    before_trades = [t for t in trades if t["time"] < BASELINE_DATE]
    after_trades = [t for t in trades if t["time"] >= BASELINE_DATE]

    if "--daily" in args:
        # Juste aujourd'hui
        today = datetime.now().strftime("%Y-%m-%d")
        today_trades = [t for t in after_trades if t["time"].startswith(today)]
        if not today_trades:
            print(f"  Aucun trade aujourd'hui ({today})")
            return
        s = day_stats(today_trades)
        print_day_row(today, s)
        by_sym = symbol_stats(today_trades)
        for sym, ss in sorted(by_sym.items(), key=lambda x: x[1]["pnl"], reverse=True):
            print_symbol_row(sym, ss)
        return

    # ── Rapport complet ──
    print_header("📊 MONITOR IMPACT — Fixes du 1 Sept 2026")

    # État actuel
    print(f"\n  🤖 Robot: PID lock={STATE_FILE.parent.parent / 'runtime' / 'robot.pid'}")
    print(f"  📈 Trades live: {len(trades)} | Consecutive losses: {state.get('consecutive_losses', 0)}")
    print(f"  📅 Baseline: {BASELINE_DATE} | AVANT: {len(before_trades)} trades | APRÈS: {len(after_trades)} trades")

    # Performance par jour
    print_header("📅 PERFORMANCE PAR JOUR")
    for day in sorted(days.keys()):
        emoji = " ⭐ FIX" if day >= BASELINE_DATE else ""
        print_day_row(day, day_stats(days[day]), emoji)

    # Comparaison
    print_header("🔄 COMPARAISON AVANT / APRÈS")
    compare_periods(before_trades, after_trades)

    # Top/Bottom symboles post-fix
    if after_trades:
        print_header("🏆 TOP/BOTTOM SYMBOLES (après fixes)")
        by_sym = symbol_stats(after_trades)
        sorted_syms = sorted(by_sym.items(), key=lambda x: x[1]["pnl"], reverse=True)
        for sym, s in sorted_syms:
            print_symbol_row(sym, s)

    # Anomalies
    if after_trades:
        alerts = detect_anomalies(after_trades)
        if alerts:
            print_header("🚨 ALERTES")
            for a in alerts:
                print(f"  {a}")
        else:
            print(f"\n  ✅ Aucune anomalie détectée dans la période post-fix.")

    # Cumulatif
    print_header("📈 CUMULATIF")
    cumul = 0
    for day in sorted(days.keys()):
        s = day_stats(days[day])
        cumul += s["pnl"]
        icon = "🟢" if s["pnl"] > 0 else ("🔴" if s["pnl"] < 0 else "⚪")
        marker = " ← FIX" if day == BASELINE_DATE else ""
        print(f"  {icon} {day}: ${s['pnl']:>+8.2f} → cumul ${cumul:>+9.2f}{marker}")

    # Snapshot
    save_snapshot(state, "report")


if __name__ == "__main__":
    main()
