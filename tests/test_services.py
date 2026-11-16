import tempfile
import unittest
import wave
from datetime import datetime
from pathlib import Path

from hitmo.database import Database
from hitmo.services import MediaLibrary, PlaylistService, ProgrammingService, TimelineService, media_category


def make_wav(path: Path, seconds: float = 0.15) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(8000 * seconds)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\x00\x00" * frames)
    return path


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "studio.sqlite3")
        self.library = MediaLibrary(self.db)
        self.playlist = PlaylistService(self.db)
        self.timeline = TimelineService(self.db)
        self.programming = ProgrammingService(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_database_initializes_and_seeds_operator(self):
        rows = self.db.all("SELECT name,role FROM operators WHERE active=1")
        self.assertEqual([(r["name"], r["role"]) for r in rows], [("Operador padrão", "Administrador")])

    def test_audio_library_hides_catalog_text_materials(self):
        self.db.execute("INSERT INTO media(path,title,category) VALUES(?,?,?)", ("hitmo://material/audio-1", "Audio", "Música"))
        self.db.execute("INSERT INTO media(path,title,category,text_content) VALUES(?,?,?,?)", ("hitmo://text/text-1", "Texto", "Texto", "Ler no ar"))
        self.assertEqual([row["title"] for row in self.library.search()], ["Audio"])

    def test_import_scans_folder_and_skips_duplicates(self):
        folder = self.root / "Comerciais"
        first = make_wav(folder / "Marca - Oferta.mp3")
        make_wav(folder / "Outro.wav")
        added, total = self.library.import_paths([folder])
        self.assertEqual((added, total), (2, 2))
        added, total = self.library.import_paths([folder])
        self.assertEqual((added, total), (0, 2))
        self.assertEqual(self.db.one("SELECT category FROM media WHERE path=?", (str(first.resolve()),))["category"], "Comercial")

    def test_playlist_order_can_be_rearranged_and_consumed(self):
        files = [make_wav(self.root / f"{name}.wav") for name in ("a", "b", "c")]
        self.library.import_paths(files)
        ids = [int(r["id"]) for r in self.db.all("SELECT id FROM media ORDER BY title")]
        for media_id in ids:
            self.playlist.append(media_id)
        queue = self.playlist.items()
        ordered = [int(r["item_id"]) for r in queue]
        new_order = [ordered[2], ordered[0], ordered[1]]
        self.playlist.reorder(new_order)
        self.assertEqual([int(r["item_id"]) for r in self.playlist.items()], new_order)
        self.assertEqual(int(self.playlist.pop_next()["item_id"]), new_order[0])
        self.assertEqual(len(self.playlist.items()), 2)
        retained = self.db.one("SELECT status FROM playlist_items WHERE id=?", (new_order[0],))
        self.assertEqual(retained["status"], "loading")

    def test_program_block_creates_durable_ordered_occurrence(self):
        files = [make_wav(self.root / f"track-{index}.wav") for index in range(2)]
        self.library.import_paths(files, category="Música")
        media_ids = [int(row["id"]) for row in self.db.all("SELECT id FROM media ORDER BY id")]
        block_id = self.programming.add_block("Manhã", "Música", "12:30", [6])
        source_items = [self.programming.add_block_item(block_id, media_id) for media_id in media_ids]
        now = datetime(2026, 11, 15, 12, 30)
        occurrence_ids = self.programming.due(now)
        self.assertEqual(len(occurrence_ids), 1)
        self.assertEqual(self.programming.due(now), [])
        rows = self.db.all("SELECT * FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position", (occurrence_ids[0],))
        self.assertEqual([int(row["source_item_id"]) for row in rows], source_items)
        self.programming.set_occurrence_item_status(int(rows[0]["id"]), "playing")
        self.programming.set_occurrence_item_status(int(rows[0]["id"]), "completed")
        self.assertEqual(int(self.programming.next_occurrence_item()["id"]), int(rows[1]["id"]))
        self.programming.remove_block_item(source_items[0])
        self.programming.remove_block(block_id)
        retained = self.db.all("SELECT title,path,status FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position", (occurrence_ids[0],))
        self.assertEqual(len(retained), 2)
        self.assertTrue(all(Path(row["path"]).is_file() for row in retained))

    def test_next_block_discards_only_unstarted_overrun_items_and_preserves_history(self):
        files = [make_wav(self.root / f"transition-{index}.wav") for index in range(4)]
        self.library.import_paths(files, category="Música")
        media = self.db.all("SELECT id,title,path FROM media ORDER BY id")
        earlier = self.programming.add_block("Bloco 12h", "Música", "12:00", [6])
        previous_items = [self.programming.add_block_item(earlier, int(row["id"])) for row in media[:3]]
        old_occurrence = self.programming.due(datetime(2026, 11, 15, 12, 0))[0]
        self.programming.set_occurrence_item_status(
            int(self.db.one("SELECT id FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position LIMIT 1", (old_occurrence,))["id"]),
            "playing",
        )
        history_id = self.db.execute(
            "INSERT INTO playback_history(media_id,title,path,category,status) VALUES(?,?,?,'Música','completed')",
            (int(media[0]["id"]), "Já foi ao ar", str(media[0]["path"])),
        )
        incoming = self.programming.add_block("Bloco 12h01", "Música", "12:01", [6])
        self.programming.add_block_item(incoming, int(media[3]["id"]))

        created = self.programming.due(datetime(2026, 11, 15, 12, 1))
        self.assertEqual(len(created), 1)
        old_items = self.db.all("SELECT source_item_id,status,error_message FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position", (old_occurrence,))
        self.assertEqual([row["source_item_id"] for row in old_items], previous_items)
        self.assertEqual([row["status"] for row in old_items], ["playing", "skipped", "skipped"])
        self.assertTrue(all("excedente" in row["error_message"] for row in old_items[1:]))
        new_item = self.db.one("SELECT status FROM program_occurrence_items WHERE occurrence_id=?", (created[0],))
        self.assertEqual(new_item["status"], "planned")
        history = self.db.one("SELECT title,status FROM playback_history WHERE id=?", (history_id,))
        self.assertEqual((history["title"], history["status"]), ("Já foi ao ar", "completed"))
        self.assertEqual(self.programming.due(datetime(2026, 11, 15, 12, 1)), [])
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM program_occurrences WHERE scheduled_for='2026-11-15 12:01'")["n"], 1)

    def test_same_minute_blocks_are_prepared_once_without_skipping_each_other(self):
        files = [make_wav(self.root / f"same-minute-{index}.wav") for index in range(2)]
        self.library.import_paths(files, category="Música")
        media_ids = [int(row["id"]) for row in self.db.all("SELECT id FROM media ORDER BY id")]
        for index, media_id in enumerate(media_ids):
            block_id = self.programming.add_block(f"Mesmo minuto {index}", "Música", "12:30", [6])
            self.programming.add_block_item(block_id, media_id)
        created = self.programming.due(datetime(2026, 11, 15, 12, 30))
        self.assertEqual(len(created), 2)
        states = self.db.all("SELECT status FROM program_occurrence_items ORDER BY occurrence_id")
        self.assertEqual([row["status"] for row in states], ["planned", "planned"])
        self.assertEqual(self.programming.due(datetime(2026, 11, 15, 12, 30)), [])
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM program_occurrences")["n"], 2)

    def test_pending_occurrence_survives_recovery_without_duplicate_dispatch(self):
        files = [make_wav(self.root / f"recovery-{index}.wav") for index in range(2)]
        self.library.import_paths(files, category="Música")
        media_ids = [int(row["id"]) for row in self.db.all("SELECT id FROM media ORDER BY id")]
        block_id = self.programming.add_block("Retomada", "Música", "12:30", [6])
        for media_id in media_ids:
            self.programming.add_block_item(block_id, media_id)
        occurrence_id = self.programming.due(datetime(2026, 11, 15, 12, 30))[0]
        first, second = self.db.all("SELECT id FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position", (occurrence_id,))
        self.programming.set_occurrence_item_status(int(first["id"]), "playing")

        ProgrammingService(Database(self.db.path)).recover_interrupted()

        statuses = self.db.all("SELECT status FROM program_occurrence_items WHERE occurrence_id=? ORDER BY position", (occurrence_id,))
        self.assertEqual([row["status"] for row in statuses], ["interrupted", "planned"])
        self.assertEqual(int(self.programming.next_occurrence_item()["id"]), int(second["id"]))
        self.assertEqual(self.programming.due(datetime(2026, 11, 15, 12, 30)), [])

    def test_empty_block_keeps_existing_ignore_behavior(self):
        self.programming.add_block("Bloco sem inserções", "Música", "12:30", [6])
        self.assertEqual(self.programming.due(datetime(2026, 11, 15, 12, 30)), [])
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM program_occurrences")["n"], 0)

    def test_missing_file_is_snapshotted_as_pending_material(self):
        path = self.root / "offline" / "ausente.wav"
        media_id = self.db.execute("INSERT INTO media(path,title,category) VALUES(?,?,?)", (str(path), "Ausente", "Música"))
        block_id = self.programming.add_block("Arquivo offline", "Música", "12:30", [6])
        self.programming.add_block_item(block_id, media_id)
        occurrence_id = self.programming.due(datetime(2026, 11, 15, 12, 30))[0]
        item = self.db.one("SELECT title,path,status FROM program_occurrence_items WHERE occurrence_id=?", (occurrence_id,))
        self.assertEqual((item["title"], item["path"], item["status"]), ("Ausente", str(path), "planned"))
        self.assertFalse(Path(item["path"]).exists())

    def test_configured_material_folder_and_category_do_not_delete_files(self):
        folder = self.root / "Spots"
        media_file = make_wav(folder / "spot.wav")
        category_id = int(self.db.one("SELECT id FROM material_categories WHERE name='Comercial'")["id"])
        folder_id = self.library.save_folder("Spots", str(folder), category_id)
        self.library.import_paths([folder], category="Comercial", folder_id=folder_id)
        row = self.db.one("SELECT folder_id,category FROM media WHERE path=?", (str(media_file.resolve()),))
        self.assertEqual(int(row["folder_id"]), folder_id)
        self.assertEqual(row["category"], "Comercial")
        self.library.deactivate_folder(folder_id)
        self.assertTrue(media_file.is_file())
        self.assertEqual(int(self.db.one("SELECT COUNT(*) AS n FROM media WHERE path=?", (str(media_file.resolve()),))["n"]), 1)

    def test_timeline_validates_time_and_fires_once_per_day_and_minute(self):
        path = make_wav(self.root / "Ad.wav")
        self.library.import_paths([path])
        media_id = int(self.db.one("SELECT id FROM media")["id"])
        with self.assertRaises(ValueError):
            self.timeline.add("25:70", [6], "Invalid", media_id, "Comercial")
        self.timeline.add("12:30", [6], "Weekend spot", media_id, "Comercial")
        now = datetime(2026, 11, 15, 12, 30)  # Sunday
        due = self.timeline.due(now)
        self.assertEqual(len(due), 1)
        self.assertEqual(int(due[0]["media_id"]), media_id)
        self.assertEqual(self.timeline.due(now), [])
        self.assertEqual(self.timeline.due(datetime(2026, 11, 16, 12, 30)), [])

    def test_timeline_tracks_missing_media_without_crashing_event_list(self):
        event_id = self.db.execute("INSERT INTO timeline_events(time_of_day,title,media_id) VALUES('12:00','Old file',NULL)")
        row = next(r for r in self.timeline.events() if int(r["id"]) == event_id)
        self.assertIsNone(row["media_title"])

    def test_category_detection_uses_folder_context(self):
        self.assertEqual(media_category(Path("/library/vinhetas/station.wav")), "Vinheta")
        self.assertEqual(media_category(Path("/library/music/artist-title.mp3")), "Música")


if __name__ == "__main__":
    unittest.main()

