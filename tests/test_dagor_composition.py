import tempfile
import unittest
from pathlib import Path

from tools.ke_dagor_composition import install_screen_composition


class NativeScreenIntegrationTests(unittest.TestCase):
    def test_injection_preserves_line_endings_and_is_idempotent(self):
        original = """  const Point3 aim = world * ((bounds[0] + bounds[1]) * 0.5f);
    const Point3 candidateForward = normalize(aim - candidateEye);
  const Point3 forward = normalize(aim - eye);
  result.setPoint3("look_at", aim);
"""
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "kolobanovEye.cpp"
            p.write_bytes(original.replace("\n", "\r\n").encode())
            self.assertTrue(install_screen_composition(p))
            data = p.read_bytes()
            self.assertIn(b"screen_bin_planned", data)
            self.assertIn(b"screen_offset_%d", data)
            self.assertIn(b"const Point3 lookAt = keShiftedLookAt(eye);", data)
            self.assertEqual(data.count(b"\r\n"), data.count(b"\n"))
            self.assertFalse(install_screen_composition(p))
            self.assertEqual(data, p.read_bytes())

    def test_does_not_modify_mismatched_source(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "kolobanovEye.cpp"
            p.write_text("int unrelated = 5;")
            with self.assertRaises(ValueError):
                install_screen_composition(p)
            self.assertEqual(p.read_text(), "int unrelated = 5;")


if __name__ == "__main__":
    unittest.main()
