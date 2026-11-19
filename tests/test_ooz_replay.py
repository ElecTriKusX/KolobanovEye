"""Offline metadata tests and AV-driven executable replay denial."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.ke_ooz_replay import choose_run, load_capture, replay


class OozReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runs = self.root / "runs"
        self.run = self.runs / "run-001"
        self.failure = self.run / "ooz-failures"
        self.failure.mkdir(parents=True)
        self.raw = self.failure / "failed-ooz.bin"
        self.raw.write_bytes(b"\x8c\x0c" + b"test")
        (self.failure / "failed-ooz.txt").write_text(
            "packed=6\nunpacked=16\ncodec=12\nsource=avg_abandoned_factory.bin\n",
            encoding="utf-8")
        self.cpp = self.root / "ke-ooz-replay.exe"
        self.cpp.touch()

    def test_metadata_and_input_are_consistent(self):
        self.assertEqual(choose_run(self.runs), self.run)
        raw, meta = load_capture(self.run)
        self.assertEqual(raw, self.raw)
        self.assertEqual(meta["codec"], 12)
        self.assertEqual(meta["unpacked"], 16)
        self.assertEqual(meta["source"], "avg_abandoned_factory.bin")

    def test_corrupt_capture_fails_without_running_codecs(self):
        self.raw.write_bytes(b"\x8c\x06test")
        with self.assertRaisesRegex(ValueError, "Заголовок"):
            load_capture(self.run)
        self.raw.write_bytes(b"\x8c\x0ctes")
        with self.assertRaisesRegex(ValueError, "неполон"):
            load_capture(self.run)

    def test_unverified_native_decoder_is_never_executed(self):
        unoodle = self.root / "unoodle.exe"
        unoodle.touch()
        with patch("tools.ke_ooz_replay.subprocess.run") as runner:
            with self.assertRaisesRegex(ValueError, "suspended after Kaspersky"):
                replay(self.run, cpp=self.cpp, unoodle=unoodle)
            runner.assert_not_called()
        self.assertFalse((self.failure / "rust-decoded.raw").exists())
        self.assertFalse((self.failure / "native-decoded.raw").exists())


if __name__ == "__main__":
    unittest.main()
