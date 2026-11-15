from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


def default_data_dir() -> Path:
    override = os.environ.get("HITMO_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return base / "HitMo Radio Studio"


class Database:
    """SQLite persistence boundary; each operation gets a short-lived connection."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else default_data_dir() / "hitmo.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=15000")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def initialize(self) -> None:
        with self.connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS operators (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL DEFAULT 'Operador', active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS media (
                    id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL, artist TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT 'Música', duration REAL NOT NULL DEFAULT 0,
                    modified REAL NOT NULL DEFAULT 0, added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_media_category ON media(category);
                CREATE TABLE IF NOT EXISTS playlist_items (
                    id INTEGER PRIMARY KEY, media_id INTEGER NOT NULL,
                    position INTEGER NOT NULL, category TEXT NOT NULL DEFAULT 'Música',
                    FOREIGN KEY(media_id) REFERENCES media(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS timeline_events (
                    id INTEGER PRIMARY KEY, time_of_day TEXT NOT NULL,
                    days TEXT NOT NULL DEFAULT '0,1,2,3,4,5,6',
                    title TEXT NOT NULL, media_id INTEGER,
                    category TEXT NOT NULL DEFAULT 'Comercial', enabled INTEGER NOT NULL DEFAULT 1,
                    last_fired TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(media_id) REFERENCES media(id) ON DELETE SET NULL
                );
                CREATE TABLE IF NOT EXISTS loop_tracks (
                    id INTEGER PRIMARY KEY, media_id INTEGER NOT NULL UNIQUE,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY(media_id) REFERENCES media(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS playback_history (
                    id INTEGER PRIMARY KEY, media_id INTEGER,
                    title TEXT NOT NULL, artist TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'Música',
                    operator TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    ended_at TEXT NOT NULL DEFAULT '', played_seconds REAL NOT NULL DEFAULT 0,
                    FOREIGN KEY(media_id) REFERENCES media(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_history_started ON playback_history(started_at DESC);
                INSERT OR IGNORE INTO operators(name, role) VALUES ('Operador padrão', 'Administrador');
            """)

    def setting(self, key: str, default: str = "") -> str:
        with self.connect() as con:
            row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as con:
            con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self.connect() as con:
            cur = con.execute(sql, params)
            return int(cur.lastrowid or 0)

    def all(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self.connect() as con:
            return list(con.execute(sql, params).fetchall())

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self.connect() as con:
            return con.execute(sql, params).fetchone()

