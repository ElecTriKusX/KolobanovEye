import tempfile
import unittest
from pathlib import Path

from tools.ke_fixed_camera import depth_edge_hits, read_fixed_final
from tools.ke_dagor_fixed_depth import (
    edge_scene_method, renderer_edits, add_fixed_renderer,
    normalize_fixed_renderer, HEADER_ANCHOR, SCENE_ANCHOR, SERVICE_ANCHOR)
from tools.ke_dagor_fixed_camera import SETUP, PRESELECT, META
from tools.ke_dagor_fixed_final import (
    EDITS, EDGE_MARKER, install_fixed_final, normalize_fixed_final)


class FixedDepthEdgesTests(unittest.TestCase):
    def test_only_visible_hidden_transitions_with_real_depth_jump(self):
        # Each row contains two hidden pixels and two visible pixels.
        scene=[.02,.02,.002,.002]*4
        model=[.01]*16
        self.assertEqual(depth_edge_hits(scene,model,4,4),4)
        self.assertEqual(depth_edge_hits(scene,[.03]*16,4,4),0)
        self.assertEqual(depth_edge_hits(scene,[0.,.01,0.,.01]*4,4,4),0)
        self.assertEqual(depth_edge_hits([.01]*16,model,4,4),0)
        self.assertEqual(depth_edge_hits([.02,.02,0.,0.]*4,model,4,4),4)

    def test_no_invalid_depth_or_smooth_subthreshold_jump(self):
        self.assertEqual(depth_edge_hits([float("nan"),.002],[.01,.01],2,1),0)
        # Model lies between two almost equal metric depths.
        self.assertEqual(depth_edge_hits([.01001,.00999],[.01,.01],2,1),0)
        with self.assertRaises(ValueError):
            depth_edge_hits([.01],[.01],2,2)

    def test_final_camera_must_equal_the_measured_view(self):
        fixed={"estimate":.48,"camera":{"eye_xz":[10.,20.]}}
        blk=("fixed_camera_selected:b=yes\n"
             "camera_attempt:i=0\n"
             "fixed_group_edge_hits:i=12\n"
             "fixed_group_eye:p3=10,5,20\n"
             "fixed_group_look_at:p3=0,2,0\n"
             "eye:p3=10,5,20\n"
             "look_at:p3=0,2,0\n"
             "tank_world:p3=0,0,0\n")
        result=read_fixed_final(blk,fixed)
        self.assertTrue(result["selected"])
        self.assertEqual(result["depth_edge_hits"],12)
        self.assertAlmostEqual(result["actual_horizontal_distance"],500**.5)
        with self.assertRaises(ValueError):
            read_fixed_final(blk.replace("look_at:p3=0,2,0\n"+"tank_world","look_at:p3=0,3,0\n"+"tank_world"),fixed)
        with self.assertRaises(ValueError):
            read_fixed_final(blk.replace("camera_attempt:i=0","camera_attempt:i=1"),fixed)
        with self.assertRaises(ValueError):
            read_fixed_final(blk.replace("edge_hits:i=12","edge_hits:i=-1"),fixed)

    def test_renderer_upgrade_from_v1_preserves_original_anchors(self):
        for key,base in (("header",HEADER_ANCHOR),
                         ("render",SCENE_ANCHOR+"\n"+SERVICE_ANCHOR)):
            v1=base
            for old,new in renderer_edits(key):
                self.assertEqual(v1.count(old),1)
                v1=v1.replace(old,new,1)
            v2=add_fixed_renderer(v1,key)
            self.assertNotEqual(v2,v1)
            self.assertEqual(add_fixed_renderer(v2,key),v2)
            self.assertEqual(normalize_fixed_renderer(v1,key),base)
            self.assertEqual(normalize_fixed_renderer(v2,key),base)
        method=edge_scene_method()
        self.assertEqual(method.count("renderGeomOpaque();"),1)
        self.assertLess(method.index("FIXED_DEPTH_EDGES width="),method.index("for (int pose=0;"))
        self.assertIn("unobstructed(i)!=unobstructed(j)",method)

    def test_final_capture_v2_to_v3_migration(self):
        original=SETUP+PRESELECT+META
        # Exact predecessor anchors that belong to the ordinary camera loop.
        for old,_ in EDITS:
            if old not in original:
                original+="\n"+old
        v2=original
        for old,new in EDITS:
            self.assertEqual(v2.count(old),1)
            v2=v2.replace(old,new,1)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"capture.cpp"
            path.write_text(v2,encoding="utf-8")
            self.assertTrue(install_fixed_final(path))
            v3=path.read_text(encoding="utf-8")
            self.assertIn(EDGE_MARKER,v3)
            self.assertEqual(normalize_fixed_final(v3),original)
            self.assertFalse(install_fixed_final(path))
            self.assertEqual(path.read_text(encoding="utf-8"),v3)
            path.write_text(original,encoding="utf-8")
            self.assertTrue(install_fixed_final(path))
            self.assertEqual(path.read_text(encoding="utf-8"),v3)
