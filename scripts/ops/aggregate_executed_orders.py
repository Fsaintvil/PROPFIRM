#!/usr/bin/env python3
"""Aggrégateur d'ordres exécutés (logs texte + fichiers JSON) -> CSV summary

Usage:
  python scripts/ops/aggregate_executed_orders.py --output artifacts/reports/executed_orders_summary.csv

Le script est non-invasif : lecture seule des dossiers `logs/` et recherche de
fichiers JSON `execute_live_trades_apply_*.json`.
"""
from __future__ import annotations
import re
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime
import argparse


DEFAULT_SYMBOLS = [
    "BTCUSD",
    "EURUSD",
    "XAUUSD",
    "USDJPY",
    "ETHUSD",
    "USDCAD",
    "AUDNZD",
    "EURJPY",
    "GBPCHF",
    "NZDJPY",
    "EURAUD",
    "GBPUSD",
]


def parse_log_line_for_symbol(line: str, symbols: list[str]) -> tuple[str | None, str | None]:
    """Return (symbol, timestamp_str) if line contains an execution marker and a known symbol.

    Support French and English log messages (e.g. 'Ordre exécuté', 'Order executed').
    """
    pattern = re.compile(
        r"Ordre exécuté|Order executed|Order executed:|✅ Order executed|✅ Ordre exécuté",
        re.IGNORECASE,
    )
    if not pattern.search(line):
        return None, None
    # try to extract ISO-like timestamp at start
    ts = None
    m = re.match(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:,\d{3})?)", line)
    if m:
        ts = m.group(1).replace(",", ".")
    # find a known symbol with word boundaries
    for s in symbols:
        if re.search(rf"\b{s}\b", line):
            return s, ts
    return None, ts


def scan_logs_for_orders(log_dir: Path, symbols: list[str]):
    counts = defaultdict(int)
    earliest = {}
    latest = {}
    samples = {}

    for p in sorted(log_dir.glob("*.log")):
        try:
            with p.open("r", encoding="utf-8", errors="ignore") as fh:
                for ln in fh:
                    sym, ts = parse_log_line_for_symbol(ln, symbols)
                    if not sym:
                        continue
                    counts[sym] += 1
                    # parse timestamp if available
                    if ts:
                        try:
                            dt = datetime.fromisoformat(ts)
                        except Exception:
                            dt = None
                    else:
                        dt = None
                    if dt:
                        if sym not in earliest or dt < earliest[sym]:
                            earliest[sym] = dt
                        if sym not in latest or dt > latest[sym]:
                            latest[sym] = dt
                    # keep one sample line
                    samples.setdefault(sym, (p.name, ln.strip()))
        except Exception as e:
            print(f"Warning: failed to read {p}: {e}")

    return counts, earliest, latest, samples


def scan_json_for_orders(root: Path, symbols: list[str]):
    counts = defaultdict(int)
    earliest = {}
    latest = {}
    samples = {}

    for p in sorted(root.glob("execute_live_trades_apply_*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        # data may be list or dict
        items = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
        for it in items:
            sym = None
            if isinstance(it, dict):
                sym = it.get("symbol") or it.get("instrument")
                ts = it.get("timestamp") or it.get("time")
            else:
                sym = None
                ts = None
            if not sym:
                # fallback: look for any known symbol in JSON string
                stext = json.dumps(it)
                for s in symbols:
                    if re.search(rf"\b{s}\b", stext):
                        sym = s
                        break
            if not sym:
                continue
            counts[sym] += 1
            dt = None
            if ts:
                try:
                    dt = datetime.fromisoformat(ts)
                except Exception:
                    dt = None
            if dt:
                if sym not in earliest or dt < earliest[sym]:
                    earliest[sym] = dt
                if sym not in latest or dt > latest[sym]:
                    latest[sym] = dt
            samples.setdefault(sym, (p.name, json.dumps(it, ensure_ascii=False)[:200]))

    return counts, earliest, latest, samples


def merge_results(*results):
    total_counts = defaultdict(int)
    earliest = {}
    latest = {}
    samples = {}
    for counts, e, l, s in results:
        for k, v in counts.items():
            total_counts[k] += v
        for k, dt in e.items():
            if k not in earliest or dt < earliest[k]:
                earliest[k] = dt
        for k, dt in l.items():
            if k not in latest or dt > latest[k]:
                latest[k] = dt
        for k, sample in s.items():
            samples.setdefault(k, sample)
    return total_counts, earliest, latest, samples


def write_csv(path: Path, symbols: list[str], counts, earliest, latest, samples):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("symbol,count,earliest,latest,sample_file,sample_line\n")
        for sym in symbols:
            cnt = counts.get(sym, 0)
            e_dt = earliest.get(sym)
            l_dt = latest.get(sym)
            samp = samples.get(sym, ("", ""))
            # escape commas in sample line
            sample_line = samp[1].replace(',', ' ')
            row = (
                f"{sym},{cnt},"
                f"{e_dt.isoformat() if e_dt else ''},"
                f"{l_dt.isoformat() if l_dt else ''},"
                f"{samp[0]},{sample_line}\n"
            )
            fh.write(row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="artifacts/reports/executed_orders_summary.csv")
    ap.add_argument("--symbols-file", default="config/symbols_live.json")
    ap.add_argument("--log-dir", default="logs")
    args = ap.parse_args()

    symbols = DEFAULT_SYMBOLS
    sf = Path(args.symbols_file)
    if sf.exists():
        try:
            d = json.loads(sf.read_text(encoding="utf-8"))
            syms = d.get("symbols") if isinstance(d, dict) else None
            if syms:
                symbols = [s.strip().upper() for s in syms]
        except Exception:
            pass

    log_dir = Path(args.log_dir)
    counts_log, e_log, l_log, s_log = scan_logs_for_orders(log_dir, symbols)
    counts_json, e_json, l_json, s_json = scan_json_for_orders(Path("."), symbols)

    counts, earliest, latest, samples = merge_results(
        (counts_log, e_log, l_log, s_log), (counts_json, e_json, l_json, s_json)
    )

    out = Path(args.output)
    write_csv(out, symbols, counts, earliest, latest, samples)

    print("Executed orders summary written to:", out)
    for s in symbols:
        print(f"  {s}: {counts.get(s, 0)}")


if __name__ == "__main__":
    main()
