import unittest

from ke_generate import validate_config
from tools.ke_refill import plan_refill_round
from tools.ke_visibility_quotas import choose_candidates, visibility_targets


class RefillTests(unittest.TestCase):
    def test_large_target_with_bounded_round_and_budget(self):
        targets = visibility_targets(250000)
        self.assertEqual(sum(targets.values()), 250000)
        cfg = validate_config({"count": 250000, "capture_visibility_masks": True,
                               "enforce_visibility_quotas": True})
        self.assertEqual(cfg["refill_batch_per_map"], 256)
        maps = {str(i): 12500 for i in range(20)}
        _, report = choose_candidates([], targets, maps)
        jobs, _ = plan_refill_round(report, dict.fromkeys(maps, 0), 999, 256, initial=True)
        self.assertEqual(sum(n for _, n in jobs), 999)
        self.assertLessEqual(max(n for _, n in jobs), 256)
        self.assertLessEqual(max(n for _, n in jobs) - min(n for _, n in jobs), 1)
        with self.assertRaises(ValueError):
            validate_config({"count": 1001})
        with self.assertRaises(ValueError):
            validate_config({"refill_batch_per_map": 1001})

    def test_residual_exchange_requests_filled_category(self):
        targets = {"open": 1, "light": 0, "moderate": 0, "heavy": 1}
        maps = {"a": 1, "b": 1}
        rows = [{"image_id": i, "map_id": "a",
                 "visibility_measurement": {"visibility_bin": b, "training_eligible": True}}
                for i, b in enumerate(("open", "heavy"), 1)]
        _, report = choose_candidates(rows, targets, maps)
        jobs, weights = plan_refill_round(report, {"a": 2, "b": 0}, 1, 256)
        self.assertEqual(jobs, [("b", 1)])
        self.assertGreater(weights["b"]["open"], 0)
        self.assertGreater(weights["b"]["heavy"], 0)
        self.assertEqual(plan_refill_round(report, {"a": 2, "b": 0}, 0, 256), ([], {}))

    def test_complete_never_schedules_more(self):
        targets = {"open": 1, "light": 0, "moderate": 0, "heavy": 0}
        _, report = choose_candidates([{"image_id": 1, "map_id": "a",
            "visibility_measurement": {"visibility_bin": "open", "training_eligible": True}}],
            targets, {"a": 1})
        self.assertEqual(plan_refill_round(report, {"a": 1}, 100, 256), ([], {}))
