import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from datetime import date, datetime
from pathlib import Path
from urllib.request import urlopen

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from hitmo.api_client import HitMoAPIClient
from hitmo.api_service import create_api_user
from hitmo.database import Database
from hitmo.programming_manager_app import ProgrammingManagerWindow
from hitmo.services import ProgrammingService


class ProgrammingManagerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.previous_data_dir = os.environ.get("HITMO_DATA_DIR")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        os.environ["HITMO_DATA_DIR"] = str(self.root)
        self.db = Database()
        self.password = "RadioStudio#2026"
        create_api_user(self.db, "program-editor", self.password, "admin")
        self.music_id = self._add_media("music.wav", "No ar", "Música")
        self.commercial_id = self._add_media("spot.wav", "Oferta", "Comercial")
        self.jingle_id = self._add_media("jingle.wav", "Assinatura", "Vinheta")
        self.programming = ProgrammingService(self.db)
        self.active_block_id = self.programming.add_block("Bloco atual", "Música", "08:00", list(range(7)))
        self.active_item_id = self.programming.add_block_item(self.active_block_id, self.music_id)
        self.occurrence_id = self.db.execute(
            "INSERT INTO program_occurrences(origin_type,block_id,scheduled_for,status,operator) VALUES('block',?,?, 'running','Operador do ar')",
            (self.active_block_id, date.today().strftime("%Y-%m-%d") + " 08:00"),
        )
        self.db.execute(
            "INSERT INTO program_occurrence_items(occurrence_id,source_item_id,media_id,position,title,path,category,status,started_at) VALUES(?,?,?,?,?,?,?,'playing',CURRENT_TIMESTAMP)",
            (self.occurrence_id, self.active_item_id, self.music_id, 0, "Snapshot no ar", str(self.root / "music.wav"), "Música"),
        )
        self.db.execute(
            "INSERT INTO playback_history(media_id,title,path,operator,status) VALUES(?,?,?,?,'playing')",
            (self.music_id, "Snapshot no ar", str(self.root / "music.wav"), "Operador do ar"),
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path.cwd()) + os.pathsep + env.get("PYTHONPATH", "")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "hitmo.server", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=Path.cwd(), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._wait_for_server()
        self.api = HitMoAPIClient(f"http://127.0.0.1:{self.port}", "program-editor", self.password, timeout=2)
        self.identity = self.api.version()
        self.windows = []

    def _add_media(self, filename: str, title: str, category: str) -> int:
        path = self.root / filename
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b"\x00\x00" * 300)
        return self.db.execute("INSERT INTO media(path,title,category) VALUES(?,?,?)", (str(path), title, category))

    def _wait_for_server(self):
        endpoint = f"http://127.0.0.1:{self.port}/healthz"
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.fail(f"API encerrou antes de iniciar: {self.process.returncode}")
            try:
                with urlopen(endpoint, timeout=0.3) as response:
                    if response.status == 200:
                        return
            except OSError:
                time.sleep(0.05)
        self.fail("API não ficou disponível em loopback")

    def tearDown(self):
        for window in getattr(self, "windows", []):
            window.close()
            window.deleteLater()
        if getattr(self, "process", None) and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=4)
        if self.previous_data_dir is None:
            os.environ.pop("HITMO_DATA_DIR", None)
        else:
            os.environ["HITMO_DATA_DIR"] = self.previous_data_dir
        self.temp.cleanup()
        self.app.processEvents()

    def test_remote_draft_publishes_without_changing_running_occurrence(self):
        manager = ProgrammingManagerWindow(self.api, self.identity)
        self.windows.append(manager)
        self.assertEqual(manager.saved_blocks[0]["id"], self.active_block_id)
        original_revision = manager.saved_revision

        manager.update_block_data(0, {
            "name": "Bloco atualizado remotamente",
            "block_type": "Música",
            "time_of_day": "08:05",
            "days": list(range(7)),
            "enabled": True,
        })
        manager.add_block_data({
            "name": "Comercial das dez",
            "block_type": "Comercial",
            "time_of_day": "10:00",
            "days": list(range(7)),
            "enabled": True,
        })
        manager.add_material_to_block(self.commercial_id)
        manager.add_material_to_block(self.jingle_id)
        manager.move_item_at(1, -1)
        self.assertEqual(manager.blocks[1]["items"], [self.jingle_id, self.commercial_id])

        manager.save_draft("Grade remota de teste")
        self.assertEqual(self.api.programs()["saved_revision"], original_revision)
        self.assertEqual(len(self.api.programs()["blocks"]), 1)
        saved_draft = self.api.get_draft(manager.draft_id)
        self.assertEqual(saved_draft["payload"]["blocks"][1]["items"], [self.jingle_id, self.commercial_id])

        manager.publish_draft(confirmed=True)
        published = self.api.programs()
        self.assertGreater(published["saved_revision"], original_revision)
        self.assertEqual(len(published["blocks"]), 2)
        self.assertEqual(published["blocks"][0]["id"], self.active_block_id)
        self.assertEqual(published["blocks"][0]["name"], "Bloco atualizado remotamente")
        self.assertEqual([item["media_id"] for item in published["blocks"][1]["items"]], [self.jingle_id, self.commercial_id])

        occurrence = self.db.one("SELECT status FROM program_occurrences WHERE id=?", (self.occurrence_id,))
        item = self.db.one("SELECT title,path,status FROM program_occurrence_items WHERE occurrence_id=?", (self.occurrence_id,))
        history = self.db.one("SELECT title,operator,status FROM playback_history WHERE media_id=?", (self.music_id,))
        self.assertEqual(occurrence["status"], "running")
        self.assertEqual((item["title"], item["path"], item["status"]), ("Snapshot no ar", str(self.root / "music.wav"), "playing"))
        self.assertEqual((history["title"], history["operator"], history["status"]), ("Snapshot no ar", "Operador do ar", "playing"))

        manager.occurrence_start.setDate(QDate.currentDate())
        manager.occurrence_end.setDate(QDate.currentDate())
        manager.refresh_occurrences()
        manager.refresh_history()
        self.assertGreaterEqual(manager.occurrence_table.rowCount(), 1)
        self.assertGreaterEqual(manager.history_table.rowCount(), 1)

    def test_remote_programming_publishes_folder_selector_and_server_records_selected_media(self):
        folder_path = self.root / "rotation-folder"
        folder_path.mkdir()
        music_category_id = int(self.db.one("SELECT id FROM material_categories WHERE name='Música'")["id"])
        folder_id = self.db.execute(
            "INSERT INTO media_folders(name,path,category_id) VALUES(?,?,?)",
            ("Rotação de teste", str(folder_path), music_category_id),
        )
        first_id = self._add_media("Alpha.wav", "Alpha", "Música")
        second_id = self._add_media("Bravo.wav", "Bravo", "Música")
        self.db.execute("UPDATE media SET folder_id=? WHERE id IN (?,?)", (folder_id, first_id, second_id))

        manager = ProgrammingManagerWindow(self.api, self.identity)
        self.windows.append(manager)
        manager.add_block_data({
            "name": "Seleção por pasta",
            "block_type": "Música",
            "time_of_day": "10:15",
            "days": list(range(7)),
            "enabled": True,
        })
        manager.blocks_table.selectRow(len(manager.blocks) - 1)
        manager.add_folder_to_block(folder_id, "sequential")
        manager.save_draft("Rascunho por pasta")
        draft = self.api.get_draft(manager.draft_id)
        self.assertEqual(draft["payload"]["blocks"][-1]["items"], [{"folder_id": folder_id, "selection_mode": "sequential"}])

        manager.publish_draft(confirmed=True)
        program = self.api.programs()
        selector = program["blocks"][-1]["items"][0]
        self.assertEqual((int(selector["folder_id"]), selector["selection_mode"]), (folder_id, "sequential"))

        occurrence_id = ProgrammingService(self.db).due(datetime(2026, 11, 16, 10, 15))[0]
        selected = self.db.one("SELECT media_id,title,selection_folder_id,selection_mode,selected_at FROM program_occurrence_items WHERE occurrence_id=?", (occurrence_id,))
        self.assertEqual((int(selected["media_id"]), selected["title"]), (first_id, "Alpha"))
        self.assertEqual((int(selected["selection_folder_id"]), selected["selection_mode"]), (folder_id, "sequential"))
        self.assertTrue(selected["selected_at"])


if __name__ == "__main__":
    unittest.main()
