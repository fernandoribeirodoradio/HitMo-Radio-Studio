import json
import tempfile
import unittest
from pathlib import Path

from hitmo.shortcuts import SHORTCUT_FORMAT, reconcile_shortcuts


class ShortcutReconciliationTests(unittest.TestCase):
    def test_catalog_shortcuts_are_id_based_and_recreated_without_copying_media(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "original.wav"
            audio.write_bytes(b"original")
            shortcuts = root / "Atalhos"
            record = {"id": 71, "title": "Programa", "artist": "HitMo", "category": "Música", "material_kind": "audio", "path": str(audio), "active": 1}
            self.assertEqual(reconcile_shortcuts([record], shortcuts)["created"], 1)
            shortcut = shortcuts / "material-71.hitmo.json"
            first = json.loads(shortcut.read_text(encoding="utf-8"))
            self.assertEqual(first["format"], SHORTCUT_FORMAT)
            self.assertEqual(first["media_id"], 71)
            self.assertTrue(audio.is_file())
            record["title"] = "Programa atualizado"
            self.assertEqual(reconcile_shortcuts([record], shortcuts)["updated"], 1)
            self.assertEqual(json.loads(shortcut.read_text(encoding="utf-8"))["title"], "Programa atualizado")
            shortcut.unlink()
            self.assertEqual(reconcile_shortcuts([record], shortcuts)["created"], 1)
            self.assertTrue(audio.is_file())

    def test_foreign_or_wrong_identity_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            shortcuts = Path(temp)
            target = shortcuts / "material-4.hitmo.json"
            foreign = {"format": "other", "media_id": 4, "keep": True}
            target.write_text(json.dumps(foreign), encoding="utf-8")
            result = reconcile_shortcuts([{"id": 4, "title": "Não sobrescrever"}], shortcuts)
            self.assertEqual(result["preserved"], 1)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), foreign)


if __name__ == "__main__":
    unittest.main()
