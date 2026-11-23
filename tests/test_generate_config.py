import json
import tempfile
import unittest
from pathlib import Path
from ke_generate import DEFAULTS, command, validate_config
from tools.ke_review import write_rgb_png
from tools.ke_validate import validate


class ConfigTest(unittest.TestCase):
    def test_config_command_paths_are_separate_arguments(self):
        config = validate_config({**DEFAULTS, 'dagor': r'D:\My Tools\Dagor', 'count': 3})
        cmd = command(config)
        self.assertEqual(cmd[cmd.index('--dagor') + 1], r'D:\My Tools\Dagor')
        self.assertEqual(cmd[cmd.index('--count') + 1], '3')
        self.assertEqual(cmd[cmd.index('--level') + 1], 'levels/avg_normandy.bin')
        self.assertEqual(cmd[cmd.index('--camera-distance-range') + 1:cmd.index('--camera-distance-range') + 3], ['18.0', '35.0'])

    def test_config_rejects_bad_values(self):
        for change in ({'count': True}, {'count': 0}, {'width': 20}, {'distance_min': 40},
                       {'fov': float('nan')}, {'wat': 1}, {'clipmap_feedback': 'wrong'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_config({**DEFAULTS, **change})

    def test_ground_map_id_and_tank_placement(self):
        cfg = validate_config({
            **DEFAULTS, 'map_id': 'levels/avg_finland.bin',
            'placement_mode': 'tank_map', 'map_margin_percent': 0.0
        })
        cmd = command(cfg)
        self.assertEqual(cmd[cmd.index('--level') + 1], 'levels/avg_finland.bin')
        self.assertEqual(cmd[cmd.index('--placement-mode') + 1], 'tank_map')
        for bad in ('levels/avn_fiji.bin', '../avg_finland.bin',
                    'levels/../avg_finland.bin', 'levels/avg_a.bin/other',
                    '', True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_config({**DEFAULTS, 'map_id': bad})

    def test_singleton_is_supported_for_sparse_map_quotas(self):
        cfg = validate_config({'count': 1, 'seed': 42})
        self.assertEqual(cfg['count'], 1)
        self.assertEqual(command(cfg)[command(cfg).index('--count') + 1], '1')

    def test_validator_checks_real_png_and_bounds(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp); (run / 'images').mkdir()
            write_rgb_png(run / 'images/000000.png', 64, 64, bytes([7, 8, 9]) * (64*64))
            (run / 'plan.json').write_text(json.dumps({'cameras': [{}]}))
            item = {'frame_index': 0, 'image': 'images/000000.png',
                    'annotation': {'class': 'tank', 'bbox_kind': 'approximate_amodal', 'bbox_xywh': [2, 3, 20, 30]},
                    'dagor_frame_metadata_blk': 'width:i=64\nheight:i=64\n'}
            meta = run / 'metadata.jsonl'; meta.write_text(json.dumps(item) + '\n')
            self.assertEqual(validate(run)['frames'], 1)
            item['annotation']['bbox_xywh'] = [60, 2, 20, 5]
            meta.write_text(json.dumps(item) + '\n')
            with self.assertRaisesRegex(ValueError, 'BBox'): validate(run)

class CameraHeightConfigTests(unittest.TestCase):
    def test_height_range_is_passed_to_renderer(self):
        from ke_generate import command
        cmd = command({'camera_height_min': 5.0, 'camera_height_max': 12.0})
        i = cmd.index('--camera-height-range')
        self.assertEqual(cmd[i+1:i+3], ['5.0', '12.0'])

    def test_impossible_height_range_rejected(self):
        from ke_generate import validate_config
        with self.assertRaises(ValueError):
            validate_config({'camera_height_min': 3.0, 'camera_height_max': 2.0})
        with self.assertRaises(ValueError):
            validate_config({'camera_height_min': 0.0, 'camera_height_max': 0.1, 'camera_clearance': 0.5})

class WaterGroundFilterTests(unittest.TestCase):
    def test_ground_threshold_passed_and_finite(self):
        from ke_generate import command, validate_config
        args = command({'minimum_ground_y': 1.25})
        self.assertEqual(args[args.index('--minimum-ground-y') + 1], '1.25')
        with self.assertRaises(ValueError): validate_config({'minimum_ground_y': float('nan')})
        with self.assertRaises(ValueError): validate_config({'minimum_ground_y': -100001})
