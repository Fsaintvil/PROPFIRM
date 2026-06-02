import sqlite3
import threading
from datetime import datetime, timedelta

DB_PATH = "runtime/trading_journal.db"


class TradeJournal:
    """Journal des trades pour historique et stats - stockage SQLite"""

    def __init__(self, max_age_days=365):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=3000")
        self._lock = threading.Lock()
        self._init_db()
        self._cleanup_orphans()
        self._retain(max_age_days)

    def _retain(self, max_age_days=365):
        with self._lock:
            cutoff = (datetime.utcnow() - timedelta(days=max_age_days)).isoformat()
            deleted = self.conn.execute(
                "DELETE FROM trades WHERE time_close!='' AND time_close<?", (cutoff,)
            ).rowcount
            if deleted > 0:
                self.conn.commit()
                logger = __import__('logging').getLogger('journal')
                logger.info(f"Journal: {deleted} vieilles entrées purgees (> {max_age_days}j)")

    def _init_db(self):
        with self._lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT, direction TEXT, entry REAL, sl REAL, tp REAL,
                    lot REAL, profit REAL, time_open TEXT, time_close TEXT, reason TEXT
                )
            """)
            self.conn.commit()

    def _cleanup_orphans(self):
        with self._lock:
            orphaned = self.conn.execute(
                "SELECT COUNT(*) FROM trades WHERE time_close='' AND profit=0"
            ).fetchone()[0]
            if orphaned > 0:
                self.conn.execute(
                    "UPDATE trades SET time_close=? WHERE time_close='' AND profit=0",
                    (datetime.utcnow().isoformat()[:19],)
                )
                self.conn.commit()
                logger = __import__('logging').getLogger('journal')
                logger.info(f"Journal: {orphaned} entries orphelines fermees")

    def record(self, trade):
        with self._lock:
            self.conn.execute("""
                INSERT INTO trades (symbol, direction, entry, sl, tp, lot, profit, time_open, time_close, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.get("symbol"), trade.get("direction"),
                trade.get("entry", 0), trade.get("sl", 0), trade.get("tp", 0),
                trade.get("lot", 0), trade.get("profit", 0),
                trade.get("time_open", ""), trade.get("time_close", ""),
                trade.get("reason", "")
            ))
            self.conn.commit()

    def close(self):
        self.conn.close()

    def get_stats(self, symbol=None, days=30):
        with self._lock:
            since = (datetime.utcnow() - timedelta(days=days)).isoformat()
            if symbol:
                rows = self.conn.execute("""
                    SELECT profit FROM trades WHERE symbol=? AND time_close!='' AND time_close>=?
                    ORDER BY time_close DESC
                """, (symbol, since)).fetchall()
            else:
                rows = self.conn.execute("""
                    SELECT profit FROM trades WHERE time_close!='' AND time_close>=?
                    ORDER BY time_close DESC
                """, (since,)).fetchall()

        if not rows:
            return None

        profits = [r[0] for r in rows]
        recent = profits
        total = len(recent)
        if total == 0:
            return None

        wins = [p for p in recent if p > 0]
        losses = [p for p in recent if p <= 0]
        win_pnl = sum(wins)
        loss_pnl = abs(sum(losses)) if losses else 0.01

        return {
            "trade_winrate": len(wins) / max(total, 1),
            "trade_winrate_5": self._winrate(recent, 5),
            "trade_winrate_10": self._winrate(recent, 10),
            "trade_winrate_20": self._winrate(recent, 20),
            "trade_count": total,
            "trade_avg_pnl": sum(recent) / max(total, 1),
            "trade_avg_win": win_pnl / max(len(wins), 1) if wins else 0,
            "trade_avg_loss": loss_pnl / max(len(losses), 1) if losses else 0,
            "trade_profit_factor": win_pnl / max(loss_pnl, 0.01),
            "trade_win_pnl_ratio": (
                (win_pnl / max(len(wins), 1)) / max((loss_pnl / max(len(losses), 1)), 0.01)
                if wins and losses else 0
            )
        }

    def _winrate(self, profits, n):
        recent = profits[-n:] if len(profits) >= n else profits
        if not recent:
            return 0.5
        wins = sum(1 for p in recent if p > 0)
        return wins / max(len(recent), 1)
