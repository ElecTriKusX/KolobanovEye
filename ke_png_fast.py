"""Fast structural validation of RGB PNG streams without reconstructing pixels."""
import struct
import zlib


def validate_rgb_png(path, expected_width, expected_height):
    data = path.read_bytes()
    if len(data) > 100 * 1024 * 1024 or data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Invalid or oversized PNG')
    pos = 8
    header = None
    chunks = []
    seen_end = False
    seen_data = False
    data_ended = False
    while pos + 12 <= len(data):
        length = int.from_bytes(data[pos:pos+4], 'big')
        end = pos + 12 + length
        if end > len(data): raise ValueError('Truncated PNG')
        kind = data[pos+4:pos+8]; payload = data[pos+8:pos+8+length]
        if zlib.crc32(kind + payload) & 0xffffffff != int.from_bytes(data[pos+8+length:end], 'big'):
            raise ValueError('Invalid PNG checksum')
        if kind == b'IHDR':
            if header is not None or pos != 8 or length != 13: raise ValueError('Invalid IHDR')
            header = struct.unpack('>IIBBBBB', payload)
        elif kind == b'IDAT':
            if header is None or data_ended: raise ValueError('Invalid IDAT order')
            seen_data = True; chunks.append(payload)
        elif kind == b'IEND':
            if length or not seen_data or end != len(data): raise ValueError('Invalid IEND')
            seen_end = True; break
        elif seen_data:
            data_ended = True
        pos = end
    if not seen_end or not header: raise ValueError('Missing PNG data')
    w,h,depth,color,compression,filtering,interlace = header
    if (w,h,depth,color,compression,filtering,interlace) != (expected_width,expected_height,8,2,0,0,0):
        raise ValueError('Expected non-interlaced 8-bit RGB image of the requested size')
    if not (0 < w <= 4096 and 0 < h <= 4096): raise ValueError('Invalid image dimensions')
    stride = 1 + w * 3; total = stride * h
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(b''.join(chunks), total + 1)
    except zlib.error as exc:
        raise ValueError('Invalid PNG compressed stream') from exc
    if len(raw) != total or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('Invalid PNG compressed stream')
    if any(x > 4 for x in raw[::stride]): raise ValueError('Invalid PNG filter')
    return w, h
