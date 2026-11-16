import os
import tempfile
import unittest
import wave
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from PySide6.QtMultimedia import QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import QApplication

from hitmo.audio_engine import AudioEngine


class AudioEngineSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_audio_decks_decode_and_loop_independently(self):
        if not QMediaDevices.audioOutputs():
            self.skipTest("Nenhuma saída de áudio disponível neste computador.")
        with tempfile.TemporaryDirectory() as temp:
            def write_silence(name, frames=2000):
                path = Path(temp) / name
                with wave.open(str(path), "wb") as stream:
                    stream.setnchannels(1)
                    stream.setsampwidth(2)
                    stream.setframerate(8000)
                    stream.writeframes(b"\x00\x00" * frames)
                return path

            program_path = write_silence("program.wav")
            controls_path = write_silence("controls.wav", 16000)
            preview_path = write_silence("preview.wav")
            loop_a = write_silence("loop_a.wav")
            loop_b = write_silence("loop_b.wav")

            engine = AudioEngine()
            def wait_ms(milliseconds):
                wait_loop = QEventLoop()
                QTimer.singleShot(milliseconds, wait_loop.quit)
                wait_loop.exec()

            engine.play_program(str(controls_path))
            wait_ms(120)
            self.assertEqual(engine.program.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
            engine.toggle_program()
            wait_ms(80)
            self.assertEqual(engine.program.playbackState(), QMediaPlayer.PlaybackState.PausedState)
            engine.set_muted(True)
            self.assertTrue(engine.program_output.isMuted())
            engine.set_muted(False)
            engine.toggle_program()
            wait_ms(80)
            self.assertEqual(engine.program.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
            engine.stop_program()
            wait_ms(30)
            self.assertEqual(engine.program.playbackState(), QMediaPlayer.PlaybackState.StoppedState)
            engine.eject_program()
            self.assertFalse(engine.program.source().isValid())

            loop = QEventLoop()
            errors = []
            ended = []
            engine.program_error.connect(errors.append)
            engine.program_finished.connect(lambda: (ended.append(True), loop.quit()))
            QTimer.singleShot(4000, loop.quit)
            engine.play_program(str(program_path))
            loop.exec()
            self.assertFalse(errors, f"Qt Multimedia retornou erro: {errors}")
            self.assertTrue(ended, "A faixa de teste não chegou ao fim dentro de quatro segundos.")

            preview_loop = QEventLoop()
            preview_ended = []
            engine.preview_finished.connect(lambda: (preview_ended.append(True), preview_loop.quit()))
            QTimer.singleShot(4000, preview_loop.quit)
            engine.preview_path(str(preview_path))
            preview_loop.exec()
            self.assertTrue(preview_ended, "A pré-escuta não concluiu o arquivo curto.")

            engine.start_loop(str(loop_a))
            engine.start_loop(str(loop_b))
            loop_start = QEventLoop()
            QTimer.singleShot(100, loop_start.quit)
            loop_start.exec()
            self.assertTrue(engine.loop_active(str(loop_a)))
            self.assertTrue(engine.loop_active(str(loop_b)))
            self.assertTrue(all(player.playbackState() == QMediaPlayer.PlaybackState.PlayingState for player, _output in engine._loop_players.values()))
            engine.stop_loop(str(loop_a))
            self.assertFalse(engine.loop_active(str(loop_a)))
            self.assertTrue(engine.loop_active(str(loop_b)))
            engine.stop_loop()
            self.assertFalse(engine.loop_active(str(loop_b)))

            engine.eject_program()
            engine.stop_preview()
            release_loop = QEventLoop()
            QTimer.singleShot(300, release_loop.quit)
            release_loop.exec()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
            engine = None

    def test_program_fade_out_reports_position_and_restores_output_volume(self):
        if not QMediaDevices.audioOutputs():
            self.skipTest("Nenhuma saída de áudio disponível neste computador.")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fade.wav"
            with wave.open(str(path), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(8000)
                stream.writeframes(b"\x00\x00" * 24000)
            engine = AudioEngine()
            ended = []
            volumes = []
            engine.program_faded_out.connect(ended.append)
            engine.program_output.volumeChanged.connect(volumes.append)
            engine.play_program(str(path))
            self.assertTrue(self._wait_until(lambda: engine.program.playbackState() == QMediaPlayer.PlaybackState.PlayingState))
            self.assertTrue(self._wait_until(lambda: engine.program.position() > 0))
            initial_volume = engine.program_output.volume()
            engine.fade_out_program(150)
            self.assertTrue(self._wait_until(lambda: bool(ended)))
            self.assertEqual(engine.program.playbackState(), QMediaPlayer.PlaybackState.StoppedState)
            self.assertGreater(ended[0], 0)
            self.assertTrue(any(0 < volume < initial_volume for volume in volumes))
            self.assertAlmostEqual(engine.program_output.volume(), initial_volume)
            engine.eject_program()
            self._wait_until(lambda: engine.program.mediaStatus() == QMediaPlayer.MediaStatus.NoMedia)
            engine.deleteLater()
            self.app.processEvents()

    @staticmethod
    def _wait_until(predicate, timeout_ms=3000):
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


if __name__ == "__main__":
    unittest.main()

