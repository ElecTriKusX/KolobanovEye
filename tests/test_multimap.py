"""Single-editor map quotas and stable map-specific seed tests."""
import unittest

from ke_generate import DEFAULTS
from ke_single_editor import jobs_for, map_seed


class MapBatchQuotaTests(unittest.TestCase):
    def test_deterministic_sorted_quotas(self):
        cfg = {**DEFAULTS, "count": 7, "map_ids": [
            "levels/avg_test2.bin", "levels/avg_test0.bin", "levels/avg_test1.bin"]}
        discovered = [{"id": f"levels/avg_test{i}.bin", "status": "header_ok"} for i in range(3)]
        self.assertEqual(jobs_for(cfg, discovered), [
            ("levels/avg_test0.bin", 3), ("levels/avg_test1.bin", 2), ("levels/avg_test2.bin", 2)])
        cfg["count"] = 2
        self.assertEqual([n for _, n in jobs_for(cfg, discovered)], [1, 1])
        self.assertEqual(map_seed(42, discovered[0]["id"]), map_seed(42, discovered[0]["id"]))
        self.assertNotEqual(map_seed(42, discovered[0]["id"]), map_seed(43, discovered[0]["id"]))

    def test_reject_missing_and_empty_selection(self):
        with self.assertRaises(ValueError):
            jobs_for({**DEFAULTS, "map_ids": ["levels/avg_missing.bin"]}, [])
        with self.assertRaises(ValueError):
            jobs_for({**DEFAULTS, "map_ids": ["levels/avg_normandy.bin"]},
                     [{"id": "levels/avg_normandy.bin", "status": "invalid"}])


if __name__ == "__main__":
    unittest.main()
