import json
import tempfile
import unittest
from pathlib import Path
from tools.ke_annotation import read_annotation, write_annotation

class AnnotationTest(unittest.TestCase):
    def test_dagor_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'frame.blk'
            p.write_text('class:t="tank"\nwidth:i=1280\nheight:i=720\nbbox_amodal_aabb_xywh:p4=351.497, 211.967, 595.145, 339.29\n')
            out = Path(d) / 'annotation.json'
            write_annotation(p, out)
            obj = json.loads(out.read_text())['annotations'][0]
            self.assertEqual(obj['bbox_kind'], 'approximate_amodal')
            self.assertIsNone(obj['visibility'])
            for bad in ('-1,0,2,2', '0,0,1281,2', '0,0,nan,2', '0,0,0,2'):
                p.write_text(f'class:t="tank"\nwidth:i=1280\nheight:i=720\nbbox_amodal_aabb_xywh:p4={bad}\n')
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    read_annotation(p)
