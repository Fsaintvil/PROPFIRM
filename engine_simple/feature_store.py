"""Persist position features across restarts using SQLite."""
import contextlib
import json
import os
import sqlite3


class FeatureStore:
    def __init__(self, path="runtime/position_features.db"):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS features (
                ticket INTEGER PRIMARY KEY,
                data TEXT NOT NULL
            )
        """)
        self.conn.commit()

    def save(self, ticket, meta_dict):
        data = json.dumps(meta_dict, default=str)
        self.conn.execute("INSERT OR REPLACE INTO features VALUES (?, ?)", (int(ticket), data))
        self.conn.commit()

    def load(self, ticket):
        row = self.conn.execute("SELECT data FROM features WHERE ticket=?", (int(ticket),)).fetchone()
        if row:
            try:
                return json.loads(row[0])
            except (json.JSONDecodeError, TypeError, ValueError):
                return {}
        return {}

    def delete(self, ticket):
        self.conn.execute("DELETE FROM features WHERE ticket=?", (int(ticket),))
        self.conn.commit()

    def close(self):
        self.conn.close()

    def __del__(self):
        with contextlib.suppress(RuntimeError, sqlite3.Error, AttributeError):
            self.conn.close()
