import tempfile
import unittest
from pathlib import Path

from tools.ke_frame_journal import JournalCursor, read_journal


class OnlineJournalCursorTests(unittest.TestCase):
    def test_incremental_matches_full_audit(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "frames"
            cursor = JournalCursor(path)
            for i in range(5):
                text = f"data:t={i}\n"
                payload = text.encode("utf-8")
                with path.open("ab") as stream:
                    stream.write(f"KEFRAME {i} {len(payload)}\n".encode() + payload)
                self.assertEqual(cursor.read_next(i), text)
                self.assertEqual(cursor.offset, path.stat().st_size)
            self.assertEqual(read_journal(path), {i: f"data:t={i}\n" for i in range(5)})

    def test_truncated_and_out_of_order_do_not_advance(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "frames"
            cursor = JournalCursor(path)
            path.write_bytes(b"KEFRAME 0 3\nx")
            with self.assertRaises(ValueError):
                cursor.read_next(0)
            self.assertEqual(cursor.offset, 0)
            with self.assertRaises(ValueError):
                cursor.read_next(1)
            path.write_bytes(b"KEFRAME 0 3\nxyz")
            self.assertEqual(cursor.read_next(0), "xyz")
            with path.open("ab") as stream:
                stream.write(b"KEFRAME 0 3\nabc")
            with self.assertRaises(ValueError):
                cursor.read_next(1)
            self.assertEqual(cursor.next_index, 1)
