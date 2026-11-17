"""Offline regression tests for the adaptive capture protocol and patch upgrade."""

import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import kolobanoveye as ke
from ke_coco import export
from ke_generate import command, validate_config
from ke_review import write_rgb_png
from ke_validate import validate


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

    def test_warmup_pipeline_on_predecessor_and_second_run(self):
        previous = ke.OFFSCREEN_STREAMING_WAIT_PATCH.read_bytes()
        hunks = re.findall(rb'(?ms)^@@ [^\n]*\n(.*?)(?=^@@ |\Z)', previous)
        fragments = [b''.join(line[1:] for line in hunk.splitlines(keepends=True)
                              if line.startswith((b' ', b'+'))) for hunk in hunks]
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'kolobanovEye.cpp'
            self.assertFalse(ke.is_upgraded_offscreen_capture(source))
            source.write_bytes(b'\n'.join(fragments).replace(b'\n', b'\r\n'))
            ke.apply_haze_capture_pipeline(source, ke.HAZE_CONVERGENCE_PATCH, ke.HAZE_STABILITY_PATCH)
            upgraded = source.read_bytes()
            self.assertIn(b'FRAME_STABILITY_FINAL', upgraded)
            self.assertNotIn(b'\n', upgraded.replace(b'\r\n', b''))
            ke.apply_haze_capture_pipeline(source, ke.HAZE_CONVERGENCE_PATCH, ke.HAZE_STABILITY_PATCH)
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
                    markers = ['MAP_OK', 'MODEL_OK', 'PLACEMENT_OK', 'WATER_PROXY', 'CAMERA_TERRAIN',
                               'SUPPORT_HEIGHTS', 'CAMERA_OK', 'WATER_GROUND_FILTER', 'MAP_BOUNDS']
                    lines = [f'[KE] {marker} ' for marker in markers]
                    payload = (b'width:i=64\nheight:i=64\nclass:t="tank"\nlevel:t="avg_normandy.bin"\n'
                               b'camera_attempt:i=0\ncomposition_index:i=0\ntank_world:p3=1,2,3\n'
                               b'eye:p3=4,5,6\nbbox_amodal_aabb_xywh:p4=2,3,20,30\n')
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
                                'count': 2, 'width': 64, 'height': 64, 'min_warmup_renders': 24})
                with patch.object(ke, 'os', SimpleNamespace(name='nt', SEEK_END=ke.os.SEEK_END)), patch.object(
                    ke, 'inspect', return_value={'errors': []}
                ), patch.object(ke, 'choose_resources', return_value=('tank', 'skeleton')), patch.object(
                    ke, 'run_logged', side_effect=pretend_editor
                ), patch.object(ke.sys, 'argv', argv[2:]):
                    self.assertEqual(ke.main(), 0 if stable else 1)
                if stable:
                    self.assertEqual(validate(out)['frames'], 2)
                    export([out], root / 'coco')
                    coco = json.loads((root / 'coco/all.json').read_text())
                    self.assertEqual(len(coco['images']), 2)
                    self.assertEqual([ann['bbox'] for ann in coco['annotations']], [[2, 3, 20, 30]] * 2)
                else:
                    self.assertFalse((out / 'metadata.jsonl').exists())
                    self.assertTrue((out / ke.JOURNAL).exists())
