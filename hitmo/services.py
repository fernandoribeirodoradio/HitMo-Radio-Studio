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

    def import_paths(self, paths: Iterable[Path | str], category: str | None = None) -> tuple[int, int]:
        added = 0
        with self.db.connect() as con:
            for raw in paths:
                path = Path(raw).expanduser()
                if path.is_dir():
                    found = (p for p in path.rglob("*") if p.is_file() and p.suffix.casefold() in SUPPORTED_AUDIO)
                else:
                    found = (path,) if path.is_file() and path.suffix.casefold() in SUPPORTED_AUDIO else ()
                for media_path in found:
                    resolved = str(media_path.resolve())
                    modified = media_path.stat().st_mtime
                    known = con.execute("SELECT modified FROM media WHERE path=?", (resolved,)).fetchone()
                    if known and float(known["modified"]) == modified:
                        continue
                    title, artist, duration = read_metadata(media_path)
                    media_type = category or media_category(media_path)
                    if known:
                        con.execute("UPDATE media SET title=?,artist=?,category=?,duration=?,modified=? WHERE path=?", (title, artist, media_type, duration, modified, resolved))
                    else:
                        try:
                            con.execute(
                                "INSERT INTO media(path,title,artist,category,duration,modified) VALUES(?,?,?,?,?,?)",
                                (resolved, title, artist, media_type, duration, modified),
                            )
                            added += 1
                        except sqlite3.IntegrityError:
                            # Another library scan may have inserted this path in parallel.
                            continue
        return added, self.count()

    def count(self) -> int:
        row = self.db.one("SELECT COUNT(*) AS n FROM media")
        return int(row["n"] if row else 0)

    def search(self, text: str = "", category: str = "Todos"):
        term = f"%{text.strip()}%"
        if category == "Todos":
            return self.db.all("SELECT * FROM media WHERE title LIKE ? OR artist LIKE ? OR path LIKE ? ORDER BY title COLLATE NOCASE", (term, term, term))
        return self.db.all("SELECT * FROM media WHERE category=? AND (title LIKE ? OR artist LIKE ? OR path LIKE ?) ORDER BY title COLLATE NOCASE", (category, term, term, term))


class PlaylistService:
    def __init__(self, db: Database):
        self.db = db

    def items(self):
        return self.db.all("SELECT p.id AS item_id,p.position,p.category,m.* FROM playlist_items p JOIN media m ON m.id=p.media_id ORDER BY p.position,p.id")

    def append(self, media_id: int, category: str | None = None) -> int:
        row = self.db.one("SELECT COALESCE(MAX(position),-1)+1 AS n FROM playlist_items")
        media = self.db.one("SELECT category FROM media WHERE id=?", (media_id,))
        return self.db.execute("INSERT INTO playlist_items(media_id,position,category) VALUES(?,?,?)", (media_id, int(row["n"]), category or (media["category"] if media else "Música")))

    def remove(self, item_id: int) -> None:
        self.db.execute("DELETE FROM playlist_items WHERE id=?", (item_id,))
        self.reorder([int(r["item_id"]) for r in self.items()])

    def reorder(self, item_ids: list[int]) -> None:
        with self.db.connect() as con:
            for pos, item_id in enumerate(item_ids):
                con.execute("UPDATE playlist_items SET position=? WHERE id=?", (pos, item_id))

    def pop_next(self):
        row = self.db.one("SELECT p.id AS item_id,m.* FROM playlist_items p JOIN media m ON m.id=p.media_id ORDER BY p.position,p.id LIMIT 1")
        if row:
            self.remove(int(row["item_id"]))
        return row


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

