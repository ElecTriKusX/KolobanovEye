import json
import tempfile
import unittest
from pathlib import Path
from ke_clipmap_map import assemble
from ke_pixel_debug import load_png

class ClipmapMapTest(unittest.TestCase):
    def test_coverage_detail_and_missing_area(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); tiles=root/'ke-clipmap-tiles'; tiles.mkdir()
            blk='tank_world:p3=25, 0, 50\nmap_bounds_min:p3=0, 0, 0\nmap_bounds_max:p3=100, 10, 100\n'
            (root/'metadata.jsonl').write_text(json.dumps({'frame_index':0,'dagor_frame_metadata_blk':blk})+'\n')
            (tiles/'a.ppm').write_bytes(b'P6\n4 4\n255\n' + bytes([255,0,0])*16)
            (tiles/'b.ppm').write_bytes(b'P6\n4 4\n255\n' + bytes([0,255,0])*16)
            (tiles/'tiles.csv').write_text('a.ppm,0,0,50,100,2,4,0\nb.ppm,0,0,25,100,0,4,0\n')
            report=assemble(root, root/'map',64)
            self.assertEqual(report['coverage_percent'],50.0)
            w,h,pixels=load_png(root/'map'/'terrain.png')
            self.assertEqual((w,h),(64,64))
            self.assertEqual(tuple(pixels[0:3]),(0,255,0))
            self.assertEqual(tuple(pixels[20*3:20*3+3]),(255,0,0))
            self.assertEqual(tuple(pixels[50*3:50*3+3]),(0,0,0))
            self.assertTrue((root/'map'/'positions.html').exists())
