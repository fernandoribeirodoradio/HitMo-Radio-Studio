import os
import tempfile
import unittest
import wave
from datetime import datetime, timedelta
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
    def _wait_for_player_position(player, errors, timeout_ms=1500):
        """Wait for a real position update, or fail fast on a media error."""
        if player.position() > 0 or errors:
            return int(player.position())
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)

        def on_position(position):
            if position > 0:
                loop.quit()

        def on_error(error, message):
            errors.append((error, message))
            loop.quit()

        player.positionChanged.connect(on_position)
        player.errorOccurred.connect(on_error)
        timer.timeout.connect(loop.quit)
        timer.start(timeout_ms)
        # Recheck after connecting to avoid missing a position event between
        # the initial read and signal registration.
        if player.position() <= 0 and not errors:
            loop.exec()
        player.positionChanged.disconnect(on_position)
        player.errorOccurred.disconnect(on_error)
        return int(player.position())

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
                # The scheduling clock is injected below; stop its wall-clock
                # timer so the test does not simulate a day-long clock jump.
                window._timer.stop()
                window.db.set_setting("auto_advance", "0")
                window.library.import_paths([current_path], category=current_category)
                window.library.import_paths([scheduled_path], category="Música")
                current = window.db.one("SELECT * FROM media WHERE path=?", (str(current_path.resolve()),))
                scheduled = window.db.one("SELECT * FROM media WHERE path=?", (str(scheduled_path.resolve()),))
                block_id = window.programming.add_block("Bloco novo", "Música", "12:30", [6])
                window.programming.add_block_item(block_id, int(scheduled["id"]))
                player = window.engine.program
                media_statuses = []
                playback_states = []
                playback_errors = []
                player.mediaStatusChanged.connect(media_statuses.append)
                player.playbackStateChanged.connect(playback_states.append)
                player.errorOccurred.connect(lambda error, message: playback_errors.append((error, message)))
                self.assertTrue(window._play_media(current, source_type="manual"))
                started = self._wait_until(lambda: player.playbackState() == QMediaPlayer.PlaybackState.PlayingState, timeout_ms=2000)
                self.assertTrue(
                    started,
                    f"Player não iniciou: status={media_statuses}, estados={playback_states}, erros={playback_errors}",
                )
                before_position = self._wait_for_player_position(player, playback_errors)
                self.assertFalse(
                    playback_errors,
                    f"Erro do QMediaPlayer antes do avanço: {playback_errors}; status={media_statuses}",
                )
                self.assertGreater(
                    before_position,
                    0,
                    f"Player iniciou, mas a posição não avançou: status={media_statuses}, estados={playback_states}",
                )

                class ControlledClock:
                    @classmethod
                    def now(cls):
                        return datetime(2026, 11, 15, 12, 30, 15)

                # MainWindow's 500 ms timer can tick before this controlled-clock
                # assertion; seed its persistent cursor to the same test timeline.
                window.db.set_setting("scheduler_last_checked_at", "2026-11-15T12:29:59")
                window._scheduler_first_tick = False
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

    def test_missed_schedule_does_not_interrupt_current_pgm(self):
        if not QMediaDevices.audioOutputs():
            self.skipTest("Nenhuma saída de áudio disponível para executar o WAV de teste.")
        previous_data_dir = os.environ.get("HITMO_DATA_DIR")
        with tempfile.TemporaryDirectory() as temp:
            os.environ["HITMO_DATA_DIR"] = temp
            current_path = self._write_test_audio(Path(temp) / "current-missed.wav", 4.0)
            scheduled_path = self._write_test_audio(Path(temp) / "scheduled-missed.wav", 0.25)
            window = MainWindow()
            try:
                window._timer.stop()
                window.db.set_setting("auto_advance", "0")
                window.library.import_paths([current_path], category="Música")
                window.library.import_paths([scheduled_path], category="Música")
                current = window.db.one("SELECT * FROM media WHERE path=?", (str(current_path.resolve()),))
                scheduled = window.db.one("SELECT * FROM media WHERE path=?", (str(scheduled_path.resolve()),))
                block_id = window.programming.add_block("Atrasado", "Música", "12:00", [6])
                window.programming.add_block_item(block_id, int(scheduled["id"]))
                window.db.set_setting("scheduler_last_checked_at", "2026-11-15T11:59:59")
                window._scheduler_first_tick = False
                self.assertTrue(window._play_media(current, source_type="manual"))
                self.assertTrue(self._wait_until(lambda: window.engine.program.position() > 0))
                with patch.object(window.engine, "stop_program", wraps=window.engine.stop_program) as stop_program:
                    window._tick(datetime(2026, 11, 15, 12, 1, 0))
                self.assertEqual(stop_program.call_count, 0)
                self.assertEqual(window._current["title"], "current-missed")
                self.assertEqual(window.engine.program.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
                occurrence = window.db.one("SELECT status FROM program_occurrences WHERE block_id=?", (block_id,))
                item = window.db.one("SELECT status,error_message FROM program_occurrence_items WHERE occurrence_id=(SELECT id FROM program_occurrences WHERE block_id=?)", (block_id,))
                self.assertEqual(occurrence["status"], "missed")
                self.assertEqual(item["status"], "ignored")
                self.assertIn("perdido", item["error_message"])
            finally:
                window.engine.stop_program()
                window.engine.eject_program()
                self._wait_until(lambda: window.engine.program.mediaStatus() == QMediaPlayer.MediaStatus.NoMedia, 2000)
                window.close()
                window.deleteLater()
                self.app.processEvents()
        if previous_data_dir is None:
            os.environ.pop("HITMO_DATA_DIR", None)
        else:
            os.environ["HITMO_DATA_DIR"] = previous_data_dir

    def test_clock_rollback_does_not_arm_a_fixed_cut_before_high_water_mark(self):
        previous_data_dir = os.environ.get("HITMO_DATA_DIR")
        with tempfile.TemporaryDirectory() as temp:
            os.environ["HITMO_DATA_DIR"] = temp
            audio_path = self._write_test_audio(Path(temp) / "fixed-rollback.wav", 1.0)
            window = MainWindow()
            try:
                window._timer.stop()
                window.library.import_paths([audio_path], category="Comercial")
                media_id = int(window.db.one("SELECT id FROM media")["id"])
                block_id = window.programming.add_block(
                    "Fixo futuro", "Comercial", "12:30", [6], transition_policy="fixed",
                )
                window.programming.add_block_item(block_id, media_id)
                window.db.set_setting("scheduler_last_checked_at", "2026-11-15T12:05:00")

                window._schedule_fixed_transition(datetime(2026, 11, 15, 12, 0, 0))

                self.assertIsNone(window._fixed_transition_target)
                self.assertFalse(window._fixed_transition_timer.isActive())
                self.assertFalse(window.engine.program_fade_active)
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()
        if previous_data_dir is None:
            os.environ.pop("HITMO_DATA_DIR", None)
        else:
            os.environ["HITMO_DATA_DIR"] = previous_data_dir

    def test_fixed_transition_fades_current_program_and_records_interruption_reason(self):
        if not QMediaDevices.audioOutputs():
            self.skipTest("Nenhuma saída de áudio disponível para executar o WAV de teste.")
        previous_data_dir = os.environ.get("HITMO_DATA_DIR")
        with tempfile.TemporaryDirectory() as temp:
            os.environ["HITMO_DATA_DIR"] = temp
            current_path = self._write_test_audio(Path(temp) / "current-fixed.wav", 2.0)
            window = MainWindow()
            try:
                window.db.set_setting("auto_advance", "0")
                window.library.import_paths([current_path], category="Música")
                current = window.db.one("SELECT * FROM media WHERE path=?", (str(current_path.resolve()),))
                block_id = window.programming.add_block("Música no ar", "Música", "12:00", [6])
                occurrence_id = window.db.execute(
                    "INSERT INTO program_occurrences(origin_type,block_id,scheduled_for,status) VALUES('block',?,'2026-11-15 12:00','running')",
                    (block_id,),
                )
                occurrence_item_id = window.db.execute(
                    "INSERT INTO program_occurrence_items(occurrence_id,media_id,position,title,path,category,status) VALUES(?,?,0,?,?,?,'playing')",
                    (occurrence_id, int(current["id"]), current["title"], current["path"], current["category"]),
                )
                self.assertTrue(window._play_media(
                    current, source_type="scheduled_block", source_ref=occurrence_item_id,
                    occurrence_item_id=occurrence_item_id, scheduled_for="2026-11-15 12:00",
                ))
                self.assertTrue(self._wait_until(lambda: window.engine.program.position() > 0))
                window._fixed_transition_due = True
                window._fixed_transition_target = datetime(2026, 11, 15, 12, 1)
                window._fixed_transition_fade_ms = 150
                window.engine.fade_out_program(150)
                self.assertTrue(self._wait_until(
                    lambda: window.db.one("SELECT status FROM playback_history WHERE title=?", (current["title"],))["status"] == "interrupted"
                ))
                history = window.db.one(
                    "SELECT played_seconds,error_message FROM playback_history WHERE title=?", (current["title"],)
                )
                item = window.db.one("SELECT status,error_message FROM program_occurrence_items WHERE id=?", (occurrence_item_id,))
                self.assertGreater(float(history["played_seconds"]), 0)
                self.assertIn("Fixo", history["error_message"])
                self.assertEqual(item["status"], "interrupted")
                self.assertIn("Fixo", item["error_message"])
            finally:
                window.engine.stop_program()
                window.engine.eject_program()
                self._wait_until(lambda: window.engine.program.mediaStatus() == QMediaPlayer.MediaStatus.NoMedia, 2000)
                window.close()
                window.deleteLater()
                self.app.processEvents()
        if previous_data_dir is None:
            os.environ.pop("HITMO_DATA_DIR", None)
        else:
            os.environ["HITMO_DATA_DIR"] = previous_data_dir

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
            scheduled_at = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
            media_id = int(media["id"])
            window._timer.stop()
            window.timeline.add(scheduled_at.strftime("%H:%M"), [scheduled_at.weekday()], "Evento A", media_id, "Comercial")
            window.timeline.add(scheduled_at.strftime("%H:%M"), [scheduled_at.weekday()], "Evento B", media_id, "Música")
            sequence = QEventLoop()
            completed = []

            def on_finished():
                completed.append(True)
                if len(completed) == 2:
                    sequence.quit()

            window.engine.program_finished.connect(on_finished)
            QTimer.singleShot(4000, sequence.quit)
            window._tick(scheduled_at)
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

    def test_playback_history_captures_occurrence_block_and_material_snapshots(self):
        previous_data_dir = os.environ.get("HITMO_DATA_DIR")
        with tempfile.TemporaryDirectory() as temp:
            os.environ["HITMO_DATA_DIR"] = temp
            window = MainWindow()
            try:
                window._timer.stop()
                now = datetime(2026, 11, 15, 12, 0)
                media_id = window.db.execute(
                    "INSERT INTO media(path,title,artist,category) VALUES(?,?,?,?)",
                    ("C:/shared/snapshot.wav", "Título original", "Artista original", "Música"),
                )
                block_id = window.programming.add_block("Bloco original", "Música", "12:00", [6])
                window.programming.add_block_item(block_id, media_id)
                occurrence_id = window.programming.due(now)[0]
                item = window.db.one("SELECT * FROM program_occurrence_items WHERE occurrence_id=?", (occurrence_id,))
                row = dict(item)
                row["id"] = media_id
                history_id = window._insert_playback_history(
                    row, status="loading", source_type="scheduled_block", source_ref=int(item["id"]),
                    scheduled_for=now.strftime("%Y-%m-%d %H:%M"), occurrence_item_id=int(item["id"]),
                )

                window.db.execute("UPDATE program_blocks SET name='Bloco renomeado' WHERE id=?", (block_id,))
                window.db.execute("UPDATE media SET title='Título novo',category='Comercial' WHERE id=?", (media_id,))
                history = window.history.query(material=str(media_id))["history"][0]

                self.assertEqual(history["id"], history_id)
                self.assertEqual(history["material_id"], media_id)
                self.assertEqual((history["title"], history["artist"], history["category"]),
                                 ("Título original", "Artista original", "Música"))
                self.assertEqual((history["occurrence_id"], history["block_id"], history["block_name_snapshot"]),
                                 (occurrence_id, block_id, "Bloco original"))
                self.assertEqual(history["status"], "loading")
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()
        if previous_data_dir is None:
            os.environ.pop("HITMO_DATA_DIR", None)
        else:
            os.environ["HITMO_DATA_DIR"] = previous_data_dir

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

