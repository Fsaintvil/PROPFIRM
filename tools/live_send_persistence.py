"""Persistence helpers for live send results.

Writes a JSON file per send and keeps a rolling append log for audit.
"""
import os
import json
from datetime import datetime


def persist_send_results(results, meta=None, artifacts_dir=None):
    artifacts_dir = artifacts_dir or os.path.join(os.path.dirname(__file__), "..", "artifacts", "live_trading")
    artifacts_dir = os.path.abspath(artifacts_dir)
    os.makedirs(artifacts_dir, exist_ok=True)
    try:
        ts = int(datetime.utcnow().timestamp())
        fn = os.path.join(artifacts_dir, f"ai_send_{ts}.json")
        payload = {"meta": meta or {}, "results": []}
        for r in results:
            req, res = r
            payload["results"].append({
                "request": req,
                "retcode": getattr(res, 'retcode', None) if res is not None else None,
                "order": getattr(res, 'order', None) if res is not None else None,
            })
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        # also append minimal summary to a rolling log
        logfn = os.path.join(artifacts_dir, "ai_send_roll.log")
        with open(logfn, "a", encoding="utf-8") as lf:
            lf.write(f"{datetime.utcnow().isoformat()} {fn} - {len(payload['results'])} orders\n")
        return fn
    except Exception as e:
        print("persist_send_results error:", e)
        return None
