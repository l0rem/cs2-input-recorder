"""Unit tests for Phase 3 analysis helpers. Run: python -m unittest test_analysis -q"""
import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analysis as A
from extract_shots import offset_by_demo as A_offset_by_demo


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
        dx = 150.0 / 64.0
        spd = A.horizontal_speed(0.0, 0.0, dx, 0.0, dt_ticks=1)
        self.assertAlmostEqual(spd, 150.0, places=5)

    def test_window_pm16_averages_across_half_second(self):
        spd = A.horizontal_speed(0.0, 0.0, 110.0, 0.0, dt_ticks=32)
        self.assertAlmostEqual(spd, 220.0, places=5)

    def test_zero_dt_is_nan(self):
        self.assertTrue(math.isnan(A.horizontal_speed(0, 0, 1, 0, dt_ticks=0)))

    def test_native_disagreement_flag(self):
        self.assertTrue(A.speed_disagreement(86.3, 207.4))
        self.assertFalse(A.speed_disagreement(40.0, 42.0))
        self.assertFalse(A.speed_disagreement(40.0, float("nan")))


class TestFirstBullet(unittest.TestCase):
    def test_shots_fired_1_is_first(self):
        self.assertTrue(A.is_first_bullet(1))
        self.assertFalse(A.is_first_bullet(2))
        self.assertFalse(A.is_first_bullet(0))
        self.assertFalse(A.is_first_bullet(None))

    def test_ffill_within_window_only(self):
        v = np.array([np.nan, np.nan, 1.0, np.nan])
        out = A.ffill_numeric(v)
        self.assertTrue(math.isnan(out[0]) and math.isnan(out[1]))
        self.assertEqual(out[2], 1.0)
        self.assertEqual(out[3], 1.0)


class TestPosture(unittest.TestCase):
    def test_standing_on_ground(self):
        self.assertEqual(A.posture_label(False, 0.0), "standing")
        self.assertTrue(A.is_standing_on_ground(False, 0.0))

    def test_full_crouch(self):
        self.assertEqual(A.posture_label(False, 1.0), "crouched")
        self.assertFalse(A.is_standing_on_ground(False, 1.0))

    def test_duck_transition(self):
        self.assertEqual(A.posture_label(False, 0.4), "transition")

    def test_airborne_wins(self):
        self.assertEqual(A.posture_label(True, 0.0), "air")
        self.assertFalse(A.is_standing_on_ground(True, 0.0))


class TestResidual(unittest.TestCase):
    def test_mouse1_rising_edges(self):
        down = np.array([0, 0, 1, 1, 0, 1], dtype=bool)
        idx = A.rising_edges(down)
        self.assertEqual(list(idx), [2, 5])


class TestMonotonicMatch(unittest.TestCase):
    def test_one_edge_cannot_match_two_shots(self):
        out = A.match_monotonic([100.0, 110.0], [105.0], max_abs_ms=400)
        matched = [m for m in out["matched_ms"] if m is not None]
        self.assertEqual(len(matched), 1)
        self.assertEqual(sum(i is not None for i in out["edge_index"]), 1)

    def test_matches_are_monotonic_in_edge_index(self):
        out = A.match_monotonic([100.0, 200.0, 300.0], [105.0, 205.0, 305.0], max_abs_ms=400)
        idx = [i for i in out["edge_index"] if i is not None]
        self.assertEqual(idx, sorted(idx))
        self.assertEqual(len(idx), 3)

    def test_prefers_two_matches_over_one_perfect_middle(self):
        pred = [0.0, 100.0]
        edges = [0.0, 50.0, 100.0]
        out = A.match_monotonic(pred, edges, max_abs_ms=400)
        self.assertEqual(out["matched_ms"], [0.0, 100.0])

    def test_wrong_offset_does_not_invent_gate_matches(self):
        rng = np.random.default_rng(0)
        pred = np.sort(rng.uniform(0, 1_800_000, 80))
        edges = pred + 4.0
        true = A.match_monotonic(pred, edges, max_abs_ms=400)
        n_true = sum(r is not None and abs(r) <= 30.0 for r in true["residual_ms"])
        shifted = A.match_monotonic(pred + 30_000.0, edges, max_abs_ms=400)
        n_gate = sum(
            r is not None and abs(r) <= 30.0
            for r in shifted["residual_ms"]
        )
        self.assertGreaterEqual(n_true, 70)
        self.assertLess(n_gate, 5)


class TestBurstAlign(unittest.TestCase):
    def test_group_bursts_splits_on_1_5s_gap_from_previous(self):
        times = [0.0, 0.2, 0.4, 2.0, 2.1]
        bursts = A.group_bursts(times, gap_s=1.5)
        self.assertEqual(list(np.round(bursts, 4)), [0.0, 2.0])

    def test_search_offset_recovers_known_shift(self):
        demo = np.array([10.0, 30.0, 55.0, 80.0])
        offset = 1234.5
        csi = demo + offset
        extra = np.array([5.0, 400.0, 2000.0, 3500.0])
        found = A.search_offset(demo, np.sort(np.concatenate([csi, extra])))
        self.assertAlmostEqual(found["offset_s"], offset, delta=0.05)
        self.assertEqual(found["matched"], 4)

    def test_two_demos_same_map_get_independent_offsets(self):
        csi = np.concatenate([
            np.array([10.0, 30.0, 50.0]) + 100.0,
            np.array([10.0, 30.0, 50.0]) + 5000.0,
        ])
        demos = [
            {"id": "a", "map": "de_inferno", "bursts": np.array([10.0, 30.0, 50.0]),
             "tmin": 10.0, "tmax": 60.0},
            {"id": "b", "map": "de_inferno", "bursts": np.array([10.0, 30.0, 50.0]),
             "tmin": 10.0, "tmax": 60.0},
        ]
        out = A.align_demos(demos, csi)
        offs = sorted(r["offset_s"] for r in out)
        self.assertAlmostEqual(offs[0], 100.0, delta=0.1)
        self.assertAlmostEqual(offs[1], 5000.0, delta=0.1)


class TestTimedCounter(unittest.TestCase):
    def test_gap_uses_timestamps_not_sample_index(self):
        t = np.arange(0.0, 50.0, 2.0)
        a = np.zeros(t.size, dtype=bool)
        d = np.zeros(t.size, dtype=bool)
        a[:10] = True
        d[15:] = True
        tr = A.axis_transition(t, a, d, left="A", right="D")
        self.assertAlmostEqual(tr["gap_ms"], 10.0, delta=0.1)

    def test_late_sample_is_not_counted_as_1ms(self):
        t = np.array([0.0, 1.0, 2.0, 3.0, 4.834, 6.0])
        a = np.array([True, True, True, False, False, False])
        d = np.array([False, False, False, False, True, True])
        tr = A.axis_transition(t, a, d, left="A", right="D")
        self.assertAlmostEqual(tr["gap_ms"], 1.834, delta=0.02)

    def test_overlap_is_local_to_the_selected_pair(self):
        t = np.arange(0.0, 400.0, 1.0)
        a = np.zeros(t.size, dtype=bool)
        d = np.zeros(t.size, dtype=bool)
        a[:40] = True
        d[:40] = True
        a[300:320] = True
        d[322:340] = True
        tr = A.axis_transition(t, a, d, left="A", right="D")
        self.assertAlmostEqual(tr["gap_ms"], 2.0, delta=1.0)
        self.assertLess(tr["overlap_ms"], 10.0)

    def test_own_is_last_release_not_majority_occupancy(self):
        t = np.arange(0.0, 300.0, 1.0)
        a = np.zeros(t.size, dtype=bool)
        d = np.zeros(t.size, dtype=bool)
        a[:200] = True
        d[210:230] = True
        a[232:260] = True
        tr = A.axis_transition(t, a, d, left="A", right="D")
        self.assertEqual(tr["own"], "D")
        self.assertEqual(tr["opp"], "A")
        self.assertAlmostEqual(tr["gap_ms"], 2.0, delta=1.0)


class TestClassifier(unittest.TestCase):
    def _cls(self, **kw):
        defaults = dict(
            map_name="de_mirage", residual_ms=5.0, speed=50.0,
            w_max=0, a_max=0, s_max=0, d_max=0,
        )
        defaults.update(kw)
        return A.classify_input(**defaults)

    def test_cache_is_skipped_for_input_cause(self):
        r = self._cls(map_name="de_cache", a_max=65535, residual_ms=0.0)
        self.assertFalse(r["sync_ok"])
        self.assertEqual(r["input_class"], "SKIP_CACHE")

    def test_wide_residual_is_sync_uncertain(self):
        r = self._cls(residual_ms=120.0, a_max=65535,
                      ad_transition={"own": "A", "opp": "D", "gap_ms": 10.0, "overlap_ms": 0.0,
                                     "own_down_at_end": False, "opp_down_at_end": True,
                                     "own_ever": True, "opp_ever": True})
        self.assertFalse(r["sync_ok"])
        self.assertEqual(r["input_class"], "SYNC_UNCERTAIN")

    def test_no_default_release_only_when_unclassified(self):
        r = self._cls(speed=80.0)
        self.assertNotEqual(r["input_class"], "RELEASE_ONLY")
        self.assertIn(r["input_class"], ("TRANSITION_AMBIGUOUS", "UNCLASSIFIED"))

    def test_mixed_axis_activity_is_not_a_diagonal_counter(self):
        r = self._cls(w_max=65535, a_max=65535, speed=60.0)
        self.assertTrue(r["ws_involved"])
        self.assertEqual(r["input_class"], "MIXED_AXIS_UNRESOLVED")
        self.assertNotEqual(r["input_class"], "DIAGONAL")
        self.assertNotEqual(r["input_class"], "RELEASE_ONLY")

    def test_ad_without_pair_is_release_without_opposite(self):
        r = self._cls(a_max=65535, speed=60.0)
        self.assertFalse(r["ws_involved"])
        self.assertEqual(r["input_class"], "LATERAL_RELEASE_WITHOUT_OPPOSITE")

    def test_ad_held_through_shot(self):
        r = self._cls(
            a_max=65535, speed=60.0,
            ad_transition={"own": "A", "opp": None, "gap_ms": None, "overlap_ms": 0.0,
                           "own_down_at_end": True, "opp_down_at_end": False,
                           "own_ever": True, "opp_ever": False},
        )
        self.assertEqual(r["input_class"], "LATERAL_HELD_THROUGH_SHOT")

    def test_clean_lateral_counter(self):
        r = self._cls(a_max=65535, d_max=65535, speed=40.0,
                      ad_transition={"own": "A", "opp": "D", "gap_ms": 14.0, "overlap_ms": 0.0,
                                     "own_down_at_end": False, "opp_down_at_end": True,
                                     "own_ever": True, "opp_ever": True})
        self.assertEqual(r["input_class"], "LATERAL_COUNTER")
        self.assertEqual(r["counter_quality"], "CLEAN")

    def test_delayed_lateral_counter(self):
        r = self._cls(a_max=65535, d_max=65535,
                      ad_transition={"own": "A", "opp": "D", "gap_ms": 140.0, "overlap_ms": 0.0,
                                     "own_down_at_end": False, "opp_down_at_end": True,
                                     "own_ever": True, "opp_ever": True})
        self.assertEqual(r["input_class"], "LATERAL_COUNTER")
        self.assertEqual(r["counter_quality"], "DELAYED")

    def test_overlap_lateral_counter(self):
        r = self._cls(a_max=65535, d_max=65535,
                      ad_transition={"own": "A", "opp": "D", "gap_ms": 5.0, "overlap_ms": 80.0,
                                     "own_down_at_end": False, "opp_down_at_end": True,
                                     "own_ever": True, "opp_ever": True})
        self.assertEqual(r["input_class"], "LATERAL_COUNTER")
        self.assertEqual(r["counter_quality"], "OVERLAP")

    def test_ws_flag_does_not_hide_lateral_counter(self):
        r = self._cls(w_max=65535, a_max=65535, d_max=65535,
                      ad_transition={"own": "A", "opp": "D", "gap_ms": 14.0, "overlap_ms": 0.0,
                                     "own_down_at_end": False, "opp_down_at_end": True,
                                     "own_ever": True, "opp_ever": True})
        self.assertTrue(r["ws_involved"])
        self.assertEqual(r["input_class"], "LATERAL_COUNTER")

    def test_stationary(self):
        r = self._cls(map_name="de_dust2", residual_ms=2.0, speed=8.0)
        self.assertEqual(r["input_class"], "STATIONARY_NO_RECENT_MOVEMENT")

    def test_wa_to_sd_is_diagonal_counter(self):
        r = self._cls(
            w_max=65535, a_max=65535, s_max=65535, d_max=65535,
            ad_transition={"own": "A", "opp": "D", "gap_ms": 8.0, "overlap_ms": 0.0,
                           "own_down_at_end": False, "opp_down_at_end": True,
                           "own_ever": True, "opp_ever": True},
            ws_transition={"own": "W", "opp": "S", "gap_ms": 8.0, "overlap_ms": 0.0,
                           "own_down_at_end": False, "opp_down_at_end": True,
                           "own_ever": True, "opp_ever": True},
        )
        self.assertEqual(r["input_class"], "DIAGONAL_COUNTER")

    def test_mirrored_wd_to_sa_is_also_diagonal_counter(self):
        r = self._cls(
            w_max=65535, a_max=65535, s_max=65535, d_max=65535,
            ad_transition={"own": "D", "opp": "A", "gap_ms": 8.0, "overlap_ms": 0.0,
                           "own_down_at_end": False, "opp_down_at_end": True,
                           "own_ever": True, "opp_ever": True},
            ws_transition={"own": "W", "opp": "S", "gap_ms": 8.0, "overlap_ms": 0.0,
                           "own_down_at_end": False, "opp_down_at_end": True,
                           "own_ever": True, "opp_ever": True},
        )
        self.assertEqual(r["input_class"], "DIAGONAL_COUNTER")


class TestKeyEvents(unittest.TestCase):
    def test_analog_threshold_crossing_emits_down_and_up(self):
        t = np.arange(0.0, 10.0, 1.0)
        analog = {
            "W": np.zeros(10), "A": np.array([0, 0, 20000, 20000, 20000, 0, 0, 0, 0, 0], dtype=float),
            "S": np.zeros(10), "D": np.zeros(10),
        }
        mask = np.zeros(10, dtype=np.uint16)
        ev = A.key_events(t, analog, mask)
        kinds = [(e["key"], e["kind"]) for e in ev]
        self.assertIn(("A", "analog_down"), kinds)
        self.assertIn(("A", "analog_up"), kinds)

    def test_digital_edges_are_separate_from_analog(self):
        t = np.arange(0.0, 6.0, 1.0)
        analog = {k: np.zeros(6) for k in "WASD"}
        analog["D"] = np.array([0, 0, 30000, 30000, 0, 0], dtype=float)
        mask = np.array([0, 0, 0x08, 0x08, 0, 0], dtype=np.uint16)
        ev = A.key_events(t, analog, mask)
        kinds = {e["kind"] for e in ev if e["key"] == "D"}
        self.assertIn("analog_down", kinds)
        self.assertIn("digital_down", kinds)


class TestSummarize(unittest.TestCase):
    def test_standing_headline_is_separate_from_all_posture(self):
        import pandas as pd
        df = pd.DataFrame({
            "in_warmup": [False] * 4,
            "in_freeze": [False] * 4,
            "is_first_bullet": [True] * 4,
            "is_headline_rifle": [True] * 4,
            "rifle_accurate": [True, True, True, False],
            "is_airborne": [False, False, False, False],
            "duck_at_fire": [0.0, 0.0, 1.0, 0.0],
            "speed_at_fire": [10.0, 40.0, 5.0, 180.0],
            "sync_ok": [True, False, True, False],
            "residual_ms": [4.0, 80.0, 2.0, None],
            "mouse1_ms": [1.0, 2.0, 3.0, None],
            "input_class": ["LATERAL_COUNTER", "SYNC_UNCERTAIN", "LATERAL_COUNTER", "SYNC_UNCERTAIN"],
            "map": ["de_mirage"] * 4,
        })
        s = A.summarize_shots(df)
        self.assertEqual(s["rifles"], 4)
        self.assertEqual(s["rifles_accurate"], 3)
        self.assertEqual(s["standing_rifles"], 3)
        self.assertEqual(s["standing_accurate"], 2)
        self.assertEqual(s["crouched_rifles"], 1)
        self.assertEqual(s["sync_ok"], 2)
        self.assertEqual(s["mouse1_candidates"], 3)


class TestOffsetByDemo(unittest.TestCase):
    def _align(self, demos):
        return {"schema": 2, "matches": [
            {"demo": d, "map": m, "offset_s": o}
            for d, m, o in demos
        ]}

    def test_two_demos_same_map_keep_own_offsets(self):
        align = self._align([
            ("one.dem", "de_inferno", 100.0),
            ("two.dem", "de_inferno", 5000.0),
        ])
        out = A_offset_by_demo(align)
        self.assertEqual(out["one.dem"], 100.0)
        self.assertEqual(out["two.dem"], 5000.0)

    def test_windows_and_forward_slash_paths_resolve_to_basename(self):
        align = self._align([
            (r"C:\replays\one.dem", "de_inferno", 100.0),
            ("C:/replays/two.dem", "de_inferno", 5000.0),
        ])
        out = A_offset_by_demo(align)
        self.assertEqual(sorted(out), ["one.dem", "two.dem"])

    def test_conflicting_offsets_for_same_demo_raise(self):
        align = self._align([
            ("one.dem", "de_inferno", 100.0),
            ("one.dem", "de_inferno", 200.0),
        ])
        with self.assertRaises(SystemExit):
            A_offset_by_demo(align)

    def test_schema1_without_matches_is_empty(self):
        self.assertEqual(A_offset_by_demo({"de_inferno": {"offset_s": 1.0}}), {})


class TestGoldenParquet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parent / "shots.parquet"
        if not path.exists():
            cls.df = None
            return
        import pandas as pd
        cls.df = pd.read_parquet(path)

    def test_locked_demo_side_counts(self):
        if self.df is None:
            self.skipTest("shots.parquet missing")
        s = A.summarize_shots(self.df)
        self.assertEqual(s["shots"], 783)
        self.assertEqual(s["live"], 783)
        self.assertEqual(s["first_bullets"], 393)
        self.assertEqual(s["rifles"], 170)
        self.assertEqual(s["rifles_accurate"], 115)
        self.assertEqual(s["standing_rifles"], 145)
        self.assertEqual(s["standing_accurate"], 92)
        self.assertEqual(s["crouched_rifles"], 24)
        self.assertEqual(s["crouched_accurate"], 22)

    def test_per_demo_counts_locked(self):
        # Locks per-demo extraction, not just aggregates: one demo losing
        # rows while totals stay plausible would pass the aggregate test.
        if self.df is None:
            self.skipTest("shots.parquet missing")
        per_demo = self.df.groupby("demo").size().to_dict()
        self.assertEqual(per_demo, {
            "match730_003840286046407361025_1338603495_187.dem": 292,
            "match730_003840287571120751068_0576888176_184.dem": 49,
            "match730_003840291013537038882_0808600625_274.dem": 121,
            "match730_003840296025763873033_0870652904_272.dem": 321,
        })

    def test_no_missing_speeds(self):
        if self.df is None:
            self.skipTest("shots.parquet missing")
        self.assertEqual(int(self.df.speed_at_fire.isna().sum()), 0)

    def test_every_row_has_threshold(self):
        if self.df is None:
            self.skipTest("shots.parquet missing")
        # every extracted shot's weapon must be in WEAPON_MAX_SPEED
        self.assertEqual(int(self.df.accuracy_threshold.isna().sum()), 0)
        unknown = set(self.df.weapon) - set(A.WEAPON_MAX_SPEED)
        self.assertEqual(unknown, set())


if __name__ == "__main__":
    unittest.main()
