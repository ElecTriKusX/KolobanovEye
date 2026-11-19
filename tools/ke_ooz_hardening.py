"""Build-local hardening of pinned powzix/ooz Kraken decoder.

The raw upstream source encodes entropy-array count in six bits (0..63) but
uses two stack arrays of only 32 entries. Rejecting >32 avoided the original
stack overflow but could reject otherwise valid game levels. The derived
compilation copy instead reserves 64 entries, so the full encoded range fits.

Never change vendor/ooz or its pinned SHA-256 files.
"""
from pathlib import Path


def guard_kraken_multi_array(path: Path) -> bool:
    """Widen both local entropy tables to cover the complete six-bit count.

    Keep the function name for compatibility with older tests/build callers.
    Return True if the build copy was patched; False if already patched.
    Refuse unknown or partially patched decoder layouts.
    """
    path = Path(path)
    raw = path.read_bytes()
    crlf = b"\r\n" in raw
    data = raw.replace(b"\r\n", b"\n")
    anchor = b"  num_arrays_in_file &= 0x3f;\n"
    before = (b"  uint8 *entropy_array_data[32];",
              b"  uint32 entropy_array_size[32];")
    after = (b"  uint8 *entropy_array_data[64];",
             b"  uint32 entropy_array_size[64];")
    marker = b"  // KolobanovEye: six-bit entropy-array count requires 64 slots.\n"
    if data.count(anchor) != 1:
        raise ValueError(f"Unknown OOZ Kraken count representation: {path}")
    if (data.count(marker) == 1 and
            all(data.count(v) == 1 for v in after) and
            all(v not in data for v in before)):
        return False
    if (marker in data or any(v not in data for v in before) or
            any(v in data for v in after) or
            b"  if (num_arrays_in_file > 32)" in data):
        raise ValueError(f"Partial or obsolete OOZ Kraken hardening: {path}")
    if any(data.count(v) != 1 for v in before):
        raise ValueError(f"Unexpected duplicate entropy arrays in OOZ: {path}")
    data = data.replace(anchor, anchor + marker, 1)
    for original, replacement in zip(before, after):
        data = data.replace(original, replacement, 1)
    path.write_bytes(data.replace(b"\n", b"\r\n") if crlf else data)
    return True
