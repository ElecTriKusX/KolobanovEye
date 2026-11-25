import tempfile
import unittest
from pathlib import Path

from tools.ke_dagor_avf_camera import EDITS as CAMERA_EDITS, FIELD_LOOP_OLD, install_avf_camera, normalize_capture
from tools.ke_dagor_avf_batch import install_avf_batch, normalize_batch
from tools.ke_dagor_avf_probe import BASE, EDITS as PROBE_EDITS, install_avf_probe
from tools.ke_dagor_avf_gpu import install_avf_gpu, normalize_gpu, SCENE_NEW, SERVICE_NEW
from tools.ke_dagor_avf_residency import EDITS as RESIDENCY_EDITS, install_avf_residency
from tools.ke_occlusion import read_occlusion_proxy


class AVFCompletionContracts(unittest.TestCase):
    def test_batch_upgrade_and_predecessor_normalization(self):
        original = "#include <cmath>\n" + "\n".join(old for old, _ in CAMERA_EDITS) + "\n" + (
            "  int cameraAttemptUsed = -1;\n" + FIELD_LOOP_OLD + "  if (cameraAttemptUsed < 0)\n")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.cpp"
            path.write_text(original, encoding="utf-8")
            self.assertTrue(install_avf_camera(path))
            v3 = path.read_text(encoding="utf-8")
            self.assertTrue(install_avf_batch(path))
            v4 = path.read_text(encoding="utf-8")
            self.assertEqual(normalize_batch(v4), v3)
            self.assertEqual(normalize_capture(v4), original)
            self.assertFalse(install_avf_camera(path))
            self.assertFalse(install_avf_batch(path))

    def test_gpu_renderer_upgrade_and_predecessor_normalization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            header = root / BASE / "include/de3_dynRenderService.h"
            render = root / BASE / "services/dynRenderSrv/dynRender.cpp"
            for path in (header, render):
                path.parent.mkdir(parents=True, exist_ok=True)
            header.write_text("bool &depthMismatch) { depthMismatch = false; return false; }\n", encoding="utf-8")
            render.write_text("#include <3d/dag_lockTexture.h>\n" +
                              "\n".join(old for old, _ in PROBE_EDITS["render"]) +
                              "\n  void closeIsolatedTargets()\n  {\n", encoding="utf-8")
            self.assertTrue(install_avf_probe(root))
            v2 = [p.read_text(encoding="utf-8") for p in (header, render)]
            self.assertTrue(install_avf_gpu(root))
            v3 = [p.read_text(encoding="utf-8") for p in (header, render)]
            self.assertEqual(normalize_gpu(v3[0], "header"), v2[0])
            self.assertEqual(normalize_gpu(v3[1], "render"), v2[1])
            self.assertFalse(install_avf_probe(root))
            self.assertFalse(install_avf_gpu(root))
            # Simulate the exact installed V3 that failed the Windows build.
            from tools.ke_dagor_avf_gpu import BUILD_FIXES
            for key, path in (("header", header), ("render", render)):
                old_v3 = path.read_text(encoding="utf-8")
                for old, new in reversed(BUILD_FIXES[key]):
                    self.assertEqual(old_v3.count(new), 1)
                    old_v3 = old_v3.replace(new, old, 1)
                path.write_text(old_v3, encoding="utf-8")
            self.assertFalse(install_avf_probe(root))
            self.assertTrue(install_avf_gpu(root))
            self.assertEqual([p.read_text(encoding="utf-8") for p in (header, render)], v3)
            self.assertFalse(install_avf_gpu(root))

    def test_gpu_skips_cpu_depth_readback_and_verification_uses_same_buffers(self):
        self.assertIn("return keSubmitProbe(slot, sceneTex, modelTex, width, height);", SCENE_NEW)
        self.assertLess(SCENE_NEW.index("return keSubmitProbe(slot"), SCENE_NEW.index("if (!read(sceneTex"))
        self.assertIn("AVF_GPU_VERIFY_OK", SCENE_NEW)
        self.assertIn("ga!=amodal || gv!=visible || gm!=missing", SCENE_NEW)
        self.assertIn("}, slot);", SERVICE_NEW)

    def test_residency_scope_and_idempotence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.cpp"
            path.write_text("\n".join(old for old, _ in RESIDENCY_EDITS), encoding="utf-8")
            self.assertTrue(install_avf_residency(path))
            once = path.read_text(encoding="utf-8")
            self.assertIn("keResidentBatch->level == level", once)
            self.assertIn("scene.hasKolobanovEyeTerrain()", once)
            self.assertIn("KeResidentScope keResidentScope;", once)
            self.assertFalse(install_avf_residency(path))
            self.assertEqual(path.read_text(encoding="utf-8"), once)

    def test_backend_contract(self):
        base = ('occlusion_target_bin:t="moderate"\n'
                'occlusion_proxy_fraction:r=1\nocclusion_proxy_candidates:i=2\n'
                'avf_probe_size:i=128\navf_probe_region:t="roi"\n'
                'avf_backend:t="gpu"\navf_batch_size:i=4\n'
                'avf_probe_attempts:i=2\navf_probe_valid:i=1\n'
                'avf_probe_method:t="opaque_probe_v1"\navf_probe_fraction:r=0.5\n'
                'avf_probe_amodal_pixels:i=100\navf_probe_visible_pixels:i=50\navf_probe_missing_pixels:i=0\n')
        truth = {"visible_fraction": .55, "visibility_bin": "moderate"}
        data = read_occlusion_proxy(base, "moderate", 8, truth, probe_size=128,
                                   probe_region="roi", probe_backend="gpu", probe_batch_size=4)
        self.assertEqual(data["avf"]["batch_size"], 4)
        with self.assertRaises(ValueError):
            read_occlusion_proxy(base, "moderate", 8, truth, probe_size=128,
                                 probe_backend="gpu", probe_batch_size=2)
        with self.assertRaises(ValueError):
            read_occlusion_proxy(base, "moderate", 8, truth, probe_size=128,
                                 probe_backend="cpu", probe_batch_size=4)
