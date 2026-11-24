import tempfile
import unittest
from pathlib import Path
from tools.ke_stability_reject import OLD, NEW, install_stability_reject


class StabilityRejectInstallerTests(unittest.TestCase):
    def test_preserves_fatal_timeout_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "kolobanovEye.cpp"
            for eol in (b"\n", b"\r\n"):
                path.write_bytes(OLD.encode().replace(b"\n", eol))
                self.assertTrue(install_stability_reject(path))
                patched = path.read_bytes()
                self.assertIn(OLD.encode().replace(b"\n", eol), patched)
                for anchor in (b"pendingBefore == 0 && pendingAfter == 0 && texturesIdle()",
                               b"keStableChecks < 3", b"renderer->isEnvironmentLightingReady()",
                               b"reason=frame_stability_timeout"):
                    self.assertIn(anchor, patched)
                self.assertFalse(install_stability_reject(path))
                self.assertEqual(path.read_bytes(), patched)
                path.write_bytes(patched.replace(b"keStableChecks < 3", b"keStableChecks < 5"))
                with self.assertRaises(ValueError):
                    install_stability_reject(path)
