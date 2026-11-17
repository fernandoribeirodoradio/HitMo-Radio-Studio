import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen

from hitmo.api_client import HitMoAPIClient
from hitmo.api_service import APIError, create_api_user, create_server, validate_program_payload
from hitmo.database import Database
from hitmo.services import ProgrammingService


class CentralAPIProcessTests(unittest.TestCase):
    password = "RadioStudio#2026"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "hitmo.sqlite3"
        self.db = Database(self.db_path)
        create_api_user(self.db, "test-admin", self.password, "admin")
        create_api_user(self.db, "test-reader", self.password, "reader")
        self.port = self._free_port()
        self.env = os.environ.copy()
        self.env["HITMO_DATA_DIR"] = str(self.root)
        self.env["PYTHONPATH"] = str(Path.cwd()) + os.pathsep + self.env.get("PYTHONPATH", "")
        self.process = None
        self._start_server()
        self.admin = HitMoAPIClient(f"http://127.0.0.1:{self.port}", "test-admin", self.password, timeout=2)
        self.reader = HitMoAPIClient(f"http://127.0.0.1:{self.port}", "test-reader", self.password, timeout=2)

    def tearDown(self):
        self._stop_server()
        self.temp.cleanup()

    @staticmethod
    def _free_port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def _start_server(self):
        self.process = subprocess.Popen(
            [sys.executable, "-m", "hitmo.server", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=Path.cwd(), env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        url = f"http://127.0.0.1:{self.port}/healthz"
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.fail(f"API server exited early with code {self.process.returncode}")
            try:
                with urlopen(url, timeout=0.3) as response:
                    if response.status == 200:
                        return
            except Exception:
                time.sleep(0.05)
        self.fail("API server did not become healthy within 8 seconds")

    def _stop_server(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=4)
        self.process = None

    def _write_audio(self, name: str) -> Path:
        path = self.root / name
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(8000)
            out.writeframes(b"\x00\x00" * 800)
        return path

    def _material(self, name: str) -> dict:
        return {"path": str(self._write_audio(name)), "title": Path(name).stem, "category": "Música"}

    def test_startup_health_and_version_are_available(self):
        self.assertEqual(self.admin.health()["status"], "ok")
        version = self.admin.version()
        self.assertIn("catalog_revision", version)
        self.assertIn("program_revision", version)
        self.assertEqual(version["authenticated_as"], "test-admin")

    def test_authentication_and_role_permissions(self):
        bad = HitMoAPIClient(f"http://127.0.0.1:{self.port}", "missing-user", "wrong-password-123")
        with self.assertRaisesRegex(RuntimeError, "401"):
            bad.version()
        self.assertIn("categories", self.reader.categories())
        with self.assertRaisesRegex(RuntimeError, "403"):
            self.reader.add_material(self._material("forbidden.wav"), self.reader.version()["catalog_revision"])

    def test_fixed_transition_is_commercial_only_and_legacy_policy_defaults_to_discard(self):
        common = {"name": "Bloco", "time_of_day": "12:30", "days": [0], "items": []}
        fixed = validate_program_payload(
            self.db,
            {"blocks": [{**common, "block_type": "Comercial", "transition_policy": "fixed", "fixed_fade_ms": 250}]},
        )["blocks"][0]
        self.assertEqual((fixed["transition_policy"], fixed["fixed_fade_ms"]), ("fixed", 250))
        with self.assertRaises(APIError):
            validate_program_payload(
                self.db,
                {"blocks": [{**common, "block_type": "Música", "transition_policy": "fixed"}]},
            )
        legacy = validate_program_payload(self.db, {"blocks": [{**common, "block_type": "Música"}]})["blocks"][0]
        self.assertEqual((legacy["transition_policy"], legacy["fixed_fade_ms"]), ("discard_insertions", 1000))

    def test_material_create_update_soft_deactivate_restore_and_history_preservation(self):
        audio = self._write_audio("original.wav")
        version = self.admin.version()
        created = self.admin.add_material({"path": str(audio), "title": "Original", "category": "Música"}, version["catalog_revision"])
        material_id = created["id"]
        self.db.execute(
            "INSERT INTO playback_history(media_id,title,path,operator) VALUES(?,?,?,?)",
            (material_id, "Original", str(audio), "test-admin"),
        )
        updated = self.admin.update_material(material_id, {"title": "Renomeada", "path": str(audio), "category": "Música"}, created["revision"])
        self.assertEqual(self.db.one("SELECT id,title FROM media WHERE path=?", (str(audio.resolve()),))["id"], material_id)
        inactive = self.admin.deactivate_material(material_id, updated["revision"])
        self.assertEqual(self.db.one("SELECT active FROM media WHERE id=?", (material_id,))["active"], 0)
        self.assertEqual(self.db.one("SELECT id FROM playback_history WHERE media_id=?", (material_id,))["id"], 1)
        self.assertTrue(audio.is_file())
        self.assertNotIn(material_id, [row["id"] for row in self.admin.materials()["materials"]])
        restored = self.admin.restore_material(material_id, inactive["revision"])
        self.assertEqual(self.db.one("SELECT active FROM media WHERE id=?", (material_id,))["active"], 1)
        self.assertIn(material_id, [row["id"] for row in self.admin.materials()["materials"]])
        self.assertGreater(restored["revision"], updated["revision"])

    def test_missing_path_is_preserved_and_text_is_not_converted_to_audio(self):
        missing = self.root / "unplugged-share" / "identificador.mp3"
        revision = self.admin.version()["catalog_revision"]
        created = self.admin.add_material({"path": str(missing), "title": "Fora do ar", "category": "Música"}, revision)
        record = next(row for row in self.admin.materials()["materials"] if row["id"] == created["id"])
        self.assertFalse(record["path_exists_on_server"])
        self.assertEqual(record["path"], str(missing.resolve()))
        previous_id = record["id"]
        moved = self.root / "novo-diretorio" / "mesmo-material.mp3"
        updated = self.admin.update_material(previous_id, {"path": str(moved), "title": "Fora do ar", "category": "Música"}, self.admin.version()["catalog_revision"])
        self.assertEqual(updated["id"], previous_id)

        text_record = self.admin.add_material(
            {"path": "hitmo://text/ref-texto-1", "title": "Texto de locução", "category": "Texto", "text_content": "Texto preservado."},
            self.admin.version()["catalog_revision"],
        )
        row = self.db.one("SELECT id,path,text_content FROM media WHERE id=?", (text_record["id"],))
        self.assertEqual((row["path"], row["text_content"]), ("hitmo://text/ref-texto-1", "Texto preservado."))
        view = next(item for item in self.admin.materials()["materials"] if item["id"] == text_record["id"])
        self.assertEqual(view["material_kind"], "text")
        self.assertFalse(view["path_exists_on_server"])

    def test_material_can_be_assigned_to_existing_server_folder(self):
        category_id = int(self.db.one("SELECT id FROM material_categories WHERE name='Música'")["id"])
        folder_id = self.db.execute(
            "INSERT INTO media_folders(name,path,category_id) VALUES(?,?,?)",
            ("Biblioteca", str(self.root / "music"), category_id),
        )
        result = self.admin.add_material(
            {"path": str(self.root / "offline.wav"), "title": "Sem arquivo presente", "category": "Música", "folder_id": folder_id},
            self.admin.version()["catalog_revision"],
        )
        record = next(row for row in self.admin.materials()["materials"] if row["id"] == result["id"])
        self.assertEqual(record["folder_id"], folder_id)
        self.assertFalse(record["path_exists_on_server"])

    def test_folder_scoped_jingle_rotation_rules_are_validated_and_persisted(self):
        category_id = int(self.db.one("SELECT id FROM material_categories WHERE name='Vinheta'")["id"])
        folder = self.admin.add_folder(
            {
                "name": "Rodízio configurado", "path": r"\\offline\vinhetas\rodizio",
                "category_id": category_id, "jingle_rotation_enabled": True, "jingle_repeat_cooldown": 3,
            },
            self.admin.version()["catalog_revision"],
        )
        folder_id = int(folder["id"])
        saved = next(item for item in self.admin.folders(True)["folders"] if int(item["id"]) == folder_id)
        self.assertEqual((saved["jingle_rotation_enabled"], saved["jingle_repeat_cooldown"]), (1, 3))

        self.admin.update_folder(
            folder_id,
            {
                "name": "Rodízio configurado", "path": r"\\offline\vinhetas\rodizio",
                "category_id": category_id, "jingle_rotation_enabled": True, "jingle_repeat_cooldown": 5,
            },
            self.admin.version()["catalog_revision"],
        )
        self.assertEqual(int(self.db.one("SELECT jingle_repeat_cooldown FROM media_folders WHERE id=?", (folder_id,))["jingle_repeat_cooldown"]), 5)

        music_category_id = int(self.db.one("SELECT id FROM material_categories WHERE name='Música'")["id"])
        with self.assertRaisesRegex(RuntimeError, "422"):
            self.admin.add_folder(
                {
                    "name": "Regra inválida", "path": r"\\offline\musicas\invalid",
                    "category_id": music_category_id, "jingle_rotation_enabled": True,
                },
                self.admin.version()["catalog_revision"],
            )

        legacy_folder = self.admin.add_folder(
            {
                "name": "Pasta antiga", "path": r"\\offline\catalogo\antigo",
                "category_id": music_category_id,
            },
            self.admin.version()["catalog_revision"],
        )
        self.admin.update_folder(
            int(legacy_folder["id"]),
            {
                "name": "Pasta antiga", "path": r"\\offline\catalogo\antigo",
                "category_id": category_id,
            },
            self.admin.version()["catalog_revision"],
        )
        self.assertEqual(
            int(self.db.one("SELECT jingle_rotation_enabled FROM media_folders WHERE id=?", (legacy_folder["id"],))["jingle_rotation_enabled"]),
            1,
        )
        self.admin.update_folder(
            int(legacy_folder["id"]),
            {
                "name": "Pasta antiga", "path": r"\\offline\catalogo\antigo",
                "category_id": music_category_id,
            },
            self.admin.version()["catalog_revision"],
        )
        self.assertEqual(
            int(self.db.one("SELECT jingle_rotation_enabled FROM media_folders WHERE id=?", (legacy_folder["id"],))["jingle_rotation_enabled"]),
            0,
        )

    def test_remote_categories_and_folders_preserve_ids_materials_and_history(self):
        revision = self.admin.version()["catalog_revision"]
        category = self.admin.add_category(
            {"name": "Jingles extras", "material_kind": "audio", "icon_path": "\\\\radio\\icons\\jingle.png"},
            revision,
        )
        category_id = category["id"]
        missing_directory = r"\\radio-offline\audio\jingles"
        folder = self.admin.add_folder(
            {"name": "Jingles do estúdio", "path": missing_directory, "category_id": category_id, "icon_path": "\\\\radio\\icons\\folder.ico"},
            category["revision"],
        )
        folder_id = folder["id"]
        folder_view = next(row for row in self.admin.folders(True)["folders"] if row["id"] == folder_id)
        self.assertEqual(folder_view["path"], missing_directory)
        self.assertIsNone(folder_view["path_exists_on_server"])

        material_path = r"\\radio-offline\audio\jingles\launch.wav"
        material = self.admin.add_material(
            {"path": material_path, "title": "Lançamento", "category": "Jingles extras", "folder_id": folder_id},
            folder["revision"],
        )
        media_id = material["id"]
        material_view = next(row for row in self.admin.materials(True)["materials"] if row["id"] == media_id)
        self.assertEqual(material_view["path"], material_path)
        self.assertIsNone(material_view["path_exists_on_server"])
        block_id = self.db.execute(
            "INSERT INTO program_blocks(name,block_type,time_of_day) VALUES('Jingles', 'Música', '11:00')"
        )
        self.db.execute("INSERT INTO program_block_items(block_id,media_id,position) VALUES(?,?,0)", (block_id, media_id))
        self.db.execute(
            "INSERT INTO playback_history(media_id,title,path,category,status) VALUES(?,?,?,'Jingles extras','completed')",
            (media_id, "Lançamento", material_path),
        )
        occurrence_id = self.db.execute(
            "INSERT INTO program_occurrences(origin_type,block_id,scheduled_for,status) VALUES('block',?,'2026-11-16 11:00','running')",
            (block_id,),
        )
        self.db.execute(
            "INSERT INTO program_occurrence_items(occurrence_id,media_id,position,title,path,category,status) VALUES(?,?,0,'Lançamento',?,'Jingles extras','playing')",
            (occurrence_id, media_id, material_path),
        )

        new_category = self.admin.update_category(
            category_id,
            {"name": "Jingles de campanha", "material_kind": "audio", "icon_path": "\\\\radio\\icons\\campaign.png"},
            self.admin.version()["catalog_revision"],
        )
        self.assertEqual(new_category["id"], category_id)
        self.assertEqual(self.db.one("SELECT category,folder_id FROM media WHERE id=?", (media_id,))["category"], "Jingles de campanha")
        self.assertEqual(self.db.one("SELECT media_id FROM program_block_items WHERE block_id=?", (block_id,))["media_id"], media_id)
        self.assertEqual(self.db.one("SELECT category,status FROM playback_history WHERE media_id=?", (media_id,))["category"], "Jingles extras")
        self.assertEqual(self.db.one("SELECT status FROM program_occurrences WHERE id=?", (occurrence_id,))["status"], "running")
        self.assertEqual(self.db.one("SELECT category,status FROM program_occurrence_items WHERE occurrence_id=?", (occurrence_id,))["category"], "Jingles extras")
        with self.assertRaisesRegex(RuntimeError, "409"):
            self.admin.update_category(
                category_id,
                {"name": "Jingles de campanha", "material_kind": "text", "icon_path": ""},
                self.admin.version()["catalog_revision"],
            )
        updated_folder = self.admin.update_folder(
            folder_id,
            {"name": "Jingles arquivados", "path": missing_directory + "-new", "category_id": category_id, "icon_path": ""},
            self.admin.version()["catalog_revision"],
        )
        self.assertEqual(updated_folder["id"], folder_id)
        self.assertEqual(self.db.one("SELECT folder_id FROM media WHERE id=?", (media_id,))["folder_id"], folder_id)
        deactivated = self.admin.set_folder_active(folder_id, False, self.admin.version()["catalog_revision"])
        self.assertFalse(deactivated["active"])
        restored = self.admin.set_folder_active(folder_id, True, deactivated["revision"])
        self.assertTrue(restored["active"])
        category_off = self.admin.set_category_active(category_id, False, restored["revision"])
        self.assertFalse(category_off["active"])
        category_on = self.admin.set_category_active(category_id, True, category_off["revision"])
        self.assertTrue(category_on["active"])
        self.assertEqual(self.db.one("SELECT id FROM media WHERE id=?", (media_id,))["id"], media_id)
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM api_audit_log WHERE resource IN ('category','folder')")["n"], 8)

    def test_path_collision_does_not_create_a_second_catalog_id(self):
        audio = self._write_audio("CaseSensitive.wav")
        self.admin.add_material(
            {"path": str(audio), "title": "Único", "category": "Música"},
            self.admin.version()["catalog_revision"],
        )
        with self.assertRaisesRegex(RuntimeError, "409"):
            self.admin.add_material(
                {"path": str(audio).swapcase(), "title": "Duplicado", "category": "Música"},
                self.admin.version()["catalog_revision"],
            )
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM media WHERE title IN ('Único','Duplicado')")["n"], 1)

    def test_two_clients_using_same_catalog_revision_get_conflict(self):
        revision = self.admin.version()["catalog_revision"]
        payloads = [self._material("first.wav"), self._material("second.wav")]
        clients = [self.admin, self.admin]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(client.add_material, payload, revision) for client, payload in zip(clients, payloads)]
        results = []
        for future in futures:
            try:
                results.append(future.result())
            except RuntimeError as exc:
                results.append(str(exc))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertEqual(sum(isinstance(result, str) and "409" in result for result in results), 1)

    def test_program_edit_conflicts_and_publish_preserves_running_occurrence(self):
        audio = self._write_audio("music.wav")
        media_id = self.db.execute("INSERT INTO media(path,title,category) VALUES(?,?,?)", (str(audio), "Music", "Música"))
        programming = ProgrammingService(self.db)
        block_id = programming.add_block("Manhã", "Música", "09:00", [0, 1, 2, 3, 4])
        item_id = programming.add_block_item(block_id, media_id)
        occurrence_id = self.db.execute(
            "INSERT INTO program_occurrences(origin_type,block_id,scheduled_for,status,program_revision_id) VALUES('block',?,'2026-11-16 09:00','running',?)",
            (block_id, self.db.one("SELECT revision FROM program_state WHERE id=1")["revision"]),
        )
        self.db.execute(
            "INSERT INTO program_occurrence_items(occurrence_id,source_item_id,media_id,position,title,path,category,status) VALUES(?,?,?,?,?,?,?,'playing')",
            (occurrence_id, item_id, media_id, 0, "Frozen title", str(audio), "Música"),
        )
        self.db.execute(
            "INSERT INTO playback_history(media_id,material_id_snapshot,title,path,status,source_type,source_ref,occurrence_id,block_id,block_name_snapshot) "
            "VALUES(?,?,?,?,'playing','scheduled_block',?,?,?,'Manhã')",
            (media_id, media_id, "Frozen title", str(audio), item_id, occurrence_id, block_id),
        )

        state = self.admin.programs()
        block = state["blocks"][0]
        proposed = [{"id": block_id, "name": "Manhã atualizada", "block_type": "Música", "time_of_day": "09:05", "days": [0, 1, 2, 3, 4], "items": [media_id]}]
        draft = self.admin.create_draft("Grade nova", state["saved_revision"], proposed)
        self.db.execute("UPDATE program_blocks SET name='Edição local concorrente' WHERE id=?", (block_id,))
        with self.assertRaisesRegex(RuntimeError, "409"):
            self.admin.publish_draft(draft["id"], draft["draft_revision"])
        self.assertEqual(self.db.one("SELECT status FROM program_occurrences WHERE id=?", (occurrence_id,))["status"], "running")

        state = self.admin.programs()
        proposed[0]["name"] = "Grade publicada"
        proposed[0]["time_of_day"] = "09:10"
        draft = self.admin.create_draft("Grade válida", state["saved_revision"], proposed)
        result = self.admin.publish_draft(draft["id"], draft["draft_revision"])
        self.assertGreater(result["published_revision"], state["saved_revision"])
        self.assertEqual(self.db.one("SELECT name,time_of_day FROM program_blocks WHERE id=?", (block_id,))["name"], "Grade publicada")
        occurrence = self.db.one("SELECT status,program_revision_id FROM program_occurrences WHERE id=?", (occurrence_id,))
        item = self.db.one("SELECT title,path,status FROM program_occurrence_items WHERE occurrence_id=?", (occurrence_id,))
        self.assertEqual(occurrence["status"], "running")
        self.assertEqual((item["title"], item["path"], item["status"]), ("Frozen title", str(audio), "playing"))
        self.assertEqual(self.db.one("SELECT status FROM playback_history")["status"], "playing")
        visible = self.admin.occurrences("2026-11-16", "2026-11-16", "running")["occurrences"]
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]["items"][0]["title"], "Frozen title")
        self.assertEqual(len(self.admin.history()["history"]), 1)

    def test_repeated_clients_and_program_drafts_are_isolated(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.reader.categories), pool.submit(self.admin.folders)]
        self.assertEqual(len(futures[0].result()["categories"]), 9)
        self.assertIn("folders", futures[1].result())
        rev = self.admin.programs()["saved_revision"]
        first = self.admin.create_draft("Rascunho A", rev, {"blocks": []}["blocks"])
        self.assertEqual(self.admin.get_draft(first["id"])["status"], "draft")

    def test_publish_rechecks_materials_after_draft_was_created(self):
        audio = self._write_audio("scheduled.wav")
        media_id = self.db.execute(
            "INSERT INTO media(path,title,category) VALUES(?,?,?)",
            (str(audio), "Scheduled", "Música"),
        )
        state = self.admin.programs()
        draft = self.admin.create_draft(
            "Material válido ao criar",
            state["saved_revision"],
            [{"name": "Bloco", "block_type": "Música", "time_of_day": "10:00", "days": [0], "items": [media_id]}],
        )
        self.admin.deactivate_material(media_id, self.admin.version()["catalog_revision"])
        with self.assertRaisesRegex(RuntimeError, "409"):
            self.admin.publish_draft(draft["id"], draft["draft_revision"])
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM program_blocks")["n"], 0)

    def test_playlist_reference_snapshot_is_read_only_and_preserves_legacy_codes(self):
        catalog_id = self.db.execute(
            "INSERT INTO playlist_catalogs(source_path,version,declared_folder_count) VALUES(?,?,?)",
            ("C:/Playlist/pgm/Folders.xml", "Playlist Digital", 1),
        )
        self.db.execute(
            "INSERT INTO playlist_folder_entries(catalog_id,position,playlist_id,title,type_code,target,shortcut_arguments,shortcut_path_name,dbf_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (catalog_id, 0, 902, "Músicas", "MUS", "M:\\Audio", "-m", "Músicas.lnk", "DBF-902"),
        )
        references = self.admin.playlist_references()
        entry = references["entries"][0]
        self.assertEqual(entry["playlist_id"], 902)
        self.assertEqual(entry["type_code"], "MUS")
        self.assertEqual(entry["dbf_id"], "DBF-902")
        self.assertEqual(references["catalog"]["source_path"], "C:/Playlist/pgm/Folders.xml")

    def test_http_is_refused_on_lan_without_tls_and_no_audio_route_exists(self):
        with self.assertRaisesRegex(ValueError, "TLS"):
            create_server(self.db, "0.0.0.0", 0)
        with self.assertRaisesRegex(RuntimeError, "404"):
            self.admin.request("POST", "/api/v1/audio/play", body={})

    def test_restart_keeps_users_catalog_and_history(self):
        audio = self._write_audio("persistent.wav")
        revision = self.admin.version()["catalog_revision"]
        result = self.admin.add_material({"path": str(audio), "title": "Persistent", "category": "Música"}, revision)
        self.db.execute("INSERT INTO playback_history(media_id,title,path) VALUES(?,?,?)", (result["id"], "Persistent", str(audio)))
        self._stop_server()
        self._start_server()
        self.admin = HitMoAPIClient(f"http://127.0.0.1:{self.port}", "test-admin", self.password)
        self.assertEqual(self.admin.materials()["materials"][0]["title"], "Persistent")
        self.assertEqual(len(self.admin.history()["history"]), 1)

    def test_remote_history_filters_and_declares_software_event_scope(self):
        for title, started_at, status, category in (
            ("Oferta", "2026-11-14 23:55:00", "completed", "Comercial"),
            ("Oferta reprise", "2026-11-15 00:10:00", "interrupted", "Comercial"),
            ("Vinheta", "2026-11-15 08:00:00", "completed", "Vinheta"),
        ):
            self.db.execute(
                "INSERT INTO playback_history(media_id,material_id_snapshot,title,path,category,operator,started_at,ended_at,status,block_name_snapshot) "
                "VALUES(NULL,?,?,?,?,?,?,?,?,?)",
                (77, title, f"C:/Audio/{title}.wav", category, "Locutor", started_at, started_at, status, "Bloco snapshot"),
            )

        result = self.admin.history(
            start_date="2026-11-15", end_date="2026-11-15", material="Oferta",
            category="Comercial", status="interrupted", limit=1,
        )
        self.assertEqual((result["total"], len(result["history"])), (1, 1))
        entry = result["history"][0]
        self.assertEqual((entry["title"], entry["material_id"], entry["block_name_snapshot"]),
                         ("Oferta reprise", 77, "Bloco snapshot"))
        self.assertEqual(result["evidence_scope"], "software_events_only")
        block_id = self.db.execute(
            "INSERT INTO program_blocks(name,block_type,time_of_day) VALUES('Bloco perdido','Comercial','09:00')"
        )
        occurrence_id = self.db.execute(
            "INSERT INTO program_occurrences(origin_type,block_id,scheduled_for,status,operator,block_name_snapshot) "
            "VALUES('block',?,'2026-11-15 09:00','missed','Operador da manhã','Bloco perdido')",
            (block_id,),
        )
        self.db.execute(
            "INSERT INTO program_occurrence_items(occurrence_id,media_id_snapshot,position,title,path,category,status,ended_at,error_message) "
            "VALUES(?,?,0,'Oferta ignorada','C:/Audio/ignorada.wav','Comercial','ignored','2026-11-15 09:00:30','Atraso')",
            (occurrence_id, 77),
        )
        ignored = self.admin.history(material="77", status="ignored")["history"]
        self.assertEqual(len(ignored), 1)
        self.assertEqual((ignored[0]["record_type"], ignored[0]["occurrence_status"], ignored[0]["operator"]),
                         ("occurrence_item", "missed", "Operador da manhã"))
        with self.assertRaisesRegex(RuntimeError, "400"):
            self.admin.history(start_date="15/11/2026")

    def test_cartouche_history_is_available_through_operational_history_api(self):
        material_id = self.db.execute(
            "INSERT INTO media(path,title,category) VALUES(?,?,?)", ("C:/radio/jingle.wav", "Jingle cartucho", "Vinheta")
        )
        event_id = self.db.execute(
            "INSERT INTO cartouche_history(slot,media_id_snapshot,title,path,category,operator,status) "
            "VALUES(3,?,?,?,?,?,'triggered')",
            (material_id, "Jingle cartucho", "C:/radio/jingle.wav", "Vinheta", "Operador Teste"),
        )
        response = self.admin.history(material="Jingle cartucho")
        event = next(row for row in response["history"] if row["record_type"] == "cartouche_event")
        self.assertEqual(event["id"], event_id)
        self.assertEqual(event["source_type"], "cartouche")
        self.assertEqual(event["status"], "triggered")
        self.assertEqual(event["operator"], "Operador Teste")


if __name__ == "__main__":
    unittest.main()
