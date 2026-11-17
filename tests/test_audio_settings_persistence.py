import json
import os
import tempfile
import unittest
import wave
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QAudioFormat, QMediaPlayer
from PySide6.QtWidgets import QApplication

from hitmo.audio_engine import AudioEngine
from hitmo.audio_mixer import CHANNELS, SAMPLE_RATE
from hitmo.app_settings import AppSettingsDialog
from hitmo.database import Database


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

    def setBufferSize(self, _size):
        pass

    def start(self, source):
        self.source = source

    def error(self):
        from PySide6.QtMultimedia import QAudio
        return QAudio.Error.NoError

    def stop(self):
        pass


class AudioSettingsPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def engine(self, state=None, devices=None):
        names = ["pgm", "cue"] if devices is None else devices
        available = [FakeDevice(name) for name in names]
        return AudioEngine("pgm", "cue", device_provider=lambda: available,
                           sink_factory=FakeSink, mixer_state=state)

    def close_engine(self, engine):
        engine.shutdown()
        engine.deleteLater()
        self.app.processEvents()

    def test_mixer_settings_survive_sqlite_close_and_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "station.sqlite3"
            db = Database(path)
            engine = self.engine()
            try:
                engine.set_channel_gain("program", 0.72)
                engine.set_channel_muted("program", True)
                engine.set_channel_gain("cart:12", 0.33)
                engine.set_channel_muted("cart:12", True)
                engine.set_bus_gain("PGM", 0.61)
                engine.set_bus_muted("CUE", True)
                engine.set_master_gain(0.44)
                engine.set_crossfade_duration(4200)
                db.set_setting("audio_output_id", "pgm")
                db.set_setting("cue_output_id", "cue")
                db.set_setting("audio_mixer_state", json.dumps(engine.export_mixer_settings()))
                db.set_setting("auto_advance", "0")
                db.set_setting("startup_play", "0")
            finally:
                self.close_engine(engine)

            reopened_db = Database(path)
            state = json.loads(reopened_db.setting("audio_mixer_state"))
            restored = self.engine(state)
            try:
                self.assertEqual(reopened_db.setting("audio_output_id"), "pgm")
                self.assertEqual(reopened_db.setting("cue_output_id"), "cue")
                self.assertEqual(reopened_db.setting("auto_advance"), "0")
                self.assertEqual(reopened_db.setting("startup_play"), "0")
                self.assertAlmostEqual(restored.channel_gain("program"), 0.72)
                self.assertTrue(restored.channel_muted("program"))
                self.assertAlmostEqual(restored.channel_gain("cart:12"), 0.33)
                self.assertTrue(restored.channel_muted("cart:12"))
                self.assertAlmostEqual(restored.bus_gain("PGM"), 0.61)
                self.assertTrue(restored.bus_muted("CUE"))
                self.assertAlmostEqual(restored.master_gain, 0.44)
                self.assertEqual(restored.crossfade_duration_ms, 4200)
                self.assertEqual(restored.program.playbackState(), QMediaPlayer.PlaybackState.StoppedState)
            finally:
                self.close_engine(restored)

    def test_loop_gain_and_mute_restore_when_dynamic_channel_is_created(self):
        with tempfile.TemporaryDirectory() as temp:
            audio = Path(temp) / "bed.wav"
            with wave.open(str(audio), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(8000)
                stream.writeframes(b"\0\0" * 800)
            channel_id = f"loop:{AudioEngine._loop_key(str(audio))}"
            state = {"channels": {channel_id: {"gain": 0.25, "muted": True}}}
            engine = self.engine(state)
            try:
                engine.start_loop(str(audio), "PGM")
                self.assertAlmostEqual(engine.channel_gain(channel_id), 0.25)
                self.assertTrue(engine.channel_muted(channel_id))
            finally:
                self.close_engine(engine)

    def test_temporary_program_fade_gain_is_not_saved_as_operator_preference(self):
        engine = self.engine()
        changes = []
        engine.settings_changed.connect(changes.append)
        try:
            engine.set_channel_gain("program", 0.75)
            changes.clear()
            engine.set_channel_gain("program", 0.2, persist=False)
            state = engine.export_mixer_settings()
            self.assertAlmostEqual(engine.channel_gain("program"), 0.2)
            self.assertAlmostEqual(state["channels"]["program"]["gain"], 0.75)
            self.assertFalse(changes)
        finally:
            self.close_engine(engine)

    def test_invalid_mixer_fields_fall_back_individually_without_mutating_database(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "station.sqlite3")
            invalid = json.dumps({
                "master_gain": 8,
                "crossfade_duration_ms": 999999,
                "buses": {"PGM": {"gain": "nan", "muted": "yes"}},
                "channels": {"cart:1": {"gain": -2, "muted": 1}},
            })
            db.set_setting("audio_mixer_state", invalid)
            engine = self.engine(json.loads(invalid))
            try:
                self.assertAlmostEqual(engine.master_gain, 10 ** (-6 / 20))
                self.assertEqual(engine.crossfade_duration_ms, 3000)
                self.assertEqual(engine.bus_gain("PGM"), 1.0)
                self.assertFalse(engine.bus_muted("PGM"))
                self.assertEqual(engine.channel_gain("cart:1"), 1.0)
                self.assertFalse(engine.channel_muted("cart:1"))
                self.assertEqual(db.setting("audio_mixer_state"), invalid)
                self.assertEqual(engine.program.playbackState(), QMediaPlayer.PlaybackState.StoppedState)
            finally:
                self.close_engine(engine)

    def test_settings_dialog_keeps_unavailable_saved_device_when_preferences_are_saved(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "station.sqlite3")
            db.set_setting("audio_output_id", "removed-pgm")
            db.set_setting("cue_output_id", "removed-cue")
            db.set_setting("station_name", "Before")
            engine = self.engine(devices=[])
            dialog = AppSettingsDialog(db, engine)
            try:
                self.assertEqual(str(dialog.output.currentData()), "removed-pgm")
                self.assertEqual(str(dialog.cue_output.currentData()), "removed-cue")
                dialog.station_name.setText("Updated")
                dialog.accept()
                self.assertEqual(db.setting("audio_output_id"), "removed-pgm")
                self.assertEqual(db.setting("cue_output_id"), "removed-cue")
                self.assertEqual(db.setting("station_name"), "Updated")
            finally:
                self.close_engine(engine)
                dialog.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
