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


def trace_kraken_failures(path: Path) -> bool:
    """Add failure-only decoder telemetry to the disposable OOZ build copy.

    Diagnostics go to stderr/host-stdout.log. Successful quanta are silent.
    Exact anchors prevent silently instrumenting an unrelated upstream version.
    """
    path = Path(path)
    data = path.read_bytes()
    crlf = b"\r\n" in data
    normalized = data.replace(b"\r\n", b"\n")
    marker = b'[KE] OOZ_STEP_FAIL'
    if marker in normalized:
        if normalized.count(marker) != 1 or b'[KE] OOZ_QUANTUM_FAIL' not in normalized:
            raise ValueError("Partial OOZ failure telemetry")
        return False
    replacements = (
        (
            b'  if (n != qhdr.compressed_size)\n    return false;\n',
            b'  if (n != qhdr.compressed_size)\n'
            b'  {\n'
            b'    fprintf(stderr, "[KE] OOZ_QUANTUM_FAIL codec=%d decoded_offset=%d quantum_packed=%u got=%d\\n",\n'
            b'      dec->hdr.decoder_type, offset, qhdr.compressed_size, n);\n'
            b'    return false;\n'
            b'  }\n',
        ),
        (
            b'    if (!Kraken_DecodeStep(dec, dst, offset, dst_len, src, src_len))\n'
            b'      goto FAIL;\n',
            b'    if (!Kraken_DecodeStep(dec, dst, offset, dst_len, src, src_len))\n'
            b'    {\n'
            b'      fprintf(stderr, "[KE] OOZ_STEP_FAIL decoded_offset=%d packed_remaining=%zu output_remaining=%zu\\n",\n'
            b'        offset, src_len, dst_len);\n'
            b'      goto FAIL;\n'
            b'    }\n',
        ),
        (
            b'        if (!Leviathan_ReadLzTable(mode,\n'
            b'            src, src + src_used,\n'
            b'            dst, dst_count,\n'
            b'            dst - dst_start,\n'
            b'            scratch + sizeof(LeviathanLzTable), scratch + scratch_usage,\n'
            b'            (LeviathanLzTable*)scratch))\n'
            b'          return -1;\n',
            b'        if (!Leviathan_ReadLzTable(mode,\n'
            b'            src, src + src_used,\n'
            b'            dst, dst_count,\n'
            b'            dst - dst_start,\n'
            b'            scratch + sizeof(LeviathanLzTable), scratch + scratch_usage,\n'
            b'            (LeviathanLzTable*)scratch))\n'
            b'        {\n'
            b'          fprintf(stderr, "[KE] OOZ_LEVIATHAN_LZ_TABLE_FAIL mode=%d output_chunk=%d packed_chunk=%d\\n",\n'
            b'            mode, dst_count, src_used);\n'
            b'          return -1;\n'
            b'        }\n',
        ),
    )
    for original, replacement in replacements:
        if normalized.count(original) != 1:
            raise ValueError(f"Missing unique OOZ trace anchor: {original[:96]!r}")
        normalized = normalized.replace(original, replacement, 1)
    path.write_bytes(normalized.replace(b"\n", b"\r\n") if crlf else normalized)
    return True
