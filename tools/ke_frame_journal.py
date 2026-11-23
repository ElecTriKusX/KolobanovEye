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
