import json
import tempfile
import unittest
from pathlib import Path
from ke_recover import recover
from ke_review import write_rgb_png


def setup_run(root):
    (root/'images').mkdir()
    camera={'azimuth_deg':1,'horizontal_distance':20,'elevation_deg':10,'offset':[1,2,3]}
    (root/'plan.json').write_text(json.dumps({'schema_version':3,'seed':42,'frames_per_position':2,
                                             'tank_yaw_deg':20,'cameras':[[camera],[camera],[camera]]}))
    return camera


def write_frame(root, index):
    write_rgb_png(root/f'images/{index:06d}.png',64,64,bytes([2,3,4])*4096)
    (root/f'frame-{index:06d}.pending.blk').write_text('class:t="tank"\nwidth:i=64\nheight:i=64\n'
        f'camera_attempt:i=0\ncomposition_index:i={index//2}\n'
        'bbox_amodal_aabb_xywh:p4=1,2,30,40\n')


class RecoverTest(unittest.TestCase):
    def test_partial_recovery_preserves_and_validates(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); setup_run(root); write_frame(root,0); write_frame(root,1)
            (root/'frame-000001.pending.blk').write_text('bad')
            result=recover(root)
            self.assertEqual(result['recovered_frames'],1)
            self.assertEqual(result['missing_indices'],[1,2])
            self.assertEqual(result['invalid'][0]['frame_index'],1)
            self.assertTrue((root/'images/000000.png').exists())
            self.assertTrue((root/'frame-000000.pending.blk').exists())
            self.assertFalse((root/'metadata.jsonl').exists())
            self.assertEqual(json.loads((root/'metadata.recovered.jsonl').read_text())['frame_index'],0)
            with self.assertRaises(ValueError): recover(root)

    def test_refuses_without_verified_pair_or_if_completed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); setup_run(root)
            with self.assertRaises(ValueError): recover(root)
            self.assertFalse((root/'metadata.recovered.jsonl').exists())
            (root/'metadata.jsonl').write_text('done')
            with self.assertRaises(ValueError): recover(root)
