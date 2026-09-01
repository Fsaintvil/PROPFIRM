#!/usr/bin/env python3
"""Monitoring automatique - snapshot toutes les 4h"""
import MetaTrader5 as mt5
import json
import os
from datetime import datetime

def run_monitoring():
    """Capture un snapshot complet du robot."""
    try:
        mt5.initialize()
        ai = mt5.account_info()
        pos = mt5.positions_get()
        
        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "account": {
                "balance": ai.balance,
                "equity": ai.equity,
                "profit": ai.profit,
                "margin": ai.margin,
                "free_margin": ai.margin_free,
            },
            "positions_count": len(pos) if pos else 0,
            "positions": []
        }
        
        if pos:
            for p in pos:
                direction = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL"
                snapshot["positions"].append({
                    "symbol": p.symbol,
                    "direction": direction,
                    "volume": p.volume,
                    "profit": p.profit,
                    "sl": p.sl,
                    "tp": p.tp,
                })
        
        mt5.shutdown()
        
        # Save
        filename = f"runtime/impact_snapshots/auto_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, "w") as f:
            json.dump(snapshot, f, indent=2)
        
        # Summary
        print(f"[{datetime.now().strftime('%H:%M')}] Balance=${ai.balance:,.2f} Equity=${ai.equity:,.2f} Floating=${ai.profit:+,.2f} Positions={len(pos) if pos else 0}")
        return True
        
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M')}] ERROR: {e}")
        return False

if __name__ == "__main__":
    run_monitoring()
