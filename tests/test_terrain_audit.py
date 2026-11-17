import unittest
from ke_terrain_audit import analyze


class TerrainAuditTest(unittest.TestCase):
    def test_range_and_unknown_are_distinct(self):
        def rec(i, blk): return {'frame_index': i, 'dagor_frame_metadata_blk': blk}
        result = analyze([rec(0, 'support_height_samples:i=4\nsupport_height_min:r=10\nsupport_height_max:r=10.4\ncamera_clearance:r=2\n'),
                          rec(1, 'support_height_samples:i=4\nsupport_height_min:r=10\nsupport_height_max:r=11\n'),
                          rec(2, 'support_height_samples:i=2\nsupport_height_min:r=10\nsupport_height_max:r=10\n'),
                          rec(3, '')])
        self.assertEqual([f['heightmap_range_result'] for f in result['frames']],
                         ['within_threshold', 'above_threshold', 'unknown', 'unknown'])
        self.assertTrue(all(f['obstacle_check']=='unknown' and f['water_check']=='unknown' for f in result['frames']))
        self.assertEqual(result['frames'][0]['camera_clearance'], 2)
        self.assertIsNone(result['frames'][3]['camera_clearance'])
        water = analyze([rec(4, 'water_plane_y:r=12.5\nterrain_above_water_plane:r=-0.2\n')])['frames'][0]
        self.assertEqual(water['water_plane_y'], 12.5)
        self.assertEqual(water['terrain_above_water_plane'], -0.2)
        self.assertEqual(water['water_check'], 'unknown')
        with self.assertRaises(ValueError): analyze([], 0.5)
        with self.assertRaises(ValueError): analyze([rec(0, '')], float('nan'))
