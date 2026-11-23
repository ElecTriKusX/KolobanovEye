"""Test the non-destructive Dagor patch installer independently of DX11."""
import tempfile
import unittest
from pathlib import Path

from tools.ke_dagor_visibility import BASE, install_visibility_capture


HEADER = "  virtual void beforeProjectSceneReset() {}\n"
RENDER = (
    "  void renderScreenShot(bool force_disable_wireframe)\n  {\n"
    "          const TMatrix viewItm = orthonormalized_inverse(viewTm);\n"
    "  }\n"
    "  void closeMicroDetails()\n"
    "  bool isEnvironmentLightingReady() override { return !dynScene || !dynScene->isEnviProbeUpdatePending(); }\n"
)
CAPTURE = "  model.beforeRender(eye);\n  const int lod = model.getCurrentLodNo();\n  DataBlock result;\n"


class DagorVisibilityInstallTests(unittest.TestCase):
    def fixture(self, root, *, bad=False):
        entries = {
            "include/de3_dynRenderService.h": HEADER,
            "services/dynRenderSrv/dynRender.cpp": RENDER,
            "kolobanovEye.cpp": CAPTURE if not bad else CAPTURE.replace("  DataBlock result;", "  DataBlock other;"),
        }
        paths = []
        for name, src in entries.items():
            p = root / BASE / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(src.replace("\n", "\r\n").encode())
            paths.append(p)
        return paths

    def test_idempotent_install_preserves_crlf(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            paths = self.fixture(root)
            self.assertTrue(install_visibility_capture(root))
            first = [p.read_bytes() for p in paths]
            self.assertTrue(all(b"VISIBILITY_CAPTURE_V1" in data for data in first))
            self.assertTrue(all(data.count(b"\r\n") == data.count(b"\n") for data in first))
            self.assertIn(b"readDepth(deferredTarget->getDepth(), mainDepth)", first[1])
            self.assertIn(b"readDepth(isolatedTarget->getDepth(), tankDepth)", first[1])
            self.assertIn(b"setDisableAutoChooseLod(keCaptureMasks)", first[2])
            self.assertFalse(install_visibility_capture(root))
            self.assertEqual(first, [p.read_bytes() for p in paths])

    def test_failed_anchor_leaves_all_files_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            paths = self.fixture(root, bad=True)
            before = [p.read_bytes() for p in paths]
            with self.assertRaisesRegex(ValueError, "Visibility insertion failed"):
                install_visibility_capture(root)
            self.assertEqual(before, [p.read_bytes() for p in paths])


if __name__ == "__main__":
    unittest.main()
