import tempfile
import unittest
from pathlib import Path

from tools.ke_avf import plan_camera_field
from tools.ke_dagor_avf_camera import (EDITS, FIELD_EDITS, FIELD_LOOP_OLD, ROI_MARKER,
    install_avf_camera, normalize_capture, normalize_roi)
from tools.ke_dagor_avf_probe import (BASE, EDITS as RENDER_EDITS, install_avf_probe,
    normalize_readback, paired_scene)
from tools.ke_occlusion import read_occlusion_proxy, summarize_search


class AVFNativeContractTests(unittest.TestCase):
    def test_camera_bank_is_reproducible_and_bounded(self):
        args = (42, 3, 64, (18, 35), (10, 25), (4, 16))
        frames = plan_camera_field(*args)
        self.assertEqual(frames, plan_camera_field(*args))
        for views in frames:
            self.assertEqual(len(views), 64)
            self.assertEqual(len({(v["avf_ring"], v["azimuth_deg"]) for v in views}), 64)
            for r in range(3):
                ring = [v for v in views if v["avf_ring"] == r]
                self.assertEqual(len({v["horizontal_distance"] for v in ring}), 1)
                self.assertEqual(len({v["height_above_ground"] for v in ring}), 1)
                self.assertTrue(all(18 <= v["horizontal_distance"] <= 35 and
                                    4 <= v["height_above_ground"] <= 16 for v in ring))

    def test_small_camera_banks(self):
        for n in (1, 2, 7, 8, 16, 32, 63, 64):
            self.assertEqual(len(plan_camera_field(1, 1, n, (18, 35), (10, 25), (4, 16))[0]), n)

    def test_camera_patch_idempotence_and_upgrade(self):
        original = "#include <cmath>\n" + "\n".join(old for old, _ in EDITS) + "\n" + FIELD_LOOP_OLD
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "capture.cpp"
            path.write_text(original, encoding="utf-8")
            self.assertTrue(install_avf_camera(path))
            installed = path.read_text(encoding="utf-8")
            self.assertEqual(normalize_capture(installed), original)
            self.assertFalse(install_avf_camera(path))
            v1 = original
            for old, new in EDITS:
                v1 = v1.replace(old, new, 1)
            path.write_text(v1, encoding="utf-8")
            self.assertTrue(install_avf_camera(path))
            self.assertEqual(path.read_text(encoding="utf-8"), installed)
            self.assertIn(ROI_MARKER, installed)
            v2 = normalize_roi(installed)
            self.assertNotIn(ROI_MARKER, v2)
            path.write_text(v2, encoding="utf-8")
            self.assertTrue(install_avf_camera(path))
            self.assertEqual(path.read_text(encoding="utf-8"), installed)

    def test_renderer_patch_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            header = root / BASE / "include/de3_dynRenderService.h"
            render = root / BASE / "services/dynRenderSrv/dynRender.cpp"
            for path in (header, render):
                path.parent.mkdir(parents=True, exist_ok=True)
            header.write_text("bool &depthMismatch) { depthMismatch = false; return false; }\n", encoding="utf-8")
            render.write_text("\n".join(old for old, _ in RENDER_EDITS["render"]) +
                              "\n  void closeIsolatedTargets()\n  {\n", encoding="utf-8")
            self.assertTrue(install_avf_probe(root))
            once = (header.read_bytes(), render.read_bytes())
            self.assertFalse(install_avf_probe(root))
            self.assertEqual(once, (header.read_bytes(), render.read_bytes()))
            render.write_text(normalize_readback(render.read_text(encoding='utf-8')), encoding='utf-8')
            self.assertTrue(install_avf_probe(root))
            self.assertEqual(once, (header.read_bytes(), render.read_bytes()))

    def test_both_draws_and_staging_requests_precede_first_map(self):
        code = paired_scene()
        first_request = code.index("request_readback_nosyslock(sceneTex, 0)")
        model_draw = code.index("render_model(Point3::ZERO)")
        second_request = code.index("request_readback_nosyslock(modelTex, 0)")
        first_map = code.index("if (!read(sceneTex, sceneDepth)")
        self.assertLess(first_request, model_draw)
        self.assertLess(model_draw, second_request)
        self.assertLess(second_request, first_map)

    def test_probe_telemetry(self):
        base = ('occlusion_target_bin:t="moderate"\n'
                'occlusion_proxy_fraction:r=1\nocclusion_proxy_candidates:i=2\n'
                'avf_probe_size:i=128\navf_probe_attempts:i=2\n'
                'avf_probe_valid:i=1\navf_probe_method:t="opaque_probe_v1"\n'
                'avf_probe_fraction:r=0.5\navf_probe_amodal_pixels:i=100\n'
                'avf_probe_visible_pixels:i=50\navf_probe_missing_pixels:i=0\n')
        truth = {"visible_fraction": .55, "visibility_bin": "moderate"}
        result = read_occlusion_proxy(base, "moderate", 8, truth, probe_size=128)
        self.assertAlmostEqual(result["avf"]["absolute_error"], .05)
        roi_blk = base + 'avf_probe_region:t="roi"\n'
        roi = read_occlusion_proxy(roi_blk, "moderate", 8, truth, probe_size=128, probe_region="roi")
        self.assertEqual(roi["avf"]["region"], "roi")
        with self.assertRaises(ValueError):
            read_occlusion_proxy(roi_blk, "moderate", 8, truth, probe_size=128, probe_region="full")
        with self.assertRaises(ValueError):
            read_occlusion_proxy(base, "moderate", 8, truth, probe_size=128, probe_region="roi")
        self.assertAlmostEqual(summarize_search([{"occlusion_search": result}])["avf"]["mean_absolute_error"], .05)
        with self.assertRaises(ValueError):
            read_occlusion_proxy(base.replace("avf_probe_fraction:r=0.5", "avf_probe_fraction:r=0.9"),
                                 "moderate", 8, truth, probe_size=128)
        with self.assertRaises(ValueError):
            read_occlusion_proxy(base, "moderate", 8, truth, probe_size=256)

    def test_fallback_is_unknown_not_zero(self):
        blk = ('occlusion_target_bin:t="heavy"\n'
               'occlusion_proxy_fraction:r=1\nocclusion_proxy_candidates:i=1\n'
               'avf_probe_size:i=128\navf_probe_attempts:i=1\n'
               'avf_probe_valid:i=0\navf_probe_method:t="collision_fallback"\n'
               'avf_probe_fraction:r=-1\navf_probe_amodal_pixels:i=0\n'
               'avf_probe_visible_pixels:i=0\navf_probe_missing_pixels:i=0\n')
        result = read_occlusion_proxy(blk, "heavy", 8,
                                      {"visible_fraction": 1., "visibility_bin": "open"}, probe_size=128)
        self.assertIsNone(result["avf"]["estimated_fraction"])
        self.assertIsNone(result["avf"]["absolute_error"])
