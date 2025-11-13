#!/usr/bin/env python3
"""Inspecte des positions MT5 par ticket et écrit un artefact JSON.

Usage:
  python tools/mt5_inspect_positions.py --tickets 123,456 --output artifacts/live_trading/out.json

Le script requiert la librairie MetaTrader5 installée et un terminal MT5 connecté.
"""
import argparse
import json
import time
import os

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickets", required=True, help="Comma-separated ticket ids")
    parser.add_argument("--output", required=False, default=None, help="Output file path")
    args = parser.parse_args()

    tickets = [t.strip() for t in args.tickets.split(",") if t.strip()]
    ts = time.strftime("%Y%m%dT%H%M%SZ")
    outpath = args.output or os.path.join("artifacts", "live_trading", f"mt5_positions_check_{ts}.json")
    os.makedirs(os.path.dirname(outpath), exist_ok=True)

    results = []
    meta = {"timestamp": ts, "queried": tickets}

    if mt5 is None:
        print("MetaTrader5 python package not available in this environment.")
        meta["error"] = "mt5_not_installed"
        with open(outpath, "w", encoding="utf-8") as f:
            json.dump({"meta": meta, "results": results}, f, indent=2)
        return

    if not mt5.initialize():
        meta["error"] = "mt5_initialize_failed"
        meta["init_last_error"] = mt5.last_error()
        with open(outpath, "w", encoding="utf-8") as f:
            json.dump({"meta": meta, "results": results}, f, indent=2)
        return

    for t in tickets:
        entry = {"ticket": None, "symbol": None}
        try:
            ticket = int(t)
        except Exception:
            results.append({"ticket": t, "error": "invalid_ticket_id"})
            continue

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            results.append({"ticket": ticket, "error": "position_not_found"})
            continue

        pos = positions[0]
        symbol = pos.symbol
        entry = {
            "ticket": int(pos.ticket),
            "symbol": symbol,
            "volume": float(pos.volume),
            "price_open": float(pos.price_open) if hasattr(pos, 'price_open') else None,
            "sl": float(pos.sl) if pos.sl is not None else None,
            "tp": float(pos.tp) if pos.tp is not None else None,
            "type": int(pos.type),
        }

        # get tick and symbol info
        try:
            tick = mt5.symbol_info_tick(symbol)
        except Exception:
            tick = None
        try:
            sinfo = mt5.symbol_info(symbol)
        except Exception:
            sinfo = None

        if tick:
            entry.update({
                "bid": float(tick.bid) if hasattr(tick, 'bid') else None,
                "ask": float(tick.ask) if hasattr(tick, 'ask') else None,
            })
            if entry.get("bid") is not None and entry.get("ask") is not None:
                entry["spread"] = abs(entry["ask"] - entry["bid"])
        else:
            entry.update({"bid": None, "ask": None, "spread": None})

        if sinfo:
            entry.update({
                "point": float(sinfo.point) if hasattr(sinfo, 'point') else None,
                "digits": int(sinfo.digits) if hasattr(sinfo, 'digits') else None,
                "trade_stops_level": int(sinfo.trade_stops_level) if hasattr(sinfo, 'trade_stops_level') else None,
            })
        else:
            entry.update({"point": None, "digits": None, "trade_stops_level": None})

        results.append(entry)

    mt5.shutdown()

    with open(outpath, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "results": results}, f, indent=2)

    print(f"Wrote {outpath}")


if __name__ == "__main__":
    main()
