import unittest

import numpy as np

from hitmo.audio_mixer import DEFAULT_MASTER_GAIN, LIMITER_CEILING, equal_power_crossfade, mix_pcm


class AudioMixerMathTests(unittest.TestCase):
    def test_routes_are_independent_and_sum_simultaneously(self):
        pgm, _ = mix_pcm({"program": np.ones((4, 2), np.float32) * 0.2}, {"program": 1.0})
        cue, _ = mix_pcm({"preview": np.ones((4, 2), np.float32) * 0.7}, {"preview": 1.0}, master_gain=1.0)
        self.assertTrue(np.allclose(pgm, 0.2 * DEFAULT_MASTER_GAIN))
        self.assertTrue(np.allclose(cue, 0.7))
        simultaneous, _ = mix_pcm(
            {"loop": np.full((4, 2), 0.2, np.float32), "cart": np.full((4, 2), 0.3, np.float32)},
            {"loop": 1.0, "cart": 1.0}, master_gain=1.0,
        )
        self.assertTrue(np.allclose(simultaneous, 0.5))

    def test_gain_mute_meter_and_master(self):
        signal = np.full((32, 2), 0.5, np.float32)
        out, meter = mix_pcm({"a": signal}, {"a": 0.5}, bus_gain=0.5, master_gain=1.0)
        self.assertTrue(np.allclose(out, 0.125))
        self.assertAlmostEqual(meter.peak, 0.125)
        self.assertAlmostEqual(meter.rms, 0.125)
        muted, muted_meter = mix_pcm({"a": signal}, {"a": 1}, {"a"}, master_gain=1.0)
        self.assertFalse(np.any(muted))
        self.assertEqual(muted_meter.rms, 0.0)

    def test_equal_power_crossfade_and_limiter_headroom(self):
        fade_out, fade_in = equal_power_crossfade(101, 0, 100)
        self.assertAlmostEqual(float(fade_out[0]), 1.0, places=6)
        self.assertAlmostEqual(float(fade_in[-1]), 1.0, places=6)
        self.assertTrue(np.allclose(fade_out**2 + fade_in**2, 1.0, atol=1e-6))
        sources = {"a": np.ones((20, 2), np.float32), "b": np.ones((20, 2), np.float32)}
        out, meter = mix_pcm(sources, {"a": 1.0, "b": 1.0}, master_gain=1.0)
        self.assertLessEqual(float(np.max(np.abs(out))), LIMITER_CEILING)
        self.assertAlmostEqual(meter.peak, LIMITER_CEILING)
        self.assertLess(meter.limiter_gain, 1.0)


if __name__ == "__main__":
    unittest.main()
