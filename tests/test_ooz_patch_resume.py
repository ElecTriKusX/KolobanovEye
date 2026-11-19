"""Regression tests for safe resumption of OOZ-patched Dagor builds."""
import tempfile
import unittest
from pathlib import Path

from kolobanoveye import ooz_patch_installed


class OozPatchResumptionTests(unittest.TestCase):
    def create_io(self, path, jam_text: bytes, cpp_text: bytes | None):
        io = path / "prog/engine/ioSys"
        io.mkdir(parents=True)
        (io / "jamfile").write_bytes(jam_text)
        if cpp_text is not None:
            (io / "oodleIoOoz.cpp").write_bytes(cpp_text)
        return io

    @staticmethod
    def jam():
        return (
            b"local ooz_enabled = ;\n"
            b"if $(UseOoz) = yes && $(KernelLinkage) = dynamic { ooz_enabled = yes ; }\n"
            b"if $(ooz_enabled) = yes {\n"
            b"  Echo Building daKernel with the decode-only ooz backend ;\n"
            b"  Sources += oodleIoOoz.cpp oodleIoFatal.cpp ;\n"
            b"}\n"
        )

    @staticmethod
    def cpp():
        return (
            b"int Kraken_Decompress(const unsigned char *src, size_t n, unsigned char *dst, size_t m);\n"
            b"void OodleLoadCB::open(int a) {}\n"
            b"inline int OodleLoadCB::decodeData(int n) { return 0; }\n"
            b"size_t oodle_decompress(int a) { return 0; }\n"
            b"bool OodleLoadCB::ceaseReading() { return true; }\n"
        )

    def test_clean_checkout_needs_patch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.create_io(root, b"if $(ProjectAllowsOodle) = yes {\n", None)
            self.assertFalse(ooz_patch_installed(root))

    def test_original_patch_and_post_patch_capture_both_recognized(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            io = self.create_io(root, self.jam(), self.cpp())
            self.assertTrue(ooz_patch_installed(root))
            source = io / "oodleIoOoz.cpp"
            source.write_bytes(source.read_bytes() +
                b"// [KE] OOZ_FAILED_STREAM_CAPTURE_V1\n"
                b"const char *dir = getenv(\"KE_OOZ_FAILURE_DIR\");\n")
            self.assertTrue(ooz_patch_installed(root))
            self.assertIn(b"OOZ_FAILED_STREAM_CAPTURE_V1", source.read_bytes())

    def test_partial_or_missing_integration_rejected_without_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            io = self.create_io(root, self.jam(), None)
            before = (io / "jamfile").read_bytes()
            with self.assertRaisesRegex(ValueError, "modified but decoder source"):
                ooz_patch_installed(root)
            self.assertEqual(before, (io / "jamfile").read_bytes())
            (io / "oodleIoOoz.cpp").write_bytes(b"// partial")
            with self.assertRaisesRegex(ValueError, "Partial"):
                ooz_patch_installed(root)

    def test_unexpected_decoder_source_without_jam_patch_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.create_io(root, b"ProjectAllowsOodle = yes ;\n", self.cpp())
            with self.assertRaisesRegex(ValueError, "Partial"):
                ooz_patch_installed(root)


if __name__ == "__main__":
    unittest.main()
