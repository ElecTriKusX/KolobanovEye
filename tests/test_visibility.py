import unittest

from tools.ke_visibility import compare_reverse_z_depth, visibility_bin


class VisibilityDepthTests(unittest.TestCase):
    def test_reverse_z_occlusion_and_exact_bbox(self):
        # 4 x 3, 5 tank pixels: 2 visible and 3 blocked by nearer terrain.
        tank = [0.0] * 12
        scene = [0.0] * 12
        for i in (1, 2, 5, 6, 9):
            tank[i] = 0.15
            scene[i] = 0.25
        for i in (2, 6):
            scene[i] = tank[i]
        metrics = compare_reverse_z_depth(scene, tank, 4, 3)
        self.assertEqual(metrics["amodal_pixels"], 5)
        self.assertEqual(metrics["visible_pixels"], 2)
        self.assertAlmostEqual(metrics["visible_fraction"], .4)
        self.assertEqual(metrics["visibility_bin"], "moderate")
        self.assertEqual(metrics["bbox_visible_xywh"], [2, 0, 1, 2])
        self.assertEqual(metrics["bbox_amodal_xywh"], [1, 0, 2, 3])

    def test_empty_and_missing_scene_depth(self):
        metrics = compare_reverse_z_depth([0.0] * 4, [0.0] * 4, 2, 2)
        self.assertEqual(metrics["visibility_bin"], "reject_empty_amodal")
        self.assertIsNone(metrics["visible_fraction"])
        self.assertIsNone(metrics["bbox_visible_xywh"])
        metrics = compare_reverse_z_depth([0, 0, 0, 0], [.1, 0, .1, 0], 2, 2)
        self.assertEqual(metrics["visible_fraction"], 0)
        self.assertEqual(metrics["visibility_bin"], "reject_too_occluded")

    def test_epsilon_and_invalid_depth(self):
        m = compare_reverse_z_depth([.15, .1500001, .2, float("nan")],
                                    [.15, .15, .1, .4], 2, 2)
        self.assertEqual(m["visible_pixels"], 2)
        with self.assertRaises(ValueError):
            compare_reverse_z_depth([0], [0], 2, 2)
        with self.assertRaises(ValueError):
            compare_reverse_z_depth([0], [0], 1, 1, absolute_tolerance=-1)

    def test_bins(self):
        for f, label in [(1, "open"), (.9, "open"), (.89, "light"),
                         (.65, "light"), (.64, "moderate"), (.3, "moderate"),
                         (.29, "heavy"), (.1, "heavy"), (.09, "reject_too_occluded")]:
            self.assertEqual(visibility_bin(f), label)


if __name__ == "__main__":
    unittest.main()
