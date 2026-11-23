"""Contract tests for screen-position quotas, determinism and non-central sampling."""
import collections
import unittest

from tools.ke_composition import BINS, plan_screen_positions, validate_weights


class ScreenCompositionTests(unittest.TestCase):
    def test_exact_quota_for_100_frames(self):
        positions = plan_screen_positions(42, 100)
        self.assertEqual(collections.Counter(p["bin"] for p in positions),
                         {"center": 15, "offset": 65, "edge": 15, "near_clip": 5})

    def test_seed_and_coordinate_range(self):
        a = plan_screen_positions(42, 87)
        self.assertEqual(a, plan_screen_positions(42, 87))
        self.assertNotEqual(a, plan_screen_positions(43, 87))
        for p in a:
            self.assertIn(p["bin"], BINS)
            self.assertTrue(all(0 <= q <= 1 for q in p["target_uv"]))
            for u, offset in zip(p["target_uv"], p["offset_uv"]):
                self.assertAlmostEqual(u - .5, offset, places=5)

    def test_bins_have_distinct_geometries(self):
        for p in plan_screen_positions(4, 100):
            u, v = p["target_uv"]
            dist = max(abs(u-.5), abs(v-.5))
            if p["bin"] == "center":
                self.assertLessEqual(dist, .06)
            if p["bin"] == "edge":
                self.assertGreaterEqual(dist, .35)
            if p["bin"] == "near_clip":
                self.assertGreaterEqual(dist, .465)

    def test_one_frame_and_custom_weights(self):
        plan = plan_screen_positions(99, 1, {"center": 0, "offset": 1, "edge": 0, "near_clip": 0})
        self.assertEqual(plan[0]["bin"], "offset")
        self.assertEqual(len(plan_screen_positions(3, 1)), 1)

    def test_rejects_invalid_weights(self):
        for w in ({}, {"center": 0, "offset": 0, "edge": 0, "near_clip": 0},
                  {"center": 1, "offset": -1, "edge": 2, "near_clip": 0},
                  {"center": float("nan"), "offset": 1, "edge": 0, "near_clip": 0}):
            with self.subTest(w=w), self.assertRaises(ValueError):
                validate_weights(w)
        with self.assertRaises(ValueError):
            plan_screen_positions(42, 0)


if __name__ == "__main__":
    unittest.main()
