from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Iterable


SHORTCUT_FORMAT = "hitmo.catalog-shortcut.v1"


def _owns_shortcut(value, media_id: int) -> bool:
    return isinstance(value, dict) and value.get("format") == SHORTCUT_FORMAT and value.get("media_id") == media_id


def reconcile_shortcuts(materials: Iterable[dict], directory: Path | str) -> dict[str, int]:
    """Create/update HitMo reference cards without copying or moving media files."""
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    result = {"created": 0, "updated": 0, "preserved": 0, "errors": 0}
    expected_names: set[str] = set()
    for material in materials:
        media_id = int(material["id"])
        name = f"material-{media_id}.hitmo.json"
        expected_names.add(name)
        target = folder / name
        payload = {
            "format": SHORTCUT_FORMAT,
            "media_id": media_id,
            "title": str(material.get("title", "")),
            "artist": str(material.get("artist", "")),
            "category": str(material.get("category", "")),
            "material_kind": str(material.get("material_kind", "audio")),
            "path": str(material.get("path", "")),
            "icon_path": str(material.get("icon_path", "")),
            "active": bool(material.get("active", 1)),
        }
        existed = target.exists()
        if target.is_symlink():
            result["preserved"] += 1
            continue
        if existed:
            try:
                current = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                result["preserved"] += 1
                continue
            if not _owns_shortcut(current, media_id):
                result["preserved"] += 1
                continue
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=folder, suffix=".tmp", delete=False) as handle:
                temp_name = handle.name
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            if target.exists():
                # Recheck identity immediately before replacement; unrelated files are never overwritten.
                if target.is_symlink():
                    result["preserved"] += 1
                    continue
                current = json.loads(target.read_text(encoding="utf-8"))
                if not _owns_shortcut(current, media_id):
                    result["preserved"] += 1
                    continue
            os.replace(temp_name, target)
            temp_name = None
            result["updated" if existed else "created"] += 1
        except (OSError, UnicodeError, json.JSONDecodeError):
            result["errors"] += 1
        finally:
            if temp_name and os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
    # Do not delete stale cards here: the remote catalog may be paginated or filtered.
    result["stale"] = sum(1 for item in folder.glob("material-*.hitmo.json") if item.name not in expected_names)
    return result
