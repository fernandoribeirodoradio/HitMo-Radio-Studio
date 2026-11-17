from __future__ import annotations

import re
import json
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from .database import Database

try:
    from mutagen import File as MutagenFile
except ImportError:  # useful fallback when only the required GUI dependency is installed
    MutagenFile = None

SUPPORTED_AUDIO = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus"}
SCHEDULER_LATE_GRACE_SECONDS = 30
FIXED_LATE_GRACE_MS = 1000
SCHEDULER_RECOVERY_HORIZON_DAYS = 31


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

    def __init__(self, db: Database, rng: random.Random | None = None):
        self.db = db
        self.rng = rng or random.Random()

    def add_block(
        self, name: str, block_type: str, time_of_day: str, days: list[int],
        transition_policy: str = "discard_insertions", fixed_fade_ms: int = 1000,
    ) -> int:
        if block_type not in {"Música", "Comercial"}:
            raise ValueError("O bloco deve ser musical ou comercial.")
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_of_day):
            raise ValueError("Use um horário válido no formato HH:MM.")
        if not days or any(day not in range(7) for day in days):
            raise ValueError("Selecione dias válidos da semana.")
        if not name.strip():
            raise ValueError("Informe um nome para o bloco.")
        if transition_policy not in {"discard_insertions", "fixed"}:
            raise ValueError("Política de transição inválida.")
        if transition_policy == "fixed" and block_type != "Comercial":
            raise ValueError("A transição Fixo só pode ser configurada em bloco comercial.")
        if type(fixed_fade_ms) is not int or not 0 <= fixed_fade_ms <= 5000:
            raise ValueError("O fade do bloco Fixo deve estar entre 0 e 5000 ms.")
        return self.db.execute(
            "INSERT INTO program_blocks(name,block_type,time_of_day,days,transition_policy,fixed_fade_ms) VALUES(?,?,?,?,?,?)",
            (name.strip(), block_type, time_of_day, ",".join(map(str, sorted(set(days)))), transition_policy, fixed_fade_ms),
        )

    def blocks(self):
        return self.db.all("SELECT * FROM program_blocks ORDER BY time_of_day,name COLLATE NOCASE")

    def recover_interrupted(self) -> None:
        with self.db.connect() as con:
            con.execute("UPDATE program_occurrence_items SET status='interrupted',ended_at=CURRENT_TIMESTAMP WHERE status IN ('loading','playing','paused')")
            con.execute("UPDATE program_occurrences SET status='interrupted' WHERE status='running' AND NOT EXISTS (SELECT 1 FROM program_occurrence_items i WHERE i.occurrence_id=program_occurrences.id AND i.status='planned')")

    def block_items(self, block_id: int):
        return self.db.all(
            "SELECT i.id AS block_item_id,i.position,i.item_kind,i.folder_id,i.selection_mode,"
            "f.name AS folder_name,f.path AS folder_path,m.* FROM program_block_items i "
            "LEFT JOIN media m ON m.id=i.media_id LEFT JOIN media_folders f ON f.id=i.folder_id "
            "WHERE i.block_id=? ORDER BY i.position,i.id",
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

    @staticmethod
    def _folder_candidates(con, folder_id: int, block_type: str):
        sql = (
            "SELECT m.* FROM media m JOIN media_folders f ON f.id=m.folder_id AND f.active=1 "
            "LEFT JOIN material_categories c ON c.name=m.category "
            "WHERE m.folder_id=? AND m.active=1 AND COALESCE(c.material_kind,'audio')='audio'"
        )
        params: list[object] = [folder_id]
        if block_type == "Comercial":
            sql += " AND m.category IN ('Comercial','Vinheta')"
        elif block_type == "Música":
            sql += " AND m.category<>'Comercial'"
        sql += " ORDER BY m.title COLLATE NOCASE,m.path COLLATE NOCASE,m.id"
        return con.execute(sql, params).fetchall()

    def add_block_folder(self, block_id: int, folder_id: int, selection_mode: str) -> int:
        if selection_mode not in {"sequential", "random", "jingle_rotation"}:
            raise ValueError("Modo de seleção de pasta inválido.")
        block = self.db.one("SELECT block_type FROM program_blocks WHERE id=?", (block_id,))
        folder = self.db.one(
            "SELECT f.id,f.jingle_rotation_enabled,c.name AS category FROM media_folders f "
            "LEFT JOIN material_categories c ON c.id=f.category_id WHERE f.id=? AND f.active=1",
            (folder_id,),
        )
        if not block or not folder:
            raise ValueError("Bloco ou pasta ativa não encontrada.")
        with self.db.connect() as con:
            if selection_mode == "jingle_rotation":
                if folder["category"] != "Vinheta" or not folder["jingle_rotation_enabled"]:
                    raise ValueError("Configure o rodízio automático nesta pasta de Vinhetas primeiro.")
                candidates = self._jingle_candidates(con, folder_id)
            else:
                candidates = self._folder_candidates(con, folder_id, block["block_type"])
        if not candidates:
            raise ValueError("A pasta não contém materiais ativos compatíveis com o bloco.")
        pos = self.db.one("SELECT COALESCE(MAX(position),-1)+1 AS n FROM program_block_items WHERE block_id=?", (block_id,))
        return self.db.execute(
            "INSERT INTO program_block_items(block_id,folder_id,position,item_kind,selection_mode) VALUES(?,?,?,'folder',?)",
            (block_id, folder_id, int(pos["n"]), selection_mode),
        )

    def _select_folder_media(self, con, folder_id: int, selection_mode: str, block_type: str, excluded_ids: set[int] | None = None):
        candidates = self._folder_candidates(con, folder_id, block_type)
        excluded_ids = excluded_ids or set()
        available_ids = {int(row["id"]) for row in candidates} - excluded_ids
        if not available_ids:
            return None
        members = [int(row["id"]) for row in candidates]
        members_json = json.dumps(members, separators=(",", ":"))
        state = con.execute(
            "SELECT * FROM folder_rotation_state WHERE folder_id=? AND selection_mode=?",
            (folder_id, selection_mode),
        ).fetchone()
        last_media_id = int(state["last_media_id"]) if state and state["last_media_id"] is not None else None
        cycle = int(state["cycle"]) if state else 0
        try:
            deck = [int(value) for value in json.loads(state["deck_json"])] if state else []
        except (TypeError, ValueError, json.JSONDecodeError):
            deck = []
        cursor = int(state["cursor"]) if state else 0
        valid_deck = len(deck) == len(members) and set(deck) == set(members)
        if not valid_deck:
            deck = list(members)
            cursor = 0
            cycle = 0
            if selection_mode == "random":
                self.rng.shuffle(deck)
        selected_id = None
        has_nonrepeat = any(media_id != last_media_id for media_id in available_ids)
        for _attempt in range(max(1, len(deck) * 2)):
            if cursor >= len(deck):
                cycle += 1
                cursor = 0
                if selection_mode == "random":
                    deck = list(members)
                    self.rng.shuffle(deck)
            candidate_id = deck[cursor]
            cursor += 1
            if candidate_id in available_ids and (not has_nonrepeat or candidate_id != last_media_id):
                selected_id = candidate_id
                break
        if selected_id is None:
            # All remaining candidates would repeat a media item already placed
            # in this same occurrence; leave this selector auditable as failed.
            return None
        con.execute(
            "INSERT INTO folder_rotation_state(folder_id,selection_mode,member_ids_json,deck_json,cursor,cycle,last_media_id,updated_at) "
            "VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(folder_id,selection_mode) DO UPDATE SET "
            "member_ids_json=excluded.member_ids_json,deck_json=excluded.deck_json,cursor=excluded.cursor,"
            "cycle=excluded.cycle,last_media_id=excluded.last_media_id,updated_at=CURRENT_TIMESTAMP",
            (folder_id, selection_mode, members_json, json.dumps(deck, separators=(",", ":")), cursor, cycle, selected_id),
        )
        return next(row for row in candidates if int(row["id"]) == selected_id)

    @staticmethod
    def _jingle_candidates(con, folder_id: int):
        return con.execute(
            "SELECT m.* FROM media m JOIN media_folders f ON f.id=m.folder_id AND f.active=1 "
            "JOIN material_categories c ON c.name=m.category AND c.material_kind='audio' "
            "WHERE m.folder_id=? AND m.active=1 AND m.category='Vinheta' "
            "ORDER BY m.title COLLATE NOCASE,m.path COLLATE NOCASE,m.id",
            (folder_id,),
        ).fetchall()

    def _select_jingle_media(self, con, folder_id: int, excluded_ids: set[int] | None = None):
        folder = con.execute(
            "SELECT f.jingle_rotation_enabled,f.jingle_repeat_cooldown,c.name AS category "
            "FROM media_folders f LEFT JOIN material_categories c ON c.id=f.category_id "
            "WHERE f.id=? AND f.active=1",
            (folder_id,),
        ).fetchone()
        if not folder or folder["category"] != "Vinheta" or not folder["jingle_rotation_enabled"]:
            return None
        candidates = self._jingle_candidates(con, folder_id)
        excluded_ids = excluded_ids or set()
        available = [row for row in candidates if int(row["id"]) not in excluded_ids]
        if not available:
            return None
        state = con.execute("SELECT * FROM jingle_rotation_state WHERE folder_id=?", (folder_id,)).fetchone()
        try:
            raw_counts = json.loads(state["counts_json"]) if state else {}
            counts = {str(int(key)): max(0, int(value)) for key, value in raw_counts.items()}
        except (TypeError, ValueError, json.JSONDecodeError, AttributeError):
            counts = {}
        try:
            raw_recent = json.loads(state["recent_ids_json"]) if state else []
            recent_ids = [int(value) for value in raw_recent if str(value).isdigit()]
        except (TypeError, ValueError, json.JSONDecodeError):
            recent_ids = []
        cooldown = max(0, min(100, int(folder["jingle_repeat_cooldown"] or 0)))
        if cooldown:
            recent = set(recent_ids[-cooldown:])
            cooled = [row for row in available if int(row["id"]) not in recent]
            # A small folder must remain schedulable: relax cooldown only when
            # every remaining candidate is inside it. Same-occurrence duplicates
            # are still excluded by `available` above.
            if cooled:
                available = cooled
        least_played = min(counts.get(str(int(row["id"])), 0) for row in available)
        balanced = [row for row in available if counts.get(str(int(row["id"])), 0) == least_played]
        selected = self.rng.choice(balanced)
        selected_id = int(selected["id"])
        counts[str(selected_id)] = counts.get(str(selected_id), 0) + 1
        recent_ids.append(selected_id)
        recent_ids = recent_ids[-100:]
        con.execute(
            "INSERT INTO jingle_rotation_state(folder_id,counts_json,recent_ids_json,total_selected,updated_at) "
            "VALUES(?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(folder_id) DO UPDATE SET "
            "counts_json=excluded.counts_json,recent_ids_json=excluded.recent_ids_json,"
            "total_selected=excluded.total_selected,updated_at=CURRENT_TIMESTAMP",
            (folder_id, json.dumps(counts, sort_keys=True, separators=(",", ":")), json.dumps(recent_ids, separators=(",", ":")), int(state["total_selected"] if state else 0) + 1),
        )
        return selected

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

    def next_fixed_transition(self, now: datetime):
        """Return the next enabled commercial Fixo boundary after ``now``."""
        rows = self.db.all(
            "SELECT id,time_of_day,days,fixed_fade_ms FROM program_blocks "
            "WHERE enabled=1 AND block_type='Comercial' AND transition_policy='fixed' "
            "AND EXISTS (SELECT 1 FROM program_block_items i WHERE i.block_id=program_blocks.id)"
        )
        candidates = []
        for offset in range(8):
            day = now.date() + timedelta(days=offset)
            for row in rows:
                days = {int(value) for value in str(row["days"]).split(",") if value.strip().isdigit()}
                if day.weekday() not in days:
                    continue
                target = datetime.combine(day, datetime.strptime(row["time_of_day"], "%H:%M").time())
                if target > now:
                    fade_ms = max(0, min(5000, int(row["fixed_fade_ms"])))
                    candidates.append((target, int(row["id"]), fade_ms))
        if not candidates:
            return None
        target, block_id, fade_ms = min(candidates)
        return {"at": target, "block_id": block_id, "fade_ms": fade_ms}

    def due(
        self, now: datetime, block_ids: set[int] | None = None,
        timeline_event_ids: set[int] | None = None, operator: str = "",
    ):
        scheduled_for = now.strftime("%Y-%m-%d %H:%M")
        weekday = now.weekday()
        created: list[int] = []
        created_blocks: list[sqlite3.Row] = []
        with self.db.connect() as con:
            program_revision = int(con.execute("SELECT revision FROM program_state WHERE id=1").fetchone()["revision"])
            blocks = con.execute(
                "SELECT * FROM program_blocks WHERE enabled=1 AND time_of_day=? ORDER BY id",
                (now.strftime("%H:%M"),),
            ).fetchall()
            for block in blocks:
                if block_ids is not None and int(block["id"]) not in block_ids:
                    continue
                if weekday not in {int(x) for x in block["days"].split(",") if x.strip().isdigit()}:
                    continue
                items = con.execute(
                    "SELECT i.id,i.media_id,i.folder_id,i.selection_mode,i.position,i.item_kind,i.label,"
                    "f.name AS folder_name,m.title,m.artist,m.path,m.category,m.duration "
                    "FROM program_block_items i LEFT JOIN media m ON m.id=i.media_id "
                    "LEFT JOIN media_folders f ON f.id=i.folder_id WHERE i.block_id=? ORDER BY i.position,i.id",
                    (int(block["id"]),),
                ).fetchall()
                if not items:
                    continue
                cursor = con.execute(
                    "INSERT OR IGNORE INTO program_occurrences(origin_type,block_id,scheduled_for,status,operator,program_revision_id,transition_policy,fixed_fade_ms,block_name_snapshot) "
                    "VALUES('block',?,?,'planned',?,?,?,?,?)",
                    (int(block["id"]), scheduled_for, operator, program_revision, block["transition_policy"], block["fixed_fade_ms"], block["name"]),
                )
                if cursor.rowcount:
                    occurrence_id = int(cursor.lastrowid)
                    created.append(occurrence_id)
                    created_blocks.append(block)
                    # Folder selections avoid tracks explicitly fixed elsewhere
                    # in this block and choices already materialized for it.
                    selected_ids = {int(row["media_id"]) for row in items if row["media_id"] is not None}
                    for item in items:
                        folder_id = int(item["folder_id"]) if item["folder_id"] is not None else None
                        selection_mode = str(item["selection_mode"] or "")
                        selected = None
                        if folder_id is not None and selection_mode in {"sequential", "random"}:
                            selected = self._select_folder_media(con, folder_id, selection_mode, block["block_type"], selected_ids)
                        elif folder_id is not None and selection_mode == "jingle_rotation":
                            selected = self._select_jingle_media(con, folder_id, selected_ids)
                        elif item["media_id"] is not None:
                            selected = item
                        selected_at = now.strftime("%Y-%m-%d %H:%M:%S")
                        if selected is None:
                            folder_title = str(item["folder_name"] or item["label"] or f"Pasta {folder_id or ''}")
                            if selection_mode == "jingle_rotation":
                                eligible = self._jingle_candidates(con, folder_id) if folder_id is not None else []
                                rotation = con.execute(
                                    "SELECT f.jingle_rotation_enabled,c.name AS category FROM media_folders f "
                                    "LEFT JOIN material_categories c ON c.id=f.category_id WHERE f.id=? AND f.active=1",
                                    (folder_id,),
                                ).fetchone() if folder_id is not None else None
                                if not rotation or rotation["category"] != "Vinheta" or not rotation["jingle_rotation_enabled"]:
                                    selection_error = "Rodízio automático desativado ou pasta fora da categoria Vinheta."
                                elif eligible and all(int(candidate["id"]) in selected_ids for candidate in eligible):
                                    selection_error = "Todas as vinhetas elegíveis já foram usadas nesta ocorrência."
                                else:
                                    selection_error = "Pasta sem vinhetas de áudio ativas e compatíveis no momento da seleção."
                            else:
                                eligible = self._folder_candidates(con, folder_id, block["block_type"]) if folder_id is not None else []
                                if eligible and all(int(candidate["id"]) in selected_ids for candidate in eligible):
                                    selection_error = "Todos os materiais ativos compatíveis da pasta já foram usados nesta ocorrência."
                                else:
                                    selection_error = "Pasta sem material de áudio ativo e compatível no momento da seleção."
                            con.execute(
                                "INSERT INTO program_occurrence_items(occurrence_id,source_item_id,media_id,media_id_snapshot,position,item_kind,title,category,selection_folder_id,selection_mode,selected_at,status,ended_at,error_message) "
                                "VALUES(?,?,NULL,NULL,?,'media',?,'',?,?,?,'failed',?,?)",
                                (occurrence_id, int(item["id"]), int(item["position"]), f"Pasta indisponível: {folder_title}", folder_id, selection_mode, selected_at, selected_at, selection_error),
                            )
                            continue
                        selected_ids.add(int(selected["id"] if "id" in selected.keys() else selected["media_id"]))
                        con.execute(
                            "INSERT INTO program_occurrence_items(occurrence_id,source_item_id,media_id,media_id_snapshot,position,item_kind,title,artist,path,category,duration,selection_folder_id,selection_mode,selected_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (occurrence_id, int(item["id"]), int(selected["id"] if "id" in selected.keys() else selected["media_id"]), int(selected["id"] if "id" in selected.keys() else selected["media_id"]), int(item["position"]), "media", selected["title"], selected["artist"], selected["path"], selected["category"], selected["duration"], folder_id, selection_mode, selected_at),
                        )
                    if not con.execute(
                        "SELECT 1 FROM program_occurrence_items WHERE occurrence_id=? AND status='planned' LIMIT 1",
                        (occurrence_id,),
                    ).fetchone():
                        con.execute("UPDATE program_occurrences SET status='failed' WHERE id=?", (occurrence_id,))
            if created_blocks:
                # The next scheduled block is the documented cutoff. Never stop
                # an item already loading/playing; discard only remaining inserts
                # from older block occurrences, leaving their audit snapshots.
                fixed_transition = any(block["transition_policy"] == "fixed" for block in created_blocks)
                reason = (
                    "Inserção excedente descartada no horário do bloco Fixo seguinte"
                    if fixed_transition else "Inserção excedente descartada no horário do próximo bloco"
                )
                con.execute(
                    "UPDATE program_occurrence_items "
                    "SET status='discarded',ended_at=CURRENT_TIMESTAMP,error_message=? "
                    "WHERE status='planned' AND occurrence_id IN ("
                    "SELECT id FROM program_occurrences WHERE origin_type='block' AND scheduled_for<?)",
                    (reason, scheduled_for),
                )
                con.execute(
                    "UPDATE program_occurrences SET status='interrupted' "
                    "WHERE origin_type='block' AND scheduled_for<? AND status IN ('planned','running') "
                    "AND NOT EXISTS (SELECT 1 FROM program_occurrence_items i WHERE i.occurrence_id=program_occurrences.id "
                    "AND i.status IN ('planned','loading','playing','paused'))",
                    (scheduled_for,),
                )
            events = con.execute(
                "SELECT t.*,m.id AS actual_media_id,m.title AS media_title,m.artist,m.path,m.category AS media_category,m.duration FROM timeline_events t JOIN media m ON m.id=t.media_id WHERE t.enabled=1 AND t.time_of_day=? AND t.last_fired<>? ORDER BY t.id",
                (now.strftime("%H:%M"), scheduled_for),
            ).fetchall()
            for event in events:
                if timeline_event_ids is not None and int(event["id"]) not in timeline_event_ids:
                    continue
                if weekday not in {int(x) for x in event["days"].split(",") if x.strip().isdigit()}:
                    continue
                cursor = con.execute(
                    "INSERT OR IGNORE INTO program_occurrences(origin_type,timeline_event_id,scheduled_for,status,operator,program_revision_id) VALUES('timeline',?,?,'planned',?,?)",
                    (int(event["id"]), scheduled_for, operator, program_revision),
                )
                if cursor.rowcount:
                    occurrence_id = int(cursor.lastrowid)
                    created.append(occurrence_id)
                    con.execute(
                        "INSERT INTO program_occurrence_items(occurrence_id,media_id,media_id_snapshot,position,item_kind,title,artist,path,category,duration) VALUES(?,?,?,0,'media',?,?,?,?,?)",
                        (occurrence_id, int(event["actual_media_id"]), int(event["actual_media_id"]), event["media_title"], event["artist"], event["path"], event["media_category"], event["duration"]),
                    )
                    con.execute("UPDATE timeline_events SET last_fired=? WHERE id=?", (scheduled_for, int(event["id"])))
        return created

    def _record_scheduler_decision(
        self, origin_type: str, source_id: int, scheduled_for: str,
        decision: str, reason: str, con: sqlite3.Connection | None = None,
    ) -> None:
        execute = con.execute if con is not None else self.db.execute
        execute(
            "INSERT OR IGNORE INTO scheduler_decisions(origin_type,source_id,scheduled_for,decision,reason) "
            "VALUES(?,?,?,?,?)",
            (origin_type, source_id, scheduled_for, decision, reason),
        )

    def _slot_candidates(self, start: datetime, end: datetime) -> dict[datetime, dict[str, list[int]]]:
        blocks = self.db.all(
            "SELECT id,time_of_day,days,transition_policy FROM program_blocks "
            "WHERE enabled=1 AND EXISTS (SELECT 1 FROM program_block_items i WHERE i.block_id=program_blocks.id) "
            "ORDER BY id"
        )
        events = self.db.all(
            "SELECT id,time_of_day,days FROM timeline_events WHERE enabled=1 AND media_id IS NOT NULL ORDER BY id"
        )
        slots: dict[datetime, dict[str, list[int]]] = {}
        day = start.date()
        while day <= end.date():
            weekday = day.weekday()
            for row in blocks:
                if weekday not in {int(value) for value in str(row["days"]).split(",") if value.strip().isdigit()}:
                    continue
                scheduled = datetime.combine(day, datetime.strptime(row["time_of_day"], "%H:%M").time())
                if start < scheduled <= end:
                    slots.setdefault(scheduled, {"blocks": [], "events": []})["blocks"].append(int(row["id"]))
            for row in events:
                if weekday not in {int(value) for value in str(row["days"]).split(",") if value.strip().isdigit()}:
                    continue
                scheduled = datetime.combine(day, datetime.strptime(row["time_of_day"], "%H:%M").time())
                if start < scheduled <= end:
                    slots.setdefault(scheduled, {"blocks": [], "events": []})["events"].append(int(row["id"]))
            day += timedelta(days=1)
        return slots

    def _record_unplayed_occurrence(
        self, origin_type: str, source_id: int, scheduled: datetime,
        decision: str, reason: str, now: datetime, operator: str = "",
    ) -> int | None:
        """Persist an ignored/missed occurrence without selecting or advancing media rotations."""
        stamp = scheduled.strftime("%Y-%m-%d %H:%M")
        ended_at = now.strftime("%Y-%m-%d %H:%M:%S")
        delay_ms = max(0, int((now - scheduled).total_seconds() * 1000))
        status = "missed" if decision == "missed" else "ignored"
        with self.db.connect() as con:
            if origin_type == "block":
                source = con.execute("SELECT * FROM program_blocks WHERE id=?", (source_id,)).fetchone()
                if not source:
                    return None
                item_rows = con.execute(
                    "SELECT i.*,f.name AS folder_name,m.title AS media_title,m.artist,m.path,m.category,m.id AS actual_media_id,m.duration "
                    "FROM program_block_items i LEFT JOIN media_folders f ON f.id=i.folder_id "
                    "LEFT JOIN media m ON m.id=i.media_id WHERE i.block_id=? ORDER BY i.position,i.id",
                    (source_id,),
                ).fetchall()
                if not item_rows:
                    return None
                con.execute(
                    "INSERT OR IGNORE INTO program_occurrences(origin_type,block_id,scheduled_for,status,operator,transition_policy,fixed_fade_ms,dispatch_delay_ms,dispatch_reason,block_name_snapshot) "
                    "VALUES('block',?,?,'planned',?,?,?,?,?,?)",
                    (source_id, stamp, operator, source["transition_policy"], source["fixed_fade_ms"], delay_ms, reason, source["name"]),
                )
                occurrence = con.execute(
                    "SELECT id,status FROM program_occurrences WHERE block_id=? AND scheduled_for=?",
                    (source_id, stamp),
                ).fetchone()
            else:
                source = con.execute(
                    "SELECT t.*,m.id AS actual_media_id,m.title AS media_title,m.artist,m.path,m.category AS media_category,m.duration "
                    "FROM timeline_events t LEFT JOIN media m ON m.id=t.media_id WHERE t.id=?",
                    (source_id,),
                ).fetchone()
                if not source:
                    return None
                item_rows = [source]
                con.execute(
                    "INSERT OR IGNORE INTO program_occurrences(origin_type,timeline_event_id,scheduled_for,status,operator,dispatch_delay_ms,dispatch_reason) "
                    "VALUES('timeline',?,?,'planned',?,?,?)",
                    (source_id, stamp, operator, delay_ms, reason),
                )
                occurrence = con.execute(
                    "SELECT id,status FROM program_occurrences WHERE timeline_event_id=? AND scheduled_for=?",
                    (source_id, stamp),
                ).fetchone()
            if not occurrence:
                return None
            occurrence_id = int(occurrence["id"])
            existing_items = con.execute(
                "SELECT 1 FROM program_occurrence_items WHERE occurrence_id=? LIMIT 1", (occurrence_id,),
            ).fetchone()
            if occurrence["status"] == "planned" and not existing_items:
                for item in item_rows:
                    if origin_type == "block":
                        is_folder = item["folder_id"] is not None and str(item["selection_mode"] or "") in {"sequential", "random", "jingle_rotation"}
                        title = str(item["folder_name"] or item["label"] or "Pasta") if is_folder else str(item["media_title"] or item["label"] or "Material indisponível")
                        media_id = None if is_folder else item["actual_media_id"]
                        artist = "" if is_folder else str(item["artist"] or "")
                        path = "" if is_folder else str(item["path"] or "")
                        category = str(item["category"] or source["block_type"])
                        item_kind = "folder_selection" if is_folder else str(item["item_kind"] or "media")
                        values = (
                            occurrence_id, item["id"], media_id, media_id, item["position"], item_kind, title,
                            artist, path, category, item["duration"] or 0, item["folder_id"],
                            item["selection_mode"] or "", "ignored", ended_at, reason,
                        )
                        con.execute(
                            "INSERT INTO program_occurrence_items(occurrence_id,source_item_id,media_id,media_id_snapshot,position,item_kind,title,artist,path,category,duration,selection_folder_id,selection_mode,status,ended_at,error_message) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            values,
                        )
                    else:
                        values = (
                            occurrence_id, source["actual_media_id"], source["actual_media_id"], 0, "media",
                            str(source["media_title"] or source["title"]), str(source["artist"] or ""),
                            str(source["path"] or ""), str(source["media_category"] or source["category"]),
                            source["duration"] or 0, "ignored", ended_at, reason,
                        )
                        con.execute(
                            "INSERT INTO program_occurrence_items(occurrence_id,media_id,media_id_snapshot,position,item_kind,title,artist,path,category,duration,status,ended_at,error_message) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            values,
                        )
            con.execute(
                "UPDATE program_occurrence_items SET status='ignored',ended_at=?,error_message=? "
                "WHERE occurrence_id=? AND status='planned'",
                (ended_at, reason, occurrence_id),
            )
            con.execute(
                "UPDATE program_occurrences SET status=?,dispatch_delay_ms=?,dispatch_reason=?,operator=CASE WHEN operator='' THEN ? ELSE operator END "
                "WHERE id=? AND status IN ('planned','delayed','interrupted')",
                (status, delay_ms, reason, operator, occurrence_id),
            )
            if origin_type == "timeline":
                con.execute("UPDATE timeline_events SET last_fired=? WHERE id=?", (stamp, source_id))
            con.execute(
                "INSERT OR IGNORE INTO scheduler_decisions(origin_type,source_id,scheduled_for,decision,reason) VALUES(?,?,?,?,?)",
                (origin_type, source_id, stamp, decision, reason),
            )
            return occurrence_id

    def _expire_unstarted_occurrences(
        self, now: datetime, grace_seconds: int, preserve_item_id: int | None, operator: str = "",
    ) -> dict[str, list[int]]:
        cutoff = (now - timedelta(seconds=grace_seconds)).strftime("%Y-%m-%d %H:%M:%S.%f")
        fixed_cutoff = (now - timedelta(milliseconds=FIXED_LATE_GRACE_MS)).strftime("%Y-%m-%d %H:%M:%S.%f")
        rows = self.db.all(
            "SELECT o.id,o.origin_type,o.block_id,o.timeline_event_id,o.scheduled_for,o.transition_policy "
            "FROM program_occurrences o WHERE o.status IN ('planned','delayed','interrupted','running') "
            "AND ((o.transition_policy='fixed' AND o.scheduled_for||':00.000000'<?) "
            "OR (o.transition_policy<>'fixed' AND o.scheduled_for||':00.000000'<?)) "
            "AND EXISTS(SELECT 1 FROM program_occurrence_items i "
            "WHERE i.occurrence_id=o.id AND i.status='planned') ORDER BY o.scheduled_for,o.id",
            (fixed_cutoff, cutoff),
        )
        expired = {"missed_ids": [], "ignored_ids": []}
        ended_at = now.strftime("%Y-%m-%d %H:%M:%S")
        for row in rows:
            occurrence_id = int(row["id"])
            grace_ms = FIXED_LATE_GRACE_MS if row["transition_policy"] == "fixed" else grace_seconds * 1000
            reason = f"Pendente expirado após janela de atraso de {grace_ms} ms; não foi reproduzido automaticamente."
            with self.db.connect() as con:
                con.execute(
                    "UPDATE program_occurrence_items SET status='ignored',ended_at=?,error_message=? "
                    "WHERE occurrence_id=? AND status='planned' AND (? IS NULL OR id<>?)",
                    (ended_at, reason, occurrence_id, preserve_item_id, preserve_item_id),
                )
                active = con.execute(
                    "SELECT 1 FROM program_occurrence_items WHERE occurrence_id=? AND status IN ('loading','playing','paused') LIMIT 1",
                    (occurrence_id,),
                ).fetchone()
                if not active:
                    con.execute(
                        "UPDATE program_occurrences SET status='missed',dispatch_reason=?,operator=CASE WHEN operator='' THEN ? ELSE operator END WHERE id=?",
                        (reason, operator, occurrence_id),
                    )
                else:
                    con.execute(
                        "UPDATE program_occurrences SET operator=CASE WHEN operator='' THEN ? ELSE operator END WHERE id=?",
                        (operator, occurrence_id),
                    )
                con.execute(
                    "INSERT OR IGNORE INTO scheduler_decisions(origin_type,source_id,scheduled_for,decision,reason) VALUES(?,?,?,?,?)",
                    (str(row["origin_type"]), int(row["block_id"] or row["timeline_event_id"] or 0),
                     str(row["scheduled_for"]), "missed" if not active else "expired_pending", reason),
                )
            expired["missed_ids" if not active else "ignored_ids"].append(occurrence_id)
        return expired

    def dispatch_window(
        self, now: datetime, preserve_item_id: int | None = None,
        force_recovery: bool = False, operator: str = "",
    ) -> dict[str, list[int] | bool]:
        """Reconcile crossed clock slots without replaying stale insertions."""
        cursor_key = "scheduler_last_checked_at"
        cursor_text = self.db.setting(cursor_key, "")
        invalid_cursor = False
        if cursor_text:
            try:
                cursor = datetime.fromisoformat(cursor_text)
            except ValueError:
                invalid_cursor = True
                cursor = now.replace(second=0, microsecond=0) - timedelta(microseconds=1)
                reason = "Marco persistido do agendador inválido; recuperação limitada ao horizonte configurado."
                self._record_scheduler_decision(
                    "scheduler", 0, cursor_text[:16], "invalid_cursor", reason,
                )
        else:
            # The first run considers only the current minute. This lets the
            # scheduler audit a slot already missed at startup without replaying
            # earlier minutes from before the application was running.
            cursor = now.replace(second=0, microsecond=0) - timedelta(microseconds=1)
        if now < cursor:
            reason = f"Relógio do sistema retrocedeu; despacho pausado até alcançar o último marco ({cursor.isoformat()})."
            self._record_scheduler_decision("scheduler", 0, cursor.strftime("%Y-%m-%d %H:%M"), "clock_rollback", reason)
            return {"dispatch_ids": [], "missed_ids": [], "ignored_ids": [], "clock_rollback": True}

        recovery_horizon = now - timedelta(days=SCHEDULER_RECOVERY_HORIZON_DAYS)
        if cursor < recovery_horizon:
            reason = (
                f"Lacuna de agendamento superior a {SCHEDULER_RECOVERY_HORIZON_DAYS} dias; "
                "eventos mais antigos não foram reconstruídos nem reproduzidos."
            )
            self._record_scheduler_decision(
                "scheduler", 0, cursor.strftime("%Y-%m-%d %H:%M"), "recovery_horizon", reason,
            )
            cursor = recovery_horizon

        gap_seconds = max(0.0, (now - cursor).total_seconds())
        expired = {"missed_ids": [], "ignored_ids": []}
        if force_recovery or gap_seconds > SCHEDULER_LATE_GRACE_SECONDS:
            expired = self._expire_unstarted_occurrences(
                now, SCHEDULER_LATE_GRACE_SECONDS, preserve_item_id, operator,
            )

        dispatch_ids: list[int] = []
        missed_ids: list[int] = list(expired["missed_ids"])
        ignored_ids: list[int] = list(expired["ignored_ids"])
        scan_start = min(cursor, now - timedelta(seconds=SCHEDULER_LATE_GRACE_SECONDS))
        slots = self._slot_candidates(scan_start, now)
        for scheduled in sorted(slots):
            candidate = slots[scheduled]
            block_ids = candidate["blocks"]
            event_ids = candidate["events"]
            stamp = scheduled.strftime("%Y-%m-%d %H:%M")
            existing_rows = self.db.all(
                "SELECT block_id,timeline_event_id,status FROM program_occurrences WHERE scheduled_for=?",
                (stamp,),
            )
            existing_sources = {
                ("block", int(row["block_id"])) for row in existing_rows if row["block_id"] is not None
            } | {
                ("timeline", int(row["timeline_event_id"])) for row in existing_rows if row["timeline_event_id"] is not None
            }
            existing_active = any(row["status"] in {"running", "loading", "playing", "paused"} for row in existing_rows)
            fixed_blocks = [
                int(row["id"]) for row in self.db.all(
                    f"SELECT id FROM program_blocks WHERE id IN ({','.join('?' for _ in block_ids)}) "
                    "AND block_type='Comercial' AND transition_policy='fixed' ORDER BY id",
                    tuple(block_ids),
                )
            ] if block_ids else []
            age_ms = max(0, int((now - scheduled).total_seconds() * 1000))
            fixed_winner = min(fixed_blocks) if fixed_blocks else None
            is_missed = age_ms > SCHEDULER_LATE_GRACE_SECONDS * 1000
            if fixed_winner is not None and age_ms > FIXED_LATE_GRACE_MS:
                is_missed = True

            if fixed_winner is not None:
                winners = {fixed_winner}
                conflicts = [("block", value) for value in block_ids if value != fixed_winner]
                conflicts.extend(("timeline", value) for value in event_ids)
                conflict_reason = f"Ignorado por conflito no mesmo horário; o bloco Fixo {fixed_winner} tem prioridade determinística."
            else:
                winners = set(block_ids)
                conflicts = []
                conflict_reason = ""

            for origin_type, source_id in conflicts:
                occurrence_id = self._record_unplayed_occurrence(
                    origin_type, source_id, scheduled, "ignored", conflict_reason, now, operator,
                )
                if occurrence_id is not None:
                    ignored_ids.append(occurrence_id)

            if fixed_winner is not None and existing_active and ("block", fixed_winner) not in existing_sources:
                reason = "Bloco Fixo ignorado: outra ocorrência do mesmo horário já iniciou; não se aplica corte retroativo."
                occurrence_id = self._record_unplayed_occurrence("block", fixed_winner, scheduled, "ignored", reason, now, operator)
                if occurrence_id is not None:
                    ignored_ids.append(occurrence_id)
                continue
            if fixed_winner is not None and ("block", fixed_winner) in existing_sources:
                continue

            if is_missed:
                reason = (
                    f"Evento perdido: atraso de {age_ms} ms excede a janela aceita; "
                    "não será executado automaticamente."
                )
                for source_id in sorted(winners):
                    occurrence_id = self._record_unplayed_occurrence("block", source_id, scheduled, "missed", reason, now, operator)
                    if occurrence_id is not None:
                        missed_ids.append(occurrence_id)
                if not fixed_winner:
                    for source_id in sorted(event_ids):
                        occurrence_id = self._record_unplayed_occurrence("timeline", source_id, scheduled, "missed", reason, now, operator)
                        if occurrence_id is not None:
                            missed_ids.append(occurrence_id)
                continue

            allowed_blocks = winners if fixed_winner is not None else {
                source_id for source_id in block_ids if ("block", source_id) not in existing_sources
            }
            allowed_events = set() if fixed_winner is not None else {
                source_id for source_id in event_ids if ("timeline", source_id) not in existing_sources
            }
            if fixed_winner is None and not allowed_blocks and not allowed_events:
                continue
            self.due(scheduled, allowed_blocks, allowed_events, operator)
            rows = self.db.all(
                "SELECT id,origin_type,block_id,timeline_event_id,status FROM program_occurrences WHERE scheduled_for=? ORDER BY id",
                (stamp,),
            )
            source_keys = {
                ("block", value) for value in allowed_blocks
            } | {
                ("timeline", value) for value in allowed_events
            }
            reason = (
                f"Despachado com atraso de {age_ms} ms dentro da janela de {FIXED_LATE_GRACE_MS} ms do Fixo."
                if fixed_winner is not None and age_ms > 0 else
                f"Despachado com atraso de {age_ms} ms dentro da janela de {SCHEDULER_LATE_GRACE_SECONDS}s."
                if age_ms > 0 else "Despachado no horário programado."
            )
            for row in rows:
                source_key = (str(row["origin_type"]), int(row["block_id"] or row["timeline_event_id"] or 0))
                if source_key not in source_keys or row["status"] not in {"planned", "delayed", "running", "interrupted"}:
                    continue
                occurrence_id = int(row["id"])
                self.db.execute(
                    "UPDATE program_occurrences SET status=CASE WHEN ? > 0 AND status='planned' THEN 'delayed' ELSE status END,dispatch_delay_ms=?,dispatch_reason=? WHERE id=?",
                    (age_ms, age_ms, reason, occurrence_id),
                )
                decision = "delayed" if age_ms else "scheduled"
                source_id = source_key[1]
                self._record_scheduler_decision(source_key[0], source_id, stamp, decision, reason)
                dispatch_ids.append(occurrence_id)

        if force_recovery:
            pending = self.db.all(
                "SELECT o.id,o.origin_type,o.block_id,o.timeline_event_id,o.scheduled_for,o.status,o.transition_policy "
                "FROM program_occurrences o WHERE o.status IN ('planned','delayed','running','interrupted') "
                "AND EXISTS(SELECT 1 FROM program_occurrence_items i WHERE i.occurrence_id=o.id AND i.status='planned') "
                "ORDER BY o.scheduled_for,CASE WHEN o.origin_type='block' THEN 0 ELSE 1 END,COALESCE(o.block_id,o.timeline_event_id),o.id"
            )
            for row in pending:
                occurrence_id = int(row["id"])
                if occurrence_id in dispatch_ids:
                    continue
                scheduled = datetime.strptime(row["scheduled_for"], "%Y-%m-%d %H:%M")
                delay_ms = max(0, int((now - scheduled).total_seconds() * 1000))
                grace_ms = FIXED_LATE_GRACE_MS if row["transition_policy"] == "fixed" else SCHEDULER_LATE_GRACE_SECONDS * 1000
                if delay_ms > grace_ms:
                    continue
                reason = f"Ocorrência pendente retomada após reinicialização com atraso de {delay_ms} ms."
                if delay_ms > 0 and row["status"] == "planned":
                    self.db.execute(
                    "UPDATE program_occurrences SET status='delayed',dispatch_delay_ms=?,dispatch_reason=?,operator=CASE WHEN operator='' THEN ? ELSE operator END WHERE id=?",
                    (delay_ms, reason, operator, occurrence_id),
                    )
                else:
                    self.db.execute(
                        "UPDATE program_occurrences SET operator=CASE WHEN operator='' THEN ? ELSE operator END WHERE id=?",
                        (operator, occurrence_id),
                    )
                self._record_scheduler_decision(
                    str(row["origin_type"]), int(row["block_id"] or row["timeline_event_id"] or 0),
                    str(row["scheduled_for"]), "resumed_pending", reason,
                )
                dispatch_ids.append(occurrence_id)

        # Save the high-water mark only after decisions are durable. Unique occurrence
        # indexes and scheduler_decisions make a retry safe after an interrupted write.
        current_cursor = now.replace(microsecond=0).isoformat()
        with self.db.connect() as con:
            if invalid_cursor:
                con.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (cursor_key, current_cursor),
                )
            else:
                con.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=CASE WHEN excluded.value>settings.value THEN excluded.value ELSE settings.value END",
                    (cursor_key, current_cursor),
                )
        return {
            "dispatch_ids": dispatch_ids,
            "missed_ids": missed_ids,
            "ignored_ids": ignored_ids,
            "clock_rollback": False,
        }

    def next_occurrence_item(self):
        return self.db.one(
            "SELECT i.*,o.scheduled_for,o.id AS occurrence_id FROM program_occurrence_items i "
            "JOIN program_occurrences o ON o.id=i.occurrence_id WHERE i.status='planned' "
            "AND o.status IN ('planned','delayed','running','interrupted') "
            "ORDER BY o.scheduled_for,CASE WHEN o.origin_type='block' THEN 0 ELSE 1 END,"
            "COALESCE(o.block_id,o.timeline_event_id),o.id,i.position,i.id LIMIT 1"
        )

    def set_occurrence_item_status(self, item_id: int, status: str, error_message: str = "") -> None:
        allowed = {"planned", "loading", "playing", "paused", "completed", "skipped", "discarded", "ignored", "stopped", "failed", "ejected", "interrupted"}
        if status not in allowed:
            raise ValueError(f"Estado de ocorrência inválido: {status}")
        with self.db.connect() as con:
            con.execute(
                "UPDATE program_occurrence_items SET status=?,error_message=?,started_at=CASE WHEN ?='playing' AND started_at='' THEN CURRENT_TIMESTAMP ELSE started_at END,ended_at=CASE WHEN ? IN ('completed','skipped','discarded','ignored','stopped','failed','interrupted') THEN CURRENT_TIMESTAMP ELSE ended_at END WHERE id=?",
                (status, error_message, status, status, item_id),
            )
            occurrence_id = con.execute("SELECT occurrence_id FROM program_occurrence_items WHERE id=?", (item_id,)).fetchone()
            if occurrence_id:
                if status == "interrupted":
                    con.execute("UPDATE program_occurrences SET status='interrupted' WHERE id=?", (occurrence_id["occurrence_id"],))
                    return
                pending = con.execute("SELECT 1 FROM program_occurrence_items WHERE occurrence_id=? AND status NOT IN ('completed','skipped','discarded','ignored','stopped','failed','interrupted') LIMIT 1", (occurrence_id["occurrence_id"],)).fetchone()
                if pending:
                    con.execute("UPDATE program_occurrences SET status='running' WHERE id=?", (occurrence_id["occurrence_id"],))
                else:
                    terminal = con.execute("SELECT 1 FROM program_occurrence_items WHERE occurrence_id=? AND status NOT IN ('completed') LIMIT 1", (occurrence_id["occurrence_id"],)).fetchone()
                    con.execute("UPDATE program_occurrences SET status=? WHERE id=?", ("completed" if not terminal else "interrupted", occurrence_id["occurrence_id"]))


class PlaybackHistoryService:
    """Read-only operational log of player events observed by HitMo."""

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _validate_date(value: str, label: str) -> str:
        value = str(value or "").strip()
        if value:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError(f"{label} deve usar AAAA-MM-DD.") from exc
        return value

    def filter_options(self) -> dict[str, list[str]]:
        categories = [str(row["category"]) for row in self.db.all(
            "SELECT category FROM playback_history WHERE category<>'' UNION "
            "SELECT category FROM program_occurrence_items WHERE category<>'' UNION "
            "SELECT category FROM cartouche_history WHERE category<>'' ORDER BY category COLLATE NOCASE"
        )]
        statuses = [str(row["status"]) for row in self.db.all(
            "SELECT status FROM playback_history WHERE status<>'' UNION "
            "SELECT status FROM program_occurrence_items WHERE status<>'' UNION "
            "SELECT status FROM program_occurrences WHERE status<>'' UNION "
            "SELECT status FROM cartouche_history WHERE status<>'' ORDER BY status COLLATE NOCASE"
        )]
        return {"categories": categories, "statuses": sorted(set(statuses) | {"selected"}, key=str.casefold)}

    def query(
        self, *, start_date: str = "", end_date: str = "", material: str = "",
        category: str = "", status: str = "", limit: int = 250, offset: int = 0,
    ) -> dict[str, object]:
        start_date = self._validate_date(start_date, "A data inicial")
        end_date = self._validate_date(end_date, "A data final")
        if start_date and end_date and start_date > end_date:
            raise ValueError("A data inicial deve ser anterior ou igual à data final.")
        try:
            limit = max(1, min(int(limit), 1000))
            offset = max(0, int(offset))
        except (TypeError, ValueError) as exc:
            raise ValueError("limit e offset devem ser inteiros.") from exc

        where: list[str] = []
        params: list[object] = []
        if start_date:
            where.append("event_time>=?")
            params.append(start_date)
        if end_date:
            end_exclusive = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            where.append("event_time<?")
            params.append(end_exclusive)
        if material.strip():
            term = material.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{term}%"
            where.append(
                "(title LIKE ? ESCAPE '\\' OR artist LIKE ? ESCAPE '\\' OR path LIKE ? ESCAPE '\\' "
                "OR CAST(material_id AS TEXT)=?)"
            )
            params.extend((pattern, pattern, pattern, material.strip()))
        if category:
            where.append("category=?")
            params.append(category)
        if status:
            where.append("(status=? OR occurrence_status=? OR selection_state=?)")
            params.extend((status, status, status))
        filter_sql = " WHERE " + " AND ".join(where) if where else ""
        audit = (
            "WITH audit AS ("
            "SELECT 'player_event' AS record_type,h.id AS id,"
            "CASE WHEN h.started_at<>'' THEN h.started_at ELSE h.ended_at END AS event_time,"
            "COALESCE(h.material_id_snapshot,h.media_id) AS material_id,h.title,h.artist,h.path,h.category,h.operator,"
            "h.started_at,h.ended_at,h.played_seconds,h.status,'' AS selection_state,h.planned_for,'' AS selected_at,h.source_type,h.source_ref,h.error_message,"
            "COALESCE(h.occurrence_id,o.id) AS occurrence_id,COALESCE(h.block_id,o.block_id) AS block_id,"
            "COALESCE(NULLIF(h.block_name_snapshot,''),NULLIF(o.block_name_snapshot,''),'') AS block_name_snapshot,"
            "COALESCE(o.status,'') AS occurrence_status "
            "FROM playback_history h "
            "LEFT JOIN program_occurrence_items oi ON oi.id=h.source_ref AND h.occurrence_id IS NULL "
            "AND h.source_type IN ('scheduled_block','scheduled_event','timeline') "
            "LEFT JOIN program_occurrences o ON o.id=COALESCE(h.occurrence_id,oi.occurrence_id) "
            "UNION ALL "
            "SELECT 'occurrence_item' AS record_type,i.id AS id,"
            "CASE WHEN i.ended_at<>'' THEN i.ended_at WHEN i.selected_at<>'' THEN i.selected_at ELSE o.scheduled_for END AS event_time,"
            "COALESCE(i.media_id_snapshot,i.media_id) AS material_id,i.title,i.artist,i.path,i.category,o.operator,"
            "i.started_at,i.ended_at,0.0 AS played_seconds,i.status,"
            "CASE WHEN i.status='planned' AND COALESCE(i.media_id_snapshot,i.media_id) IS NOT NULL THEN 'selected' ELSE '' END AS selection_state,"
            "o.scheduled_for,i.selected_at,'program_occurrence_item',i.id,i.error_message,"
            "o.id AS occurrence_id,o.block_id,o.block_name_snapshot,o.status AS occurrence_status "
            "FROM program_occurrence_items i JOIN program_occurrences o ON o.id=i.occurrence_id "
            "WHERE NOT EXISTS (SELECT 1 FROM playback_history h WHERE h.source_ref=i.id "
            "AND (h.occurrence_id=o.id OR (h.occurrence_id IS NULL AND h.source_type IN ('scheduled_block','scheduled_event','timeline')))) "
            "UNION ALL "
            "SELECT 'cartouche_event' AS record_type,c.id AS id,c.triggered_at AS event_time,"
            "c.media_id_snapshot AS material_id,c.title,'' AS artist,c.path,c.category,c.operator,"
            "c.started_at,c.ended_at,0.0 AS played_seconds,c.status,'' AS selection_state,'' AS planned_for,'' AS selected_at,"
            "'cartouche' AS source_type,c.slot AS source_ref,c.error_message,NULL AS occurrence_id,NULL AS block_id,"
            "'' AS block_name_snapshot,'' AS occurrence_status FROM cartouche_history c"
            ")"
        )
        total = int(self.db.one(audit + " SELECT COUNT(*) AS n FROM audit" + filter_sql, tuple(params))["n"])
        rows = self.db.all(
            audit + " SELECT * FROM audit" + filter_sql + " ORDER BY event_time DESC,record_type,id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return {"history": rows, "total": total, "limit": limit, "offset": offset}


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

