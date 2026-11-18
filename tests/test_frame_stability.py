"""Offline regression tests for the adaptive capture protocol and patch upgrade."""

import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import kolobanoveye as ke
from tools.ke_coco import export
from ke_generate import command, validate_config
from tools.ke_review import write_rgb_png
from tools.ke_validate import validate


class FrameStabilityTest(unittest.TestCase):
    def test_saved_config_and_explicit_opt_out(self):
        config = validate_config({'count': 40, 'resolve_no_ssr': False, 'source_indices': None})
        self.assertIn('--wait-frame-stability', command(config))
        self.assertIn('--no-wait-frame-stability', command({'wait_frame_stability': False}))
        self.assertIn('--wait-sky-convergence', command({'wait_sky_convergence': True}))
        for invalid in ({'min_warmup_renders': 7}, {'min_warmup_renders': '8'},
                        {'wait_frame_stability': 1}, {'resolve_no_ssr': True}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_config(invalid)

    def test_articulation_seed_ranges_and_pose_validation(self):
        args = command({'turret_yaw_min': -90, 'turret_yaw_max': 90, 'gun_pitch_min': -4, 'gun_pitch_max': 10})
        self.assertEqual(args[args.index('--gun-pitch-range') + 1:args.index('--gun-pitch-range') + 3], ['-4', '10'])
        self.assertEqual(ke.plan_articulation(None, 1, (0, 0), (0, 0)), [])
        self.assertEqual(ke.plan_articulation(None, 1, (45, 45), (10, 10)), [(45, 10)])
        poses = ke.plan_articulation(42, 12, (-90, 90), (-4, 10))
        self.assertEqual(poses, ke.plan_articulation(42, 12, (-90, 90), (-4, 10)))
        self.assertNotEqual(poses, ke.plan_articulation(43, 12, (-90, 90), (-4, 10)))
        self.assertTrue(all(-90 <= yaw <= 90 and -4 <= pitch <= 10 for yaw, pitch in poses))
        with self.assertRaises(ValueError):
            validate_config({'gun_pitch_min': -9})
        with self.assertRaises(ValueError):
            ke.read_articulation("turret_yaw_deg:r=15\ngun_pitch_deg:r=2\n", (15, 3))

    def test_warmup_pipeline_on_predecessor_and_second_run(self):
        def normalize(data):
            return data.replace(b'\r\n', b'\n')

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'kolobanovEye.cpp'
            self.assertFalse(ke.is_upgraded_offscreen_capture(source))
            for ending in (b'\n', b'\r\n'):
                with self.subTest(patch_line_ending=ending):
                    previous = normalize(ke.OFFSCREEN_STREAMING_WAIT_PATCH.read_bytes()).replace(b'\n', ending)
                    hunks = re.findall(rb'(?ms)^@@ [^\n]*\n(.*?)(?=^@@ |\Z)', normalize(previous))
                    fragments = [b''.join(line[1:] for line in hunk.splitlines(keepends=True)
                                          if line.startswith((b' ', b'+'))) for hunk in hunks]
                    source.write_bytes(b'\n'.join(fragments).replace(b'\n', b'\r\n'))
                    convergence, stability = [
                        root / patch_path.name for patch_path in (ke.HAZE_CONVERGENCE_PATCH, ke.HAZE_STABILITY_PATCH)
                    ]
                    for src, dst in ((ke.HAZE_CONVERGENCE_PATCH, convergence), (ke.HAZE_STABILITY_PATCH, stability)):
                        dst.write_bytes(normalize(src.read_bytes()).replace(b'\n', ending))
                    ke.apply_haze_capture_pipeline(source, convergence, stability)
                    upgraded = source.read_bytes()
                    self.assertIn(b'FRAME_STABILITY_FINAL', upgraded)
                    self.assertNotIn(b'\n', upgraded.replace(b'\r\n', b''))
                    self.assertNotIn(b'\r', upgraded.replace(b'\r\n', b''))
                    ke.apply_haze_capture_pipeline(source, convergence, stability)
                    self.assertEqual(source.read_bytes(), upgraded)
            source.write_bytes(b'[KE] HAZE_CONVERGENCE ')
            with self.assertRaisesRegex(ValueError, 'Partial offscreen-capture upgrade'):
                ke.is_upgraded_offscreen_capture(source)

    def test_sequence_protocol_requires_stability_and_exports_normal_annotations(self):
        for stable in (True, False):
            with self.subTest(stable=stable), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                out, dagor, game = root / 'run', root / 'dagor', root / 'game'
                binary = dagor / ke.BIN
                binary.mkdir(parents=True)
                for name in ('dumpGrp-dev.exe', 'daEditor3x-dev.exe'):
                    (binary / name).touch()

                def pretend_editor(argv, log, cwd, timeout):
                    if log.name == 'grp.txt':
                        log.touch()
                        return
                    config = (out / 'capture.blk').read_text()
                    self.assertIn('min_warmup_renders:i=24\n', config)
                    self.assertIn('wait_frame_stability:b=yes\n', config)
                    self.assertIn('wait_sky_convergence:b=no\n', config)
                    self.assertIn('articulate_tank:b=yes\n', config)
                    self.assertIn('articulation_1:p2=45, 5\n', config)
                    markers = ['MAP_OK', 'MODEL_OK', 'PLACEMENT_OK', 'WATER_PROXY', 'CAMERA_TERRAIN',
                               'SUPPORT_HEIGHTS', 'CAMERA_OK', 'WATER_GROUND_FILTER', 'MAP_BOUNDS']
                    lines = [f'[KE] {marker} ' for marker in markers]
                    lines.append('[KE] TANK_BOUNDS min_x=0.000 max_x=4096.000 min_z=0.000 max_z=4096.000')
                    payload = (b'width:i=64\nheight:i=64\nclass:t="tank"\nlevel:t="avg_normandy.bin"\n'
                               b'camera_attempt:i=0\ncomposition_index:i=0\ntank_world:p3=1,2,3\n'
                               b'eye:p3=4,5,6\nbbox_amodal_aabb_xywh:p4=2,3,20,30\n'
                               b'turret_yaw_deg:r=45\ngun_pitch_deg:r=5\n'
                               b'pose_bounds_method:t="conservative_joint_envelope"\n')
                    with (out / ke.JOURNAL).open('wb') as journal:
                        for index in range(2):
                            journal.write(f'KEFRAME {index} {len(payload)}\n'.encode() + payload)
                            write_rgb_png(out / f'images/{index:06d}.png', 64, 64, bytes([7, 8, 9]) * (64 * 64))
                            lines += ['[KE] FRAME_OK ', '[KE] ORBIT_FRAME ']
                            if stable or index == 0:
                                lines.append('[KE] FRAME_STABILITY_FINAL enabled=1 stable=1 checks=3')
                    lines.append('[KE] SEQUENCE_OK frames=2')
                    (out / 'probe.dcmd.log').write_text('\n'.join(lines))

                argv = command({'dagor': str(dagor), 'game': str(game), 'output': str(out),
                                'count': 2, 'width': 64, 'height': 64, 'min_warmup_renders': 24, 'capture_tank_map': False,
                                'turret_yaw_min': 45, 'turret_yaw_max': 45, 'gun_pitch_min': 5, 'gun_pitch_max': 5})
                with patch.object(ke, 'os', SimpleNamespace(name='nt', SEEK_END=ke.os.SEEK_END)), patch.object(
                    ke, 'inspect', return_value={'errors': []}
                ), patch.object(ke, 'choose_resources', return_value=('pzkpfw_VI_ausf_h1_tiger', 'pzkpfw_VI_ausf_h1_tiger_skeleton')), patch.object(
                    ke, 'run_logged', side_effect=pretend_editor
                ), patch.object(ke.sys, 'argv', argv[2:]):
                    self.assertEqual(ke.main(), 0 if stable else 1)
                if stable:
                    self.assertEqual(validate(out)['frames'], 2)
                    bounds = json.loads((out / 'tank-map-bounds.json').read_text())
                    self.assertEqual(bounds['tank_square']['max_x'], 4096.0)
                    export([out], root / 'coco')
                    coco = json.loads((root / 'coco/all.json').read_text())
                    self.assertEqual(len(coco['images']), 2)
                    self.assertEqual(coco['images'][0]['ke_scene']['gun_pitch_deg'], 5)
                    self.assertEqual([ann['bbox'] for ann in coco['annotations']], [[2, 3, 20, 30]] * 2)
                else:
                    self.assertFalse((out / 'metadata.jsonl').exists())
                    self.assertTrue((out / ke.JOURNAL).exists())
