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
