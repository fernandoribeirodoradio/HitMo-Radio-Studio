from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .database import Database


@dataclass(frozen=True)
class PlaylistSharedRoot:
    position: int
    server: str
    title: str
    path: str


@dataclass(frozen=True)
class PlaylistFolderEntry:
    position: int
    playlist_id: int
    title: str
    type_code: str
    target: str
    icon_location: str
    shortcut_arguments: str
    shortcut_path_name: str
    icon_index: int | None
    output: str
    total_files: int
    dbf_id: str


@dataclass(frozen=True)
class PlaylistFolderCatalog:
    source_path: str
    version: str
    declared_folder_count: int
    declared_shared_count: int
    shared_roots: tuple[PlaylistSharedRoot, ...]
    folders: tuple[PlaylistFolderEntry, ...]


@dataclass(frozen=True)
class PlaylistImportResult:
    snapshot_id: int
    folder_count: int
    existing_target_count: int
    missing_target_count: int


def _child_text(parent: ET.Element | None, name: str, default: str = "") -> str:
    if parent is None:
        return default
    child = parent.find(name)
    return child.text.strip() if child is not None and child.text else default


def _integer(value: str, field: str, *, optional: bool = False) -> int | None:
    if not value and optional:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Campo numérico inválido em Folders.xml: {field}={value!r}") from exc


def parse_playlist_folders(path: Path | str) -> PlaylistFolderCatalog:
    """Read Playlist Digital's folder catalog without changing the source file."""
    source = Path(path).expanduser().resolve()
    try:
        root = ET.parse(source).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"Não foi possível ler o catálogo XML: {exc}") from exc
    if root.tag != "Folders":
        raise ValueError("XML incompatível: elemento raiz esperado é <Folders>.")

    shared = root.find("Shared")
    shared_entries: list[PlaylistSharedRoot] = []
    if shared is not None:
        for child in shared:
            match = re.fullmatch(r"Folder(\d+)", child.tag)
            if not match:
                continue
            shared_entries.append(PlaylistSharedRoot(
                position=int(match.group(1)),
                server=_child_text(shared, "Server"),
                title=_child_text(child, "Name"),
                path=_child_text(child, "Path"),
            ))

    entries: list[PlaylistFolderEntry] = []
    for child in root:
        match = re.fullmatch(r"Folder(\d+)", child.tag)
        if not match:
            continue
        playlist_id = _integer(_child_text(child, "ID"), f"{child.tag}.ID")
        icon_index = _integer(_child_text(child, "IconIndex"), f"{child.tag}.IconIndex", optional=True)
        total_files = _integer(_child_text(child, "TotalFiles", "0"), f"{child.tag}.TotalFiles")
        entries.append(PlaylistFolderEntry(
            position=int(match.group(1)),
            playlist_id=int(playlist_id),
            title=_child_text(child, "Title"),
            type_code=_child_text(child, "Type"),
            target=_child_text(child, "Target"),
            icon_location=_child_text(child, "IconLocation"),
            shortcut_arguments=_child_text(child, "ShortcutArguments"),
            shortcut_path_name=_child_text(child, "ShortcutPathName"),
            icon_index=icon_index,
            output=_child_text(child, "Output"),
            total_files=int(total_files),
            dbf_id=_child_text(child, "DBFId"),
        ))

    if not entries:
        raise ValueError("O catálogo não contém registros <FolderN> para importar.")
    ids = [entry.playlist_id for entry in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("O catálogo contém IDs de pasta duplicados.")

    declared_folder_count = _integer(_child_text(root, "Folders"), "Folders", optional=True)
    declared_shared_count = _integer(_child_text(shared, "Folders"), "Shared.Folders", optional=True)
    return PlaylistFolderCatalog(
        source_path=str(source),
        version=_child_text(root, "Version"),
        declared_folder_count=len(entries) if declared_folder_count is None else declared_folder_count,
        declared_shared_count=len(shared_entries) if declared_shared_count is None else declared_shared_count,
        shared_roots=tuple(sorted(shared_entries, key=lambda item: item.position)),
        folders=tuple(sorted(entries, key=lambda item: item.position)),
    )


class PlaylistConfigImporter:
    """Store immutable snapshots of Folders.xml in HitMo's own SQLite database.

    The import never scans or opens media, writes to the Playlist installation,
    or updates HitMo's active library/programming tables.
    """

    def __init__(self, db: Database, path_exists: Callable[[str], bool] | None = None):
        self.db = db
        self.path_exists = path_exists or os.path.isdir

    def import_file(self, path: Path | str) -> PlaylistImportResult:
        catalog = parse_playlist_folders(path)
        existing = sum(bool(folder.target and self.path_exists(folder.target)) for folder in catalog.folders)
        missing = len(catalog.folders) - existing
        with self.db.connect() as con:
            cursor = con.execute(
                "INSERT INTO playlist_catalogs(source_path,version,declared_folder_count,declared_shared_count) VALUES(?,?,?,?)",
                (catalog.source_path, catalog.version, catalog.declared_folder_count, catalog.declared_shared_count),
            )
            snapshot_id = int(cursor.lastrowid)
            con.executemany(
                "INSERT INTO playlist_shared_roots(catalog_id,position,server,title,path) VALUES(?,?,?,?,?)",
                [(snapshot_id, item.position, item.server, item.title, item.path) for item in catalog.shared_roots],
            )
            con.executemany(
                """INSERT INTO playlist_folder_entries(
                    catalog_id,position,playlist_id,title,type_code,target,icon_location,
                    shortcut_arguments,shortcut_path_name,icon_index,output,total_files,
                    dbf_id,target_exists
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        snapshot_id, item.position, item.playlist_id, item.title, item.type_code, item.target,
                        item.icon_location, item.shortcut_arguments, item.shortcut_path_name,
                        item.icon_index, item.output, item.total_files, item.dbf_id,
                        int(bool(item.target and self.path_exists(item.target))),
                    )
                    for item in catalog.folders
                ],
            )
        return PlaylistImportResult(snapshot_id, len(catalog.folders), existing, missing)
