import unittest
from tools.ke_adaptive_quotas import AdaptiveQuotaPlanner
from tools.ke_visibility_quotas import choose_candidates, visibility_targets


def row(image_id, mid, b):
    return {"image_id": image_id, "map_id": mid,
            "visibility_measurement": {"visibility_bin": b,
                                       "training_eligible": b != "reject_too_occluded"}}


class AdaptiveQuotaTests(unittest.TestCase):
    def test_ideal_oracle_stops_at_exact_target(self):
        p = AdaptiveQuotaPlanner(visibility_targets(200),
                                 dict.fromkeys("abcd", 50), dict.fromkeys("abcd", 250))
        for i in range(200):
            mid, b = p.next_request()
            p.finish(row(i, mid, b))
        self.assertIsNone(p.next_request())
        self.assertTrue(p.status()["complete"])
        self.assertEqual(p.status()["selected_by_map"], dict.fromkeys("abcd", 50))
        self.assertEqual(sum(p.status()["attempts_by_map"].values()), 200)
        self.assertEqual(len(p.selected), 200)

    def test_proxy_misses_redistribute_without_forcing_equal_bin_mix(self):
        targets = {"open": 4, "light": 0, "moderate": 0, "heavy": 4}
        p = AdaptiveQuotaPlanner(targets, {"a": 4, "b": 4}, {"a": 12, "b": 12})
        i = 0
        while request := p.next_request():
            mid, _ = request
            p.finish(row(i, mid, "heavy" if mid == "a" else "open"))
            i += 1
        self.assertTrue(p.status()["complete"])
        self.assertEqual(p.status()["selected_by_map_and_bin"]["a"]["heavy"], 4)
        self.assertEqual(p.status()["selected_by_map_and_bin"]["b"]["open"], 4)
        self.assertLessEqual(i, 24)

    def test_impossible_quota_stops_and_does_not_fill_with_open(self):
        p = AdaptiveQuotaPlanner({"open": 2, "light": 0, "moderate": 0, "heavy": 2},
                                 {"a": 2, "b": 2}, {"a": 5, "b": 5})
        i = 0
        while request := p.next_request():
            p.finish(row(i, request[0], "open"))
            i += 1
        self.assertEqual(i, 10)
        self.assertEqual(p.status()["status"], "INCOMPLETE")
        self.assertEqual(p.status()["deficit"]["heavy"], 2)
        self.assertEqual(len(p.selected), 2)

    def test_inflight_duplicate_and_cheap_rejection(self):
        p = AdaptiveQuotaPlanner({"open": 2, "light": 0, "moderate": 0, "heavy": 0},
                                 {"a": 2}, {"a": 4})
        p.next_request()
        with self.assertRaises(RuntimeError):
            p.next_request()
        p.finish()
        self.assertEqual(len(p.selected), 0)
        p.next_request()
        p.finish(row(1, "a", "open"))
        p.next_request()
        with self.assertRaises(ValueError):
            p.finish(row(1, "a", "open"))
        p.finish(row(2, "a", "open"))
        self.assertEqual(p.status()["status"], "COMPLETE")
        self.assertEqual(sum(p.status()["attempts_by_map"].values()), 3)

    def test_residual_useful_pair_can_reassign_a_filled_bin(self):
        targets = {"open": 1, "light": 0, "moderate": 0, "heavy": 1}
        # Open is already full on a, heavy can only come from a for now.
        # A new open on b frees a's slot for heavy.
        records = [row(1, "a", "open"), row(2, "a", "heavy")]
        selected, report = choose_candidates(records, targets, {"a": 1, "b": 1})
        self.assertEqual(len(selected), 1)
        # Whichever current flow was chosen, both bins can augment on b.
        self.assertEqual(set(report["useful_next_bins_by_map"]["b"]), {"open", "heavy"})
        self.assertFalse(report["useful_next_bins_by_map"]["a"])
        self.assertTrue(choose_candidates(records + [row(3, "b", "open")],
                                         targets, {"a": 1, "b": 1})[1]["complete"])
