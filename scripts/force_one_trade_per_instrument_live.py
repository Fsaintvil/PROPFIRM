#!/usr/bin/env python3
"""
Forcer exactement 1 trade RÉEL par instrument (pas de simulation/paper).

Exemples:
  python scripts/force_one_trade_per_instrument_live.py --symbols EURUSD,XAUUSD,BTCUSD --action alternate --lot 0.01
  python scripts/force_one_trade_per_instrument_live.py --action buy --lot 0.02 --deviation 80
"""

import sys
import os
import json
import time
from datetime import datetime
import argparse

# Ajouter le répertoire parent au path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
sys.path.append('scripts')

try:
    from live_trading_engine import LiveTradingEngine  # type: ignore
    import MetaTrader5 as mt5  # type: ignore
except Exception as e:
    print(f"❌ Erreur import: {e}")
    sys.exit(1)


class ForceOneTradeLive(LiveTradingEngine):
    def __init__(self, symbols, lot_size=0.01, force_hours=False):
        super().__init__(
            symbols=symbols,
            lot_sizes={s: lot_size for s in symbols},
            max_risk_per_trade=0.02,
        )
        self.force_hours = force_hours

    def is_market_open(self, symbol):  # type: ignore[override]
        if self.force_hours:
            return True
        return super().is_market_open(symbol)

    def send_market_order(self, action: str, symbol: str, lot_size: float, deviation: int = 50) -> dict:
        """Envoie un ordre marché réel avec quelques fallbacks de remplissage."""
        out = {
            "symbol": symbol,
            "action": action,
            "volume": lot_size,
            "success": False,
            "error": None,
            "order_id": None,
            "price": None,
        }
        if not mt5.initialize():
            out["error"] = "MT5 initialize failed"
            return out

        try:
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                out["error"] = "No tick"
                return out

            if action == "buy":
                order_type = mt5.ORDER_TYPE_BUY
                price = tick.ask
            else:
                order_type = mt5.ORDER_TYPE_SELL
                price = tick.bid
            out["price"] = float(price)

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": float(lot_size),
                "type": order_type,
                "price": float(price),
                "deviation": int(deviation),
                "magic": 235001,
                "comment": f"FORCE_ONE_LIVE_{action.upper()}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            result = mt5.order_send(request)
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                request["type_filling"] = mt5.ORDER_FILLING_FOK
                result = mt5.order_send(request)
                if result.retcode != mt5.TRADE_RETCODE_DONE:
                    request["type_filling"] = mt5.ORDER_FILLING_RETURN
                    request["deviation"] = max(int(deviation), 100)
                    result = mt5.order_send(request)

            if result.retcode == mt5.TRADE_RETCODE_DONE:
                out["success"] = True
                out["order_id"] = int(result.order)
                self.logger.info(
                    f"✅ RÉEL {action.upper()} {symbol} {lot_size} @ {price} (order={result.order})"
                )
                # historiser
                self.trade_history.append({
                    "timestamp": datetime.now(),
                    "symbol": symbol,
                    "action": action,
                    "volume": lot_size,
                    "price": float(price),
                    "order_id": int(result.order),
                    "forced": True,
                    "simulated": False,
                })
                self.performance_metrics["total_trades"] += 1
            else:
                out["error"] = f"{result.comment} (code {result.retcode})"
                self.logger.error(f"❌ Échec {symbol}: {out['error']}")

            return out
        except Exception as e:
            out["error"] = str(e)
            self.logger.error(f"❌ Exception ordre {symbol}: {e}")
            return out


def parse_args():
    ap = argparse.ArgumentParser(description="Force 1 trade RÉEL par instrument")
    ap.add_argument("--symbols", default="EURUSD,XAUUSD,BTCUSD", help="Liste de symboles séparés par des virgules")
    ap.add_argument("--action", choices=["buy", "sell", "alternate"], default="alternate", help="Action à exécuter")
    ap.add_argument("--lot", type=float, default=0.01, help="Taille de lot par trade")
    ap.add_argument("--deviation", type=int, default=50, help="Déviation MT5 (points/pips selon broker)")
    ap.add_argument("--force-hours", action="store_true", help="Ignorer la vérification heures de marché")
    return ap.parse_args()


def main():
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    engine = ForceOneTradeLive(symbols=symbols, lot_size=args.lot, force_hours=args.force_hours)

    print("🚀 FORCE LIVE: 1 trade par instrument (RÉEL)")
    print("=" * 58)
    print(f"📊 Symboles: {symbols}")
    print(f"🎯 Action: {args.action}")
    print(f"💰 Lot: {args.lot}")
    print()

    # Connexion MT5 réelle (utilise credentials de la config moteur)
    if not engine.connect_mt5():
        print("❌ Connexion MT5 échouée — abandon")
        sys.exit(2)

    results = []
    for i, sym in enumerate(symbols):
        action = ("buy" if i % 2 == 0 else "sell") if args.action == "alternate" else args.action
        print(f"🎯 {sym} -> {action.upper()} (live)")
        # récupérer qques données (pas obligatoire pour l'ordre)
        try:
            _ = engine.get_live_data(sym, 50)
        except Exception:
            pass
        res = engine.send_market_order(action, sym, args.lot, args.deviation)
        results.append(res)
        time.sleep(0.5)

    # Sauvegarde artefact
    try:
        os.makedirs("artifacts/forced_trades", exist_ok=True)
        session = {
            "timestamp": datetime.now().isoformat(),
            "type": "one_per_instrument_live",
            "symbols": symbols,
            "executed": sum(1 for r in results if r.get("success")),
            "failed": sum(1 for r in results if not r.get("success")),
            "results": results,
            "performance_metrics": engine.performance_metrics,
        }
        fn = f"artifacts/forced_trades/one_per_instrument_live_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(session, f, indent=2)
        print(f"💾 Session sauvegardée: {fn}")
    except Exception as e:
        print(f"❌ Erreur sauvegarde session: {e}")

    print("\n📊 RÉSUMÉ")
    print("-" * 32)
    print(f"✅ Réels OK: {sum(1 for r in results if r.get('success'))}")
    print(f"❌ Échecs: {sum(1 for r in results if not r.get('success'))}")
    print(f"📈 Total: {len(results)}")


if __name__ == "__main__":
    main()
