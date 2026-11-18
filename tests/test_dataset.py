"""Dagor patch integration tests; dataset pipeline tests are in test_single_editor.py."""
import tempfile
import unittest
from pathlib import Path
from kolobanoveye import ensure_level_settings_accessor, has_last_clip_bounds


class LegacyClipBoundsTests(unittest.TestCase):
    def test_detects_installed_last_clip_bounds_even_after_successor(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "kolobanovEye.cpp"
            source.write_text('''
  const auto cellOrigin = scene.lmeshMgr->getCellOrigin();
  const float lastClipX0 = cellOrigin.x * cellSize;
  const float lastClipZ1 = 256.f;
  return fail("LAST_CLIP_BOUNDS", "Invalid fixed clip bounds");
  result.setPoint3("last_clip_min", Point3(lastClipX0, 0.f, 0.f));
  result.setPoint3("last_clip_max", Point3(256.f, 0.f, lastClipZ1));
  result.setPoint3("tank_map_min", Point3(0.f, 0.f, 0.f));
''', encoding='utf-8')
            self.assertTrue(has_last_clip_bounds(source))
            source.write_text('just baseline source', encoding='utf-8')
            self.assertFalse(has_last_clip_bounds(source))

    def test_rejects_partial_last_clip_bounds(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "kolobanovEye.cpp"
            source.write_text('  result.setPoint3("last_clip_min", Point3(0,0,0));', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Partial last-clip bounds'):
                has_last_clip_bounds(source)

class DagorAccessorTests(unittest.TestCase):
    def test_safe_injection_accepts_modified_header_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            header = Path(temp) / "strmlevel.h"
            for eol in (b"\n", b"\r\n"):
                original = eol.join((b"class AcesScene {", b"public:",
                                     b"  void extraPatchChanges();",
                                     b"  LandMeshManager *lmeshMgr;",
                                     b"protected:", b"  DataBlock levelSettingsBlk;", b"};")) + eol
                header.write_bytes(original)
                self.assertTrue(ensure_level_settings_accessor(header))
                self.assertFalse(ensure_level_settings_accessor(header))
                updated = header.read_bytes()
                self.assertEqual(updated.count(b"getKolobanovEyeLevelSettings"), 1)
                self.assertEqual(updated.count(b"\n"), original.count(b"\n") + 2)
                if eol == b"\r\n":
                    self.assertNotIn(b"\n", updated.replace(b"\r\n", b""))


if __name__ == "__main__":
    unittest.main()
