import math
import tempfile
import unittest
from pathlib import Path

from tools.ke_guided_sampling import OccluderIndex
from tools.ke_fixed_camera import (fixed_camera_group, fixed_block,
    environment_visibility, read_fixed_metadata)
from tools.ke_dagor_fixed_camera import (EDITS, install_fixed_capture,
    normalize_fixed_capture)
from tools.ke_dagor_fixed_depth import (scene_method, add_fixed_renderer,
    normalize_fixed_renderer, HEADER_ANCHOR, SCENE_ANCHOR, SERVICE_ANCHOR)

CFG={"distance_min":18.,"distance_max":35.,"camera_height_min":4.,
     "camera_height_max":16.,"camera_clearance":1.,"map_margin_percent":10.}
BOUNDS=(-100.,100.,-100.,100.)
POINTS=[(.5,.5,"heavy"),(.7,.7,"open"),(.8,.8,"open"),
        (.3,.3,"open"),(.4,.4,"moderate"),(.6,.6,"light"),
        (.2,.2,"open"),(.75,.75,"open")]
OBJECT={"analysis_layer":"building","duplicate_of":None,
        "bbox_min":[-3.,0.,10.],"bbox_max":[3.,9.,15.]}


class FixedCameraTests(unittest.TestCase):
    def test_group_is_bounded_deterministic_and_preserves_fallback(self):
        index=OccluderIndex([OBJECT])
        points,group=fixed_camera_group(index,BOUNDS,POINTS,"light",CFG,42)
        self.assertIsNotNone(group)
        self.assertEqual((points,group),fixed_camera_group(index,BOUNDS,POINTS,"light",CFG,42))
        self.assertEqual(len(points),len(POINTS))
        n=group["count"]
        self.assertTrue(2<=n<=6)
        self.assertTrue(all(p[2]=="fixed_camera_local" for p in points[:n]))
        self.assertTrue(all(p in POINTS for p in points[n:]))
        self.assertTrue(points[n:])
        self.assertEqual(len(set(p[:2] for p in points)),len(points))
        for u,v,_ in points[:n]:
            x,z=-100+200*u,-100+200*v
            self.assertTrue(.1<=u<=.9 and .1<=v<=.9)
            self.assertTrue(18<=math.hypot(x-group["eye_xz"][0],z-group["eye_xz"][1])<=35)
        self.assertIn("fixed_count:i="+str(n),fixed_block(group))
        self.assertIn("fixed_eye:p3=",fixed_block(group))

    def test_open_or_missing_geometry_falls_back(self):
        self.assertEqual(fixed_camera_group(OccluderIndex([OBJECT]),BOUNDS,POINTS,"open",CFG,42),
                         (POINTS,None))
        self.assertEqual(fixed_camera_group(OccluderIndex([]),BOUNDS,POINTS,"heavy",CFG,42),
                         (POINTS,None))
        self.assertEqual(fixed_block(None),"")

    def test_environment_reverse_z(self):
        # Clear and farther environment are visible; closer environment occludes.
        self.assertEqual(environment_visibility([0.]*16,[.4]*16),1.)
        self.assertEqual(environment_visibility([.2]*16,[.4]*16),1.)
        self.assertEqual(environment_visibility([.8]*16,[.4]*16),0.)
        self.assertEqual(environment_visibility([.2]*8+[.8]*8,[.4]*16),.5)
        self.assertEqual(environment_visibility([.400001]*16,[.4]*16),1.)
        self.assertIsNone(environment_visibility([0.]*15,[.4]*15))
        self.assertIsNone(environment_visibility([float("nan")]*16,[.4]*16))
        with self.assertRaises(ValueError):
            environment_visibility([0.],[.4]*16)

    def test_native_telemetry_requires_actual_group_and_depth_pass(self):
        group={"count":4,"eye_xz":[1,2],"height":4,"aim_xz":[3,4]}
        blk=("fixed_group_count:i=4\n"
             "fixed_group_valid_placements:i=3\n"
             "fixed_group_valid_probes:i=2\n"
             "fixed_group_environment_passes:i=1\n"
             "fixed_group_estimate:r=.75\n")
        self.assertEqual(read_fixed_metadata(blk,group,2)["estimate"],.75)
        for old,new in (("valid_probes:i=2","valid_probes:i=4"),
                        ("environment_passes:i=1","environment_passes:i=0"),
                        ("estimate:r=.75","estimate:r=nan")):
            with self.assertRaises(ValueError):
                read_fixed_metadata(blk.replace(old,new),group,2)
        with self.assertRaises(ValueError):
            read_fixed_metadata(blk,group,4)
        with self.assertRaises(ValueError):
            read_fixed_metadata(blk,None,2)

    def test_native_pass_has_one_environment_draw_outside_pose_loop(self):
        method=scene_method()
        self.assertEqual(method.count("renderGeomOpaque();"),1)
        self.assertLess(method.index("renderGeomOpaque();"),method.index("for (int pose=0;"))
        self.assertIn("env<=model+tolerance",method)
        self.assertIn("ke_fixed_depth",method)
        self.assertNotIn("keProbeTarget->",method)
        for key,source in (("header",HEADER_ANCHOR),
                           ("render",SCENE_ANCHOR+"\n"+SERVICE_ANCHOR)):
            patched=add_fixed_renderer(source,key)
            self.assertEqual(add_fixed_renderer(patched,key),patched)
            self.assertEqual(normalize_fixed_renderer(patched,key),source)

    def test_capture_patch_idempotent_and_reversible(self):
        original="\n".join(old for old,_ in EDITS)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"capture.cpp"
            path.write_text(original,encoding="utf-8")
            self.assertTrue(install_fixed_capture(path))
            patched=path.read_text(encoding="utf-8")
            self.assertEqual(normalize_fixed_capture(patched),original)
            self.assertFalse(install_fixed_capture(path))
            self.assertEqual(path.read_text(encoding="utf-8"),patched)
