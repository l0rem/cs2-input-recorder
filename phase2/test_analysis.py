"""Unit tests for Phase 3 analysis helpers. Run: python -m unittest phase2.test_analysis"""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analysis as A


class TestThresholds(unittest.TestCase):
    def test_ak_threshold_is_34_percent_of_215(self):
        self.assertEqual(A.max_speed("weapon_ak47"), 215.0)
        self.assertAlmostEqual(A.accuracy_threshold("weapon_ak47"), 215.0 * 0.34)

    def test_m4s_threshold(self):
        self.assertEqual(A.max_speed("weapon_m4a1_silencer"), 225.0)
        self.assertAlmostEqual(A.accuracy_threshold("weapon_m4a1_silencer"), 225.0 * 0.34)

    def test_awp_unscoped_vs_scoped(self):
        self.assertEqual(A.max_speed("weapon_awp", is_scoped=False), 200.0)
        self.assertEqual(A.max_speed("weapon_awp", is_scoped=True), 100.0)
        self.assertAlmostEqual(A.accuracy_threshold("weapon_awp", False), 68.0)
        self.assertAlmostEqual(A.accuracy_threshold("weapon_awp", True), 34.0)

    def test_glock_is_not_a_headline_rifle(self):
        self.assertFalse(A.is_headline_rifle("weapon_glock"))
        self.assertTrue(A.is_headline_rifle("weapon_ak47"))
        self.assertFalse(A.is_headline_rifle("weapon_awp"))
        self.assertFalse(A.is_headline_rifle("weapon_mac10"))

    def test_rifle_accurate_uses_threshold_not_130(self):
        th = A.accuracy_threshold("weapon_ak47")
        self.assertTrue(A.rifle_accurate("weapon_ak47", speed=70.0, is_scoped=False))
        self.assertFalse(A.rifle_accurate("weapon_ak47", speed=80.0, is_scoped=False))
        self.assertTrue(th < 130.0)


class TestSpeed(unittest.TestCase):
    def test_1tick_posdiff_at_64_tick(self):
        # 150 u/s along X: 150/64 units per tick
        dx = 150.0 / 64.0
        spd = A.horizontal_speed(0.0, 0.0, dx, 0.0, dt_ticks=1)
        self.assertAlmostEqual(spd, 150.0, places=5)

    def test_window_pm16_averages_across_half_second(self):
        # start moving 220 u/s, end stopped: ±16 ticks = 32 ticks = 0.5s, 220*0.5=110 units
        spd = A.horizontal_speed(0.0, 0.0, 110.0, 0.0, dt_ticks=32)
        self.assertAlmostEqual(spd, 220.0, places=5)

    def test_zero_dt_is_nan(self):
        self.assertTrue(math.isnan(A.horizontal_speed(0, 0, 1, 0, dt_ticks=0)))


class TestFirstBullet(unittest.TestCase):
    def test_shots_fired_1_is_first(self):
        self.assertTrue(A.is_first_bullet(1))
        self.assertFalse(A.is_first_bullet(2))
        self.assertFalse(A.is_first_bullet(0))
        self.assertFalse(A.is_first_bullet(None))

    def test_ffill_within_window_only(self):
        import numpy as np
        v = np.array([np.nan, np.nan, 1.0, np.nan])
        out = A.ffill_numeric(v)
        self.assertTrue(math.isnan(out[0]) and math.isnan(out[1]))
        self.assertEqual(out[2], 1.0)
        self.assertEqual(out[3], 1.0)


class TestResidual(unittest.TestCase):
    def test_nearest_mouse1_within_gate(self):
        edges = [100.0, 500.0, 900.0]
        resid, matched = A.nearest_residual_ms(508.0, edges, max_abs_ms=400)
        self.assertAlmostEqual(matched, 500.0)
        self.assertAlmostEqual(resid, -8.0)

    def test_no_edge_within_max_abs_is_none(self):
        resid, matched = A.nearest_residual_ms(0.0, [1000.0], max_abs_ms=400)
        self.assertIsNone(resid)
        self.assertIsNone(matched)

    def test_mouse1_rising_edges(self):
        import numpy as np
        down = np.array([0, 0, 1, 1, 0, 1], dtype=bool)
        idx = A.rising_edges(down)
        self.assertEqual(list(idx), [2, 5])


class TestClassifier(unittest.TestCase):
    def test_cache_is_skipped_for_input_cause(self):
        r = A.classify_input(
            map_name="de_cache", residual_ms=0.0, speed=50.0,
            w_max=0, a_max=65535, s_max=0, d_max=0,
            gap_ms=None, overlap_ms=None,
        )
        self.assertFalse(r["sync_ok"])
        self.assertEqual(r["input_class"], "SKIP_CACHE")

    def test_wide_residual_is_sync_uncertain(self):
        r = A.classify_input(
            map_name="de_mirage", residual_ms=120.0, speed=50.0,
            w_max=0, a_max=65535, s_max=0, d_max=0,
            gap_ms=10.0, overlap_ms=0.0,
        )
        self.assertFalse(r["sync_ok"])
        self.assertEqual(r["input_class"], "SYNC_UNCERTAIN")

    def test_no_default_release_only_when_unclassified(self):
        r = A.classify_input(
            map_name="de_mirage", residual_ms=5.0, speed=80.0,
            w_max=0, a_max=0, s_max=0, d_max=0,
            gap_ms=None, overlap_ms=None,
        )
        self.assertEqual(r["input_class"], "UNCLASSIFIED")
        self.assertNotEqual(r["input_class"], "RELEASE_ONLY")

    def test_ws_flag_and_diagonal_not_called_release_only(self):
        r = A.classify_input(
            map_name="de_mirage", residual_ms=5.0, speed=60.0,
            w_max=65535, a_max=65535, s_max=0, d_max=0,
            gap_ms=None, overlap_ms=None,
        )
        self.assertTrue(r["ws_involved"])
        self.assertEqual(r["input_class"], "DIAGONAL")

    def test_ad_only_without_opposite_is_release_no_counter(self):
        r = A.classify_input(
            map_name="de_mirage", residual_ms=5.0, speed=60.0,
            w_max=0, a_max=65535, s_max=0, d_max=0,
            gap_ms=None, overlap_ms=None,
        )
        self.assertFalse(r["ws_involved"])
        self.assertEqual(r["input_class"], "RELEASE_NO_COUNTER")

    def test_clean_counter_when_gap_small(self):
        r = A.classify_input(
            map_name="de_mirage", residual_ms=4.0, speed=40.0,
            w_max=0, a_max=65535, s_max=0, d_max=65535,
            gap_ms=14.0, overlap_ms=0.0,
        )
        self.assertEqual(r["input_class"], "COUNTER_CLEAN")

    def test_stationary(self):
        r = A.classify_input(
            map_name="de_dust2", residual_ms=2.0, speed=8.0,
            w_max=0, a_max=0, s_max=0, d_max=0,
            gap_ms=None, overlap_ms=None,
        )
        self.assertEqual(r["input_class"], "STATIONARY")


if __name__ == "__main__":
    unittest.main()
