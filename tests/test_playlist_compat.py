import tempfile
import sqlite3
import unittest
from pathlib import Path

from hitmo.database import Database
from hitmo.playlist_compat import PlaylistConfigImporter, parse_playlist_folders


SAMPLE_XML = """<?xml version="1.0" encoding="utf-8"?>
<Folders>
  <Folders>2</Folders><Version>1.2</Version>
  <Shared><Folders>1</Folders><Server>StudioServer</Server>
    <Folder0><Name>Playlist</Name><Path>C:\\Playlist</Path></Folder0>
  </Shared>
  <Folder0><ID>4</ID><Title>Músicas</Title><Type>M</Type>
    <Target>C:\\Playlist\\Músicas</Target>
    <IconLocation>C:\\Playlist\\pgm\\ICONS.dll</IconLocation>
    <ShortcutArguments>M</ShortcutArguments><ShortcutPathName>C:\\Playlist\\pgm\\Atalhos\\Musicas.lnk</ShortcutPathName>
    <IconIndex>397</IconIndex><Output>-1</Output><TotalFiles>22</TotalFiles><DBFId>M</DBFId>
  </Folder0>
  <Folder1><ID>5</ID><Title>Pausa</Title><Type>P</Type>
    <Target>C:\\Playlist\\pgm\\Atalhos</Target>
    <IconLocation>C:\\Playlist\\pgm\\ICONS.dll</IconLocation>
    <ShortcutPathName>C:\\Playlist\\pgm\\Atalhos\\Pausa.lnk</ShortcutPathName>
    <IconIndex>396</IconIndex><Output>-1</Output><TotalFiles>1</TotalFiles>
  </Folder1>
</Folders>"""


class PlaylistCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "hitmo.sqlite3")
        self.xml_path = self.root / "Folders.xml"
        self.xml_path.write_text(SAMPLE_XML, encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_parser_preserves_playlist_fields_and_folder_order(self):
        catalog = parse_playlist_folders(self.xml_path)
        self.assertEqual(catalog.version, "1.2")
        self.assertEqual(catalog.declared_folder_count, 2)
        self.assertEqual(catalog.declared_shared_count, 1)
        self.assertEqual(catalog.shared_roots[0].server, "StudioServer")
        self.assertEqual(catalog.shared_roots[0].title, "Playlist")
        self.assertEqual([item.playlist_id for item in catalog.folders], [4, 5])
        music, pause = catalog.folders
        self.assertEqual(music.type_code, "M")
        self.assertEqual(music.icon_index, 397)
        self.assertEqual(music.shortcut_path_name, r"C:\Playlist\pgm\Atalhos\Musicas.lnk")
        self.assertEqual(music.total_files, 22)
        self.assertEqual(pause.type_code, "P")
        self.assertEqual(pause.target, r"C:\Playlist\pgm\Atalhos")

    def test_import_stores_snapshot_without_scanning_media_or_mutating_source(self):
        audio = self.root / "original.wav"
        audio.write_bytes(b"original media bytes")
        media_id = self.db.execute(
            "INSERT INTO media(path,title,category) VALUES(?,?,?)",
            (str(audio), "Existing", "Música"),
        )
        playlist_item_id = self.db.execute(
            "INSERT INTO playlist_items(media_id,position,category) VALUES(?,?,?)",
            (media_id, 0, "Música"),
        )
        program_block_id = self.db.execute(
            "INSERT INTO program_blocks(name,block_type,time_of_day) VALUES(?,?,?)",
            ("Grade preservada", "Música", "12:00"),
        )
        history_id = self.db.execute(
            "INSERT INTO playback_history(media_id,title,path) VALUES(?,?,?)",
            (media_id, "Existing", str(audio)),
        )
        source_before = self.xml_path.read_bytes()
        media_count_before = self.db.one("SELECT COUNT(*) AS n FROM media")["n"]
        result = PlaylistConfigImporter(
            self.db,
            path_exists=lambda target: target == r"C:\Playlist\Músicas",
        ).import_file(self.xml_path)

        self.assertEqual((result.folder_count, result.existing_target_count, result.missing_target_count), (2, 1, 1))
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM media")["n"], media_count_before)
        self.assertEqual(self.db.one("SELECT id FROM playback_history")["id"], history_id)
        self.assertEqual(self.db.one("SELECT id FROM playlist_items")["id"], playlist_item_id)
        self.assertEqual(self.db.one("SELECT id FROM program_blocks")["id"], program_block_id)
        self.assertEqual(audio.read_bytes(), b"original media bytes")
        self.assertEqual(self.xml_path.read_bytes(), source_before)
        self.assertEqual(self.db.one("SELECT total_files FROM playlist_folder_entries WHERE catalog_id=? AND playlist_id=4", (result.snapshot_id,))["total_files"], 22)
        self.assertEqual(self.db.one("SELECT target_exists FROM playlist_folder_entries WHERE catalog_id=? AND playlist_id=5", (result.snapshot_id,))["target_exists"], 0)
        self.assertEqual(self.db.one("SELECT path FROM playlist_shared_roots WHERE catalog_id=?", (result.snapshot_id,))["path"], r"C:\Playlist")

    def test_reimport_creates_a_new_snapshot_and_keeps_previous_snapshot(self):
        importer = PlaylistConfigImporter(self.db, path_exists=lambda _target: False)
        first = importer.import_file(self.xml_path)
        revised_xml = SAMPLE_XML.replace("<Title>Músicas</Title>", "<Title>Música principal</Title>")
        second_path = self.root / "Folders-revised.xml"
        second_path.write_text(revised_xml, encoding="utf-8")
        second = importer.import_file(second_path)

        self.assertNotEqual(first.snapshot_id, second.snapshot_id)
        self.assertEqual(self.db.one("SELECT title FROM playlist_folder_entries WHERE catalog_id=? AND playlist_id=4", (first.snapshot_id,))["title"], "Músicas")
        self.assertEqual(self.db.one("SELECT title FROM playlist_folder_entries WHERE catalog_id=? AND playlist_id=4", (second.snapshot_id,))["title"], "Música principal")
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM playlist_catalogs")["n"], 2)

    def test_invalid_catalog_does_not_create_a_snapshot(self):
        self.xml_path.write_text("<NotFolders />", encoding="utf-8")
        with self.assertRaises(ValueError):
            PlaylistConfigImporter(self.db).import_file(self.xml_path)
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM playlist_catalogs")["n"], 0)

    def test_additive_migration_preserves_existing_hitmo_rows(self):
        legacy_path = self.root / "legacy-hitmo.sqlite3"
        con = sqlite3.connect(legacy_path)
        con.executescript("""
            CREATE TABLE material_categories(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,material_kind TEXT NOT NULL DEFAULT 'audio',icon_path TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE media_folders(id INTEGER PRIMARY KEY,name TEXT NOT NULL,path TEXT NOT NULL UNIQUE,category_id INTEGER,icon_path TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE media(id INTEGER PRIMARY KEY,path TEXT NOT NULL UNIQUE,title TEXT NOT NULL,artist TEXT NOT NULL DEFAULT '',category TEXT NOT NULL DEFAULT 'Música',duration REAL NOT NULL DEFAULT 0,modified REAL NOT NULL DEFAULT 0,added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE playlist_items(id INTEGER PRIMARY KEY,media_id INTEGER NOT NULL,position INTEGER NOT NULL,category TEXT NOT NULL DEFAULT 'Música');
            CREATE TABLE playback_history(id INTEGER PRIMARY KEY,media_id INTEGER,title TEXT NOT NULL,artist TEXT NOT NULL DEFAULT '',path TEXT NOT NULL,category TEXT NOT NULL DEFAULT 'Música',operator TEXT NOT NULL DEFAULT '',started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,ended_at TEXT NOT NULL DEFAULT '',played_seconds REAL NOT NULL DEFAULT 0);
            CREATE TABLE program_occurrence_items(id INTEGER PRIMARY KEY,occurrence_id INTEGER NOT NULL,source_item_id INTEGER,media_id INTEGER,position INTEGER NOT NULL,item_kind TEXT NOT NULL DEFAULT 'media',title TEXT NOT NULL DEFAULT '',artist TEXT NOT NULL DEFAULT '',path TEXT NOT NULL DEFAULT '',category TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'planned',started_at TEXT NOT NULL DEFAULT '',ended_at TEXT NOT NULL DEFAULT '',error_message TEXT NOT NULL DEFAULT '');
            INSERT INTO material_categories(name) VALUES('Música');
            INSERT INTO media_folders(name,path,category_id) VALUES('Pasta antiga','C:/Archive',NULL);
            INSERT INTO media(path,title) VALUES('C:/Archive/song.wav','Canção antiga');
            INSERT INTO playback_history(media_id,title,path,operator) VALUES(1,'Canção antiga','C:/Archive/song.wav','Operador antigo');
        """)
        con.commit()
        con.close()

        migrated = Database(legacy_path)
        self.assertEqual(migrated.one("SELECT title FROM media WHERE id=1")["title"], "Canção antiga")
        self.assertEqual(migrated.one("SELECT operator FROM playback_history WHERE id=1")["operator"], "Operador antigo")
        self.assertIn("folder_id", {row["name"] for row in migrated.all("PRAGMA table_info(media)")})
        self.assertIn("text_content", {row["name"] for row in migrated.all("PRAGMA table_info(media)")})
        self.assertIn("status", {row["name"] for row in migrated.all("PRAGMA table_info(playlist_items)")})
        self.assertIn("duration", {row["name"] for row in migrated.all("PRAGMA table_info(program_occurrence_items)")})
        self.assertIn("transition_policy", {row["name"] for row in migrated.all("PRAGMA table_info(program_blocks)")})
        self.assertIn("fixed_fade_ms", {row["name"] for row in migrated.all("PRAGMA table_info(program_blocks)")})
        occurrence_columns = {row["name"] for row in migrated.all("PRAGMA table_info(program_occurrences)")}
        self.assertIn("transition_policy", occurrence_columns)
        self.assertIn("fixed_fade_ms", occurrence_columns)
        self.assertIn("dispatch_delay_ms", occurrence_columns)
        self.assertIn("dispatch_reason", occurrence_columns)
        self.assertTrue(migrated.one("SELECT name FROM sqlite_master WHERE type='table' AND name='scheduler_decisions'"))
        folder_columns = {row["name"] for row in migrated.all("PRAGMA table_info(media_folders)")}
        self.assertIn("jingle_rotation_enabled", folder_columns)
        self.assertIn("jingle_repeat_cooldown", folder_columns)
        self.assertEqual(
            int(migrated.one("SELECT jingle_rotation_enabled FROM media_folders WHERE id=1")["jingle_rotation_enabled"]),
            0,
        )
        self.assertEqual(migrated.one("SELECT COUNT(*) AS n FROM jingle_rotation_state")["n"], 0)
        self.assertEqual(migrated.one("SELECT COUNT(*) AS n FROM playlist_folder_entries")["n"], 0)


if __name__ == "__main__":
    unittest.main()
