"""Regression tests for the Kraken stack-cookie fail-fast found in Nov 19 dump.

Only synthetic source fragments are stored here. The pinned OOZ snapshot remains
verified and unchanged; the build adapter patches a separate working copy.
"""
import tempfile
import unittest
from pathlib import Path

from tools.ke_ooz_hardening import guard_kraken_multi_array


ORIGINAL = (
    "int Kraken_DecodeMultiArray() {\n"
    "  int num_arrays_in_file = *src++;\n"
    "  if (!(num_arrays_in_file & 0x80))\n"
    "    return -1;\n"
    "  num_arrays_in_file &= 0x3f;\n"
    "\n"
    "  if (dst == scratch) {\n"
    "    scratch += (scratch_end - scratch - 0xc000) >> 1;\n"
    "    dst_end = scratch;\n"
    "  }\n"
    "  uint8 *entropy_array_data[32];\n"
    "  uint32 entropy_array_size[32];\n"
    "  for (int i = 0; i < num_arrays_in_file; i++) {\n"
    "    entropy_array_data[i] = scratch_cur;\n"
    "  }\n"
    "}\n"
)


class KrakenHardeningTests(unittest.TestCase):
    def test_guard_is_before_array_writes_and_repeat_build_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "kraken.cpp"
            for newline in ("\n", "\r\n"):
                original = ORIGINAL.replace("\n", newline).encode("ascii")
                target.write_bytes(original)
                self.assertTrue(guard_kraken_multi_array(target))
                patched = target.read_bytes()
                self.assertFalse(guard_kraken_multi_array(target))
                self.assertEqual(patched, target.read_bytes())
                code = patched.replace(b"\r\n", b"\n")
                self.assertLess(code.index(b"  if (num_arrays_in_file > 32)"),
                                code.index(b"  uint8 *entropy_array_data[32];"))
                self.assertLess(code.index(b"  if (num_arrays_in_file > 32)"),
                                code.index(b"  for (int i = 0; i < num_arrays_in_file; i++)"))
                self.assertEqual(code.count(b"if (num_arrays_in_file > 32)"), 1)
                if newline == "\r\n":
                    self.assertNotIn(b"\n", patched.replace(b"\r\n", b""))

    def test_unknown_decoder_layout_rejected_without_mutation(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "kraken.cpp"
            for bad in (
                ORIGINAL.replace("entropy_array_data[32]", "entropy_array_data[64]"),
                ORIGINAL.replace("  num_arrays_in_file &= 0x3f;", ""),
                ORIGINAL.replace("  uint32 entropy_array_size[32];", ""),
            ):
                target.write_text(bad, encoding="ascii")
                before = target.read_bytes()
                with self.assertRaisesRegex(ValueError, "Unsupported OOZ"):
                    guard_kraken_multi_array(target)
                self.assertEqual(target.read_bytes(), before)

    def test_partial_guard_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "kraken.cpp"
            target.write_text(
                ORIGINAL.replace("  num_arrays_in_file &= 0x3f;\n",
                                 "  num_arrays_in_file &= 0x3f;\n"
                                 "  // KolobanovEye: reject counts above the fixed local arrays[32].\n"),
                encoding="ascii")
            with self.assertRaisesRegex(ValueError, "Partial OOZ"):
                guard_kraken_multi_array(target)


if __name__ == "__main__":
    unittest.main()
