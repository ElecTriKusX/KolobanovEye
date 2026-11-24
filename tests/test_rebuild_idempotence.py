import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.ke_patch_predecessors import predecessor_additions_present
from tools.ke_dagor_visibility import SCREEN_SNAPSHOT
from tools import vendor_ooz


class RebuildTests(unittest.TestCase):
    def test_predecessor_with_successor_inside_added_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "src/dynRender.cpp"
            path.parent.mkdir()
            diff = root / "old.patch"
            diff.write_text("--- a/src/dynRender.cpp\n+++ b/src/dynRender.cpp\n@@ -1 +1,4 @@\n context\n+first\n+second\n+third\n")
            path.write_text("changed context\nfirst\n" + SCREEN_SNAPSHOT + "second\nthird\n")
            self.assertTrue(predecessor_additions_present(root, diff))
            path.write_text("first\n" + SCREEN_SNAPSHOT + "second\n")
            self.assertFalse(predecessor_additions_present(root, diff))

    def test_only_exact_lf_snapshot_is_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = root / "a.cpp", root / "b.cpp"
            a.write_bytes(b"a\r\n")
            b.write_bytes(b"modified\n")
            hashes = {name: hashlib.sha256(data).hexdigest()
                      for name, data in [("a.cpp", b"a\n"), ("b.cpp", b"b\n")]}
            with patch.object(vendor_ooz, "required_hashes", return_value=hashes):
                with self.assertRaises(ValueError):
                    vendor_ooz.verify_files(root, repair_eol=True)
                self.assertEqual(a.read_bytes(), b"a\r\n")
                b.write_bytes(b"b\r\n")
                vendor_ooz.verify_files(root, repair_eol=True)
                self.assertEqual(a.read_bytes(), b"a\n")
                self.assertEqual(b.read_bytes(), b"b\n")
                vendor_ooz.verify_files(root)
