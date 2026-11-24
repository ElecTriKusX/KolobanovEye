import tempfile
import unittest
from pathlib import Path

from kolobanoveye import editor_failure
from tools.ke_ri_trace_props import EDITS, install_ri_trace_props


class RendInstTracePropsTests(unittest.TestCase):
    def test_installer_is_idempotent_and_rejects_partial_source(self):
        fixture = "\n".join(old for old, _ in EDITS).encode()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rendInstGenCollision.cpp"
            for eol in (b"\n", b"\r\n"):
                path.write_bytes(fixture.replace(b"\n", eol))
                self.assertTrue(install_ri_trace_props(path))
                patched = path.read_bytes()
                self.assertIn(b"unsigned(pool) < rgl.rtData->riProperties.size()", patched)
                self.assertIn(b"std::is_same_v<Strategy, TraceRayStrat>", patched)
                self.assertIn(b"strategy.rayMatId == PHYSMAT_INVALID", patched)
                self.assertFalse(install_ri_trace_props(path))
                self.assertEqual(path.read_bytes(), patched)
                path.write_bytes(patched.replace(b"p.matId = PHYSMAT_INVALID;", b"p.matId = 0;"))
                with self.assertRaisesRegex(ValueError, "Partial"):
                    install_ri_trace_props(path)

    def test_failed_anchor_does_not_modify_source(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rendInstGenCollision.cpp"
            original = EDITS[0][0].encode()
            path.write_bytes(original)
            with self.assertRaises(ValueError):
                install_ri_trace_props(path)
            self.assertEqual(path.read_bytes(), original)

    def test_native_access_violation_is_reported_with_stack_context(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp)
            native = run / ".logs" / "daEditor3x-dev.exe-test"
            native.mkdir(parents=True)
            (native / "fatalerr").write_text(
                "Exception details: C0000005 at 123: access violation\n"
                "TraverseRayCell<TraceRayStrat>::traverse\n"
                "traceRayRendInstsNormalized\n", encoding="utf-8")
            (native / "debug").write_text(
                "[D] CON: [KE] MAP_TASK_BEGIN index=1/0 capture=test.blk\n"
                "[D] CON: [KE] FRAME_OK 1280x720 test.png\n", encoding="utf-8")
            detail = editor_failure(run, "DaEditor exited (13)")
            self.assertIn("C0000005", detail)
            self.assertIn("RendInst collision ray traversal", detail)
            self.assertIn("Completed RGB frames: 1", detail)
