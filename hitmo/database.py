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
                CREATE TABLE IF NOT EXISTS catalog_state (
                    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS program_state (
                    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO catalog_state(id,revision) VALUES(1,0);
                INSERT OR IGNORE INTO program_state(id,revision) VALUES(1,0);
                CREATE TABLE IF NOT EXISTS material_categories (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                    material_kind TEXT NOT NULL DEFAULT 'audio', icon_path TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS media_folders (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
                    category_id INTEGER, icon_path TEXT NOT NULL DEFAULT '',
                    jingle_rotation_enabled INTEGER NOT NULL DEFAULT 0 CHECK(jingle_rotation_enabled IN (0,1)),
                    jingle_repeat_cooldown INTEGER NOT NULL DEFAULT 2 CHECK(jingle_repeat_cooldown BETWEEN 0 AND 100),
                    active INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY(category_id) REFERENCES material_categories(id) ON DELETE SET NULL
                );
                CREATE TABLE IF NOT EXISTS jingle_rotation_state (
                    folder_id INTEGER PRIMARY KEY, counts_json TEXT NOT NULL DEFAULT '{}',
                    recent_ids_json TEXT NOT NULL DEFAULT '[]', total_selected INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(folder_id) REFERENCES media_folders(id)
                );
                CREATE TABLE IF NOT EXISTS playlist_catalogs (
                    id INTEGER PRIMARY KEY, source_path TEXT NOT NULL,
                    version TEXT NOT NULL DEFAULT '', declared_folder_count INTEGER NOT NULL DEFAULT 0,
                    declared_shared_count INTEGER NOT NULL DEFAULT 0,
                    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_playlist_catalog_source
                    ON playlist_catalogs(source_path,imported_at DESC);
                CREATE TABLE IF NOT EXISTS playlist_shared_roots (
                    catalog_id INTEGER NOT NULL, position INTEGER NOT NULL,
                    server TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(catalog_id,position),
                    FOREIGN KEY(catalog_id) REFERENCES playlist_catalogs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS playlist_folder_entries (
                    catalog_id INTEGER NOT NULL, position INTEGER NOT NULL,
                    playlist_id INTEGER NOT NULL,
                    title TEXT NOT NULL DEFAULT '', type_code TEXT NOT NULL DEFAULT '',
                    target TEXT NOT NULL DEFAULT '', icon_location TEXT NOT NULL DEFAULT '',
                    shortcut_arguments TEXT NOT NULL DEFAULT '', shortcut_path_name TEXT NOT NULL DEFAULT '',
                    icon_index INTEGER, output TEXT NOT NULL DEFAULT '',
                    total_files INTEGER NOT NULL DEFAULT 0, dbf_id TEXT NOT NULL DEFAULT '',
                    target_exists INTEGER NOT NULL DEFAULT 0,
                    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(catalog_id,playlist_id),
                    FOREIGN KEY(catalog_id) REFERENCES playlist_catalogs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_playlist_folder_source
                    ON playlist_folder_entries(catalog_id,position,title COLLATE NOCASE);
                CREATE TABLE IF NOT EXISTS operators (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL DEFAULT 'Operador', active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS api_users (
                    id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    salt BLOB NOT NULL, password_hash BLOB NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin','editor','reader')),
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS api_audit_log (
                    id INTEGER PRIMARY KEY, username TEXT NOT NULL, action TEXT NOT NULL,
                    resource TEXT NOT NULL, resource_id TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_api_audit_created ON api_audit_log(created_at DESC);
                CREATE TABLE IF NOT EXISTS api_program_drafts (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                    owner TEXT NOT NULL, base_revision INTEGER NOT NULL,
                    draft_revision INTEGER NOT NULL DEFAULT 1,
                    payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft'
                        CHECK(status IN ('draft','published','conflict')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    published_revision INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_api_program_drafts_owner ON api_program_drafts(owner,status);
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
                CREATE TABLE IF NOT EXISTS cartouche_slots (
                    slot INTEGER PRIMARY KEY CHECK(slot BETWEEN 1 AND 12),
                    media_id INTEGER
                );
                CREATE TABLE IF NOT EXISTS cartouche_history (
                    id INTEGER PRIMARY KEY,
                    slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 12),
                    media_id_snapshot INTEGER,
                    title TEXT NOT NULL,
                    path TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    operator TEXT NOT NULL DEFAULT '',
                    triggered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    started_at TEXT NOT NULL DEFAULT '',
                    ended_at TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'triggered',
                    error_message TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_cartouche_history_triggered ON cartouche_history(triggered_at DESC);
                CREATE TABLE IF NOT EXISTS playback_history (
                    id INTEGER PRIMARY KEY, media_id INTEGER,
                    title TEXT NOT NULL, artist TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'Música',
                    operator TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    ended_at TEXT NOT NULL DEFAULT '', played_seconds REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'completed', planned_for TEXT NOT NULL DEFAULT '',
                    source_type TEXT NOT NULL DEFAULT 'manual', source_ref INTEGER,
                    material_id_snapshot INTEGER, occurrence_id INTEGER, block_id INTEGER,
                    block_name_snapshot TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(media_id) REFERENCES media(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_history_started ON playback_history(started_at DESC);
                CREATE TABLE IF NOT EXISTS program_blocks (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                    block_type TEXT NOT NULL CHECK(block_type IN ('Música','Comercial')),
                    time_of_day TEXT NOT NULL, days TEXT NOT NULL DEFAULT '0,1,2,3,4,5,6',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    transition_policy TEXT NOT NULL DEFAULT 'discard_insertions'
                        CHECK(transition_policy IN ('discard_insertions','fixed')),
                    fixed_fade_ms INTEGER NOT NULL DEFAULT 1000 CHECK(fixed_fade_ms BETWEEN 0 AND 5000),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_program_blocks_time ON program_blocks(enabled,time_of_day);
                CREATE TABLE IF NOT EXISTS program_block_items (
                    id INTEGER PRIMARY KEY, block_id INTEGER NOT NULL, media_id INTEGER,
                    position INTEGER NOT NULL, item_kind TEXT NOT NULL DEFAULT 'media',
                    label TEXT NOT NULL DEFAULT '', pause_ms INTEGER NOT NULL DEFAULT 0,
                    folder_id INTEGER, selection_mode TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(block_id) REFERENCES program_blocks(id) ON DELETE CASCADE,
                    FOREIGN KEY(media_id) REFERENCES media(id) ON DELETE SET NULL
                );
                CREATE TABLE IF NOT EXISTS folder_rotation_state (
                    folder_id INTEGER NOT NULL, selection_mode TEXT NOT NULL,
                    member_ids_json TEXT NOT NULL DEFAULT '[]', deck_json TEXT NOT NULL DEFAULT '[]',
                    cursor INTEGER NOT NULL DEFAULT 0, cycle INTEGER NOT NULL DEFAULT 0,
                    last_media_id INTEGER, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(folder_id,selection_mode),
                    CHECK(selection_mode IN ('sequential','random'))
                );
                CREATE TABLE IF NOT EXISTS program_occurrences (
                    id INTEGER PRIMARY KEY, origin_type TEXT NOT NULL,
                    block_id INTEGER, timeline_event_id INTEGER,
                    scheduled_for TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'planned',
                    operator TEXT NOT NULL DEFAULT '', program_revision_id INTEGER NOT NULL DEFAULT 0,
                    transition_policy TEXT NOT NULL DEFAULT 'discard_insertions',
                    fixed_fade_ms INTEGER NOT NULL DEFAULT 1000,
                    dispatch_delay_ms INTEGER NOT NULL DEFAULT 0,
                    dispatch_reason TEXT NOT NULL DEFAULT '',
                    block_name_snapshot TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(block_id) REFERENCES program_blocks(id) ON DELETE SET NULL,
                    FOREIGN KEY(timeline_event_id) REFERENCES timeline_events(id) ON DELETE SET NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_occurrence_block_time
                    ON program_occurrences(block_id,scheduled_for) WHERE block_id IS NOT NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS idx_occurrence_event_time
                    ON program_occurrences(timeline_event_id,scheduled_for) WHERE timeline_event_id IS NOT NULL;
                CREATE TABLE IF NOT EXISTS program_occurrence_items (
                    id INTEGER PRIMARY KEY, occurrence_id INTEGER NOT NULL, source_item_id INTEGER,
                    media_id INTEGER, media_id_snapshot INTEGER, position INTEGER NOT NULL, item_kind TEXT NOT NULL DEFAULT 'media',
                    title TEXT NOT NULL DEFAULT '', artist TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '', duration REAL NOT NULL DEFAULT 0,
                    selection_folder_id INTEGER, selection_mode TEXT NOT NULL DEFAULT '', selected_at TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'planned', started_at TEXT NOT NULL DEFAULT '',
                    ended_at TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(occurrence_id) REFERENCES program_occurrences(id) ON DELETE CASCADE,
                    FOREIGN KEY(media_id) REFERENCES media(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_occurrence_items_pending
                    ON program_occurrence_items(status,occurrence_id,position);
                CREATE INDEX IF NOT EXISTS idx_occurrence_items_history
                    ON program_occurrence_items(occurrence_id,status);
                CREATE TABLE IF NOT EXISTS scheduler_decisions (
                    id INTEGER PRIMARY KEY,
                    origin_type TEXT NOT NULL,
                    source_id INTEGER NOT NULL,
                    scheduled_for TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(origin_type,source_id,scheduled_for,decision)
                );
                CREATE INDEX IF NOT EXISTS idx_scheduler_decisions_time
                    ON scheduler_decisions(scheduled_for DESC,origin_type,source_id);
                INSERT OR IGNORE INTO operators(name, role) VALUES ('Operador padrão', 'Administrador');
            """)
            self._migrate_columns(con)
            self._install_revision_triggers(con)
            for name, kind in (
                ('Música', 'audio'), ('Comercial', 'audio'), ('Vinheta', 'audio'),
                ('Trilha', 'audio'), ('Locução', 'audio'), ('Chamada', 'audio'),
                ('Hora Certa', 'audio'), ('Pausa', 'audio'), ('Texto', 'text'),
            ):
                con.execute(
                    "INSERT OR IGNORE INTO material_categories(name,material_kind) VALUES(?,?)",
                    (name, kind),
                )

    @staticmethod
    def _migrate_columns(con: sqlite3.Connection) -> None:
        """Additive migrations only; existing user data and tables are retained."""
        columns = {
            "media": {
                "folder_id": "INTEGER",
                "icon_path": "TEXT NOT NULL DEFAULT ''",
                "description": "TEXT NOT NULL DEFAULT ''",
                "text_content": "TEXT NOT NULL DEFAULT ''",
                "active": "INTEGER NOT NULL DEFAULT 1",
                "deactivated_at": "TEXT NOT NULL DEFAULT ''",
                "revision": "INTEGER NOT NULL DEFAULT 1",
            },
            "playlist_items": {
                "status": "TEXT NOT NULL DEFAULT 'planned'",
            },
            "playback_history": {
                "status": "TEXT NOT NULL DEFAULT 'completed'",
                "planned_for": "TEXT NOT NULL DEFAULT ''",
                "source_type": "TEXT NOT NULL DEFAULT 'manual'",
                "source_ref": "INTEGER",
                "material_id_snapshot": "INTEGER",
                "occurrence_id": "INTEGER",
                "block_id": "INTEGER",
                "block_name_snapshot": "TEXT NOT NULL DEFAULT ''",
                "error_message": "TEXT NOT NULL DEFAULT ''",
            },
            "program_occurrence_items": {
                "duration": "REAL NOT NULL DEFAULT 0",
                "media_id_snapshot": "INTEGER",
                "selection_folder_id": "INTEGER",
                "selection_mode": "TEXT NOT NULL DEFAULT ''",
                "selected_at": "TEXT NOT NULL DEFAULT ''",
            },
            "program_block_items": {
                "folder_id": "INTEGER",
                "selection_mode": "TEXT NOT NULL DEFAULT ''",
            },
            "media_folders": {
                "jingle_rotation_enabled": "INTEGER NOT NULL DEFAULT 0",
                "jingle_repeat_cooldown": "INTEGER NOT NULL DEFAULT 2",
            },
            "program_occurrences": {
                "program_revision_id": "INTEGER NOT NULL DEFAULT 0",
                "transition_policy": "TEXT NOT NULL DEFAULT 'discard_insertions'",
                "fixed_fade_ms": "INTEGER NOT NULL DEFAULT 1000",
                "dispatch_delay_ms": "INTEGER NOT NULL DEFAULT 0",
                "dispatch_reason": "TEXT NOT NULL DEFAULT ''",
                "block_name_snapshot": "TEXT NOT NULL DEFAULT ''",
            },
            "program_blocks": {
                "transition_policy": "TEXT NOT NULL DEFAULT 'discard_insertions'",
                "fixed_fade_ms": "INTEGER NOT NULL DEFAULT 1000",
            },
        }
        for table, additions in columns.items():
            present = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            for name, declaration in additions.items():
                if name not in present:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        con.execute(
            "UPDATE program_occurrence_items SET media_id_snapshot=media_id "
            "WHERE media_id_snapshot IS NULL AND media_id IS NOT NULL"
        )
        con.execute(
            "UPDATE playback_history SET material_id_snapshot=media_id "
            "WHERE material_id_snapshot IS NULL AND media_id IS NOT NULL"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_source ON playback_history(source_type,source_ref,occurrence_id)"
        )

    @staticmethod
    def _install_revision_triggers(con: sqlite3.Connection) -> None:
        for table in ("media", "material_categories", "media_folders"):
            for operation in ("INSERT", "UPDATE", "DELETE"):
                trigger = f"hitmo_catalog_{table}_{operation.lower()}"
                con.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {trigger} AFTER {operation} ON {table} "
                    "BEGIN UPDATE catalog_state SET revision=revision+1 WHERE id=1; END"
                )
        for table in ("program_blocks", "program_block_items"):
            for operation in ("INSERT", "UPDATE", "DELETE"):
                trigger = f"hitmo_program_{table}_{operation.lower()}"
                con.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {trigger} AFTER {operation} ON {table} "
                    "BEGIN UPDATE program_state SET revision=revision+1 WHERE id=1; END"
                )
        for operation in ("INSERT", "DELETE"):
            trigger = f"hitmo_program_timeline_events_{operation.lower()}"
            con.execute(
                f"CREATE TRIGGER IF NOT EXISTS {trigger} AFTER {operation} ON timeline_events "
                "BEGIN UPDATE program_state SET revision=revision+1 WHERE id=1; END"
            )
        con.execute(
            """CREATE TRIGGER IF NOT EXISTS hitmo_program_timeline_events_update
            AFTER UPDATE OF time_of_day,days,title,media_id,category,enabled ON timeline_events
            BEGIN UPDATE program_state SET revision=revision+1 WHERE id=1; END"""
        )

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

