import tempfile
import unittest
from pathlib import Path

from tools.ke_dagor_online import EDITS, install_online_feedback, NEW_TARGET
from tools.ke_dagor_occlusion import SETUP, EDITS as OCCLUSION_EDITS, install_occlusion_search


class OnlineInstallerTests(unittest.TestCase):
    def test_rebuild_and_predecessor(self):
        # Use full original installed occlusion blocks, plus the other
        # independent native anchors. No generated Dagor source is reset.
        fixture = "\n".join(new for _, new in OCCLUSION_EDITS)
        for old, _ in EDITS:
            if old not in fixture:
                fixture += "\n" + old
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "kolobanovEye.cpp"
            for eol in (b"\n", b"\r\n"):
                p.write_bytes(fixture.encode().replace(b"\n", eol))
                self.assertFalse(install_occlusion_search(p))
                self.assertTrue(install_online_feedback(p))
                upgraded = p.read_bytes()
                self.assertIn(NEW_TARGET.encode(), upgraded)
                self.assertFalse(install_occlusion_search(p))
                self.assertFalse(install_online_feedback(p))
                self.assertEqual(p.read_bytes(), upgraded)
                p.write_bytes(upgraded.replace(b"keOnlineWaitFile(path)", b"brokenWait(path)"))
                damaged = p.read_bytes()
                with self.assertRaises(ValueError):
                    install_online_feedback(p)
                self.assertEqual(p.read_bytes(), damaged)

    def test_missing_anchor_no_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "kolobanovEye.cpp"
            original = b"#include <chrono>\n"
            p.write_bytes(original)
            with self.assertRaises(ValueError):
                install_online_feedback(p)
            self.assertEqual(p.read_bytes(), original)
