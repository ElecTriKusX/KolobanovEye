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

    def test_exact_balance_requires_residual_reassignment(self):
        # Scarce open first on a, but heavy can only come from a.
        # A greedy choice of open/a would lose a feasible solution.
        targets = {"open": 1, "light": 0, "moderate": 0, "heavy": 1}
        rows = [{"image_id": i, "map_id": m,
                 "visibility_measurement": {"visibility_bin": b, "training_eligible": True}}
                for i, m, b in [(1, "a", "open"), (2, "b", "open"),
                                (3, "a", "heavy"), (4, "a", "heavy")]]
        chosen, report = choose_candidates(rows, targets, {"a": 1, "b": 1})
        self.assertTrue(report["complete"])
        self.assertEqual(chosen, {2, 3})
        self.assertEqual(report["selected_by_map"], {"a": 1, "b": 1})
        self.assertEqual(report["selected_by_map_and_bin"]["b"]["open"], 1)

    def test_missing_map_never_silently_reallocated(self):
        rows = [{"image_id": i, "map_id": "a",
                 "visibility_measurement": {"visibility_bin": "open", "training_eligible": True}}
                for i in range(4)]
        targets = {"open": 4, "light": 0, "moderate": 0, "heavy": 0}
        selected, report = choose_candidates(rows, targets, {"a": 2, "b": 2})
        self.assertEqual(len(selected), 2)
        self.assertFalse(report["complete"])
        self.assertEqual(report["deficit_by_map"], {"a": 0, "b": 2})
        self.assertEqual(report["deficit"]["open"], 2)

    def test_small_pools_match_exhaustive_search(self):
        import itertools
        import random
        from collections import Counter
        rng = random.Random(42)
        for _ in range(100):
            rows = [{"image_id": i, "map_id": rng.choice(["a", "b", "c"]),
                     "visibility_measurement": {"visibility_bin": rng.choice(["open", "light", "heavy"]),
                                                "training_eligible": rng.choice([True, True, False])}}
                    for i in range(8)]
            target = {"open": 1, "light": 2, "moderate": 0, "heavy": 1}
            maps = {"a": 2, "b": 1, "c": 1}
            selected, report = choose_candidates(rows, target, maps)
            best = 0
            eligible = [r for r in rows if r["visibility_measurement"]["training_eligible"]]
            for n in range(len(eligible) + 1):
                for subset in itertools.combinations(eligible, n):
                    mc = Counter(r["map_id"] for r in subset)
                    bc = Counter(r["visibility_measurement"]["visibility_bin"] for r in subset)
                    if all(mc[m] <= cap for m, cap in maps.items()) and all(bc[b] <= cap for b, cap in target.items()):
                        best = max(best, n)
            self.assertEqual(len(selected), best)
            self.assertEqual(report["complete"], best == 4)

    def test_budget_and_validation(self):
        self.assertEqual(candidate_allocation([("a",10),("b",900)],5), [("a",50),("b",1000)])
        with self.assertRaises(ValueError):
            validate_config({"enforce_visibility_quotas": True})
        with self.assertRaises(ValueError):
            validate_config({"visibility_candidate_multiplier": 21})
