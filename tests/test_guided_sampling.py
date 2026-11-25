import math
import tempfile
import unittest
from pathlib import Path

from tools.ke_avf import plan_camera_field
from tools.ke_guided_sampling import (OccluderIndex, guided_field, candidate_fields,
    fields_block, prioritize_points, wrap)
from tools.ke_dagor_guided import install_guided, normalize_guided
from tools.ke_dagor_avf_camera import (EDITS as CAMERA_EDITS, FIELD_LOOP_OLD,
    install_avf_camera, normalize_capture)
from tools.ke_dagor_avf_batch import install_avf_batch, normalize_batch

CFG = {"distance_min":18., "distance_max":35., "camera_height_min":4., "camera_height_max":16.}
OBJECT = {"analysis_layer":"building", "duplicate_of":None,
          "bbox_min":[-3.,0.,10.], "bbox_max":[3.,9.,15.]}


class GuidedSamplingTests(unittest.TestCase):
    def setUp(self):
        self.index = OccluderIndex([OBJECT])
        self.base = plan_camera_field(42,1,32,(18,35),(10,25),(4,16))[0]

    def test_edge_directions_and_camera_range(self):
        edges = list(self.index.edges(0,0,35))
        self.assertEqual(len(edges),2)
        self.assertAlmostEqual(edges[0][0], -math.degrees(math.atan2(3,10)))
        self.assertAlmostEqual(edges[1][0], math.degrees(math.atan2(3,10)))
        field = guided_field(self.base,self.index,0,0,"moderate",CFG,1)
        self.assertEqual(field,guided_field(self.base,self.index,0,0,"moderate",CFG,1))
        self.assertEqual(len(field),len(self.base))
        self.assertNotEqual(field,self.base)
        for view in field:
            self.assertTrue(18 <= view["horizontal_distance"] <= 35)
            self.assertTrue(4 <= view["height_above_ground"] <= 16)
        for ring in {v["avf_ring"] for v in field}:
            views = [v for v in field if v["avf_ring"]==ring]
            self.assertEqual(len({v["height_above_ground"] for v in views}),1)
            self.assertEqual(len({v["horizontal_distance"] for v in views}),1)
        self.assertEqual([v for v in field if v["avf_ring"]==2],
                         [v for v in self.base if v["avf_ring"]==2])

    def test_open_and_empty_fallback(self):
        self.assertEqual(guided_field(self.base,self.index,0,0,"open",CFG,1),self.base)
        self.assertEqual(guided_field(self.base,OccluderIndex([]),0,0,"heavy",CFG,1),self.base)

    def test_selected_placement_has_own_camera_plan(self):
        points=[(.5,.5,"heavy"),(.95,.95,"open")]
        fields=candidate_fields(self.base,self.index,(-100,100,-100,100),points,"light",CFG,42)
        self.assertNotEqual(fields[0],self.base)
        self.assertEqual(fields[1],self.base)
        block=fields_block(fields)
        self.assertEqual(len(block.splitlines()),2*32*4)
        self.assertIn("guided_offset_1_31:p3=",block)

    def test_prioritization_keeps_all_points_and_open_order(self):
        points=[(.9,.9,"open"),(.85,.85,"open"),(.1,.1,"open"),(.5,.5,"heavy")]
        bounds=(-100,100,-100,100)
        ranked=prioritize_points(self.index,bounds,points,"heavy",CFG)
        self.assertEqual(ranked[0],points[-1])
        self.assertCountEqual(ranked,points)
        self.assertEqual(prioritize_points(self.index,bounds,points,"open",CFG),points)

    def test_installer_and_predecessor_normalization(self):
        original = "#include <cmath>\n" + "\n".join(old for old,_ in CAMERA_EDITS) + "\n" + (
            "  int cameraAttemptUsed = -1;\n"+FIELD_LOOP_OLD+"  if (cameraAttemptUsed < 0)\n"
            "    Point3 candidateEye = aim + config.getPoint3(offsetKey, legacy);\n"
            "    const float desiredHeight = config.getReal(heightKey, -1.f);\n")
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"capture.cpp"
            path.write_text(original,encoding="utf-8")
            self.assertTrue(install_avf_camera(path))
            v3=path.read_text(encoding="utf-8")
            self.assertTrue(install_avf_batch(path))
            v4=path.read_text(encoding="utf-8")
            self.assertTrue(install_guided(path))
            guided=path.read_text(encoding="utf-8")
            self.assertEqual(normalize_guided(guided),v4)
            self.assertEqual(normalize_batch(guided),v3)
            self.assertEqual(normalize_capture(guided),original)
            self.assertFalse(install_avf_camera(path))
            self.assertFalse(install_avf_batch(path))
            self.assertFalse(install_guided(path))
            self.assertEqual(path.read_text(encoding="utf-8"),guided)
            from tools.ke_guided_prerender import install_prerender, normalize_prerender
            self.assertTrue(install_prerender(path))
            filtered=path.read_text(encoding="utf-8")
            self.assertEqual(normalize_prerender(filtered),guided)
            self.assertEqual(normalize_guided(filtered),v4)
            self.assertEqual(normalize_capture(filtered),original)
            self.assertFalse(install_avf_camera(path))
            self.assertFalse(install_avf_batch(path))
            self.assertFalse(install_guided(path))
            self.assertFalse(install_prerender(path))
            self.assertEqual(path.read_text(encoding="utf-8"),filtered)
