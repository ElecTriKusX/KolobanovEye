import tempfile
import unittest
from pathlib import Path
from tools.ke_placement_reject import OLD, NEW, install_placement_reject


class PlacementRejectInstallerTests(unittest.TestCase):
    def test_rebuild_is_idempotent_and_preserves_other_failures(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "kolobanovEye.cpp"
            for eol in (b"\n", b"\r\n"):
                path.write_bytes(OLD.encode().replace(b"\n", eol))
                self.assertTrue(install_placement_reject(path))
                installed = path.read_bytes()
                self.assertIn(b'if (!*feedbackDir || !*metadataJournal)', installed)
                self.assertIn(b'keOnlineDecision(feedbackDir, rejectedIndex + 1, nextDecision)', installed)
                self.assertIn(b'FRAME_REJECT index=%d reason=heightmap_candidates_exhausted', installed)
                self.assertFalse(install_placement_reject(path))
                self.assertEqual(installed, path.read_bytes())
                path.write_bytes(installed.replace(b'placement_attempts', b'damaged'))
                with self.assertRaises(ValueError):
                    install_placement_reject(path)
            path.write_bytes(b"unrelated")
            with self.assertRaises(ValueError):
                install_placement_reject(path)
            self.assertEqual(path.read_bytes(), b"unrelated")
