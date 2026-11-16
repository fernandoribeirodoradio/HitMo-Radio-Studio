import os
import tempfile
import unittest
import wave
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtMultimedia import QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import QApplication, QMessageBox

from hitmo.main_window import MainWindow
from hitmo.database import Database
from hitmo.operator_dialog import OperatorDialog


class MainWindowIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _write_test_audio(path: Path, seconds: float):
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b"\x01\x00" * int(8000 * seconds))
        return path

    @staticmethod
    def _wait_until(predicate, timeout_ms=5000):
        if predicate():
            return True
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(20)

        def check():
            if predicate():
                loop.quit()

        poll.timeout.connect(check)
        poll.start()
        QTimer.singleShot(timeout_ms, loop.quit)
        check()
        loop.exec()
        poll.stop()
        return bool(predicate())

    @staticmethod
    def _wait_ms(milliseconds):
        loop = QEventLoop()
        QTimer.singleShot(milliseconds, loop.quit)
        loop.exec()

    def _scheduled_transition_does_not_cut_current_item(self, current_category):
        if not QMediaDevices.audioOutputs():
            self.skipTest("Nenhuma saída de áudio disponível para executar o WAV de teste.")
        previous_data_dir = os.environ.get("HITMO_DATA_DIR")
        with tempfile.TemporaryDirectory() as temp:
            os.environ["HITMO_DATA_DIR"] = temp
            current_path = self._write_test_audio(Path(temp) / "current.wav", 2.0)
            scheduled_path = self._write_test_audio(Path(temp) / "scheduled.wav", 0.25)
            window = MainWindow()
            try:
                window.db.set_setting("auto_advance", "0")
                window.library.import_paths([current_path], category=current_category)
                window.library.import_paths([scheduled_path], category="Música")
                current = window.db.one("SELECT * FROM media WHERE path=?", (str(current_path.resolve()),))
                scheduled = window.db.one("SELECT * FROM media WHERE path=?", (str(scheduled_path.resolve()),))
                block_id = window.programming.add_block("Bloco novo", "Música", "12:30", [6])
                window.programming.add_block_item(block_id, int(scheduled["id"]))
                self.assertTrue(window._play_media(current, source_type="manual"))
                self.assertTrue(self._wait_until(lambda: window.engine.program.playbackState() == QMediaPlayer.PlaybackState.PlayingState))
                self._wait_ms(250)
                before_position = int(window.engine.program.position())
                self.assertGreater(before_position, 0)

                class ControlledClock:
                    @classmethod
                    def now(cls):
                        return datetime(2026, 11, 15, 12, 30, 15)

                with patch("hitmo.main_window.datetime", ControlledClock), patch.object(
                    window.engine, "stop_program", wraps=window.engine.stop_program
                ) as stop_program:
                    window._tick()
                self.assertEqual(stop_program.call_count, 0)
                self.assertEqual(window._current["title"], "current")
                self.assertEqual(window.engine.program.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
                self.assertGreaterEqual(int(window.engine.program.position()), before_position)
                pending = window.db.one("SELECT status FROM program_occurrence_items")
                self.assertEqual(pending["status"], "planned")
                self.assertEqual(window.db.one("SELECT status FROM playback_history WHERE title='current'")["status"], "playing")

                self.assertTrue(self._wait_until(lambda: window.db.one("SELECT status FROM playback_history WHERE title='current'")["status"] == "completed"))
                self.assertTrue(self._wait_until(lambda: window.db.one("SELECT status FROM program_occurrence_items")["status"] == "completed"))
                self.assertEqual(window.db.one("SELECT COUNT(*) AS n FROM playback_history")["n"], 2)
                self.assertEqual(window.db.one("SELECT status FROM playback_history WHERE title='current'")["status"], "completed")
                self.assertEqual(window.db.one("SELECT status FROM program_occurrence_items")["status"], "completed")
            finally:
                window.engine.stop_program()
                window.engine.eject_program()
                window.engine.stop_preview()
                window.engine.stop_loop()
                self._wait_until(
                    lambda: window.engine.program.mediaStatus() == QMediaPlayer.MediaStatus.NoMedia,
                    timeout_ms=2000,
                )
                self.app.processEvents()
                window.close()
                window.deleteLater()
                self.app.processEvents()
        if previous_data_dir is None:
            os.environ.pop("HITMO_DATA_DIR", None)
        else:
            os.environ["HITMO_DATA_DIR"] = previous_data_dir

    def test_new_scheduled_music_waits_for_current_music_to_finish(self):
        self._scheduled_transition_does_not_cut_current_item("Música")

    def test_new_scheduled_block_waits_for_current_commercial_to_finish(self):
        self._scheduled_transition_does_not_cut_current_item("Comercial")

    def test_missing_scheduled_file_is_recorded_failed_once(self):
        previous_data_dir = os.environ.get("HITMO_DATA_DIR")
        with tempfile.TemporaryDirectory() as temp:
            os.environ["HITMO_DATA_DIR"] = temp
            window = MainWindow()
            try:
                missing = Path(temp) / "offline" / "missing.wav"
                media_id = window.db.execute("INSERT INTO media(path,title,category) VALUES(?,?,?)", (str(missing), "Ausente", "Música"))
                block_id = window.programming.add_block("Arquivo indisponível", "Música", "12:30", [6])
                window.programming.add_block_item(block_id, media_id)

                class ControlledClock:
                    @classmethod
                    def now(cls):
                        return datetime(2026, 11, 15, 12, 30, 15)

                with patch("hitmo.main_window.datetime", ControlledClock), patch.object(QMessageBox, "warning"):
                    window._tick()
                    self._wait_until(lambda: window.db.one("SELECT status FROM program_occurrence_items")["status"] == "failed")
                self.assertEqual(window.db.one("SELECT COUNT(*) AS n FROM playback_history WHERE source_type='scheduled_block'")["n"], 1)
                self.assertEqual(window.db.one("SELECT status,error_message FROM playback_history")["status"], "failed")
                self.assertEqual(window.db.one("SELECT COUNT(*) AS n FROM program_occurrences")["n"], 1)
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()
        if previous_data_dir is None:
            os.environ.pop("HITMO_DATA_DIR", None)
        else:
            os.environ["HITMO_DATA_DIR"] = previous_data_dir

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

