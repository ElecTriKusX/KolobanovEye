import json
import tempfile
import unittest
from pathlib import Path
from ke_coco import export, group_for, split_groups
from ke_review import write_rgb_png


def frame_blk(x, z, yaw=0):
    return (f'level:t="D:/Game/levels/map.bin"\ntank_world:p3={x}, 50, {z}\nyaw_deg:r={yaw}\n'
            'width:i=64\nheight:i=64\n')


def make_run(root, name, x, z):
    run = root / name; (run / 'images').mkdir(parents=True)
    write_rgb_png(run / 'images/000000.png', 64, 64, bytes([1,2,3])*4096)
    (run / 'plan.json').write_text(json.dumps({'cameras': [{}]}))
    item = {'frame_index': 0, 'image': 'images/000000.png', 'dagor_frame_metadata_blk': frame_blk(x,z),
            'annotation': {'class':'tank', 'bbox_kind':'approximate_amodal',
                           'bbox_method':'projected_3d_aabb', 'bbox_xywh':[1,2,20,30]}}
    (run / 'metadata.jsonl').write_text(json.dumps(item)+'\n')
    return run


class CocoTest(unittest.TestCase):
    def test_cell_groups_ignore_yaw_and_seed_and_handle_negative(self):
        self.assertEqual(group_for(frame_blk(10, 20, 0), 256), group_for(frame_blk(10, 20, 180), 256))
        self.assertNotEqual(group_for(frame_blk(-1, 0), 256), group_for(frame_blk(0, 0), 256))
        self.assertEqual(split_groups(['a','b','c','d'], 42), split_groups(['d','b','a','c'],42))
        with self.assertRaises(ValueError): split_groups(['a', 'a'], 1)

    def test_export_does_not_duplicate_images_and_splits_groups(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            runs=[make_run(root, 'r0', 1,1), make_run(root,'r1',2,2),
                  make_run(root,'r2',300,1), make_run(root,'r3',600,1)]
            out=root/'export'; manifest=export(runs,out,split=True)
            self.assertEqual(sum(manifest['images_per_split'].values()), 4)
            seen={}
            for sub in ('train','val','test'):
                coco=json.loads((out/f'{sub}.json').read_text())
                self.assertEqual(coco['categories'][0]['name'], 'tank')
                for img in coco['images']:
                    self.assertTrue((out/img['file_name']).is_file())
                    seen.setdefault(img['scene_group'],set()).add(sub)
                for ann in coco['annotations']:
                    self.assertEqual(ann['bbox_kind'], 'approximate_amodal')
            self.assertTrue(all(len(s)==1 for s in seen.values()))
            self.assertFalse(list(out.glob('*.png')))
            with self.assertRaises(ValueError): export(runs,out,split=True)
