import unittest
from tools.ke_avf import search_visibility


class AVFTests(unittest.TestCase):
    def test_refines_cyclic_boundary(self):
        def oracle(views):
            return [1. if 15 < v.azimuth_deg < 345 else
                    max(0., min(1., (min(v.azimuth_deg, 360-v.azimuth_deg)-2)/13))
                    for v in views]
        result = search_visibility(oracle, "moderate", rings=1, coarse=12,
                                   budget=60, seed=0)
        self.assertTrue(result.matched)
        self.assertGreater(len(result.probes), 12)
        self.assertLessEqual(len(result.probes), 60)
        self.assertEqual(len({p.view for p in result.probes}), len(result.probes))

    def test_invalid_is_not_occluded(self):
        result = search_visibility(lambda vs: [None]*len(vs), "heavy",
                                   rings=1, coarse=4, budget=12)
        self.assertIsNone(result.best)
        self.assertFalse(result.matched)

    def test_constant_open_does_not_fake_heavy(self):
        result = search_visibility(lambda vs: [1.]*len(vs), "heavy",
                                   rings=1, coarse=4, budget=12)
        self.assertFalse(result.matched)
        self.assertEqual(result.best.fraction, 1.)
        self.assertEqual(len(result.probes), 12)

    def test_boundary_and_reproducibility(self):
        oracle = lambda vs: [.65]*len(vs)
        a = search_visibility(oracle, "light", rings=1, coarse=4, budget=8)
        b = search_visibility(oracle, "light", rings=1, coarse=4, budget=8)
        self.assertEqual(a, b)
        self.assertTrue(a.matched)
        self.assertFalse(search_visibility(oracle, "moderate", rings=1,
                                           coarse=4, budget=8).matched)

    def test_batch_contract(self):
        with self.assertRaises(ValueError):
            search_visibility(lambda vs: [], "open")
        with self.assertRaises(ValueError):
            search_visibility(lambda vs: [float("nan")]*len(vs), "open")
        with self.assertRaises(ValueError):
            search_visibility(lambda vs: [1.]*len(vs), "open", budget=2)


if __name__ == "__main__":
    unittest.main()
