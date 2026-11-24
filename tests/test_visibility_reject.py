import tempfile
import unittest
from pathlib import Path

from tools.ke_visibility_reject import EDITS, install_visibility_reject
from tools.ke_dagor_visibility import BASE
from tools.ke_dagor_online import AFTER_FRAME, FRAME_OK


class VisibilityRejectInstallTests(unittest.TestCase):
    def test_install_rebuild_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = {"header": root / BASE / "include/de3_dynRenderService.h",
                     "render": root / BASE / "services/dynRenderSrv/dynRender.cpp",
                     "capture": root / BASE / "kolobanovEye.cpp"}
            for k, p in paths.items():
                p.parent.mkdir(parents=True, exist_ok=True)
                source = "\n".join(old for old, _ in EDITS[k])
                if k == "capture":
                    source = source.replace(FRAME_OK, FRAME_OK + AFTER_FRAME)
                p.write_bytes(source.replace("\n", "\r\n").encode())
            self.assertTrue(install_visibility_reject(root))
            snapshot = {k: p.read_bytes() for k, p in paths.items()}
            self.assertIn(b"missingCount > max(4, amodalCount / 20)", snapshot["render"])
            self.assertIn(b"depthMismatch = false;", snapshot["render"])
            self.assertIn(b"if (!*keOnlineDir || !keDepthMismatch)", snapshot["capture"])
            self.assertIn((FRAME_OK + AFTER_FRAME).replace("\n", "\r\n").encode(), snapshot["capture"])
            self.assertFalse(install_visibility_reject(root))
            self.assertEqual(snapshot, {k: p.read_bytes() for k, p in paths.items()})
            paths["render"].write_bytes(snapshot["render"].replace(b"depthMismatch = true;", b"depthMismatch = false;"))
            damaged = {k: p.read_bytes() for k, p in paths.items()}
            with self.assertRaises(ValueError):
                install_visibility_reject(root)
            self.assertEqual(damaged, {k: p.read_bytes() for k, p in paths.items()})
