"""TradeJournal — stockage SQLite des trades avec stats."""
import sqlite3
import threading
from datetime import datetime, timedelta

DB_PATH = "runtime/trading_journal.db"

_lock = threading.Lock()


class TradeJournal:
    def __init__(self, max_age_days: int = 365):
        self.max_age_days = max_age_days
        with _lock:
            self._conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    ticket TEXT PRIMARY KEY,
                    symbol TEXT, action TEXT,
                    lot REAL, entry_price REAL, exit_price REAL,
                    profit REAL, rr REAL, regime TEXT,
                    adx REAL, atr REAL, dl_score REAL,
                    entry_time TEXT, exit_time TEXT,
                    duration_min INTEGER
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT, action TEXT, reason TEXT
                )
            """)
            # Schema migration: add missing columns from old DB
            existing = {r[1] for r in self._conn.execute("PRAGMA table_info(trades)").fetchall()}
            new_cols = {
                "ticket": "TEXT", "action": "TEXT",
                "entry_price": "REAL", "exit_price": "REAL",
                "rr": "REAL", "regime": "TEXT",
                "adx": "REAL", "atr": "REAL", "dl_score": "REAL",
                "entry_time": "TEXT", "exit_time": "TEXT", "duration_min": "INTEGER",
            }
            for col, coltype in new_cols.items():
                if col not in existing:
                    self._conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {coltype}")
            self._conn.commit()

    def record_trade(self, trade: dict) -> None:
        with _lock:
            self._conn.execute("""
                INSERT OR REPLACE INTO trades
                (ticket, symbol, action, lot, entry_price, exit_price,
                 profit, rr, regime, adx, atr, dl_score,
                 entry_time, exit_time, duration_min)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                trade["ticket"], trade["symbol"], trade["action"],
                trade["lot"], trade["entry_price"], trade["exit_price"],
                trade["profit"], trade["rr"], trade["regime"],
                trade["adx"], trade["atr"], trade["dl_score"],
                trade["entry_time"], trade["exit_time"],
                trade["duration_min"],
            ))
            self._conn.commit()

    _record_counter: int = 0

    def record(self, trade: dict) -> None:
        """Accepte les deux formats (ancien: direction/entry/..., nouveau: ticket/action/entry_price/...)."""
        if "ticket" in trade:
            return self.record_trade(trade)
        TradeJournal._record_counter += 1
        mapped = {
            "ticket": str(TradeJournal._record_counter),
            "symbol": trade.get("symbol", ""),
            "action": trade.get("action", trade.get("direction", "")),
            "lot": trade.get("lot", 0.0),
            "entry_price": trade.get("entry_price", trade.get("entry", 0.0)),
            "exit_price": trade.get("exit_price", trade.get("tp", 0.0)),
            "profit": trade.get("profit", 0.0),
            "rr": trade.get("rr", 0.0),
            "regime": trade.get("regime", trade.get("reason", "")),
            "adx": trade.get("adx", 0.0),
            "atr": trade.get("atr", 0.0),
            "dl_score": trade.get("dl_score", 0.0),
            "entry_time": trade.get("entry_time", trade.get("time_open", "")),
            "exit_time": trade.get("exit_time", trade.get("time_close", "")),
            "duration_min": trade.get("duration_min", 0),
        }
        self.record_trade(mapped)

    def record_decision(self, symbol: str, action: str, reason: str) -> None:
        with _lock:
            self._conn.execute(
                "INSERT INTO decisions (symbol, action, reason) VALUES (?,?,?)",
                (symbol, action, reason),
            )
            self._conn.commit()

    def get_stats(self, symbol: str | None = None, days: int | None = None) -> dict | None:
        with _lock:
            conditions = []
            params = []
            if symbol:
                conditions.append("symbol=?")
                params.append(symbol)
            if days is not None:
                conditions.append("exit_time >= datetime('now', ?)")
                params.append(f"-{days} days")
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            rows = self._conn.execute(
                f"SELECT profit, rr FROM trades{where}", params
            ).fetchall()
        if not rows:
            return None
        wins = sum(1 for p, _ in rows if p is not None and p > 0)
        losses = len(rows) - wins
        net_profit = sum(p for p, _ in rows if p is not None)
        gross_profit = sum(p for p, _ in rows if p is not None and p > 0)
        gross_loss = sum(abs(p) for p, _ in rows if p is not None and p < 0)
        avg_rr = sum(r for _, r in rows if r is not None) / len(rows) if rows else 0
        return {
            "total": len(rows),
            "wins": wins,
            "losses": losses,
            "profit": net_profit,
            "avg_rr": avg_rr,
            "win_rate": wins / len(rows) if rows else 0,
            "trade_count": len(rows),
            "trade_winrate": wins / len(rows) if rows else 0,
            "trade_profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
            "trade_avg_pnl": net_profit / len(rows) if rows else 0,
        }

    def get_trades(self, symbol: str | None = None) -> list[dict]:
        """Retourne tous les trades (backward compat)."""
        with _lock:
            if symbol:
                rows = self._conn.execute(
                    "SELECT profit, rr FROM trades WHERE symbol=?", (symbol,)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT profit, rr FROM trades"
                ).fetchall()
        return [{"profit": p, "rr": r} for p, r in rows]

    def get_recent(self, symbol: str | None = None, limit: int = 50) -> list[dict]:
        with _lock:
            if symbol:
                rows = self._conn.execute(
                    "SELECT * FROM trades WHERE symbol=? ORDER BY exit_time DESC LIMIT ?",
                    (symbol, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM trades ORDER BY exit_time DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        columns = ["ticket", "symbol", "action", "lot", "entry_price",
                    "exit_price", "profit", "rr", "regime", "adx", "atr",
                    "dl_score", "entry_time", "exit_time", "duration_min"]
        return [dict(zip(columns, r)) for r in rows]

    def close(self) -> None:
        with _lock:
            self._conn.close()
