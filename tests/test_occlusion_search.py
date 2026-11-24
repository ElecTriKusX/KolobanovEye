import tempfile
import unittest
from pathlib import Path

from tools.ke_dagor_occlusion import EDITS, install_occlusion_search
from tools.ke_occlusion import plan_occlusion_targets, read_occlusion_proxy
from ke_generate import validate_config


class OcclusionTests(unittest.TestCase):
    def test_native_installer_is_idempotent_and_preserves_crlf(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "capture.cpp"
            old = "\n".join(old for old, _ in EDITS)
            p.write_bytes(old.replace("\n", "\r\n").encode())
            self.assertTrue(install_occlusion_search(p))
            result = p.read_bytes()
            self.assertIn(b"traceRayRendInstsNormalized", result)
            self.assertIn(b"tracerayNormalizedHeightmap", result)
            self.assertIn(b"if (!keSearchOcclusion)", result)
            self.assertEqual(result.count(b"\r\n"), result.count(b"\n"))
            self.assertFalse(install_occlusion_search(p))
            self.assertEqual(p.read_bytes(), result)

    def test_missing_anchor_does_not_mutate(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "capture.cpp"
            p.write_text(EDITS[0][0])
            before = p.read_bytes()
            with self.assertRaises(ValueError):
                install_occlusion_search(p)
            self.assertEqual(before, p.read_bytes())

    def test_target_plan_and_proxy_is_not_ground_truth(self):
        plan = plan_occlusion_targets(42, 20, {"open":40,"light":25,"moderate":25,"heavy":10})
        self.assertEqual(plan.count("heavy"), 2)
        self.assertEqual(plan, plan_occlusion_targets(42,20,{"open":40,"light":25,"moderate":25,"heavy":10}))
        blk = 'occlusion_target_bin:t="heavy"\nocclusion_proxy_fraction:r=0.2\nocclusion_proxy_candidates:i=5\n'
        measured = {"visibility_bin":"open", "visible_fraction":.95}
        result = read_occlusion_proxy(blk,"heavy",8,measured)
        self.assertFalse(result["target_match"])
        self.assertAlmostEqual(result["absolute_error"], .75)
        with self.assertRaises(ValueError):
            read_occlusion_proxy(blk,"open",8,measured)
        with self.assertRaises(ValueError):
            validate_config({"search_occlusion": True})
