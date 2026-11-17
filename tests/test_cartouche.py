import os
import tempfile
import unittest
import wave
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtMultimedia import QMediaDevices, QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from hitmo.database import Database
from hitmo.main_window import MainWindow
from hitmo.services import PlaybackHistoryService


class CartouchePersistenceTests(unittest.TestCase):
    def test_associations_keep_material_ids_and_removing_slot_keeps_original(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hitmo.sqlite3"
            db = Database(path)
            media_id = db.execute("INSERT INTO media(path,title,category) VALUES(?,?,?)", (str(Path(temp) / "source.wav"), "Original", "Música"))
            db.execute("INSERT INTO cartouche_slots(slot,media_id) VALUES(1,?)", (media_id,))
            db = Database(path)
            self.assertEqual(int(db.one("SELECT media_id FROM cartouche_slots WHERE slot=1")["media_id"]), media_id)
            db.execute("UPDATE cartouche_slots SET media_id=NULL WHERE slot=1")
            self.assertIsNone(db.one("SELECT media_id FROM cartouche_slots WHERE slot=1")["media_id"])
            self.assertEqual(db.one("SELECT id,title FROM media WHERE id=?", (media_id,))["title"], "Original")

    def test_cartouche_and_loop_routes_migrate_and_persist(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "hitmo.sqlite3")
            media_id = db.execute("INSERT INTO media(path,title,category) VALUES(?,?,?)", (str(Path(temp) / "route.wav"), "Route", "Vinheta"))
            db.execute("INSERT INTO cartouche_slots(slot,media_id,route) VALUES(1,?,'CUE')", (media_id,))
            db.execute("INSERT INTO loop_tracks(media_id,enabled,route) VALUES(?,1,'CUE')", (media_id,))
            reopened = Database(Path(temp) / "hitmo.sqlite3")
            self.assertEqual(reopened.one("SELECT route FROM cartouche_slots WHERE slot=1")["route"], "CUE")
            self.assertEqual(reopened.one("SELECT route FROM loop_tracks WHERE media_id=?", (media_id,))["route"], "CUE")

    def test_cartouche_history_is_in_operational_history_and_not_player_airplay_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "hitmo.sqlite3")
            media_id = db.execute("INSERT INTO media(path,title,category) VALUES(?,?,?)", (str(Path(temp) / "source.wav"), "Cart audio", "Vinheta"))
            event_id = db.execute(
                "INSERT INTO cartouche_history(slot,media_id_snapshot,title,path,category,operator,status) VALUES(1,?,?,?,?,?,'triggered')",
                (media_id, "Cart audio", str(Path(temp) / "source.wav"), "Vinheta", "Operador A"),
            )
            page = PlaybackHistoryService(db).query(limit=20)
            record = next(item for item in page["history"] if item["record_type"] == "cartouche_event")
            self.assertEqual(record["id"], event_id)
            self.assertEqual(record["source_type"], "cartouche")
            self.assertEqual(record["status"], "triggered")
            self.assertEqual(record["operator"], "Operador A")
            self.assertEqual(db.one("SELECT COUNT(*) AS n FROM playback_history")["n"], 0)


class CartoucheWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def write_audio(path: Path, seconds=3):
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b"\x01\x00" * (8000 * seconds))
        return path

    @staticmethod
    def wait_until(predicate, timeout_ms=4000):
        if predicate():
            return True
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(10)
        poll.timeout.connect(lambda: loop.quit() if predicate() else None)
        poll.start()
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()
        poll.stop()
        return bool(predicate())

    def test_shortcuts_f1_to_f12_and_cart_does_not_interrupt_program(self):
        if not QMediaDevices.audioOutputs():
            self.skipTest("Nenhuma saída de áudio disponível neste computador.")
        old_data_dir = os.environ.get("HITMO_DATA_DIR")
        with tempfile.TemporaryDirectory() as temp:
            os.environ["HITMO_DATA_DIR"] = temp
            program = self.write_audio(Path(temp) / "program.wav")
            cart_audio = self.write_audio(Path(temp) / "cart.wav")
            window = MainWindow()
            try:
                self.assertEqual(len(window.cart_slots), 12)
                self.assertEqual([item.key().toString() for item in window.cart_shortcuts], [f"F{i}" for i in range(1, 13)])
                window.show()
                program_id = window.db.execute("INSERT INTO media(path,title,category) VALUES(?,?,?)", (str(program), "PGM source", "Música"))
                cart_id = window.db.execute("INSERT INTO media(path,title,category) VALUES(?,?,?)", (str(cart_audio), "Cart source", "Vinheta"))
                window.db.execute("INSERT INTO cartouche_slots(slot,media_id) VALUES(1,?)", (cart_id,))
                window._refresh_cartouches()
                pgm = dict(window.db.one("SELECT * FROM media WHERE id=?", (program_id,)))
                self.assertTrue(window._play_media(pgm))
                self.assertTrue(self.wait_until(lambda: window.engine.program.playbackState() == QMediaPlayer.PlaybackState.PlayingState))
                QTest.keyClick(window, Qt.Key.Key_F1)
                self.assertTrue(self.wait_until(lambda: window.engine._cart_players[1][0].playbackState() == QMediaPlayer.PlaybackState.PlayingState))
                self.assertEqual(window.engine.program.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
                self.assertEqual(window._current["id"], program_id)
                self.assertFalse(window._fire_cartouche(1), "Disparo repetido deve ser recusado enquanto o cartucho toca.")
                cart_history = window.db.one("SELECT * FROM cartouche_history WHERE slot=1 ORDER BY id DESC LIMIT 1")
                self.assertEqual(cart_history["status"], "playing")
                self.assertEqual(cart_history["media_id_snapshot"], cart_id)
                self.assertFalse(window._fire_cartouche(2))
                self.assertEqual(window.db.one("SELECT status FROM cartouche_history WHERE id=?", (cart_history["id"],))["status"], "playing")
                window._stop_all_cartouches()
                self.assertEqual(window.db.one("SELECT status FROM cartouche_history WHERE id=?", (cart_history["id"],))["status"], "stopped")
                self.assertEqual(window.engine.program.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
                window._remove_cartouche(1)
                self.assertIsNone(window.db.one("SELECT media_id FROM cartouche_slots WHERE slot=1")["media_id"])
                self.assertIsNotNone(window.db.one("SELECT id FROM media WHERE id=?", (cart_id,)))
            finally:
                window.close()
                if old_data_dir is None:
                    os.environ.pop("HITMO_DATA_DIR", None)
                else:
                    os.environ["HITMO_DATA_DIR"] = old_data_dir

    def test_missing_cart_file_is_logged_as_failed_not_transmitted(self):
        old_data_dir = os.environ.get("HITMO_DATA_DIR")
        with tempfile.TemporaryDirectory() as temp:
            os.environ["HITMO_DATA_DIR"] = temp
            window = MainWindow()
            try:
                absent = str(Path(temp) / "absent.wav")
                media_id = window.db.execute("INSERT INTO media(path,title,category) VALUES(?,?,?)", (absent, "Absent", "Vinheta"))
                window.db.execute("INSERT INTO cartouche_slots(slot,media_id) VALUES(12,?)", (media_id,))
                self.assertFalse(window._fire_cartouche(12))
                row = window.db.one("SELECT status,error_message,started_at FROM cartouche_history WHERE slot=12 ORDER BY id DESC LIMIT 1")
                self.assertEqual(row["status"], "failed")
                self.assertIn("não encontrado", row["error_message"])
                self.assertEqual(row["started_at"], "")
            finally:
                window.close()
                if old_data_dir is None:
                    os.environ.pop("HITMO_DATA_DIR", None)
                else:
                    os.environ["HITMO_DATA_DIR"] = old_data_dir

    def test_natural_end_records_observed_start_and_end(self):
        if not QMediaDevices.audioOutputs():
            self.skipTest("Nenhuma saída de áudio disponível neste computador.")
        old_data_dir = os.environ.get("HITMO_DATA_DIR")
        with tempfile.TemporaryDirectory() as temp:
            os.environ["HITMO_DATA_DIR"] = temp
            window = MainWindow()
            try:
                audio = self.write_audio(Path(temp) / "short-cart.wav", seconds=1)
                media_id = window.db.execute("INSERT INTO media(path,title,category) VALUES(?,?,?)", (str(audio), "Fim natural", "Vinheta"))
                window.db.execute("INSERT INTO cartouche_slots(slot,media_id) VALUES(7,?)", (media_id,))
                self.assertTrue(window._fire_cartouche(7))
                self.assertTrue(self.wait_until(
                    lambda: window.db.one("SELECT status FROM cartouche_history WHERE slot=7 ORDER BY id DESC LIMIT 1")["status"] == "completed",
                    timeout_ms=5000,
                ))
                history = window.db.one("SELECT * FROM cartouche_history WHERE slot=7 ORDER BY id DESC LIMIT 1")
                self.assertTrue(history["started_at"])
                self.assertTrue(history["ended_at"])
                self.assertEqual(history["status"], "completed")
            finally:
                window.close()
                if old_data_dir is None:
                    os.environ.pop("HITMO_DATA_DIR", None)
                else:
                    os.environ["HITMO_DATA_DIR"] = old_data_dir

    def test_decoder_failure_is_logged_with_operator_and_without_air_confirmation(self):
        old_data_dir = os.environ.get("HITMO_DATA_DIR")
        with tempfile.TemporaryDirectory() as temp:
            os.environ["HITMO_DATA_DIR"] = temp
            window = MainWindow()
            try:
                corrupt = Path(temp) / "corrupt.wav"
                corrupt.write_bytes(b"not a decodable audio file")
                media_id = window.db.execute("INSERT INTO media(path,title,category) VALUES(?,?,?)", (str(corrupt), "Áudio inválido", "Vinheta"))
                window.db.execute("INSERT INTO cartouche_slots(slot,media_id) VALUES(8,?)", (media_id,))
                self.assertTrue(window._fire_cartouche(8))
                self.assertTrue(self.wait_until(
                    lambda: window.db.one("SELECT status FROM cartouche_history WHERE slot=8 ORDER BY id DESC LIMIT 1")["status"] == "failed",
                    timeout_ms=5000,
                ))
                history = window.db.one("SELECT * FROM cartouche_history WHERE slot=8 ORDER BY id DESC LIMIT 1")
                self.assertFalse(history["started_at"])
                self.assertTrue(history["ended_at"])
                self.assertTrue(history["error_message"])
                self.assertEqual(history["operator"], window.operator_combo.currentText())
            finally:
                window.close()
                if old_data_dir is None:
                    os.environ.pop("HITMO_DATA_DIR", None)
                else:
                    os.environ["HITMO_DATA_DIR"] = old_data_dir
