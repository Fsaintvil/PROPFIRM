"""
Agrégateur de performance basé sur les trades réalisés (logs/trades.json[l]).

Principe:
- Lit les lignes JSON des logs de trades (format JSON Lines)
- Ne conserve que les entrées possédant un champ `closed_profit`
- Déduplique par (ticket, timestamp) quand possible
- Agrège par jour (UTC) les métriques: PnL, nb trades, win rate, drawdown, etc.
- Écrit des rapports journaliers dans artifacts/performance/

Aucune dépendance externe; utilisable en import (API) ou en script (__main__).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass
class ClosedTrade:
    timestamp: datetime
    ticket: Optional[int]
    symbol: Optional[str]
    profit: float


def _parse_trade_line(line: str) -> Optional[ClosedTrade]:
    try:
        rec = json.loads(line)
        if "closed_profit" not in rec:
            return None
        # Timestamp: tenter ISO 8601, sinon format commun
        ts_raw = rec.get("timestamp")
        if not ts_raw:
            return None
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except Exception:
            try:
                ts = datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S")
                ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                return None

        # Normaliser en UTC si naïf
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)

        profit = float(rec.get("closed_profit"))
        ticket = rec.get("ticket")
        symbol = rec.get("symbol")
        return ClosedTrade(timestamp=ts, ticket=ticket, symbol=symbol, profit=profit)
    except Exception:
        return None


def load_closed_trades(logs_dir: Path | str = "logs") -> List[ClosedTrade]:
    logs_dir = Path(logs_dir)
    candidates = [
        logs_dir / "trades.jsonl",
        logs_dir / "trades.json",
    ]

    lines: List[str] = []
    for path in candidates:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    lines.extend(f.readlines())
            except Exception:
                # Continuer avec les autres fichiers
                pass

    trades: List[ClosedTrade] = []
    seen: set[Tuple[Optional[int], str]] = set()

    for line in lines:
        ct = _parse_trade_line(line)
        if not ct:
            continue
        key = (ct.ticket, ct.timestamp.isoformat())
        if key in seen:
            continue
        seen.add(key)
        trades.append(ct)

    # Tri par temps croissant
    trades.sort(key=lambda t: t.timestamp)
    return trades


def _max_drawdown(series: List[float]) -> float:
    """Retourne le max drawdown (valeur négative) pour une courbe cumulée.
    series: liste des cumuls successifs (pnl cumulatif)
    """
    if not series:
        return 0.0
    peak = series[0]
    max_dd = 0.0
    for x in series:
        if x > peak:
            peak = x
        dd = (x - peak)
        if dd < max_dd:
            max_dd = dd
    return max_dd


def _profit_factor(profits: Iterable[float]) -> float:
    pos = sum(p for p in profits if p > 0)
    neg = -sum(p for p in profits if p < 0)
    if neg == 0:
        return float("inf") if pos > 0 else 0.0
    return pos / neg


def aggregate_daily_metrics(trades: List[ClosedTrade]) -> Dict[str, dict]:
    by_day: Dict[date, List[ClosedTrade]] = {}
    for t in trades:
        d = t.timestamp.date()  # UTC date
        by_day.setdefault(d, []).append(t)

    out: Dict[str, dict] = {}
    for d, lst in by_day.items():
        profits = [t.profit for t in lst]
        pnl = float(sum(profits))
        total = len(lst)
        wins = sum(1 for p in profits if p > 0)
        losses = sum(1 for p in profits if p <= 0)
        win_rate = (wins / total * 100.0) if total else 0.0

        # Courbe cumulée intrajournalière
        cumul = []
        acc = 0.0
        for p in profits:
            acc += p
            cumul.append(acc)
        max_dd = _max_drawdown(cumul)

        # Sharpe proxy (sur profits, pas des returns), échelle sqrt(n)
        mean = pnl / total if total else 0.0
        variance = 0.0
        if total > 1:
            variance = sum((p - mean) ** 2 for p in profits) / (total - 1)
        std = variance ** 0.5
        sharpe = (mean / std) * (total ** 0.5) if std > 0 else 0.0

        out[d.isoformat()] = {
            "date": d.isoformat(),
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(win_rate, 2),
            "pnl": round(pnl, 2),
            "max_drawdown": round(max_dd, 2),  # même unité que PnL
            "profit_factor": round(_profit_factor(profits), 3),
            "avg_profit": round(mean, 2),
            "sharpe_proxy": round(sharpe, 3),
        }

    return out


def write_reports(
    metrics_by_day: Dict[str, dict],
    out_dir: Path | str = "artifacts/performance",
) -> List[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: List[Path] = []
    for day, payload in metrics_by_day.items():
        path = out_dir / f"perf_{day.replace('-', '')}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            written.append(path)
        except Exception:
            # ignorer l'échec d'écriture par fichier
            pass

    # Écrire un résumé global (dernière journée + agrégés)
    if metrics_by_day:
        days_sorted = sorted(metrics_by_day.keys())
        last_day = days_sorted[-1]
        total_trades = sum(m["total_trades"] for m in metrics_by_day.values())
        total_pnl = sum(m["pnl"] for m in metrics_by_day.values())
        summary = {
            "last_day": metrics_by_day[last_day],
            "total_trades": total_trades,
            "total_pnl": round(total_pnl, 2),
            "days": days_sorted,
        }
        try:
            with open(out_dir / "summary_latest.json", "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    return written


def get_today_summary(logs_dir: Path | str = "logs") -> Optional[dict]:
    trades = load_closed_trades(logs_dir)
    if not trades:
        return None
    metrics = aggregate_daily_metrics(trades)
    iso_today = datetime.now(timezone.utc).date().isoformat()
    return metrics.get(iso_today)


def run(logs_dir: Path | str = "logs", out_dir: Path | str = "artifacts/performance") -> List[Path]:
    trades = load_closed_trades(logs_dir)
    metrics = aggregate_daily_metrics(trades)
    return write_reports(metrics, out_dir)


if __name__ == "__main__":
    written = run()
    if written:
        print("✅ Rapports générés:")
        for p in written:
            print(" -", p)
    else:
        print("⚠️ Aucun trade clôturé trouvé dans les logs.")
