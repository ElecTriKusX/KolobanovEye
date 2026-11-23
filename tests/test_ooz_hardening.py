"""Regression tests for the Kraken stack-cookie fail-fast found in Nov 19 dump.

Only synthetic source fragments are stored here. The pinned OOZ snapshot remains
verified and unchanged; the build adapter patches a separate working copy.
"""
import tempfile
import unittest
from pathlib import Path

from tools.ke_ooz_hardening import guard_kraken_multi_array, trace_kraken_failures
from kolobanoveye import enable_failed_ooz_stream_capture


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
    def test_full_six_bit_range_fits_arrays_and_patch_is_idempotent(self):
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
                self.assertIn(b"  num_arrays_in_file &= 0x3f;", code)
                self.assertIn(b"  uint8 *entropy_array_data[64];", code)
                self.assertIn(b"  uint32 entropy_array_size[64];", code)
                self.assertNotIn(b"if (num_arrays_in_file > 32)", code)
                for count in range(64):
                    self.assertLess(count, 64)
                self.assertLess(code.index(b"  uint8 *entropy_array_data[64];"),
                                code.index(b"  for (int i = 0; i < num_arrays_in_file; i++)"))
                if newline == "\r\n":
                    self.assertNotIn(b"\n", patched.replace(b"\r\n", b""))

    def test_trace_insertions_are_exact_and_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "kraken.cpp"
            source = (
                "  if (n != qhdr.compressed_size)\n"
                "    return false;\n"
                "    if (!Kraken_DecodeStep(dec, dst, offset, dst_len, src, src_len))\n"
                "      goto FAIL;\n"
                "        if (!Leviathan_ReadLzTable(mode,\n"
                "            src, src + src_used,\n"
                "            dst, dst_count,\n"
                "            dst - dst_start,\n"
                "            scratch + sizeof(LeviathanLzTable), scratch + scratch_usage,\n"
                "            (LeviathanLzTable*)scratch))\n"
                "          return -1;\n"
            )
            for newline in ("\n", "\r\n"):
                target.write_bytes(source.replace("\n", newline).encode("ascii"))
                self.assertTrue(trace_kraken_failures(target))
                after = target.read_bytes()
                self.assertFalse(trace_kraken_failures(target))
                self.assertEqual(after, target.read_bytes())
                self.assertIn(b"OOZ_QUANTUM_FAIL", after)
                self.assertIn(b"OOZ_STEP_FAIL", after)
                self.assertIn(b"OOZ_LEVIATHAN_LZ_TABLE_FAIL", after)
                if newline == "\r\n":
                    self.assertNotIn(b"\n", after.replace(b"\r\n", b""))

    def test_failed_ooz_capture_engine_migration(self):
        with tempfile.TemporaryDirectory() as root:
            code = (
                "#include <cstdlib>\n"
                "bool decode() {\n"
                "  if (result != static_cast<int>(unpacked_size))\n"
                "  {\n"
                "    logerr(\"ooz: decode failed\");\n"
                "    return false;\n"
                "  }\n"
                "}\n"
            )
            source = Path(root) / "prog/engine/ioSys/oodleIoOoz.cpp"
            source.parent.mkdir(parents=True)
            for newline in ("\n", "\r\n"):
                source.write_bytes(code.replace("\n", newline).encode("ascii"))
                self.assertTrue(enable_failed_ooz_stream_capture(Path(root)))
                after = source.read_bytes()
                self.assertFalse(enable_failed_ooz_stream_capture(Path(root)))
                self.assertEqual(after, source.read_bytes())
                for keyword in (b"KE_OOZ_FAILURE_DIR", b"failed-ooz.bin",
                                b"failed-ooz.txt", b"std::atomic_flag", b"std::fwrite"):
                    self.assertIn(keyword, after)
                if newline == "\r\n":
                    self.assertNotIn(b"\n", after.replace(b"\r\n", b""))

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
                with self.assertRaisesRegex(ValueError, "OOZ"):
                    guard_kraken_multi_array(target)
                self.assertEqual(target.read_bytes(), before)

    def test_obsolete_32_limit_guard_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "kraken.cpp"
            target.write_text(
                ORIGINAL.replace("  num_arrays_in_file &= 0x3f;\n",
                                 "  num_arrays_in_file &= 0x3f;\n"
                                 "  if (num_arrays_in_file > 32)\n"
                                 "    return -1;\n"),
                encoding="ascii")
            with self.assertRaisesRegex(ValueError, "Partial or obsolete"):
                guard_kraken_multi_array(target)


if __name__ == "__main__":
    unittest.main()
