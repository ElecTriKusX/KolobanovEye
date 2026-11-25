import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools.ke_guided_prerender import (allow_skip, read_prerender_rejection,
    install_prerender, normalize_prerender, ANCHOR, REPLACEMENT)


class GuidedPrerenderTests(unittest.TestCase):
    def test_only_saturated_cells_and_regular_audit(self):
        p = SimpleNamespace(attempts={"m":6}, targets={"open":4},
            map_targets={"m":10}, capacity={"m":{"open":4}},
            report={"useful_next_bins_by_map":{"m":["light"]}})
        self.assertTrue(allow_skip(p,"m","light"))
        self.assertFalse(allow_skip(p,"m","open"))
        p.attempts["m"]=10
        self.assertFalse(allow_skip(p,"m","heavy"))
        p.attempts["m"]=11
        p.capacity["m"]["open"]=3
        self.assertFalse(allow_skip(p,"m","light"))
        p.capacity["m"]["open"]=4
        p.report["useful_next_bins_by_map"]["m"]=["open","light"]
        self.assertFalse(allow_skip(p,"m","light"))

    def test_installation_is_reversible(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"capture.cpp"
            source.write_text(ANCHOR,encoding="utf-8")
            self.assertTrue(install_prerender(source))
            self.assertEqual(source.read_text(encoding="utf-8"),REPLACEMENT)
            self.assertFalse(install_prerender(source))
            self.assertEqual(normalize_prerender(REPLACEMENT),ANCHOR)

    def test_skip_requires_permission_evidence_and_no_files(self):
        blk = ('level:t="avg_test.bin"\n'
               'guided_rejection:t="redundant_open_probe"\n'
               'composition_index:i=0\n'
               'guided_placement_index:i=0\n'
               'guided_target:i=1\n'
               'guided_probe_attempts:i=32\n'
               'guided_probe_valid:i=32\n'
               'guided_min_fraction:r=0.985\n')
        cfg={"guided_prerender":"conservative","camera_attempts":32}
        with tempfile.TemporaryDirectory() as directory:
            raw=Path(directory)
            image=raw/"00000001.png"
            task={"map_id":"levels/avg_test.bin","guided_skip":{0:True},
                  "smart_points":{0:[(.5,.5,"heavy")]},
                  "occlusion_targets":["light"],"outputs":[image]}
            self.assertIsNone(read_prerender_rejection(cfg,raw,task,0,""))
            row=read_prerender_rejection(cfg,raw,task,0,blk)
            self.assertEqual(row["image_id"],1)
            self.assertEqual(row["probe_valid"],32)
            for old,new in (("guided_probe_valid:i=32","guided_probe_valid:i=31"),
                            ("guided_min_fraction:r=0.985","guided_min_fraction:r=0.9"),
                            ("guided_min_fraction:r=0.985","guided_min_fraction:r=nan"),
                            ("guided_target:i=1","guided_target:i=0"),
                            ("guided_placement_index:i=0","guided_placement_index:i=1")):
                with self.assertRaises(ValueError):
                    read_prerender_rejection(cfg,raw,task,0,blk.replace(old,new))
            task["guided_skip"][0]=False
            with self.assertRaises(ValueError):
                read_prerender_rejection(cfg,raw,task,0,blk)
            task["guided_skip"][0]=True
            image.touch()
            with self.assertRaises(ValueError):
                read_prerender_rejection(cfg,raw,task,0,blk)
