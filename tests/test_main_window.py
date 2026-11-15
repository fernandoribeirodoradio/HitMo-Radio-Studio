import os
import tempfile
import unittest
import wave
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import QApplication

from hitmo.main_window import MainWindow
from hitmo.database import Database
from hitmo.operator_dialog import OperatorDialog


class MainWindowIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_program_playback_is_written_to_history(self):
        if not QMediaDevices.audioOutputs():
            self.skipTest("Nenhuma saída de áudio disponível neste computador.")
        previous_data_dir = os.environ.get("HITMO_DATA_DIR")

        def restore_data_dir():
            if previous_data_dir is None:
                os.environ.pop("HITMO_DATA_DIR", None)
            else:
                os.environ["HITMO_DATA_DIR"] = previous_data_dir

        self.addCleanup(restore_data_dir)
        with tempfile.TemporaryDirectory() as temp:
            os.environ["HITMO_DATA_DIR"] = temp
            path = Path(temp) / "jingle.wav"
            with wave.open(str(path), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(8000)
                stream.writeframes(b"\x00\x00" * 2000)

            window = MainWindow()
            window.db.set_setting("auto_advance", "0")
            window.library.import_paths([path])
            media = window.db.one("SELECT * FROM media WHERE path=?", (str(path.resolve()),))
            finished = QEventLoop()
            window.engine.program_finished.connect(finished.quit)
            QTimer.singleShot(4000, finished.quit)
            window._play_media(media)
            finished.exec()
            history = window.db.all("SELECT title,operator,played_seconds FROM playback_history")
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["title"], "jingle")
            self.assertEqual(history[0]["operator"], "Operador padrão")
            self.assertGreaterEqual(float(history[0]["played_seconds"]), 0)

            now = datetime.now()
            media_id = int(media["id"])
            window.timeline.add(now.strftime("%H:%M"), [now.weekday()], "Evento A", media_id, "Comercial")
            window.timeline.add(now.strftime("%H:%M"), [now.weekday()], "Evento B", media_id, "Música")
            sequence = QEventLoop()
            completed = []

            def on_finished():
                completed.append(True)
                if len(completed) == 2:
                    sequence.quit()

            window.engine.program_finished.connect(on_finished)
            QTimer.singleShot(4000, sequence.quit)
            window._tick()
            self.assertEqual(int(window.db.one("SELECT COUNT(*) AS n FROM program_occurrence_items WHERE status='planned'")["n"]), 1)
            sequence.exec()
            self.assertEqual(len(completed), 2, "Os eventos da timeline não foram reproduzidos em sequência.")
            self.assertEqual(len(window.db.all("SELECT id FROM playback_history")), 3)
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_operator_dialog_opens_with_seeded_operator(self):
        with tempfile.TemporaryDirectory() as temp:
            dialog = OperatorDialog(Database(Path(temp) / "operators.sqlite3"))
            self.assertEqual(dialog.list.count(), 1)
            self.assertIn("Operador padrão", dialog.list.item(0).text())
            dialog.close()

    def test_central_service_controls_exist_without_server(self):
        previous_data_dir = os.environ.get("HITMO_DATA_DIR")
        def restore_data_dir():
            if previous_data_dir is None:
                os.environ.pop("HITMO_DATA_DIR", None)
            else:
                os.environ["HITMO_DATA_DIR"] = previous_data_dir
        self.addCleanup(restore_data_dir)
        with tempfile.TemporaryDirectory() as temp:
            os.environ["HITMO_DATA_DIR"] = temp
            window = MainWindow()
            self.assertIn("offline", window.api_status_label.text())
            service_menu = next(action.menu() for action in window.menuBar().actions() if action.text() == "Serviço central")
            actions = [action.text() for action in service_menu.actions()]
            self.assertIn("Ativar serviço local…", actions)
            self.assertIn("Clientes autenticados recentes…", actions)
            window.close()
            window.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()

