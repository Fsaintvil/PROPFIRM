import json
import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger("rate_cache")

DB_PATH = Path("runtime/rate_cache.db")


class RateCache:
    def __init__(self, db_path=None, default_ttl=15):
        self._path = str(db_path or DB_PATH)
        self._default_ttl = default_ttl
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self):
        return sqlite3.connect(self._path, timeout=5)

    def _init_db(self):
        try:
            with self._conn() as c:
                c.execute("""
                    CREATE TABLE IF NOT EXISTS rates (
                        key TEXT PRIMARY KEY,
                        data TEXT,
                        expires_at REAL
                    )
                """)
                c.execute("""
                    CREATE TABLE IF NOT EXISTS volatility (
                        symbol TEXT PRIMARY KEY,
                        data TEXT,
                        expires_at REAL
                    )
                """)
                c.execute("CREATE INDEX IF NOT EXISTS idx_rates_expires ON rates(expires_at)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_volatility_expires ON volatility(expires_at)")
        except (sqlite3.Error, OSError) as e:
            logger.warning(f"SQLite init failed: {e}")

    def _serialize(self, obj):
        return json.dumps(obj, default=str)

    def _deserialize(self, raw):
        if raw is None:
            return None
        return json.loads(raw)

    def get_rates(self, symbol, tf, count):
        key = f"{symbol}_{tf}_{count}"
        try:
            with self._conn() as c:
                row = c.execute("SELECT data, expires_at FROM rates WHERE key=?", (key,)).fetchone()
                if row and row[1] > time.time():
                    return self._deserialize(row[0])
        except (sqlite3.Error, json.JSONDecodeError, TypeError) as e:
            logger.debug(f"Cache get failed: {e}")
        return None

    def set_rates(self, symbol, tf, count, data, ttl=None):
        key = f"{symbol}_{tf}_{count}"
        ttl = self._default_ttl if ttl is None else ttl
        try:
            with self._conn() as c:
                c.execute("INSERT OR REPLACE INTO rates VALUES (?,?,?)",
                          (key, self._serialize(data), time.time() + ttl))
        except (sqlite3.Error, TypeError) as e:
            logger.debug(f"Cache set failed: {e}")

    def get_volatility(self, symbol):
        try:
            with self._conn() as c:
                row = c.execute("SELECT data, expires_at FROM volatility WHERE symbol=?", (symbol,)).fetchone()
                if row and row[1] > time.time():
                    return self._deserialize(row[0])
        except (sqlite3.Error, json.JSONDecodeError, TypeError) as e:
            logger.debug(f"Vol cache get failed: {e}")
        return None

    def set_volatility(self, symbol, data, ttl=60):
        try:
            with self._conn() as c:
                c.execute("INSERT OR REPLACE INTO volatility VALUES (?,?,?)",
                          (symbol, self._serialize(data), time.time() + ttl))
        except (sqlite3.Error, TypeError) as e:
            logger.debug(f"Vol cache set failed: {e}")

    def purge_expired(self):
        try:
            with self._conn() as c:
                c.execute("DELETE FROM rates WHERE expires_at < ?", (time.time(),))
                c.execute("DELETE FROM volatility WHERE expires_at < ?", (time.time(),))
        except sqlite3.Error as e:
            logger.debug(f"Purge failed: {e}")

    def clear(self):
        try:
            with self._conn() as c:
                c.execute("DELETE FROM rates")
                c.execute("DELETE FROM volatility")
        except sqlite3.Error as e:
            logger.debug(f"Clear failed: {e}")
