import tempfile
import unittest
from pathlib import Path

from tools.ke_map_resource_gc import ANCHOR, MARKER, install_map_resource_gc
from kolobanoveye import editor_failure, reset_level_settings_on_reload


class MapResourceGCTests(unittest.TestCase):
    def test_refcount_cleanup_is_before_next_map_preload_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "strmLevel.cpp"
            p.write_bytes(b"#include <gameRes/dag_gameResSystem.h>\r\n" +
                          ANCHOR.replace(b"\n", b"\r\n") +
                          b"\r\n  bindumpHandle = load_binary_dump(bindump, *this);\r\n")
            self.assertTrue(install_map_resource_gc(p))
            data = p.read_bytes()
            self.assertIn(MARKER, data)
            self.assertLess(data.index(b"  clear();"), data.index(b"free_unused_game_resources();"))
            self.assertLess(data.index(b"free_unused_game_resources();"), data.index(b"load_binary_dump"))
            self.assertIn(b'if (dgs_get_argv("kolobanoveye-probe"))', data)
            self.assertIn(b"ri_before=%d ri_after=%d released=%d", data)
            self.assertNotIn(b"reset_game_resources()", data)
            self.assertFalse(reset_level_settings_on_reload(p))
            self.assertFalse(install_map_resource_gc(p))
            self.assertEqual(data, p.read_bytes())
            self.assertEqual(data.count(b"\r\n"), data.count(b"\n"))

    def test_missing_anchor_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "strmLevel.cpp"
            original = b"#include <gameRes/dag_gameResSystem.h>\n  clear();\n"
            p.write_bytes(original)
            with self.assertRaises(ValueError):
                install_map_resource_gc(p)
            self.assertEqual(p.read_bytes(), original)

    def test_oom_reports_map_before_long_resource_dump(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            folder = run / ".logs" / "daEditor3x-dev.exe-test"
            folder.mkdir(parents=True)
            (folder / "fatalerr").write_text(
                'FATAL ERROR:\nassert failed in shaderResUnitedData.cpp:1814\n'
                'unitedVdata<rendInst> OOM, but allowSepTightVdata=0 and allowRebuild=0\n'
                '---fatal context---\nD:/Games/WarThunder/levels/avg_finland.bin\n',
                encoding="utf-8")
            (folder / "debug").write_text(
                '100.0 [D] CON: [KE] FRAME_OK 1280x720 frame.png\n'
                '101.0 [D] CON: [KE] MAP_TASK_BEGIN index=17/20 capture=017-avg_finland.blk\n'
                + 'long resource dump\n' * 30000, encoding="utf-8")
            result = editor_failure(run, "daEditor3x-dev.exe exited with 1")
            self.assertIn("unitedVdata<rendInst> OOM", result)
            self.assertIn("avg_finland.bin", result)
            self.assertIn("index=17/20", result)
            self.assertIn("Completed RGB frames: 1", result)


if __name__ == "__main__":
    unittest.main()
