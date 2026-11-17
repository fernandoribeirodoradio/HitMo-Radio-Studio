import os
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudioFormat, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import QApplication

from hitmo.audio_engine import AudioEngine
from hitmo.audio_mixer import CHANNELS, SAMPLE_RATE
from hitmo.database import Database
from hitmo.main_window import MainWindow


class FakeDevice:
    def __init__(self, ident):
        self.ident = ident

    def id(self):
        return self.ident.encode()

    def isNull(self):
        return False

    def isFormatSupported(self, _fmt):
        return True

    def preferredFormat(self):
        fmt = QAudioFormat()
        fmt.setSampleRate(SAMPLE_RATE)
        fmt.setChannelCount(CHANNELS)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
        return fmt


class DrainingFakeSink(QObject):
    """Test sink boundary; it exposes state but does not claim audible output."""
    stateChanged = Signal(object)

    def __init__(self, _device, _fmt, _parent):
        super().__init__(_parent)
        from PySide6.QtMultimedia import QAudio
        self._error = QAudio.Error.NoError

    def setBufferSize(self, _size):
        pass

    def start(self, source):
        self.source = source

    def state(self):
        from PySide6.QtMultimedia import QAudio
        return QAudio.State.StoppedState

    def error(self):
        from PySide6.QtMultimedia import QAudio
        return QAudio.Error.NoError

    def stop(self):
        pass


class AudioStabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        if hasattr(self, "_previous_data_dir"):
            if self._previous_data_dir is None:
                os.environ.pop("HITMO_DATA_DIR", None)
            else:
                os.environ["HITMO_DATA_DIR"] = self._previous_data_dir

    @staticmethod
    def write_wav(path: Path, seconds=2.0):
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b"\x01\x00" * int(8000 * seconds))
        return path

    @staticmethod
    def wait_until(predicate, timeout_ms=5000):
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

    def test_three_hour_equivalent_pcm_render_soak_keeps_queue_and_players_bounded(self):
        devices = [FakeDevice("pgm")]
        engine = AudioEngine("pgm", "", device_provider=lambda: devices,
                             sink_factory=DrainingFakeSink)
        rendered_seconds = 0
        block = np.full((SAMPLE_RATE, CHANNELS), 0.05, dtype=np.float32)
        queue = engine._channels["program"]["queue"]
        try:
            initial_channel_count = len(engine._channels)
            for _ in range(3 * 60 * 60):
                queue.push(block)
                output = engine._render_bus("PGM", SAMPLE_RATE)
                self.assertEqual(queue.queued_frames, 0)
                self.assertEqual(output.shape, (SAMPLE_RATE, CHANNELS))
                self.assertTrue(np.isfinite(output).all())
                self.assertLessEqual(float(np.max(np.abs(output))), 0.98)
                rendered_seconds += 1
            self.assertEqual(rendered_seconds, 10800)
            self.assertEqual(len(engine._channels), initial_channel_count)
            self.assertEqual(engine._loop_players, {})
            self.assertEqual(engine._cart_active, set())
        finally:
            engine.shutdown()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()

    def test_repeated_loop_preview_and_cart_cycles_release_dynamic_players(self):
        outputs = QMediaDevices.audioOutputs()
        if not outputs:
            self.skipTest("Nenhuma saída de áudio disponível para decodificação Qt.")
        with tempfile.TemporaryDirectory() as temp:
            loop_a = self.write_wav(Path(temp) / "loop-a.wav")
            loop_b = self.write_wav(Path(temp) / "loop-b.wav")
            cart = self.write_wav(Path(temp) / "cart.wav")
            engine = AudioEngine()
            player_ids = {slot: id(player) for slot, (player, _control) in engine._cart_players.items()}
            base_channel_count = len(engine._channels)
            try:
                cue_available = len(outputs) > 1
                if cue_available:
                    engine.set_cue_output(bytes(outputs[1].id()).decode(errors="ignore"))
                for index in range(6):
                    engine.start_loop(str(loop_a), "PGM")
                    engine.start_loop(str(loop_b), "CUE" if cue_available else "PGM")
                    self.assertTrue(self.wait_until(lambda: len(engine._loop_players) == 2))
                    for _ in range(3):
                        if cue_available:
                            engine.preview_path(str(cart))
                            self.assertTrue(self.wait_until(lambda: engine.preview.playbackState() == QMediaPlayer.PlaybackState.PlayingState))
                            engine.stop_preview()
                    self.assertTrue(engine.play_cartouche(index + 1, str(cart)))
                    self.assertTrue(self.wait_until(lambda: engine.cartouche_active(index + 1)))
                    engine.stop_all_cartouches()
                    engine.stop_loop()
                    self.assertEqual(len(engine._loop_players), 0)
                    self.assertEqual(len(engine._channels), base_channel_count)
                    self.assertEqual({slot: id(player) for slot, (player, _control) in engine._cart_players.items()}, player_ids)
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    self.app.processEvents()
                self.assertFalse(engine._cart_active)
                self.assertFalse(engine.loop_active(str(loop_a)))
            finally:
                engine.shutdown()
                engine.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.app.processEvents()

    def test_twelve_cartouches_and_history_survive_stop_close_and_restart(self):
        outputs = QMediaDevices.audioOutputs()
        if not outputs:
            self.skipTest("Nenhuma saída de áudio disponível para decodificação Qt.")
        self._previous_data_dir = os.environ.get("HITMO_DATA_DIR")
        with tempfile.TemporaryDirectory() as temp:
            os.environ["HITMO_DATA_DIR"] = temp
            audio = self.write_wav(Path(temp) / "cart.wav", seconds=8)
            window = MainWindow()
            try:
                window._timer.stop()
                media_id = window.db.execute(
                    "INSERT INTO media(path,title,category) VALUES(?,?,?)",
                    (str(audio.resolve()), "Pulsador de estabilidade", "Vinheta"),
                )
                for slot in range(1, 13):
                    window.db.execute(
                        "INSERT INTO cartouche_slots(slot,media_id) VALUES(?,?) "
                        "ON CONFLICT(slot) DO UPDATE SET media_id=excluded.media_id",
                        (slot, media_id),
                    )
                cart_player_ids = {slot: id(pair[0]) for slot, pair in window.engine._cart_players.items()}
                for _round in range(3):
                    for slot in range(1, 13):
                        self.assertTrue(window._fire_cartouche(slot), f"Cartucho {slot}: {window.statusBar().currentMessage()}")
                    self.assertTrue(self.wait_until(lambda: len(window.engine._cart_active) == 12, timeout_ms=6000))
                    window._stop_all_cartouches()
                    self.assertTrue(self.wait_until(lambda: not window.engine._cart_active))
                    self.assertTrue(self.wait_until(lambda: window.db.one(
                        "SELECT COUNT(*) AS n FROM cartouche_history WHERE status IN ('triggered','loading','playing','paused')"
                    )["n"] == 0))
                    self.assertEqual({slot: id(pair[0]) for slot, pair in window.engine._cart_players.items()}, cart_player_ids)
                    check = window.db.one("PRAGMA integrity_check")
                    self.assertEqual(str(check[0]), "ok")

                self.assertEqual(window.db.one("SELECT COUNT(*) AS n FROM cartouche_history")["n"], 36)
                self.assertTrue(window._fire_cartouche(1))
                self.assertTrue(self.wait_until(lambda: window.db.one(
                    "SELECT status FROM cartouche_history ORDER BY id DESC LIMIT 1"
                )["status"] == "playing"))
            finally:
                window.close()
                self.app.processEvents()

            reopened = Database(Path(temp) / "hitmo.sqlite3")
            self.assertEqual(str(reopened.one("PRAGMA integrity_check")[0]), "ok")
            self.assertEqual(reopened.one("SELECT status FROM cartouche_history ORDER BY id DESC LIMIT 1")["status"], "interrupted")
            restarted = MainWindow()
            try:
                restarted._timer.stop()
                self.assertEqual(restarted.engine.program.playbackState(), QMediaPlayer.PlaybackState.StoppedState)
                self.assertFalse(restarted.engine._cart_active)
                self.assertEqual(restarted.db.one("SELECT COUNT(*) AS n FROM cartouche_history")["n"], 37)
                self.assertEqual(str(restarted.db.one("PRAGMA integrity_check")[0]), "ok")
            finally:
                restarted.close()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
