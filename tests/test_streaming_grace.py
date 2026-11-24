import tempfile
import unittest
from pathlib import Path

from tools.ke_streaming_grace import EDITS, install_streaming_grace


class StreamingGraceTests(unittest.TestCase):
    def test_upgrade_preserves_old_deadline_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "kolobanovEye.cpp"
            old = "\n".join(old for old, _ in EDITS)
            p.write_bytes(old.replace("\n", "\r\n").encode())
            self.assertTrue(install_streaming_grace(p))
            saved = p.read_bytes()
            text = saved.decode().replace("\r\n", "\n")
            self.assertIn("std::chrono::seconds(120)", text)
            self.assertIn("if (!texturesIdle())", text)
            self.assertIn("next < keHardDeadline ? next : keHardDeadline", text)
            self.assertIn("while (sceneReady && !texturesIdle() && Clock::now() < keEffectiveDeadline)", text)
            self.assertIn("return fail(\"STREAMING\", reason);", text)
            self.assertEqual(text.count("keRefreshStreamingDeadline();"), 2)
            self.assertEqual(saved.count(b"\r\n"), saved.count(b"\n"))
            self.assertFalse(install_streaming_grace(p))
            self.assertEqual(saved, p.read_bytes())

    def test_partial_input_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "kolobanovEye.cpp"
            original = EDITS[0][0].encode()
            p.write_bytes(original)
            with self.assertRaises(ValueError):
                install_streaming_grace(p)
            self.assertEqual(original, p.read_bytes())
