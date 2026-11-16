from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .database import Database

try:
    from mutagen import File as MutagenFile
except ImportError:  # useful fallback when only the required GUI dependency is installed
    MutagenFile = None

SUPPORTED_AUDIO = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus"}


def media_category(path: Path) -> str:
    name = path.as_posix().casefold()
    if any(token in name for token in ("commercial", "comercial", "comerciais", "spot", "publicidade")):
        return "Comercial"
    if any(token in name for token in ("jingle", "vinheta", "sweep")):
        return "Vinheta"
    if any(token in name for token in ("bed", "trilha", "loop")):
        return "Trilha"
    if any(token in name for token in ("voice", "locucao", "locução")):
        return "Locução"
    return "Música"


def _tag_text(value: object) -> str:
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    if hasattr(value, "text"):
        values = getattr(value, "text")
        return str(values[0]) if values else ""
    return str(value) if value else ""


def read_metadata(path: Path) -> tuple[str, str, float]:
    title = path.stem
    artist = ""
    duration = 0.0
    if MutagenFile:
        try:
            audio = MutagenFile(path, easy=True)
            if audio:
                info = getattr(audio, "info", None)
                duration = float(getattr(info, "length", 0) or 0)
                tags = audio.tags or {}
                title = _tag_text(tags.get("title")) or title
                artist = _tag_text(tags.get("artist"))
        except Exception:
            pass
    if not artist and " - " in title:
        artist, title = title.split(" - ", 1)
    return title, artist, duration


class MediaLibrary:
    def __init__(self, db: Database):
        self.db = db

    def import_paths(self, paths: Iterable[Path | str], category: str | None = None, folder_id: int | None = None, include_non_audio: bool = False) -> tuple[int, int]:
        added = 0
        with self.db.connect() as con:
            for raw in paths:
                path = Path(raw).expanduser()
                supported = lambda p: p.is_file() and (include_non_audio or p.suffix.casefold() in SUPPORTED_AUDIO)
                if path.is_dir():
                    found = (p for p in path.rglob("*") if supported(p))
                else:
                    found = (path,) if supported(path) else ()
                for media_path in found:
                    resolved = str(media_path.resolve())
                    modified = media_path.stat().st_mtime
                    known = con.execute("SELECT modified FROM media WHERE path=?", (resolved,)).fetchone()
                    if known and float(known["modified"]) == modified:
                        if folder_id is not None:
                            con.execute("UPDATE media SET category=COALESCE(?,category),folder_id=? WHERE path=?", (category, folder_id, resolved))
                        continue
                    title, artist, duration = read_metadata(media_path)
                    media_type = category or media_category(media_path)
                    if known:
                        con.execute("UPDATE media SET title=?,artist=?,category=?,duration=?,modified=?,folder_id=COALESCE(?,folder_id) WHERE path=?", (title, artist, media_type, duration, modified, folder_id, resolved))
                    else:
                        try:
                            con.execute(
                                "INSERT INTO media(path,title,artist,category,duration,modified,folder_id) VALUES(?,?,?,?,?,?,?)",
                                (resolved, title, artist, media_type, duration, modified, folder_id),
                            )
                            added += 1
                        except sqlite3.IntegrityError:
                            # Another library scan may have inserted this path in parallel.
                            continue
        return added, self.count()

    def count(self) -> int:
        row = self.db.one("SELECT COUNT(*) AS n FROM media")
        return int(row["n"] if row else 0)

    def categories(self, active_only: bool = True):
        where = "WHERE active=1" if active_only else ""
        return self.db.all(f"SELECT * FROM material_categories {where} ORDER BY name COLLATE NOCASE")

    def save_category(self, name: str, material_kind: str = "audio", icon_path: str = "", category_id: int | None = None) -> int:
        name = name.strip()
        if not name:
            raise ValueError("Informe o nome da categoria.")
        if material_kind not in {"audio", "text", "file"}:
            raise ValueError("Tipo de material inválido.")
        if category_id:
            old = self.db.one("SELECT name FROM material_categories WHERE id=?", (category_id,))
            self.db.execute(
                "UPDATE material_categories SET name=?,material_kind=?,icon_path=?,active=1 WHERE id=?",
                (name, material_kind, icon_path, category_id),
            )
            if old:
                self.db.execute("UPDATE media SET category=? WHERE category=?", (name, old["name"]))
            return category_id
        return self.db.execute(
            "INSERT INTO material_categories(name,material_kind,icon_path) VALUES(?,?,?)",
            (name, material_kind, icon_path),
        )

    def deactivate_category(self, category_id: int) -> None:
        row = self.db.one("SELECT name FROM material_categories WHERE id=?", (category_id,))
        if not row:
            return
        in_use = self.db.one("SELECT 1 FROM media WHERE category=? LIMIT 1", (row["name"],))
        if in_use:
            raise ValueError("A categoria ainda está associada a materiais. Reclassifique-os antes de desativá-la.")
        self.db.execute("UPDATE material_categories SET active=0 WHERE id=?", (category_id,))

    def folders(self, active_only: bool = True):
        where = "WHERE f.active=1" if active_only else ""
        return self.db.all(
            f"SELECT f.*,c.name AS category FROM media_folders f LEFT JOIN material_categories c ON c.id=f.category_id {where} ORDER BY f.name COLLATE NOCASE"
        )

    def save_folder(self, name: str, path: str, category_id: int | None, icon_path: str = "", folder_id: int | None = None) -> int:
        name = name.strip() or Path(path).name
        canonical = str(Path(path).expanduser().resolve())
        if not Path(canonical).is_dir():
            raise ValueError("O diretório selecionado não existe.")
        if folder_id:
            self.db.execute(
                "UPDATE media_folders SET name=?,path=?,category_id=?,icon_path=?,active=1 WHERE id=?",
                (name, canonical, category_id, icon_path, folder_id),
            )
            return folder_id
        return self.db.execute(
            "INSERT INTO media_folders(name,path,category_id,icon_path) VALUES(?,?,?,?)",
            (name, canonical, category_id, icon_path),
        )

    def deactivate_folder(self, folder_id: int) -> None:
        # Deactivating a library root never deletes media files or database records.
        self.db.execute("UPDATE media_folders SET active=0 WHERE id=?", (folder_id,))

    def set_folder_enabled(self, folder_id: int, enabled: bool) -> None:
        self.db.execute("UPDATE media_folders SET active=? WHERE id=?", (int(enabled), folder_id))

    def set_category_enabled(self, category_id: int, enabled: bool) -> None:
        if enabled:
            self.db.execute("UPDATE material_categories SET active=1 WHERE id=?", (category_id,))
        else:
            self.deactivate_category(category_id)

    def update_material(self, media_id: int, *, title: str, artist: str, category: str, description: str = "", icon_path: str = "") -> None:
        if not title.strip() or not category.strip():
            raise ValueError("Título e categoria são obrigatórios.")
        self.db.execute(
            "UPDATE media SET title=?,artist=?,category=?,description=?,icon_path=? WHERE id=?",
            (title.strip(), artist.strip(), category.strip(), description.strip(), icon_path.strip(), media_id),
        )

    def search(self, text: str = "", category: str = "Todos"):
        term = f"%{text.strip()}%"
        if category == "Todos":
            return self.db.all(
                "SELECT m.* FROM media m LEFT JOIN material_categories c ON c.name=m.category "
                "WHERE m.active=1 AND COALESCE(c.material_kind,'audio')='audio' "
                "AND (m.title LIKE ? OR m.artist LIKE ? OR m.path LIKE ?) ORDER BY m.title COLLATE NOCASE",
                (term, term, term),
            )
        return self.db.all(
            "SELECT m.* FROM media m LEFT JOIN material_categories c ON c.name=m.category "
            "WHERE m.active=1 AND COALESCE(c.material_kind,'audio')='audio' AND m.category=? "
            "AND (m.title LIKE ? OR m.artist LIKE ? OR m.path LIKE ?) ORDER BY m.title COLLATE NOCASE",
            (category, term, term, term),
        )


class PlaylistService:
    def __init__(self, db: Database):
        self.db = db

    def items(self, status: str = "planned"):
        if status == "all":
            return self.db.all("SELECT p.id AS item_id,p.position,p.category,p.status,m.* FROM playlist_items p JOIN media m ON m.id=p.media_id ORDER BY p.position,p.id")
        return self.db.all("SELECT p.id AS item_id,p.position,p.category,p.status,m.* FROM playlist_items p JOIN media m ON m.id=p.media_id WHERE p.status=? ORDER BY p.position,p.id", (status,))

    def append(self, media_id: int, category: str | None = None) -> int:
        row = self.db.one("SELECT COALESCE(MAX(position),-1)+1 AS n FROM playlist_items")
        media = self.db.one("SELECT category FROM media WHERE id=?", (media_id,))
        return self.db.execute("INSERT INTO playlist_items(media_id,position,category) VALUES(?,?,?)", (media_id, int(row["n"]), category or (media["category"] if media else "Música")))

    def remove(self, item_id: int) -> None:
        self.db.execute("DELETE FROM playlist_items WHERE id=? AND status='planned'", (item_id,))
        self.reorder([int(r["item_id"]) for r in self.items()])

    def reorder(self, item_ids: list[int]) -> None:
        with self.db.connect() as con:
            for pos, item_id in enumerate(item_ids):
                con.execute("UPDATE playlist_items SET position=? WHERE id=?", (pos, item_id))

    def pop_next(self):
        with self.db.connect() as con:
            row = con.execute("SELECT p.id AS item_id,p.position,p.category,m.* FROM playlist_items p JOIN media m ON m.id=p.media_id WHERE p.status='planned' ORDER BY p.position,p.id LIMIT 1").fetchone()
        if row:
            self.db.execute("UPDATE playlist_items SET status='loading' WHERE id=? AND status='planned'", (int(row["item_id"]),))
        return row

    def claim(self, item_id: int):
        with self.db.connect() as con:
            row = con.execute("SELECT p.id AS item_id,p.position,p.category,m.* FROM playlist_items p JOIN media m ON m.id=p.media_id WHERE p.id=? AND p.status='planned'", (item_id,)).fetchone()
            if row:
                con.execute("UPDATE playlist_items SET status='loading' WHERE id=? AND status='planned'", (item_id,))
            return row

    def set_status(self, item_id: int, status: str) -> None:
        allowed = {"planned", "loading", "playing", "paused", "completed", "skipped", "stopped", "failed", "ejected", "interrupted"}
        if status not in allowed:
            raise ValueError(f"Estado de programação inválido: {status}")
        self.db.execute("UPDATE playlist_items SET status=? WHERE id=?", (status, item_id))

    def recover_interrupted(self) -> None:
        # A manual item was not completed if the prior process disappeared mid-play.
        self.db.execute("UPDATE playlist_items SET status='planned' WHERE status IN ('loading','playing','paused')")


class ProgrammingService:
    """Persistent recurring blocks and immutable dated playback occurrences."""

    def __init__(self, db: Database):
        self.db = db

    def add_block(self, name: str, block_type: str, time_of_day: str, days: list[int]) -> int:
        if block_type not in {"Música", "Comercial"}:
            raise ValueError("O bloco deve ser musical ou comercial.")
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_of_day):
            raise ValueError("Use um horário válido no formato HH:MM.")
        if not days or any(day not in range(7) for day in days):
            raise ValueError("Selecione dias válidos da semana.")
        if not name.strip():
            raise ValueError("Informe um nome para o bloco.")
        return self.db.execute(
            "INSERT INTO program_blocks(name,block_type,time_of_day,days) VALUES(?,?,?,?)",
            (name.strip(), block_type, time_of_day, ",".join(map(str, sorted(set(days))))),
        )

    def blocks(self):
        return self.db.all("SELECT * FROM program_blocks ORDER BY time_of_day,name COLLATE NOCASE")

    def recover_interrupted(self) -> None:
        with self.db.connect() as con:
            con.execute("UPDATE program_occurrence_items SET status='interrupted',ended_at=CURRENT_TIMESTAMP WHERE status IN ('loading','playing','paused')")
            con.execute("UPDATE program_occurrences SET status='interrupted' WHERE status='running' AND NOT EXISTS (SELECT 1 FROM program_occurrence_items i WHERE i.occurrence_id=program_occurrences.id AND i.status='planned')")

    def block_items(self, block_id: int):
        return self.db.all(
            "SELECT i.id AS block_item_id,i.position,i.item_kind,m.* FROM program_block_items i LEFT JOIN media m ON m.id=i.media_id WHERE i.block_id=? ORDER BY i.position,i.id",
            (block_id,),
        )

    def add_block_item(self, block_id: int, media_id: int) -> int:
        block = self.db.one("SELECT block_type FROM program_blocks WHERE id=?", (block_id,))
        media = self.db.one("SELECT category,active FROM media WHERE id=?", (media_id,))
        if not block or not media:
            raise ValueError("Bloco ou material não encontrado.")
        if not bool(media["active"]):
            raise ValueError("Material inativo não pode ser adicionado a uma nova programação.")
        category = self.db.one("SELECT material_kind FROM material_categories WHERE name=? AND active=1", (media["category"],))
        if category and category["material_kind"] != "audio":
            raise ValueError("Somente materiais de áudio podem entrar em um bloco executável.")
        if block["block_type"] == "Comercial" and media["category"] not in {"Comercial", "Vinheta"}:
            raise ValueError("Bloco comercial aceita materiais classificados como Comercial ou Vinheta.")
        if block["block_type"] == "Música" and media["category"] == "Comercial":
            raise ValueError("Materiais classificados como Comercial não podem entrar em bloco musical.")
        pos = self.db.one("SELECT COALESCE(MAX(position),-1)+1 AS n FROM program_block_items WHERE block_id=?", (block_id,))
        return self.db.execute(
            "INSERT INTO program_block_items(block_id,media_id,position,item_kind) VALUES(?,?,?,'media')",
            (block_id, media_id, int(pos["n"])),
        )

    def remove_block_item(self, item_id: int) -> None:
        # This removes only the program reference. The media row and physical file remain.
        self.db.execute("DELETE FROM program_block_items WHERE id=?", (item_id,))

    def reorder_block_items(self, block_id: int, item_ids: list[int]) -> None:
        with self.db.connect() as con:
            for position, item_id in enumerate(item_ids):
                con.execute("UPDATE program_block_items SET position=? WHERE id=? AND block_id=?", (position, item_id, block_id))

    def remove_block(self, block_id: int) -> None:
        self.db.execute("DELETE FROM program_blocks WHERE id=?", (block_id,))

    def set_block_enabled(self, block_id: int, enabled: bool) -> None:
        self.db.execute("UPDATE program_blocks SET enabled=? WHERE id=?", (int(enabled), block_id))

    def due(self, now: datetime):
        scheduled_for = now.strftime("%Y-%m-%d %H:%M")
        weekday = now.weekday()
        created: list[int] = []
        created_block = False
        with self.db.connect() as con:
            program_revision = int(con.execute("SELECT revision FROM program_state WHERE id=1").fetchone()["revision"])
            blocks = con.execute("SELECT * FROM program_blocks WHERE enabled=1 AND time_of_day=?", (now.strftime("%H:%M"),)).fetchall()
            for block in blocks:
                if weekday not in {int(x) for x in block["days"].split(",") if x.strip().isdigit()}:
                    continue
                items = con.execute(
                    "SELECT i.id,i.media_id,i.position,i.item_kind,m.title,m.artist,m.path,m.category,m.duration FROM program_block_items i JOIN media m ON m.id=i.media_id WHERE i.block_id=? ORDER BY i.position,i.id",
                    (int(block["id"]),),
                ).fetchall()
                if not items:
                    continue
                cursor = con.execute(
                    "INSERT OR IGNORE INTO program_occurrences(origin_type,block_id,scheduled_for,status,program_revision_id) VALUES('block',?,?,'planned',?)",
                    (int(block["id"]), scheduled_for, program_revision),
                )
                if cursor.rowcount:
                    occurrence_id = int(cursor.lastrowid)
                    created.append(occurrence_id)
                    created_block = True
                    for item in items:
                        con.execute(
                            "INSERT INTO program_occurrence_items(occurrence_id,source_item_id,media_id,position,item_kind,title,artist,path,category,duration) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (occurrence_id, int(item["id"]), int(item["media_id"]), int(item["position"]), item["item_kind"], item["title"], item["artist"], item["path"], item["category"], item["duration"]),
                        )
            if created_block:
                # The next scheduled block is the documented cutoff. Never stop
                # an item already loading/playing; discard only remaining inserts
                # from older block occurrences, leaving their audit snapshots.
                con.execute(
                    "UPDATE program_occurrence_items "
                    "SET status='skipped',ended_at=CURRENT_TIMESTAMP,"
                    "error_message='Inserção excedente descartada no horário do próximo bloco' "
                    "WHERE status='planned' AND occurrence_id IN ("
                    "SELECT id FROM program_occurrences WHERE origin_type='block' AND scheduled_for<?)",
                    (scheduled_for,),
                )
                con.execute(
                    "UPDATE program_occurrences SET status='interrupted' "
                    "WHERE origin_type='block' AND scheduled_for<? AND status IN ('planned','running') "
                    "AND NOT EXISTS (SELECT 1 FROM program_occurrence_items i WHERE i.occurrence_id=program_occurrences.id "
                    "AND i.status IN ('planned','loading','playing','paused'))",
                    (scheduled_for,),
                )
            events = con.execute(
                "SELECT t.*,m.id AS actual_media_id,m.title AS media_title,m.artist,m.path,m.category AS media_category,m.duration FROM timeline_events t JOIN media m ON m.id=t.media_id WHERE t.enabled=1 AND t.time_of_day=? AND t.last_fired<>?",
                (now.strftime("%H:%M"), scheduled_for),
            ).fetchall()
            for event in events:
                if weekday not in {int(x) for x in event["days"].split(",") if x.strip().isdigit()}:
                    continue
                cursor = con.execute(
                    "INSERT OR IGNORE INTO program_occurrences(origin_type,timeline_event_id,scheduled_for,status,program_revision_id) VALUES('timeline',?,?,'planned',?)",
                    (int(event["id"]), scheduled_for, program_revision),
                )
                if cursor.rowcount:
                    occurrence_id = int(cursor.lastrowid)
                    created.append(occurrence_id)
                    con.execute(
                        "INSERT INTO program_occurrence_items(occurrence_id,media_id,position,item_kind,title,artist,path,category,duration) VALUES(?,?,0,'media',?,?,?,?,?)",
                        (occurrence_id, int(event["actual_media_id"]), event["media_title"], event["artist"], event["path"], event["media_category"], event["duration"]),
                    )
                    con.execute("UPDATE timeline_events SET last_fired=? WHERE id=?", (scheduled_for, int(event["id"])))
        return created

    def next_occurrence_item(self):
        return self.db.one(
            "SELECT i.*,o.scheduled_for,o.id AS occurrence_id FROM program_occurrence_items i JOIN program_occurrences o ON o.id=i.occurrence_id WHERE i.status='planned' AND o.status IN ('planned','running','interrupted') ORDER BY CASE o.status WHEN 'planned' THEN 0 WHEN 'running' THEN 1 ELSE 2 END,o.scheduled_for,o.id,i.position,i.id LIMIT 1"
        )

    def set_occurrence_item_status(self, item_id: int, status: str, error_message: str = "") -> None:
        allowed = {"planned", "loading", "playing", "paused", "completed", "skipped", "stopped", "failed", "ejected", "interrupted"}
        if status not in allowed:
            raise ValueError(f"Estado de ocorrência inválido: {status}")
        with self.db.connect() as con:
            con.execute(
                "UPDATE program_occurrence_items SET status=?,error_message=?,started_at=CASE WHEN ?='playing' AND started_at='' THEN CURRENT_TIMESTAMP ELSE started_at END,ended_at=CASE WHEN ? IN ('completed','skipped','stopped','failed','interrupted') THEN CURRENT_TIMESTAMP ELSE ended_at END WHERE id=?",
                (status, error_message, status, status, item_id),
            )
            occurrence_id = con.execute("SELECT occurrence_id FROM program_occurrence_items WHERE id=?", (item_id,)).fetchone()
            if occurrence_id:
                if status == "interrupted":
                    con.execute("UPDATE program_occurrences SET status='interrupted' WHERE id=?", (occurrence_id["occurrence_id"],))
                    return
                pending = con.execute("SELECT 1 FROM program_occurrence_items WHERE occurrence_id=? AND status NOT IN ('completed','skipped','stopped','failed','interrupted') LIMIT 1", (occurrence_id["occurrence_id"],)).fetchone()
                if pending:
                    con.execute("UPDATE program_occurrences SET status='running' WHERE id=?", (occurrence_id["occurrence_id"],))
                else:
                    terminal = con.execute("SELECT 1 FROM program_occurrence_items WHERE occurrence_id=? AND status NOT IN ('completed') LIMIT 1", (occurrence_id["occurrence_id"],)).fetchone()
                    con.execute("UPDATE program_occurrences SET status=? WHERE id=?", ("completed" if not terminal else "interrupted", occurrence_id["occurrence_id"]))


class TimelineService:
    def __init__(self, db: Database):
        self.db = db

    def add(self, time_of_day: str, days: list[int], title: str, media_id: int | None, category: str) -> int:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_of_day):
            raise ValueError("Use um horário válido no formato HH:MM.")
        if not days:
            raise ValueError("Selecione pelo menos um dia da semana.")
        return self.db.execute("INSERT INTO timeline_events(time_of_day,days,title,media_id,category) VALUES(?,?,?,?,?)", (time_of_day, ",".join(map(str, sorted(set(days)))), title.strip(), media_id, category))

    def events(self):
        return self.db.all("SELECT t.*,m.path,m.title AS media_title FROM timeline_events t LEFT JOIN media m ON m.id=t.media_id ORDER BY t.time_of_day,t.title COLLATE NOCASE")

    def due(self, now: datetime):
        stamp = now.strftime("%Y-%m-%d %H:%M")
        rows = self.db.all("SELECT t.*,m.id AS media_id,m.path,m.title AS media_title,m.artist,m.category AS media_category,m.duration FROM timeline_events t JOIN media m ON m.id=t.media_id WHERE t.enabled=1 AND t.time_of_day=? AND t.last_fired<>? ORDER BY t.id", (now.strftime("%H:%M"), stamp))
        due_rows = [r for r in rows if now.weekday() in {int(x) for x in r["days"].split(",") if x.strip().isdigit()}]
        for row in due_rows:
            self.db.execute("UPDATE timeline_events SET last_fired=? WHERE id=?", (stamp, int(row["id"])))
        return due_rows

