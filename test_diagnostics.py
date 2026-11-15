import struct
import tempfile
import unittest
import zlib
from pathlib import Path

import ke_diagnose as diagnostic


def chunk(kind, payload):
    return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)


class DiagnosticsTests(unittest.TestCase):
    def test_png_statistics_and_checksums(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'frame.png'
            raw = b'\x00\x00\x00\x00\x01\x02\x03'
            p.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))
            stats = diagnostic.image_stats(p)
            self.assertEqual((stats['black_pixels'], stats['near_black_pixels'], stats['pixels']), (1, 2, 2))

    def test_cpu_upload_does_not_hide_gpu_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            log = root / '.logs' / 'run' / 'debug'
            log.parent.mkdir(parents=True)
            log.write_text('[KE] INDIRECTION_CHECK source=gpu entries=10 mismatches=10 zeros=10 tiles=3\n'
                           '[KE] INDIRECTION_CPU_UPLOAD success=1\n'
                           '[KE] INDIRECTION_CHECK source=cpu_upload entries=10 mismatches=0 zeros=0 tiles=3\n')
            report = diagnostic.analyze(root)
            checks = {x['name']: x['status'] for x in report['checks']}
            self.assertEqual(checks['indirection_gpu'], 'error')
            self.assertEqual(checks['indirection_cpu_upload'], 'ok')
            self.assertEqual(checks['indirection_before_draw'], 'unknown')
            out, archive = diagnostic.write_outputs(root, report)
            self.assertTrue((out / 'report.json').is_file())
            self.assertTrue(archive.is_file())


if __name__ == '__main__':
    unittest.main()
