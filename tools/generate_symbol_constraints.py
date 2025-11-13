#!/usr/bin/env python3
"""Collecte `symbol_info` et `symbol_info_tick` pour les SYMBOLS et écrit
artifacts/live_trading/symbol_constraints.json.

Usage: python tools/generate_symbol_constraints.py --output artifacts/live_trading/symbol_constraints.json
"""
import argparse
import json
import os
import time

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None


def collect(symbols):
    results = {}
    if mt5 is None:
        return {s: {"error": "mt5_not_installed"} for s in symbols}

    if not mt5.initialize():
        return {s: {"error": "mt5_initialize_failed", "last_error": mt5.last_error()} for s in symbols}

    for s in symbols:
        try:
            sinfo = mt5.symbol_info(s)
            tick = mt5.symbol_info_tick(s)
        except Exception as e:
            results[s] = {"error": "exception", "message": str(e)}
            continue

        if sinfo is None:
            results[s] = {"error": "symbol_info_none"}
            continue

        entry = {
            "point": float(sinfo.point) if hasattr(sinfo, 'point') else None,
            "digits": int(sinfo.digits) if hasattr(sinfo, 'digits') else None,
            "trade_stops_level": int(sinfo.trade_stops_level) if hasattr(sinfo, 'trade_stops_level') else None,
            "trade_tick_value": float(sinfo.trade_tick_value) if hasattr(sinfo, 'trade_tick_value') else None,
            "trade_contract_size": float(sinfo.trade_contract_size) if hasattr(sinfo, 'trade_contract_size') else None,
        }

        if tick:
            entry.update({
                "bid": float(tick.bid) if hasattr(tick, 'bid') else None,
                "ask": float(tick.ask) if hasattr(tick, 'ask') else None,
            })
            if entry.get("bid") is not None and entry.get("ask") is not None:
                entry["spread"] = abs(entry["ask"] - entry["bid"])
        else:
            entry.update({"bid": None, "ask": None, "spread": None})

        # compute a conservative minimal stops distance in price units
        min_by_stop_level = None
        if entry.get("trade_stops_level") is not None and entry.get("point") is not None:
            min_by_stop_level = entry["trade_stops_level"] * entry["point"]

        # require stops to be at least spread + small safety buffer
        safety = (entry.get("spread") or 0.0) + (entry.get("point") or 0.0) * 2

        # choose final minimal stop distance
        candidates = [c for c in [min_by_stop_level, safety] if c is not None]
        final_min = max(candidates) if candidates else None
        entry["min_stop_distance"] = final_min

        results[s] = entry

    mt5.shutdown()
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    symbols_env = os.getenv('SYMBOLS')
    if symbols_env:
        symbols = [s.strip() for s in symbols_env.split(',') if s.strip()]
    else:
        symbols = ["EURUSD","EURJPY","USDCAD","AUDNZD"]

    ts = time.strftime("%Y%m%dT%H%M%SZ")
    out = args.output or os.path.join("artifacts", "live_trading", f"symbol_constraints_{ts}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    res = collect(symbols)
    meta = {"timestamp": ts, "symbols": symbols}
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({"meta": meta, "results": res}, f, indent=2)

    print(f"Wrote {out}")


if __name__ == '__main__':
    main()
