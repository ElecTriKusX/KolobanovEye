"""Regression tests for the fourth-map DX11 impostor atlas assertion.

Native fatal from map 4: 7 mips, texture array has 1 slice, rotation palette
requested subresource 7 (mip 0, slice 1). Never disable the DX11 validator.
"""
import tempfile
import unittest
from pathlib import Path

from kolobanoveye import editor_failure, guard_impostor_shadow_atlas_slices
from ke_single_editor import read_impostor_quality


ORIGINAL = (
    "void ImpostorTextureManager::update_shadow() {\n"
    "  ArrayTexture *arrayTex = acquire_managed_tex(res->getImpostorTextures().shadowAtlas);\n"
    "  G_ASSERT_RETURN(arrayTex != nullptr, false);\n"
    "  TextureInfo texInfo;\n"
    "  arrayTex->getinfo(texInfo);\n"
    "  BaseTexture *targetTex = arrayTex;\n"
    "  int targetSlice = palette_id;\n"
    "}\n"
)


class ImpostorShadowSliceTests(unittest.TestCase):
    def test_guard_is_exact_idempotent_and_does_not_change_driver_validator(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "prog/gameLibs/rendInst/impostorTextureMgr.cpp"
            path.parent.mkdir(parents=True)
            for eol in ("\n", "\r\n"):
                path.write_bytes(ORIGINAL.replace("\n", eol).encode("ascii"))
                self.assertTrue(guard_impostor_shadow_atlas_slices(Path(folder)))
                contents = path.read_bytes()
                self.assertFalse(guard_impostor_shadow_atlas_slices(Path(folder)))
                self.assertEqual(contents, path.read_bytes())
                normalized = contents.replace(b"\r\n", b"\n")
                self.assertIn(b"IMPOSTOR_SHADOW_SLICE_SKIPPED", normalized)
                self.assertIn(b'keCapture = dgs_get_argv("kolobanoveye-probe")', normalized)
                self.assertIn(b"palette_id < 0 || palette_id >= texInfo.a", normalized)
                self.assertLess(normalized.index(b"palette_id < 0 || palette_id >= texInfo.a"),
                                normalized.index(b"  BaseTexture *targetTex = arrayTex;"))
                self.assertLess(normalized.index(b"release_managed_tex(res->getImpostorTextures().shadowAtlas);"),
                                normalized.index(b"    return keCapture;"))
                self.assertIn(b"int targetSlice = palette_id;", normalized)
                self.assertNotIn(b"targetSlice = 0", normalized)
                if eol == "\r\n":
                    self.assertNotIn(b"\n", contents.replace(b"\r\n", b""))

    def test_guard_refuses_partial_or_unknown_native_layout(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "prog/gameLibs/rendInst/impostorTextureMgr.cpp"
            path.parent.mkdir(parents=True)
            for text in (
                ORIGINAL.replace("  arrayTex->getinfo(texInfo);", "  arrayTex->getinfo(texInfo, 0);"),
                ORIGINAL + "  // [KE] IMPOSTOR_SHADOW_SLICE_GUARD_V1\n",
            ):
                path.write_text(text, encoding="ascii")
                before = path.read_bytes()
                with self.assertRaises(ValueError):
                    guard_impostor_shadow_atlas_slices(Path(folder))
                self.assertEqual(before, path.read_bytes())

    def test_shadow_warnings_associated_with_actual_map_task(self):
        with tempfile.TemporaryDirectory() as folder:
            debug = Path(folder) / ".logs/daEditor3x-dev.exe-2026.11.19-22.05.26/debug"
            debug.parent.mkdir(parents=True)
            debug.write_text(
                "10 [D] CON: [KE] MAP_TASK_BEGIN index=1/2 capture=one.blk\n"
                "11 [W] [KE] IMPOSTOR_SHADOW_SLICE_SKIPPED asset=tree_1 palette_id=1 atlas_slices=1 mips=7 layer=0 ri=2\n"
                "12 [D] CON: [KE] MAP_TASK_OK index=1/2\n"
                "13 [D] CON: [KE] MAP_TASK_BEGIN index=2/2 capture=two.blk\n"
                "14 [W] [KE] IMPOSTOR_SHADOW_SLICE_SKIPPED asset=tree_2 palette_id=2 atlas_slices=1 mips=7 layer=0 ri=5\n"
                "15 [W] [KE] IMPOSTOR_SHADOW_SLICE_SKIPPED asset=tree_2 palette_id=1 atlas_slices=1 mips=7 layer=0 ri=5\n",
                encoding="utf-8")
            got = read_impostor_quality(debug, 2)
            self.assertEqual(got, [
                {"skipped_updates": 1, "assets": ["tree_1"]},
                {"skipped_updates": 2, "assets": ["tree_2"]},
            ])
            self.assertEqual(read_impostor_quality(debug.parent / "not-debug", 2),
                             [{"skipped_updates": 0, "assets": []}] * 2)

    def test_native_assert_in_exit_code_one_shows_asset_and_last_map(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            logs = run / ".logs" / "daEditor3x-dev.exe-2026.11.19-22.05.26"
            logs.mkdir(parents=True)
            (logs / "fatalerr").write_text(
                "71.50 [F] FATAL ERROR:\n"
                "assert failed in validateUpdateSubRegion.h:22:\n"
                "Invalid update_sub_region dst: subres=7 -> mip=0 slice=1 >= "
                "tex.slices=1, tree_fagus_hurtgen_pl_l_a_s\n\nCall stack ...\n",
                encoding="utf-8")
            (logs / "debug").write_text(
                "10.2 [D] CON: [KE] MAP_TASK_BEGIN index=4/20 capture=004-avg_alaska_town.blk\n",
                encoding="utf-8")
            message = editor_failure(run, "daEditor3x-dev.exe exited with 1")
            self.assertIn("tree_fagus_hurtgen_pl_l_a_s", message)
            self.assertIn("index=4/20", message)
            self.assertIn("fatalerr", message)


if __name__ == "__main__":
    unittest.main()
