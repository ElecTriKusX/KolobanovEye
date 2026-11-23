import unittest
from tools.ke_pilot import plan

class PilotTest(unittest.TestCase):
    def test_seeded_plan(self):
        self.assertEqual(plan(42, 3), plan(42, 3))
        self.assertNotEqual(plan(42, 3), plan(43, 3))
        self.assertEqual(len(set(plan(42, 3))), 3)
        self.assertTrue(all(-180 <= x <= 180 for x in plan(42, 100)))
