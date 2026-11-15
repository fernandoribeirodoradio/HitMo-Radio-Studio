import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from urllib.request import urlopen

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from hitmo.api_client import HitMoAPIClient
from hitmo.api_service import create_api_user
from hitmo.config_manager_app import ConfigManagerWindow
from hitmo.database import Database
from hitmo.main_window import MainWindow
from hitmo.services import ProgrammingService


class ConfigManagerStudioIntegrationTests(unittest.TestCase):
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
        create_api_user(self.db, "config-admin", self.password, "admin")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path.cwd()) + os.pathsep + env.get("PYTHONPATH", "")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "hitmo.server", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=Path.cwd(), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        endpoint = f"http://127.0.0.1:{self.port}/healthz"
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.fail(f"API encerrou antes de iniciar: {self.process.returncode}")
            try:
                with urlopen(endpoint, timeout=0.3) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("API não ficou disponível em loopback")
        self.api = HitMoAPIClient(f"http://127.0.0.1:{self.port}", "config-admin", self.password, timeout=2)
        self.identity = self.api.version()

    def tearDown(self):
        for window in getattr(self, "windows", []):
            window.close()
            window.deleteLater()
        if self.process and self.process.poll() is None:
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

    def test_standalone_catalog_updates_same_ids_while_studio_is_open(self):
        self.windows = []
        manager = ConfigManagerWindow(self.api, self.identity, self.root / "Atalhos")
        studio = MainWindow()
        self.windows.extend((manager, studio))
        self.assertEqual(studio.library_list.count(), 0)
        media_path = self.root / "shared" / "station.wav"
        media_path.parent.mkdir()
        with wave.open(str(media_path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(b"\x00\x00" * 300)
        category_id = int(self.db.one("SELECT id FROM material_categories WHERE name='Música'")["id"])
        folder_id = self.db.execute("INSERT INTO media_folders(name,path,category_id) VALUES(?,?,?)", ("Biblioteca", str(media_path.parent), category_id))
        created = manager.api.add_material(
            {"path": str(media_path), "title": "Estação", "artist": "Equipe", "category": "Música", "folder_id": folder_id},
            manager.api.version()["catalog_revision"],
        )
        manager.refresh()
        self.assertEqual(manager.material_table.rowCount(), 1)
        self.assertEqual(manager.material_table.item(0, 0).text(), str(created["id"]))
        manager.material_table.selectRow(0)
        self.assertEqual(manager._selected_material()["id"], created["id"])
        manager.material_table.clearSelection()
        self.assertTrue(manager.materials[0]["path_exists_on_server"])
        manager.folder_filter.setCurrentIndex(manager.folder_filter.findData(folder_id))
        self.assertEqual(manager.material_table.rowCount(), 1)
        manager.folder_filter.setCurrentIndex(0)
        self.assertTrue((self.root / "Atalhos" / f"material-{created['id']}.hitmo.json").is_file())
        block_id = ProgrammingService(self.db).add_block("Bloco existente", "Música", "11:00", [0, 1, 2, 3, 4, 5, 6])
        ProgrammingService(self.db).add_block_item(block_id, created["id"])
        occurrence_id = self.db.execute(
            "INSERT INTO program_occurrences(origin_type,block_id,scheduled_for,status) VALUES('block',?,'2026-11-15 11:00','running')",
            (block_id,),
        )

        moved_path = self.root / "offline-share" / "station-renamed.wav"
        manager.api.update_material(
            created["id"],
            {"path": str(moved_path), "title": "Estação renomeada", "artist": "Equipe", "category": "Música"},
            manager.api.version()["catalog_revision"],
        )
        manager.refresh()
        wait = QEventLoop()
        poll = QTimer()
        poll.timeout.connect(studio._poll_catalog_revision)
        poll.timeout.connect(lambda: wait.quit() if studio.library_list.count() == 1 else None)
        poll.start(50)
        QTimer.singleShot(2500, wait.quit)
        wait.exec()
        self.assertEqual(studio.library_list.count(), 1)
        self.assertIn("Estação renomeada", studio.library_list.item(0).text())
        self.assertEqual(manager.materials[0]["id"], created["id"])
        self.assertEqual(manager.materials[0]["path"], str(moved_path.resolve()))
        self.assertFalse(manager.materials[0]["path_exists_on_server"])

        history_id = self.db.execute(
            "INSERT INTO playback_history(media_id,title,path,operator) VALUES(?,?,?,?)",
            (created["id"], "Estação", str(media_path), "Operador padrão"),
        )
        manager.api.deactivate_material(created["id"], manager.api.version()["catalog_revision"])
        self.assertEqual(self.db.one("SELECT id FROM playback_history WHERE id=?", (history_id,))["id"], history_id)
        self.assertEqual(self.db.one("SELECT status FROM program_occurrences WHERE id=?", (occurrence_id,))["status"], "running")
        self.assertEqual(self.db.one("SELECT media_id FROM program_block_items WHERE block_id=?", (block_id,))["media_id"], created["id"])
        manager.api.restore_material(created["id"], manager.api.version()["catalog_revision"])
        self.assertEqual(self.db.one("SELECT active FROM media WHERE id=?", (created["id"],))["active"], 1)
        poll.stop()

    def test_disconnection_is_reported_and_refresh_reconnects(self):
        self.windows = []
        manager = ConfigManagerWindow(self.api, self.identity, self.root / "Atalhos")
        self.windows.append(manager)
        self.process.terminate()
        self.process.wait(timeout=4)

        def dismiss_error():
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, QMessageBox):
                    widget.accept()

        QTimer.singleShot(100, dismiss_error)
        manager.refresh()
        self.assertIn("Desconectado", manager.status.text())

        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path.cwd()) + os.pathsep + env.get("PYTHONPATH", "")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "hitmo.server", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=Path.cwd(), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        endpoint = f"http://127.0.0.1:{self.port}/healthz"
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                with urlopen(endpoint, timeout=0.3) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("API não reiniciou para validar reconexão")
        manager.refresh()
        self.assertIn("Catálogo sincronizado", manager.status.text())


if __name__ == "__main__":
    unittest.main()
