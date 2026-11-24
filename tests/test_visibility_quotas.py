import unittest
from tools.ke_visibility_quotas import visibility_targets, choose_candidates, candidate_allocation
from ke_generate import validate_config


class VisibilityQuotaTests(unittest.TestCase):
    def test_targets_exact(self):
        self.assertEqual(visibility_targets(200), {"open":80,"light":50,"moderate":50,"heavy":20})
        for n in range(1, 1001):
            self.assertEqual(sum(visibility_targets(n).values()), n)

    def test_selection_exact_deterministic_and_reports_deficit(self):
        rows = [{"image_id": i, "map_id": "a" if i % 2 else "b",
                 "visibility_measurement": {"visibility_bin": b, "training_eligible": b != "reject_too_occluded"}}
                for i,b in enumerate(["open","open","light","moderate","heavy","reject_too_occluded"],1)]
        targets = {"open":1,"light":1,"moderate":1,"heavy":1}
        selected, report = choose_candidates(rows, targets)
        self.assertEqual(len(selected),4)
        self.assertNotIn(6,selected)
        self.assertTrue(report["complete"])
        self.assertEqual(choose_candidates(rows, targets), (selected,report))
        _, report = choose_candidates(rows, {**targets,"heavy":2})
        self.assertEqual(report["deficit"]["heavy"],1)
        self.assertFalse(report["complete"])

    def test_budget_and_validation(self):
        self.assertEqual(candidate_allocation([("a",10),("b",900)],5), [("a",50),("b",1000)])
        with self.assertRaises(ValueError):
            validate_config({"enforce_visibility_quotas": True})
        with self.assertRaises(ValueError):
            validate_config({"visibility_candidate_multiplier": 21})
