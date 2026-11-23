import tempfile
import unittest
from pathlib import Path
from tools.ke_png_fast import validate_rgb_png
from tools.ke_review import write_rgb_png

class FastPNGTest(unittest.TestCase):
    def test_accept_valid_and_reject_corrupt(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'image.png'; write_rgb_png(p,64,64,bytes([1,2,3])*4096)
            self.assertEqual(validate_rgb_png(p,64,64),(64,64))
            with self.assertRaises(ValueError): validate_rgb_png(p,65,64)
            data=bytearray(p.read_bytes());data[45]^=1;p.write_bytes(data)
            with self.assertRaises(ValueError): validate_rgb_png(p,64,64)

    def test_invalid_zlib_with_valid_chunk_crc(self):
        import struct
        import zlib
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'image.png'
            write_rgb_png(p, 64, 64, bytes([1, 2, 3]) * 4096)
            data = bytearray(p.read_bytes())
            i = data.index(b'IDAT')
            length = struct.unpack('>I', data[i-4:i])[0]
            data[i+4] ^= 255
            data[i+4+length:i+8+length] = struct.pack('>I', zlib.crc32(data[i:i+4+length]))
            p.write_bytes(data)
            with self.assertRaises(ValueError):
                validate_rgb_png(p, 64, 64)
