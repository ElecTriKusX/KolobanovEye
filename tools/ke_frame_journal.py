"""Read completed BLK records from the Dagor batch journal."""
import re

JOURNAL = "metadata.pending.blk"


def read_journal(path, *, allow_truncated=False):
    records = {}
    with path.open("rb") as stream:
        while header := stream.readline(64):
            match = re.fullmatch(rb"KEFRAME (\d{1,4}) (\d{1,5})\n", header)
            if not match:
                if allow_truncated and not header.endswith(b"\n"):
                    break
                raise ValueError("Invalid frame journal header")
            index, size = map(int, match.groups())
            if index >= 1000 or not 0 < size <= 65536 or index in records:
                raise ValueError("Invalid or duplicate frame journal record")
            payload = stream.read(size)
            if len(payload) != size:
                if allow_truncated:
                    break
                raise ValueError("Truncated frame journal record")
            records[index] = payload.decode("utf-8")
    return records


class JournalCursor:
    """Sequential online reader; the final full journal audit remains mandatory.

    Call only after the immutable ready marker is observed. A bad or partial
    record is a protocol error, and the cursor is not advanced on failure.
    """
    def __init__(self, path):
        self.path = path
        self.offset = 0
        self.next_index = 0

    def read_next(self, index):
        if index != self.next_index:
            raise ValueError("Unexpected online journal index")
        with self.path.open("rb") as stream:
            stream.seek(self.offset)
            header = stream.readline(64)
            match = re.fullmatch(rb"KEFRAME (\d{1,4}) (\d{1,5})\n", header)
            if not match:
                raise ValueError("Invalid or missing online frame journal header")
            actual, size = map(int, match.groups())
            if actual != index or actual >= 1000 or not 0 < size <= 65536:
                raise ValueError("Unexpected online frame journal record")
            payload = stream.read(size)
            if len(payload) != size:
                raise ValueError("Truncated online frame journal record")
            text = payload.decode("utf-8")
            offset = stream.tell()
        self.offset = offset
        self.next_index += 1
        return text
