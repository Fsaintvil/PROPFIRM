"""
Watchdog automatique : surveille MT5, détecte les fermetures de trades,
enregistre chaque trade (P&L, RR, SL/TP, ATR) dans runtime/trades_log.csv
"""
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta

import MetaTrader5 as mt5
import pandas as pd

import config_simple as cfg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("logs/watchdog.log", encoding="utf-8"), logging.StreamHandler()]
)
logger = logging.getLogger("watchdog")

WATCH_INTERVAL = int(os.environ.get("WATCH_INTERVAL", "60"))   # 60s (était 300s)
RUNTIME_DIR = "runtime"
os.makedirs(RUNTIME_DIR, exist_ok=True)
TRADES_CSV = os.path.join(RUNTIME_DIR, "trades_log.csv")
STATE_FILE = os.path.join(RUNTIME_DIR, "watchdog_snapshot.json")
HEARTBEAT_FILE = os.path.join(RUNTIME_DIR, "watchdog_heartbeat.txt")

def get_atr(symbol, period=20, tf=mt5.TIMEFRAME_H1):
    r = mt5.copy_rates_from_pos(symbol, tf, 0, period + 2)
    if r is None or len(r) < period:
        return None
    df = pd.DataFrame(r)
    df["atr"] = df["high"] - df["low"]
    return df["atr"].iloc[-period:].mean()

def load_previous_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"positions": []}

def save_state(positions):
    data = {
        "timestamp": datetime.now().isoformat(),
        "positions": [{
            "ticket": p.ticket, "symbol": p.symbol, "type": p.type,
            "volume": p.volume, "price_open": p.price_open,
            "sl": p.sl, "tp": p.tp, "profit": p.profit, "time": p.time
        } for p in positions]
    }
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, default=str)

def _get(pos, key, default=None):
    return pos[key] if isinstance(pos, dict) else getattr(pos, key, default)

def log_trade_close(pos, entry_price, entry_time, reason, pnl):
    """Enregistre un trade ferme dans le CSV"""
    now = datetime.now()
    duration = (now - datetime.fromtimestamp(entry_time)).total_seconds() / 3600 if entry_time else 0
    symbol = _get(pos, "symbol")
    atr = get_atr(symbol)

    price_open = _get(pos, "price_open")
    sl_price = _get(pos, "sl")
    tp_price = _get(pos, "tp")
    ptype = _get(pos, "type")

    sl_dist = abs(price_open - sl_price) / atr if atr and atr > 0 and sl_price else None
    tp_dist = abs(price_open - tp_price) / atr if atr and atr > 0 and tp_price else None

    row = {
        "timestamp": now.isoformat(),
        "symbol": symbol,
        "direction": "BUY" if ptype == 0 else "SELL",
        "volume": _get(pos, "volume"),
        "entry_price": round(entry_price, 5) if entry_price else 0,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "exit_price": None,
        "sl_atr": round(sl_dist, 2) if sl_dist else None,
        "tp_atr": round(tp_dist, 2) if tp_dist else None,
        "pnl": round(pnl, 2),
        "reason": reason,
        "duration_h": round(duration, 2),
        "atr_h1": round(atr, 2) if atr else None,
    }

    tick = mt5.symbol_info_tick(symbol)
    if tick:
        row["exit_price"] = round(tick.bid if ptype == 0 else tick.ask, 5)

    file_exists = os.path.exists(TRADES_CSV)
    with open(TRADES_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    sl_str = f"{sl_dist:.1f}" if sl_dist else "?"
    tp_str = f"{tp_dist:.1f}" if tp_dist else "?"
    logger.info(f"[CLOSE] {symbol} {row['direction']} P&L={pnl:+.1f} "
                f"(SL={sl_str}ATR TP={tp_str}ATR) duration={duration:.1f}h "
                f"reason={reason}")

def _write_heartbeat():
    try:
        with open(HEARTBEAT_FILE, "w") as f:
            f.write(datetime.utcnow().isoformat())
    except Exception as e:
        logger.warning(f"Heartbeat write failed: {e}")

def check_closed_positions(prev, current):
    """Detecte les positions fermees entre deux snapshots"""
    prev_tickets = {p["ticket"]: p for p in prev["positions"]}
    curr_tickets = {p.ticket: p for p in current}

    for ticket, old in prev_tickets.items():
        if ticket not in curr_tickets:
            # Position fermee
            pnl = old["profit"]
            logger.info(f"[DETECTED CLOSE] ticket=#{ticket} symbol={old['symbol']} P&L={pnl:+.1f}")
            log_trade_close(
                old, old["price_open"], old["time"],
                "MANUAL_CLOSE" if abs(pnl) < 0.01 else ("WIN" if pnl > 0 else "LOSS"),
                pnl
            )

def check_sl_tp_modifications(prev, current):
    """Detecte les modifications de SL/TP (step trailing)"""
    prev_map = {p["ticket"]: p for p in prev["positions"]}
    for p in current:
        ticket = p.ticket
        if ticket in prev_map:
            old = prev_map[ticket]
            new_sl = p.sl
            old_sl = old["sl"]
            if old_sl != new_sl:
                delta_sl = new_sl - old_sl if isinstance(new_sl, (int, float)) else 0
                sym = p.symbol
                logger.info(f"[MODIFY] #{ticket} {sym}: SL {old_sl:.5f} -> {new_sl:.5f} (delta={delta_sl:+.5f})")

def check_history_deals(prev, current, since_time=None):
    """Recover trades that opened AND closed between snapshots (missed by poll)"""
    if since_time is None:
        prev_state = load_previous_state()
        ts = prev_state.get("timestamp")
        if not ts:
            return
        try:
            since_time = datetime.fromisoformat(ts)
        except Exception:
            return
    until_time = datetime.now() + timedelta(seconds=1)
    try:
        deals = mt5.history_deals_get(int(since_time.timestamp()), int(until_time.timestamp()))
    except Exception as e:
        logger.warning(f"History deals check failed: {e}")
        return
    if not deals:
        return

    prev_tickets = {p["ticket"] for p in prev.get("positions", [])}
    curr_tickets = {p.ticket for p in current}

    for d in deals:
        if d.magic != cfg.ROBOT_MAGIC:
            continue
        # DEAL_ENTRY_OUT=1 (close), DEAL_ENTRY_INOUT=2 (reverse)
        if d.entry in (1, 2) and d.profit != 0 and d.position_id:
            if d.ticket not in prev_tickets | curr_tickets:
                direction = "BUY" if d.type == 1 else "SELL"
                logger.info(f"[HISTORY] pos=#{d.position_id} {d.symbol} {direction} "
                           f"P&L={d.profit:+.1f} (recovered from history)")
                row = {
                    "timestamp": datetime.now().isoformat(),
                    "symbol": d.symbol,
                    "direction": direction,
                    "volume": d.volume,
                    "entry_price": round(d.price, 5),
                    "sl_price": None,
                    "tp_price": None,
                    "exit_price": round(d.price, 5),
                    "sl_atr": None,
                    "tp_atr": None,
                    "pnl": round(d.profit, 2),
                    "reason": "WIN" if d.profit > 0 else "LOSS",
                    "duration_h": 0,
                    "atr_h1": None,
                }
                try:
                    tick = mt5.symbol_info_tick(d.symbol)
                    if tick:
                        row["exit_price"] = round(tick.bid if d.type == 0 else tick.ask, 5)
                except Exception:
                    pass
                file_exists = os.path.exists(TRADES_CSV)
                with open(TRADES_CSV, "a", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=list(row.keys()))
                    if not file_exists:
                        writer.writeheader()
                    writer.writerow(row)

def print_summary():
    """Affiche le recap du CSV"""
    if not os.path.exists(TRADES_CSV):
        logger.info("Aucun trade enregistre encore")
        return
    df = pd.read_csv(TRADES_CSV)
    if len(df) == 0:
        logger.info("CSV vide")
        return

    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] < 0]
    total_pnl = df["pnl"].sum()

    logger.info(f"=== RECAP ({len(df)} trades) ===")
    logger.info(f"Wins: {len(wins)} | Losses: {len(losses)} | WR: {len(wins)/len(df)*100:.1f}% | P&L: {total_pnl:+.1f}")

    if len(wins) > 0 and len(losses) > 0:
        avg_win = wins["pnl"].mean()
        avg_loss = abs(losses["pnl"].mean())
        logger.info(f"Avg win: {avg_win:.1f} | Avg loss: {avg_loss:.1f} | RR: {avg_win/avg_loss:.2f}")
        logger.info(f"Avg SL distance: {df['sl_atr'].mean():.2f} ATR | Avg TP distance: {df['tp_atr'].mean():.2f} ATR")

    # Per symbol
    for sym, grp in df.groupby("symbol"):
        w = grp[grp["pnl"] > 0]
        lo = grp[grp["pnl"] < 0]
        r = abs(w["pnl"].mean() / lo["pnl"].mean()) if len(w) > 0 and len(lo) > 0 and lo["pnl"].mean() != 0 else None
        wr_pct = len(w) / len(grp) * 100
        logger.info(f"  {sym}: {len(grp)} trades WR={wr_pct:.0f}% "
                    f"P&L={grp['pnl'].sum():+.1f} RR={r if r else 'N/A'}")

def main_loop():
    logger.info(f"Watchdog demarre (interval={WATCH_INTERVAL}s)")

    if not mt5.initialize():
        logger.error("MT5 non connecte")
        return

    logger.info(f"Compte: {mt5.account_info().balance:.0f}")

    first_run = True
    while True:
        try:
            pos = mt5.positions_get()
            our = [p for p in pos if p.magic == cfg.ROBOT_MAGIC] if pos else []

            prev = load_previous_state()

            if not first_run:
                check_closed_positions(prev, our)
                check_sl_tp_modifications(prev, our)
                check_history_deals(prev, our)

            save_state(our)
            _write_heartbeat()
            first_run = False

            for _ in range(WATCH_INTERVAL):
                time.sleep(1)
                # Lightweight MT5 connection check (not initialize which is heavy)
                try:
                    _ = mt5.account_info()
                except Exception:
                    logger.error("MT5 deconnecte, reconnexion...")
                    mt5.initialize()

        except KeyboardInterrupt:
            logger.info("Watchdog arrete")
            break
        except Exception as e:
            logger.error(f"Erreur: {e}", exc_info=True)
            time.sleep(10)

    mt5.shutdown()

if __name__ == "__main__":
    if "--recap" in sys.argv:
        print_summary()
    elif "--check" in sys.argv:
        # One-shot: check current state and any trades since last snapshot
        if not mt5.initialize():
            print("MT5 NOT CONNECTED")
            sys.exit(1)
        pos = mt5.positions_get()
        our = [p for p in pos if p.magic == cfg.ROBOT_MAGIC] if pos else []
        prev = load_previous_state()
        if prev["positions"]:
            check_closed_positions(prev, our)
        save_state(our)
        print_summary()
        mt5.shutdown()
    else:
        main_loop()
