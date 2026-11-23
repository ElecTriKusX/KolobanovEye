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

class OrbitPlanTest(unittest.TestCase):
    def test_seed_reproducible_and_ranges(self):
        from kolobanoveye import plan_camera_orbits
        plan = plan_camera_orbits(42, 100, (18, 35), (10, 25))
        self.assertEqual(plan, plan_camera_orbits(42, 100, (18, 35), (10, 25)))
        self.assertNotEqual(plan, plan_camera_orbits(43, 100, (18, 35), (10, 25)))
        for camera in plan:
            self.assertTrue(-180 <= camera['azimuth_deg'] <= 180)
            self.assertTrue(18 <= camera['horizontal_distance'] <= 35)
            self.assertTrue(10 <= camera['elevation_deg'] <= 25)
            x, y, z = camera['offset']
            self.assertAlmostEqual(math.hypot(x, z), camera['horizontal_distance'], places=5)
            self.assertAlmostEqual(math.degrees(math.atan2(y, math.hypot(x, z))), camera['elevation_deg'], places=4)

    def test_invalid_ranges_and_fixed_values(self):
        from kolobanoveye import plan_camera_orbits
        for distance, elevation in [((35, 18), (10, 25)), ((0, 10), (10, 25)),
                                    ((20, 30), (-1, 25)), ((20, 30), (10, 90)),
                                    ((float('nan'), 30), (10, 25))]:
            with self.subTest(distance=distance, elevation=elevation), self.assertRaises(ValueError):
                plan_camera_orbits(42, 1, distance, elevation)
        plan = plan_camera_orbits(1, 3, (20, 20), (10, 10))
        self.assertTrue(all(c['horizontal_distance'] == 20 and c['elevation_deg'] == 10 for c in plan))

class CameraRetryMetadataTest(unittest.TestCase):
    def test_selects_actual_attempt_and_rejects_bad_metadata(self):
        from kolobanoveye import select_camera_attempt
        candidates = [{'offset': [0,1,2]}, {'offset': [3,4,5]}]
        self.assertEqual(select_camera_attempt('camera_attempt:i=1\n', candidates), (1, candidates[1]))
        for raw in ('', 'camera_attempt:i=2\n', 'camera_attempt:i=-1\n'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                select_camera_attempt(raw, candidates)
