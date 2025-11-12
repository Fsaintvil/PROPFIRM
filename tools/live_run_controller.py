"""
live_run_controller.py

Orchestre l'exécution régulière de `start_production.py` en suivant les règles LIVE.

Sécurité: par défaut le script tourne en DRY-RUN (ne permet pas d'envoyer des ordres).
Pour autoriser l'envoi live il faut créer le fichier de confirmation `control/apply_live.confirm`
contenant exactement la ligne `APPLY LIVE` et définir les variables d'environnement requises
comme `ALLOW_MT5_SEND=1`.

Fonctionnalités:
- Planifie l'appel à `start_production.py` toutes les `SEND_INTERVAL` secondes (par défaut 930)
- Si confirmation explicite présente et env autorisant l'envoi -> passe les vars à start_production
- Lance un contrôleur d'auto-close: ferme les positions ouvertes depuis plus de `AUTO_CLOSE_MINUTES`
  (default 30) seulement si l'envoi live est autorisé et confirmé.

Usage (exemple):
    $env:PYTHONPATH='.'; C:/.../python.exe tools/live_run_controller.py

Remarques: ce script respecte les garde‑fous du projet et n'altère pas les backups/logs.
"""

import os
import time
import subprocess
import threading
from datetime import datetime, timedelta
import json

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONTROL_DIR = os.path.join(ROOT, "control")
CONFIRM_FILE = os.path.join(CONTROL_DIR, "apply_live.confirm")
AUTO_CONFIRM_FILE = os.path.join(CONTROL_DIR, "apply_live.auto.confirm")
LOCK_FILE = os.path.join(CONTROL_DIR, "ai_sending.lock")
ARTIFACTS_DIR = os.path.join(ROOT, "artifacts", "live_trading")

# Defaults (modifiable via env)
SEND_INTERVAL = int(os.getenv("SEND_INTERVAL", "930"))  # seconds
AUTO_CLOSE_MINUTES = int(os.getenv("AUTO_CLOSE_MINUTES", "30"))
SYMBOLS_DEFAULT = [
    "BTCUSD",
    "ETHUSD",
    "XAUUSD",
    "USDCAD",
    "AUDNZD",
    "EURJPY",
    "GBPCHF",
    "NZDJPY",
    "EURUSD",
    "EURAUD",
    "US500.cash",
    "JP225.cash",
]
SYMBOLS = os.getenv("SYMBOLS", ",".join(SYMBOLS_DEFAULT))

START_PROD_CMD = ["python", os.path.join(ROOT, "start_production.py")]


def has_confirmation():
    """Retourne True si le fichier de confirmation existe et contient 'APPLY LIVE'."""
    try:
        if not os.path.exists(CONFIRM_FILE):
            return False
        with open(CONFIRM_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
        return content == "APPLY LIVE"
    except Exception:
        return False


def has_auto_confirmation():
    """Retourne True si le fichier de confirmation auto existe et contient 'APPLY LIVE AUTO'."""
    try:
        if not os.path.exists(AUTO_CONFIRM_FILE):
            return False
        with open(AUTO_CONFIRM_FILE, "r", encoding="utf-8") as f:
            return f.read().strip() == "APPLY LIVE AUTO"
    except Exception:
        return False


def ai_decide_orders(symbols_list, volume=0.01):
    """Heuristique simple MTF M15: si dernière close > moyenne des 20 dernières -> BUY sinon SELL.
    Retourne une liste d'ordres (dict) avec SL/TP par défaut.
    """
    try:
        import MetaTrader5 as mt5
    except Exception:
        print("MetaTrader5 binding not available for ai_decide_orders")
        return []

    orders = []
    try:
        tf = mt5.TIMEFRAME_M15
        if not mt5.initialize():
            print("mt5.initialize() FAILED (ai_decide_orders)", mt5.last_error())
            return orders

        for s in symbols_list:
            info = mt5.symbol_info(s)
            tick = mt5.symbol_info_tick(s)
            if info is None or tick is None:
                print(f"AI: symbol {s} not available -> skip")
                continue
            rates = mt5.copy_rates_from_pos(s, tf, 0, 20)
            if rates is None or len(rates) < 5:
                print(f"AI: insufficient rates for {s} -> skip")
                continue
            # rates is a numpy structured array; access 'close' field
            closes = [float(r['close']) for r in rates]
            avg = sum(closes) / len(closes)
            last = closes[-1]
            side = "BUY" if last > avg else "SELL"
            price = tick.ask if side == "BUY" else tick.bid
            # SL: 1% away, TP: 2% away (heuristique)
            sl = price * (0.99 if side == "BUY" else 1.01)
            tp = price * (1.02 if side == "BUY" else 0.98)
            digits = info.digits if info and hasattr(info, 'digits') else 5
            orders.append({
                "symbol": s,
                "side": side,
                "volume": volume,
                "type": "MARKET",
                "price": price,
                "sl": round(sl, digits),
                "tp": round(tp, digits),
                "comment": "ai_mtf_m15",
            })
        mt5.shutdown()
    except Exception as e:
        print("ai_decide_orders error:", e)
    return orders


def send_orders_via_mt5(orders):
    """Envoie les ordres via MetaTrader5 et retourne les résultats.
    NOTE: Doit être appelé uniquement si ALLOW_MT5_SEND=1 et has_auto_confirmation().
    """
    results = []
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            print("mt5.initialize() FAILED (send_orders)", mt5.last_error())
            return results
        for o in orders:
            try:
                order_type = mt5.ORDER_TYPE_BUY if o['side'] == 'BUY' else mt5.ORDER_TYPE_SELL
                req = {
                    'action': mt5.TRADE_ACTION_DEAL,
                    'symbol': o['symbol'],
                    'volume': float(o['volume']),
                    'type': order_type,
                    'price': float(o['price']),
                    'deviation': 20,
                    'sl': float(o['sl']),
                    'tp': float(o['tp']),
                    'comment': o.get('comment', 'ai_auto'),
                }
                res = mt5.order_send(req)
                results.append((o, res))
            except Exception as ee:
                print("send_orders_via_mt5 order error:", ee)
        mt5.shutdown()
    except Exception as e:
        print("send_orders_via_mt5 error:", e)
    return results


def acquire_lock(timeout_seconds=7200):
    """Attempt to acquire a simple filesystem lock. Returns True if lock acquired."""
    try:
        if os.path.exists(LOCK_FILE):
            try:
                st = os.stat(LOCK_FILE)
                age = time.time() - st.st_mtime
                if age < timeout_seconds:
                    print(f"Lock file exists and is recent (age={age:.0f}s) -> cannot acquire")
                    return False
                else:
                    print("Stale lock detected -> removing and acquiring")
                    os.remove(LOCK_FILE)
            except Exception:
                return False
        with open(LOCK_FILE, "w", encoding="utf-8") as f:
            f.write(f"pid:{os.getpid()}\n")
            f.write(f"ts:{int(time.time())}\n")
        return True
    except Exception as e:
        print("acquire_lock error:", e)
        return False


def release_lock():
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception as e:
        print("release_lock error:", e)



def launch_start_production(dry_run=True):
    env = os.environ.copy()
    env["PYTHONPATH"] = env.get("PYTHONPATH", "") or "."
    # ensure SYMBOLS is passed
    env["SYMBOLS"] = SYMBOLS

    # propagate requested AUTO_* and other control environment variables if present
    for k in [
        "LIVE_ENGINE_LIGHT_MODE",
        "CONFIRME_DEPLACEMENT",
        "AUTO_APPLY",
        "AUTO_DEPLOY",
        "AUTO_LEARN",
        "AUTO_ADAPT",
        "AUTO_ENRICH",
        "AI_AUTOMATE",
        "AI_VOLUME",
    ]:
        if k in os.environ:
            env[k] = os.environ[k]

    # If dry_run, force ALLOW_MT5_SEND=0 to avoid live sends
    if dry_run:
        env["ALLOW_MT5_SEND"] = "0"
    else:
        # expect operator has set ALLOW_MT5_SEND etc externally; keep current env
        pass

    cmd = START_PROD_CMD + ["--symbols", SYMBOLS]
    # use --force to obey user's intention to force run, but keep safety via dry_run
    cmd += ["--force"]

    print(f"[{datetime.utcnow().isoformat()}] Launching start_production (dry_run={dry_run})")
    try:
        res = subprocess.run(cmd, env=env, cwd=ROOT, capture_output=True, text=True, timeout=600)
        print(res.stdout)
        if res.stderr:
            print("ERR:", res.stderr)
    except subprocess.TimeoutExpired:
        print("start_production timed out")
    except Exception as e:
        print("Failed to launch start_production:", e)


def auto_close_positions_loop(stop_event):
    """Boucle qui ferme les positions ouvertes plus vieilles qu'AUTO_CLOSE_MINUTES.

    IMPORTANT: cette boucle n'exécutera des fermetures réelles que si has_confirmation() et
    si ALLOW_MT5_SEND est activé (safety guard). Sinon elle se contente de rapporter l'état.
    """
    try:
        import MetaTrader5 as mt5
    except Exception:
        print("MetaTrader5 binding not available in this environment for auto-close checks.")
        return

    while not stop_event.is_set():
        try:
            mt5.initialize()
            positions = mt5.positions_get()
            now = datetime.utcnow()
            if positions:
                for pos in positions:
                    # attempt to inspect time / ticket - keep safe if attributes missing
                    open_time = getattr(pos, 'time', None) or getattr(pos, 'time_msc', None)
                    # If time is in seconds since epoch or ms
                    pos_dt = None
                    if open_time:
                        if open_time > 1e12:
                            pos_dt = datetime.utcfromtimestamp(open_time / 1000.0)
                        else:
                            pos_dt = datetime.utcfromtimestamp(open_time)

                    if pos_dt and (now - pos_dt) > timedelta(minutes=AUTO_CLOSE_MINUTES):
                        ticket = getattr(pos, 'ticket', getattr(pos, 'position', 'N/A'))
                        print("Position", ticket, "open since", pos_dt,
                              f"(>{AUTO_CLOSE_MINUTES}min)")
                        if has_confirmation() and os.getenv("ALLOW_MT5_SEND") == '1':
                            # perform a close (conservative: close by ticket)
                            try:
                                ticket = int(getattr(pos, 'ticket', getattr(pos, 'position', 0)))
                                # build close request - best-effort and may need broker specifics
                                tick = mt5.symbol_info_tick(pos.symbol)
                                price = tick.bid if pos.type == 0 else tick.ask
                                order_type = (
                                    mt5.ORDER_TYPE_SELL if pos.type == 0
                                    else mt5.ORDER_TYPE_BUY
                                )
                                close_req = {
                                    "action": mt5.TRADE_ACTION_DEAL,
                                    "symbol": pos.symbol,
                                    "volume": pos.volume,
                                    "type": order_type,
                                    "position": ticket,
                                    "price": price,
                                    "deviation": 20,
                                    "comment": "auto_close_after_30min",
                                }
                                result = mt5.order_send(close_req)
                                print("close result:", result)
                            except Exception as e:
                                print("auto-close failed:", e)
                        else:
                            print(
                                "Auto-close required but not confirmed or",
                                "ALLOW_MT5_SEND!=1 -> SKIPPING close",
                            )
            mt5.shutdown()
        except Exception as e:
            print("auto_close loop error:", e)

        # Sleep before next check
        stop_event.wait(60)


def main_loop():
    stop_event = threading.Event()
    thread_args = (stop_event,)
    closer_thread = threading.Thread(
        target=auto_close_positions_loop, args=thread_args, daemon=True
    )
    closer_thread.start()

    try:
        while True:
            confirmed = has_confirmation()
            dry_run = not confirmed or os.getenv("ALLOW_MT5_SEND") != '1'
            # AI automation: decide orders if requested
            if os.getenv("AI_AUTOMATE") == '1':
                symbols_list = [s.strip() for s in SYMBOLS.split(',') if s.strip()]
                ai_volume = float(os.getenv("AI_VOLUME", "0.01"))
                ai_orders = ai_decide_orders(symbols_list, volume=ai_volume)
                if ai_orders:
                    print("AI decided orders (preview):")
                    for ao in ai_orders:
                        print(ao)
                    # send if auto-confirmation & ALLOW_MT5_SEND
                    if has_auto_confirmation() and os.getenv("ALLOW_MT5_SEND") == '1':
                        print("Auto-confirmation present -> attempting to acquire lock and send AI orders")
                        if acquire_lock():
                            try:
                                print("Lock acquired -> sending AI orders")
                                send_results = send_orders_via_mt5(ai_orders)
                                print("Send results:")
                                for r in send_results:
                                    print(r)
                                # persist results locally for audit
                                try:
                                    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
                                    meta = {
                                        "timestamp": datetime.utcnow().isoformat(),
                                        "symbols": symbols_list,
                                        "env": {k: os.environ.get(k) for k in [
                                            "ALLOW_MT5_SEND",
                                            "AI_AUTOMATE",
                                            "AI_VOLUME",
                                            "LIVE_ENGINE_LIGHT_MODE",
                                            "CONFIRME_DEPLACEMENT",
                                        ]},
                                    }
                                    fn = os.path.join(ARTIFACTS_DIR, f"ai_send_{int(time.time())}.json")
                                    with open(fn, "w", encoding="utf-8") as fh:
                                        json.dump({"meta": meta, "results": [
                                            {
                                                "order": getattr(r[1], 'order', None) if r[1] is not None else None,
                                                "retcode": getattr(r[1], 'retcode', None) if r[1] is not None else None,
                                                "request": r[0],
                                            } for r in send_results
                                        ]}, fh, indent=2)
                                    print("Persisted send results to", fn)
                                except Exception as e:
                                    print("Failed to persist send results:", e)
                            finally:
                                release_lock()
                        else:
                            print("Could not acquire lock -> skipping send to avoid duplicate executions")
                    else:
                        print(
                            "AI orders generated but not sent (missing",
                            "auto-confirmation or ALLOW_MT5_SEND!=1)",
                        )
            launch_start_production(dry_run=dry_run)
            # Wait SEND_INTERVAL seconds until next cycle
            time.sleep(SEND_INTERVAL)
    except KeyboardInterrupt:
        print("Interrupted, stopping")
    finally:
        stop_event.set()


if __name__ == '__main__':
    print("Live run controller starting")
    os.makedirs(CONTROL_DIR, exist_ok=True)
    main_loop()
