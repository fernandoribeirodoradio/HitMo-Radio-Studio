import tempfile
import unittest
import wave
from datetime import datetime
from pathlib import Path

from hitmo.database import Database
from hitmo.services import MediaLibrary, PlaylistService, TimelineService, media_category


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

    def tearDown(self):
        self.temp.cleanup()

    def test_database_initializes_and_seeds_operator(self):
        rows = self.db.all("SELECT name,role FROM operators WHERE active=1")
        self.assertEqual([(r["name"], r["role"]) for r in rows], [("Operador padrão", "Administrador")])

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

