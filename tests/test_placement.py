import math
import unittest
from kolobanoveye import plan_placements
from ke_generate import DEFAULTS, command, validate_config

class PlacementTest(unittest.TestCase):
    def test_reproducibility_and_disc(self):
        candidates = plan_placements(42, (100, -50), 200, 24)
        self.assertEqual(candidates, plan_placements(42, (100, -50), 200, 24))
        self.assertNotEqual(candidates, plan_placements(43, (100, -50), 200, 24))
        self.assertTrue(all(math.hypot(x-100,z+50) <= 200.0001 for x,z in candidates))
        self.assertEqual(plan_placements(42, (1,2), 0, 24), [(1,2)])
        for radius, attempts in ((-1,3),(float('nan'),3),(10,65),(10,0)):
            with self.assertRaises(ValueError): plan_placements(1,(0,0),radius,attempts)

    def test_settings_passed_to_engine(self):
        c=validate_config({**DEFAULTS,'placement_radius':100, 'placement_attempts':7, 'max_height_range':0.4})
        cmd=command(c)
        self.assertEqual(cmd[cmd.index('--placement-attempts')+1], '7')
        self.assertEqual(cmd[cmd.index('--max-height-range')+1], '0.4')
        with self.assertRaises(ValueError): validate_config({**DEFAULTS,'placement_attempts':65})

    def test_multiple_compositions_and_final_partial_group(self):
        from kolobanoveye import plan_compositions
        a = plan_compositions(42, (0,0), 50, 4, 7, 3)
        self.assertEqual(len(a), 3)
        self.assertEqual([len(x) for x in a], [4,4,4])
        self.assertEqual(a, plan_compositions(42, (0,0), 50, 4, 7, 3))
        self.assertEqual(len({group[0] for group in a}), 3)
        self.assertEqual(plan_compositions(1,(0,0),0,4,2,1), [[(0,0)],[(0,0)]])

class WholeMapPlacementTest(unittest.TestCase):
    def test_seed_margin_and_attempts(self):
        from kolobanoveye import plan_map_compositions
        groups = plan_map_compositions(42, 15, 8, 7, 3)
        self.assertEqual(len(groups), 3)
        self.assertEqual([len(g) for g in groups], [8, 8, 8])
        self.assertEqual(groups, plan_map_compositions(42, 15, 8, 7, 3))
        self.assertNotEqual(groups, plan_map_compositions(43, 15, 8, 7, 3))
        self.assertTrue(all(.15 <= x <= .85 and .15 <= z <= .85 for g in groups for x,z in g))
        for margin in (-1, 50, float('nan')):
            with self.assertRaises(ValueError): plan_map_compositions(42, margin, 8, 7, 3)

    def test_mode_and_margin_forwarded(self):
        args = command({'placement_mode': 'whole_map', 'map_margin_percent': 20})
        self.assertEqual(args[args.index('--placement-mode') + 1], 'whole_map')
        self.assertEqual(args[args.index('--map-margin-percent') + 1], '20')
        with self.assertRaises(ValueError): validate_config({'map_margin_percent': 50})
