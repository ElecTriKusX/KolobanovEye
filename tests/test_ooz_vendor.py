"""Pinned OOZ snapshot and Dagor ROI patch-migration regression tests."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import vendor_ooz
from kolobanoveye import upgrade_roi_unique_tex, enable_batch_tank_map_output, reset_level_settings_on_reload, preserve_landmesh_renderer_release_on_reload, enable_single_editor_map_batch


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

    def test_single_editor_map_export_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "strmLevel.cpp"
            for eol in (b"\n", b"\r\n"):
                source = eol.join((
                    b'if (const char *output = dgs_get_argv("ke-tank-map-output"); output && *output && !dd_file_exist(output))',
                    b'{',
                    b'  const bool saved = pixels && save_png32(output, pixels, 128, 128, 512, nullptr, 0, false);',
                    b'}'
                )) + eol
                path.write_bytes(source)
                self.assertTrue(enable_batch_tank_map_output(path))
                self.assertFalse(enable_batch_tank_map_output(path))
                content = path.read_bytes()
                self.assertIn(b"ke-tank-map-output-dir", content)
                self.assertIn(b"keMapOutput.str()", content)
                if eol == b"\r\n":
                    self.assertNotIn(b"\n", content.replace(b"\r\n", b""))

    def test_level_settings_reset_for_each_map(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "strmLevel.cpp"
            for eol in (b"\n", b"\r\n"):
                path.write_bytes(eol.join((b'  levelFileName = bindump;', b'  clear();', b'  // next level')) + eol)
                self.assertTrue(reset_level_settings_on_reload(path))
                self.assertFalse(reset_level_settings_on_reload(path))
                contents = path.read_bytes()
                self.assertIn(b'  levelSettingsBlk.clearData();', contents)
                if eol == b"\r\n":
                    self.assertNotIn(b"\n", contents.replace(b"\r\n", b""))

    def test_previous_map_landmesh_renderer_is_not_orphaned(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "strmLevel.cpp"
            for eol in (b"\n", b"\r\n"):
                source.write_bytes(eol.join((
                    b"  rebuildLastClip = true;",
                    b"  lmeshRenderer = NULL;",
                    b"  hasWater = false;",
                    b"  levelFileName = bindump;",
                    b"  clear();",
                )) + eol)
                self.assertTrue(preserve_landmesh_renderer_release_on_reload(source))
                self.assertFalse(preserve_landmesh_renderer_release_on_reload(source))
                content = source.read_bytes()
                self.assertIn(b"clear() releases the previous map", content)
                self.assertNotIn(b"  lmeshRenderer = NULL;", content)
                if eol == b"\r\n":
                    self.assertNotIn(b"\n", content.replace(b"\r\n", b""))

    def test_native_batch_command_installs_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "prog/tools/sceneTools/daEditorX"
            base.mkdir(parents=True)
            for eol in (b"\n", b"\r\n"):
                (base / "de_appwnd.h").write_bytes(
                    b"  bool runKolobanovEyeFrame(const char *config_path);" + eol)
                lines = [
                    b'  CONSOLE_CHECK_NAME_EX("kolobanoveye", "frame", 2, 2, "Render one compatibility probe frame.", "<config.blk>")',
                    b"  {",
                    b"    CONSOLE_COMMAND_BATCH_MODE_RESULT(get_app().runKolobanovEyeFrame(params[0]));",
                    b"  }",
                ]
                (base / "de_appwndConCmd.cpp").write_bytes(eol.join(lines) + eol)
                (base / "kolobanovEye.cpp").write_bytes(
                    b'bool DagorEdAppWindow::runKolobanovEyeFrame(const char *config_path)\n'
                    b'{ return true; }\n'.replace(b"\n", eol))
                self.assertTrue(enable_single_editor_map_batch(Path(tmp)))
                snapshot = {p.name: p.read_bytes() for p in base.iterdir()}
                self.assertFalse(enable_single_editor_map_batch(Path(tmp)))
                self.assertEqual(snapshot, {p.name: p.read_bytes() for p in base.iterdir()})
                self.assertIn(b"MAP_BATCH_OK", snapshot["kolobanovEye.cpp"])
                self.assertIn(b'CONSOLE_CHECK_NAME_EX("kolobanoveye", "batch"',
                              snapshot["de_appwndConCmd.cpp"])
                self.assertEqual(snapshot["kolobanovEye.cpp"].count(b"runKolobanovEyeBatch("), 1)
                if eol == b"\r\n":
                    for data in snapshot.values():
                        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))

    def test_new_patch_uses_unique_tex(self):
        patch_text = (Path(__file__).resolve().parent.parent /
                      "engine/patches/dagor-tank-map-render.patch").read_text()
        self.assertIn("+        UniqueTex target = dag::create_tex", patch_text)
        self.assertNotIn("+        auto target = dag::create_tex", patch_text)


if __name__ == "__main__":
    unittest.main()
