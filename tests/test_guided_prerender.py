import tempfile
import unittest
from pathlib import Path
from tools.ke_guided_prerender import remove_prerender, normalize_prerender, ANCHOR, REPLACEMENT

class RetiredPrerenderMigrationTests(unittest.TestCase):
    def test_remove_old_patch_preserves_crlf_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"capture.cpp"
            path.write_bytes(("prefix\n"+REPLACEMENT+"suffix\n").replace("\n","\r\n").encode())
            self.assertTrue(remove_prerender(path))
            self.assertEqual(path.read_bytes(),("prefix\n"+ANCHOR+"suffix\n").replace("\n","\r\n").encode())
            self.assertFalse(remove_prerender(path))
            self.assertEqual(normalize_prerender(REPLACEMENT),ANCHOR)

    def test_partial_patch_rejected(self):
        with self.assertRaises(ValueError):
            normalize_prerender("// [KE] GUIDED_PRERENDER_V1\n")
