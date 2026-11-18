import os
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat, QMediaPlayer
from PySide6.QtWidgets import QApplication

from hitmo.audio_engine import AudioEngine
from hitmo.audio_mixer import CHANNELS, SAMPLE_RATE


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


class FakeSink(QObject):
    stateChanged = Signal(object)

    def __init__(self, _device, _fmt, parent):
        super().__init__(parent)
        self._error = QAudio.Error.NoError
        self.started = False
        self.stopped = False

    def setBufferSize(self, _size):
        pass

    def start(self, source):
        self.source = source
        self.started = True

    def error(self):
        return self._error

    def stop(self):
        self.stopped = True

    def fail(self):
        self._error = QAudio.Error.OpenError
        self.stateChanged.emit(QAudio.State.StoppedState)


class FakePlayer:
    def __init__(self, state):
        self.state = state
        self.pauses = 0
        self.plays = 0

    def playbackState(self):
        return self.state

    def pause(self):
        self.pauses += 1
        self.state = QMediaPlayer.PlaybackState.PausedState

    def play(self):
        self.plays += 1
        self.state = QMediaPlayer.PlaybackState.PlayingState

    def stop(self):
        self.state = QMediaPlayer.PlaybackState.StoppedState

    def source(self):
        return QUrl("file:///fake.wav")

    def setSource(self, _source):
        pass


class AudioDeviceRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_engine(self, available, pgm="pgm", cue="cue"):
        devices = [FakeDevice(name) for name in available]
        return AudioEngine(pgm, cue, device_provider=lambda: devices,
                           sink_factory=FakeSink), devices

    def close_engine(self, engine):
        engine.shutdown()
        engine.deleteLater()
        self.app.processEvents()

    def test_independent_outputs_and_missing_cue(self):
        engine, _ = self.make_engine(["pgm", "cue"])
        try:
            self.assertEqual(engine.pgm_device_id, "pgm")
            self.assertEqual(engine.cue_device_id, "cue")
            self.assertIsNot(engine._buses["PGM"].sink, engine._buses["CUE"].sink)
        finally:
            self.close_engine(engine)

        engine, _ = self.make_engine(["pgm"])
        try:
            self.assertEqual(engine.pgm_device_id, "pgm")
            self.assertTrue(engine.cue_available)
            self.assertTrue(engine._shared_output)
            self.assertEqual(engine.cue_device_id, "pgm")
            self.assertIsNone(engine._buses["CUE"].sink)
            engine.configure_outputs("pgm", "removed-cue")
            self.assertTrue(engine.cue_available)
            self.assertTrue(engine._shared_output)
        finally:
            self.close_engine(engine)

    def test_shared_output_reads_both_logical_buses_into_one_sink(self):
        engine, _ = self.make_engine(["pgm"])
        try:
            engine._master_gain = 1.0
            engine.set_bus_gain("PGM", 0.5)
            engine.set_bus_gain("CUE", 0.25)
            engine._channels["program"]["queue"].push(np.full((4, 2), 0.4, np.float32))
            engine._channels["preview"]["queue"].push(np.full((4, 2), 0.2, np.float32))
            pgm_sink = engine._buses["PGM"].sink
            encoded = pgm_sink.source.readData(4 * 2 * 4)
            output = np.frombuffer(encoded, dtype=np.float32).reshape(-1, 2)
            self.assertTrue(np.allclose(output, 0.25))
            self.assertIsNone(engine._buses["CUE"].sink)
        finally:
            self.close_engine(engine)

    def test_shared_pgm_and_cue_render_alone_and_cue_continues_after_pgm_queue_ends(self):
        engine, _ = self.make_engine(["pgm"])
        try:
            engine._master_gain = 1.0
            sink_source = engine._buses["PGM"].source
            engine._channels["program"]["queue"].push(np.full((4, 2), 0.2, np.float32))
            pgm_only = np.frombuffer(sink_source.readData(32), dtype=np.float32).reshape(-1, 2)
            self.assertTrue(np.allclose(pgm_only, 0.2))
            engine._channels["preview"]["queue"].push(np.full((4, 2), 0.3, np.float32))
            cue_only_after_pgm_end = np.frombuffer(sink_source.readData(32), dtype=np.float32).reshape(-1, 2)
            self.assertTrue(np.allclose(cue_only_after_pgm_end, 0.3))
        finally:
            self.close_engine(engine)

    def test_activate_shared_cue_while_pgm_runs_does_not_rebuild_pgm_sink(self):
        engine, _ = self.make_engine(["pgm", "cue"])
        player = FakePlayer(QMediaPlayer.PlaybackState.PlayingState)
        engine._channels["program"]["player"] = player
        try:
            pgm_sink = engine._buses["PGM"].sink
            engine.configure_outputs("pgm", "pgm")
            self.assertIs(engine._buses["PGM"].sink, pgm_sink)
            self.assertTrue(pgm_sink.started)
            self.assertEqual(player.state, QMediaPlayer.PlaybackState.PlayingState)
            self.assertTrue(engine._shared_output)
            self.assertIsNone(engine._buses["CUE"].sink)
        finally:
            self.close_engine(engine)

    def test_preview_pause_resume_and_stop_do_not_change_pgm(self):
        engine, _ = self.make_engine(["pgm"])
        original_preview = engine.preview
        original_channel_player = engine._channels["preview"]["player"]
        cue_player = FakePlayer(QMediaPlayer.PlaybackState.PlayingState)
        pgm_player = FakePlayer(QMediaPlayer.PlaybackState.PlayingState)
        engine.preview = cue_player
        engine._channels["preview"]["player"] = cue_player
        engine._channels["program"]["player"] = pgm_player
        try:
            engine.pause_preview()
            self.assertEqual(cue_player.state, QMediaPlayer.PlaybackState.PausedState)
            self.assertEqual(pgm_player.state, QMediaPlayer.PlaybackState.PlayingState)
            engine.resume_preview()
            self.assertEqual(cue_player.state, QMediaPlayer.PlaybackState.PlayingState)
            engine.stop_preview()
            self.assertEqual(cue_player.state, QMediaPlayer.PlaybackState.StoppedState)
            self.assertEqual(pgm_player.state, QMediaPlayer.PlaybackState.PlayingState)
        finally:
            engine.preview = original_preview
            engine._channels["preview"]["player"] = original_channel_player
            self.close_engine(engine)

    def test_stopping_pgm_keeps_shared_cue_player_running(self):
        engine, _ = self.make_engine(["pgm"])
        original_program = engine.program
        original_preview = engine.preview
        original_program_channel = engine._channels["program"]["player"]
        original_preview_channel = engine._channels["preview"]["player"]
        pgm_player = FakePlayer(QMediaPlayer.PlaybackState.PlayingState)
        cue_player = FakePlayer(QMediaPlayer.PlaybackState.PlayingState)
        engine.program = pgm_player
        engine.preview = cue_player
        engine._channels["program"]["player"] = pgm_player
        engine._channels["preview"]["player"] = cue_player
        try:
            engine.stop_program()
            self.assertEqual(pgm_player.state, QMediaPlayer.PlaybackState.StoppedState)
            self.assertEqual(cue_player.state, QMediaPlayer.PlaybackState.PlayingState)
            self.assertTrue(engine.cue_available)
        finally:
            engine.program = original_program
            engine.preview = original_preview
            engine._channels["program"]["player"] = original_program_channel
            engine._channels["preview"]["player"] = original_preview_channel
            self.close_engine(engine)

    def test_missing_explicit_pgm_does_not_fall_back_to_another_device(self):
        engine, _ = self.make_engine(["other"])
        try:
            self.assertEqual(engine.pgm_device_id, "")
            self.assertEqual(engine._requested_device_ids["PGM"], "pgm")
            self.assertIn("não está disponível", engine._buses["PGM"].error_message)
        finally:
            self.close_engine(engine)

    def test_sink_initialization_failure_is_reported_without_crashing_engine(self):
        devices = [FakeDevice("pgm"), FakeDevice("cue")]

        def broken_factory(_device, _fmt, _parent):
            raise OSError("backend indisponível")

        engine = AudioEngine("pgm", "cue", device_provider=lambda: devices,
                             sink_factory=broken_factory)
        try:
            self.assertIsNone(engine._buses["PGM"].sink)
            self.assertIsNone(engine._buses["CUE"].sink)
            self.assertIn("backend indisponível", engine._buses["PGM"].error_message)
            self.assertIn("backend indisponível", engine.cue_configuration_error)
        finally:
            self.close_engine(engine)

    def test_device_loss_pauses_only_affected_bus_and_reconnect_resumes_it(self):
        engine, devices = self.make_engine(["pgm", "cue"])
        pgm_player = FakePlayer(QMediaPlayer.PlaybackState.PlayingState)
        cue_player = FakePlayer(QMediaPlayer.PlaybackState.PlayingState)
        engine._channels["program"]["player"] = pgm_player
        engine._channels["preview"]["player"] = cue_player
        try:
            devices[:] = [devices[1]]
            engine.refresh_audio_devices()
            self.assertEqual(pgm_player.state, QMediaPlayer.PlaybackState.PausedState)
            self.assertEqual(cue_player.state, QMediaPlayer.PlaybackState.PlayingState)
            self.assertFalse(engine._buses["PGM"].sink)
            devices.insert(0, FakeDevice("pgm"))
            engine.refresh_audio_devices()
            self.assertEqual(pgm_player.state, QMediaPlayer.PlaybackState.PlayingState)
            self.assertEqual(pgm_player.plays, 1)
            self.assertEqual(cue_player.plays, 0)
            self.assertEqual(engine.pgm_device_id, "pgm")
        finally:
            self.close_engine(engine)

    def test_sink_error_is_visible_and_other_bus_remains_available(self):
        engine, _ = self.make_engine(["pgm", "cue"])
        errors = []
        engine.bus_error.connect(lambda *args: errors.append(args))
        try:
            sink = engine._buses["CUE"].sink
            sink.fail()
            self.assertFalse(engine.cue_available)
            self.assertTrue(engine._buses["PGM"].sink)
            self.assertTrue(any(item[0] == "CUE" for item in errors))
        finally:
            self.close_engine(engine)

    def test_same_device_uses_one_shared_sink(self):
        engine, _ = self.make_engine(["pgm", "cue"])
        try:
            engine.configure_outputs("pgm", "pgm")
            self.assertEqual(engine.pgm_device_id, "pgm")
            self.assertEqual(engine.cue_device_id, "pgm")
            self.assertTrue(engine.cue_available)
            self.assertTrue(engine._shared_output)
            self.assertIsNone(engine._buses["CUE"].sink)
        finally:
            self.close_engine(engine)


if __name__ == "__main__":
    unittest.main()
