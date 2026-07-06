"""Persist position features across restarts using SQLite."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any


class FeatureStore:
    def __init__(self, path: str | None = None) -> None:
        if path is None:
            path = str(Path(__file__).resolve().parent.parent / "runtime" / "position_features.db")
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # 🐛 FIX 26 Juin 2026: check_same_thread=False pour accès multi-thread
        # + chemin absolu pour éviter les problèmes de CWD
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")  # C-03: WAL mode
        self.conn.execute("PRAGMA busy_timeout=5000")  # évite contention
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS features (
                ticket INTEGER PRIMARY KEY,
                data TEXT NOT NULL
            )
        """)
        self.conn.commit()

    def save(self, ticket: int, meta_dict: dict[str, Any]) -> None:
        data = json.dumps(meta_dict, default=str)
        self.conn.execute("INSERT OR REPLACE INTO features VALUES (?, ?)", (int(ticket), data))
        self.conn.commit()

    def load(self, ticket: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT data FROM features WHERE ticket=?", (int(ticket),)).fetchone()
        if row:
            try:
                return json.loads(row[0])
            except (json.JSONDecodeError, TypeError, ValueError):
                return {}
        return {}

    def delete(self, ticket: int) -> None:
        self.conn.execute("DELETE FROM features WHERE ticket=?", (int(ticket),))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __del__(self) -> None:
        with contextlib.suppress(RuntimeError, sqlite3.Error, AttributeError):
            self.conn.close()
