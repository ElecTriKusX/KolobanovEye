import json
import tempfile
import unittest
from pathlib import Path
from ke_map import read_landscape_bounds, positions, create

class MapTest(unittest.TestCase):
    def test_bounds_and_actual_positions(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            blk = 'tank_world:p3=10, 5, 20\nmap_bounds_min:p3=-100, 0, -200\nmap_bounds_max:p3=100, 20, 200\n'
            (root/'metadata.jsonl').write_text(json.dumps({'frame_index': 7, 'dagor_frame_metadata_blk': blk})+'\n')
            self.assertEqual(read_landscape_bounds(root), (-100,100,-200,200))
            self.assertEqual(positions(root)[0]['x'], 10)
            (root/'map.png').write_bytes(b'png')
            create(root, root/'map.png', root/'result.html')
            content=(root/'result.html').read_text()
            self.assertIn('"u": 0.55', content)
            self.assertIn('"v": 0.55', content)
            with self.assertRaises(FileExistsError): create(root, root/'map.png', root/'result.html')
    def test_missing_bounds_fails(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); (p/'metadata.jsonl').write_text(json.dumps({'dagor_frame_metadata_blk':'tank_world:p3=0,0,0'})+'\n')
            with self.assertRaises(ValueError): read_landscape_bounds(p)

class FixedClipBoundsTest(unittest.TestCase):
    def test_fixed_clip_uses_own_bounds(self):
        import json
        import tempfile
        from pathlib import Path
        from ke_map import read_landscape_bounds
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            blk=('map_bounds_min:p3=-32768,0,-32768\nmap_bounds_max:p3=32736,0,32736\n'
                 'last_clip_min:p3=-100,0,-200\nlast_clip_max:p3=300,0,400\n')
            (p/'metadata.jsonl').write_text(json.dumps({'dagor_frame_metadata_blk':blk})+'\n')
            self.assertEqual(read_landscape_bounds(p,fixed_clip=True), (-100,300,-200,400))
            self.assertEqual(read_landscape_bounds(p), (-32768,32736,-32768,32736))
