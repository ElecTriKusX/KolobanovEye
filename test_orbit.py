import math
import unittest
from kolobanoveye import camera_orbit_offset


class OrbitTest(unittest.TestCase):
    def test_cardinal_angles_and_distance(self):
        for angle, expected in ((0, (0, 5, 20)), (90, (20, 5, 0)),
                                (180, (0, 5, -20)), (-90, (-20, 5, 0))):
            with self.subTest(angle=angle):
                result = camera_orbit_offset(angle)
                for actual, target in zip(result, expected):
                    self.assertAlmostEqual(actual, target, places=5)
                self.assertAlmostEqual(math.hypot(result[0], result[2]), 20, places=5)
