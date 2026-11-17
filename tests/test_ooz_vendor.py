"""Pinned OOZ snapshot and Dagor ROI patch-migration regression tests."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import vendor_ooz
from kolobanoveye import upgrade_roi_unique_tex


class LocalOOZSnapshotTests(unittest.TestCase):
    def test_local_snapshot_is_pinned_and_stable(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            src = root / "staged"
            src.mkdir()
            data = {"kraken.cpp": b"kraken", "lzna.cpp": b"lzna",
                    "bitknit.cpp": b"bitknit", "stdafx.h": b"header",
                    "targetver.h": b"version"}
            for name, content in data.items():
                (src / name).write_bytes(content)
            lock = root / "ooz.lock.json"
            lock.write_text(json.dumps({
                "files": {name: hashlib.sha256(content).hexdigest()
                          for name, content in data.items()}}), encoding="utf-8")
            vendor = root / "vendor" / "ooz"
            with patch.object(vendor_ooz, "VENDOR", vendor), \
                 patch.object(vendor_ooz, "LOCK", lock):
                self.assertEqual(vendor_ooz.install(src), vendor)
                self.assertEqual(len(vendor_ooz.verify_vendor()), 5)
                self.assertEqual(vendor_ooz.install(src), vendor)
                (src / "kraken.cpp").write_bytes(b"future update")
                with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                    vendor_ooz.install(src)
                (vendor / "kraken.cpp").write_bytes(b"unexpected edit")
                with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                    vendor_ooz.verify_vendor()
                self.assertFalse(any((root / "vendor").glob("ooz.pending.*")))

    def test_incomplete_source_is_rejected_without_creating_vendor(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            lock = root / "lock.json"
            lock.write_text(json.dumps({"files": {
                "kraken.cpp": hashlib.sha256(b"expected").hexdigest()}}), encoding="utf-8")
            source = root / "source"
            source.mkdir()
            dest = root / "vendor" / "ooz"
            with patch.object(vendor_ooz, "VENDOR", dest), \
                 patch.object(vendor_ooz, "LOCK", lock):
                with self.assertRaisesRegex(ValueError, "missing"):
                    vendor_ooz.install(source)
                self.assertFalse(dest.exists())


class DagorCompiledROIRegressionTests(unittest.TestCase):
    def test_existing_roi_patch_migrates_then_stays_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "strmLevel.cpp"
            path.write_bytes(
                b"render_last_clip_region_to_tex\n"
                b"        auto target = dag::create_tex(nullptr, size, size,\n")
            self.assertTrue(upgrade_roi_unique_tex(path))
            self.assertFalse(upgrade_roi_unique_tex(path))
            self.assertIn(b"UniqueTex target = dag::create_tex", path.read_bytes())
            self.assertNotIn(b"auto target = dag::create_tex", path.read_bytes())

    def test_bad_partial_roi_patch_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "strmLevel.cpp"
            path.write_bytes(b"auto target = dag::create_tex(nullptr, size, size,\n")
            with self.assertRaisesRegex(ValueError, "Ambiguous"):
                upgrade_roi_unique_tex(path)

    def test_new_patch_uses_unique_tex(self):
        patch_text = (Path(__file__).resolve().parent.parent /
                      "engine/patches/dagor-tank-map-render.patch").read_text()
        self.assertIn("+        UniqueTex target = dag::create_tex", patch_text)
        self.assertNotIn("+        auto target = dag::create_tex", patch_text)


if __name__ == "__main__":
    unittest.main()
