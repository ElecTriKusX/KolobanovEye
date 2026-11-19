"""Conservative, deterministic safety fixes for the pinned decode-only OOZ build.

The original five vendor/ooz source files remain byte-for-byte identical to
vendor/ooz.lock.json. Only the temporary build-logs/ooz-local compilation copy
is patched after verifying the pinned snapshot.
"""
from pathlib import Path


def guard_kraken_multi_array(path: Path) -> bool:
    """Reject malicious/corrupt multi-array counts before writing local arrays[32].

    powzix/ooz commit 05038060aa68f9187ae9923b2388ca8db40e58d1
    Kraken_DecodeMultiArray reads (input_byte & 0x3f) => 0..63, but declares
    entropy_array_data[32] and entropy_array_size[32]. Values 33..63 could
    overrun those stack arrays. This guard does not prove all codec paths safe.
    """
    path = Path(path)
    raw = path.read_bytes()
    eol = b"\r\n" if b"\r\n" in raw else b"\n"
    data = raw.replace(b"\r\n", b"\n")
    anchor = b"  num_arrays_in_file &= 0x3f;\n"
    arrays = (b"  uint8 *entropy_array_data[32];",
              b"  uint32 entropy_array_size[32];")
    if data.count(anchor) != 1 or any(data.count(v) != 1 for v in arrays):
        raise ValueError(f"Unsupported OOZ Kraken source layout (missing arrays[32]): {path}")
    marker = b"  // KolobanovEye: reject counts above the fixed local arrays[32].\n"
    guard = (marker +
             b"  if (num_arrays_in_file > 32)\n"
             b"    return -1;\n")
    if data.count(guard) == 1 and data.count(marker) == 1:
        return False
    if marker in data or guard in data:
        raise ValueError(f"Partial OOZ Kraken multi-array hardening: {path}")
    # The check must run before any arrays[32] indexing or codec calls.
    data = data.replace(anchor, anchor + guard, 1)
    path.write_bytes(data.replace(b"\n", b"\r\n") if eol == b"\r\n" else data)
    return True
