import os
import tempfile
import unittest
import wave
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import QApplication

from hitmo.main_window import MainWindow


class AudioConsoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_console_routing_widgets_and_real_cue_meter(self):
        outputs = QMediaDevices.audioOutputs()
        if len(outputs) < 2:
            self.skipTest("O teste de console PGM/CUE exige dois dispositivos de saída distintos.")
        old_data_dir = os.environ.get("HITMO_DATA_DIR")
        with tempfile.TemporaryDirectory() as temp:
            os.environ["HITMO_DATA_DIR"] = temp
            path = Path(temp) / "console-tone.wav"
            with wave.open(str(path), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(8000)
                stream.writeframes(b"\x00\x04" * 24000)
            window = MainWindow()
            try:
                window.engine.set_cue_output(bytes(outputs[1].id()).decode(errors="ignore"))
                window._refresh_audio_console()
                self.assertEqual(window.cart_mixer_table.rowCount(), 12)
                self.assertIn("PGM →", window.output_summary.text())
                self.assertIn("CUE →", window.output_summary.text())

                media_id = window.db.execute(
                    "INSERT INTO media(path,title,category) VALUES(?,?,?)",
                    (str(path), "Tom de console", "Vinheta"),
                )
                window.db.execute("INSERT INTO cartouche_slots(slot,media_id) VALUES(1,?)", (media_id,))
                window._refresh_cartouches()
                route = window.cart_mixer_table.cellWidget(0, 2)
                route.setCurrentIndex(route.findData("CUE"))
                self.assertTrue(self._wait_until(lambda: window.db.one("SELECT route FROM cartouche_slots WHERE slot=1")["route"] == "CUE"))
                self.assertEqual(window.engine.channel_route("cart:1"), "CUE")
                self.assertTrue(self._wait_until(lambda: window.cart_mixer_table.cellWidget(0, 2).currentData() == "CUE"))
                window.cart_mixer_table.cellWidget(0, 3).setValue(45)
                window.cart_mixer_table.cellWidget(0, 4).setChecked(True)
                self.assertAlmostEqual(window.engine.channel_gain("cart:1"), 0.45)
                self.assertTrue(window.engine.channel_muted("cart:1"))
                window.master_gain_slider.setValue(65)
                self.assertAlmostEqual(window.engine.master_gain, 0.65)

                window.db.execute("INSERT INTO loop_tracks(media_id,enabled,route) VALUES(?,0,'PGM')", (media_id,))
                window._refresh_audio_console()
                loop_route = window.loop_mixer_table.cellWidget(0, 1)
                loop_route.setCurrentIndex(loop_route.findData("CUE"))
                self.assertEqual(window.db.one("SELECT route FROM loop_tracks WHERE media_id=?", (media_id,))["route"], "CUE")
                window.loop_mixer_table.cellWidget(0, 4).click()
                self.assertTrue(self._wait_until(lambda: window.engine.loop_active(str(path))))
                self.assertEqual(window.engine.channel_route(f"loop:{window.engine._loop_key(str(path))}"), "CUE")
                window.loop_mixer_table.cellWidget(0, 4).click()
                self.assertFalse(window.engine.loop_active(str(path)))

                initial = window.bus_widgets["CUE"]["level"].text()
                window.engine.preview_path(str(path))
                self.assertTrue(self._wait_until(lambda: window.bus_widgets["CUE"]["level"].text() != initial))
                self.assertIn("dBFS", window.bus_widgets["CUE"]["level"].text())
                window.cart_mixer_table.cellWidget(0, 4).setChecked(False)
                self.assertTrue(window._fire_cartouche(1))
                self.assertTrue(self._wait_until(lambda: len(window.engine.active_sources("CUE")) == 2))
                window.crossfade_bus.setCurrentText("CUE")
                self.assertTrue(window.crossfade_button.isEnabled())
                self.assertNotEqual(window.crossfade_source_a.currentData(), window.crossfade_source_b.currentData())
                window.crossfade_button.click()
                self.assertTrue({window.crossfade_source_a.currentData(), window.crossfade_source_b.currentData()} & set(window.engine._crossfades))
            finally:
                window.close()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.app.processEvents()
                if old_data_dir is None:
                    os.environ.pop("HITMO_DATA_DIR", None)
                else:
                    os.environ["HITMO_DATA_DIR"] = old_data_dir

    @staticmethod
    def _wait_until(predicate, timeout_ms=4000):
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
