import unittest
from tools.ke_gui_policy import RECOMMENDED, TESTED_RANGES, effective_config, visible_rows

BASE = dict(RECOMMENDED, **TESTED_RANGES, smart_placement_cache="map-cache",
            scene_cache="scene-cache", tank_x=10., tank_z=20., placement_radius=30.,
            wait_sky_convergence=False)

class GuiPolicyTests(unittest.TestCase):
    def test_recommended_forces_pipeline_and_neutral_pose(self):
        raw=dict(BASE, placement_mode="around_point", frames_per_position="6",
                 capture_visibility_masks=False, render_debug=True, guided_sampling="off")
        c=effective_config(raw, True)
        for k,v in RECOMMENDED.items():
            self.assertEqual(c[k],v)
        self.assertEqual(c["smart_placement_cache"],"map-cache")
        self.assertEqual(c["scene_cache"],"scene-cache")
        self.assertEqual(c["tank_x"],0.)
        self.assertEqual(c["tank_z"],0.)
        self.assertIn("avf_backend",visible_rows(raw, True))
        self.assertNotIn("smart_placement_cache",visible_rows(raw, True))
        self.assertNotIn("scene_cache",visible_rows(raw, True))

    def test_manual_around_point_disables_map_dependencies(self):
        raw=dict(BASE,placement_mode="around_point")
        c=effective_config(raw)
        self.assertEqual(c["smart_placement_cache"],"")
        self.assertEqual(c["scene_cache"],"")
        self.assertEqual(c["avf_probe_size"],0)
        self.assertEqual(c["guided_sampling"],"off")
        self.assertEqual(c["tank_x"],10.)
        self.assertEqual(c["map_margin_percent"],0.)
        hidden=visible_rows(raw)
        self.assertIn("map_margin_percent",hidden)
        self.assertNotIn("tank_x",hidden)
        self.assertIn("smart_placement_cache",hidden)
        self.assertEqual(raw["smart_placement_cache"],"map-cache")

    def test_manual_masks_and_debug_dependencies(self):
        c=effective_config(dict(BASE,capture_visibility_masks=False))
        self.assertFalse(c["enforce_visibility_quotas"])
        self.assertFalse(c["search_occlusion"])
        self.assertEqual(c["avf_probe_size"],0)
        self.assertEqual(c["scene_cache"],"")
        c=effective_config(dict(BASE,render_debug=True))
        self.assertEqual(c["avf_probe_size"],0)
        self.assertEqual(c["guided_sampling"],"off")

    def test_manual_avf_off_hides_only_dependent_rows(self):
        raw=dict(BASE,avf_probe_size="0",guided_prerender="conservative")
        c=effective_config(raw)
        self.assertEqual(c["guided_prerender"],"off")
        self.assertEqual(c["scene_cache"],"")
        self.assertIn("guided_sampling",visible_rows(raw))
        self.assertNotIn("avf_probe_size",visible_rows(raw))
        self.assertNotIn("smart_placement_cache",visible_rows(raw))

    def test_recommended_missing_cache_does_not_silently_downgrade(self):
        raw=dict(BASE,smart_placement_cache="")
        self.assertEqual(effective_config(raw,True)["avf_probe_size"],128)
        self.assertNotIn("smart_placement_cache",visible_rows(raw,True))
