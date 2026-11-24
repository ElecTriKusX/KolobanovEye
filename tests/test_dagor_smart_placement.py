import unittest
from tools.ke_dagor_smart_placement import upgrade_capture, EDITS


class DagorSmartPlacementTests(unittest.TestCase):
    def test_patch_idempotent_and_checks_before_accept(self):
        src="\n".join(old for old,_ in EDITS)
        patched=upgrade_capture(src)
        self.assertEqual(upgrade_capture(patched),patched)
        self.assertLess(patched.index("SMART_PLACEMENT_REJECT"),patched.index("    requested = candidate;"))
        self.assertIn("keOnlineDecision(dir, sceneIndex",patched)
        self.assertIn("ri_collision_bounds_static_rays_v1",patched)
        with self.assertRaises(ValueError):
            upgrade_capture(patched.replace("smart_placement_source","bad"))
