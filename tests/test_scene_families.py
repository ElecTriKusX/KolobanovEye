import tempfile
import unittest
from pathlib import Path

from tools.ke_avf import plan_camera_field
from tools.ke_guided_sampling import OccluderIndex
from tools.ke_scene_families import (
    SceneIndexes, family_for, reserve_aperture, profile, prepare_family, family_fields,
    count_family, aperture_scanlines, read_aperture_hits)
from tools.ke_dagor_terrain_family import (
    EDITS, install_terrain_family, normalize_terrain_family)
from tools.ke_dagor_aperture import renderer_edits, CAPTURE_EDITS
from tools.ke_dagor_depth_proposals import apply_successors, reverse_successors

CFG={"distance_min":18.,"distance_max":35.,"camera_height_min":4.,
     "camera_height_max":16.,"camera_clearance":1.,"map_margin_percent":10.}
BOUNDS=(-100.,100.,-100.,100.)
POINTS=[(.5,.5,"heavy"),(.7,.7,"open"),(.8,.8,"open"),
        (.3,.3,"open"),(.4,.4,"moderate"),(.6,.6,"light"),
        (.2,.2,"open"),(.75,.75,"open")]
BUILDING={"analysis_layer":"building","duplicate_of":None,"resource":"ruin_hangar",
          "bbox_min":[-3.,0.,10.],"bbox_max":[3.,9.,15.]}
VEGETATION={"analysis_layer":"vegetation","duplicate_of":None,"resource":"bush",
            "bbox_min":[9.,0.,-3.],"bbox_max":[12.,5.,3.]}


class SceneFamilyTests(unittest.TestCase):
    def test_round_robin_non_open_and_open_exploration(self):
        self.assertEqual([family_for("heavy",i) for i in range(1,6)],
                         ["building","vegetation","aperture","terrain","explore"])
        self.assertEqual(family_for("light",6),"building")
        self.assertEqual(family_for("open",0),"explore")
        with self.assertRaises(ValueError):
            family_for("heavy",0)

    def test_reserve_direct_aperture_slots(self):
        attempts = [i for i in range(1, 61) if family_for('heavy',i)=='aperture']
        self.assertEqual([i for i in attempts if reserve_aperture('aperture',i)], [3,18,33,48])
        self.assertFalse(reserve_aperture('terrain',4))
        self.assertFalse(reserve_aperture('explore',0))

    def test_category_separation_and_missing_geometry_fallback(self):
        indexes=SceneIndexes([BUILDING,VEGETATION])
        self.assertTrue(indexes.indices["building"].cells)
        self.assertTrue(indexes.indices["vegetation"].cells)
        self.assertTrue(indexes.indices["aperture"].cells)
        general=OccluderIndex([BUILDING])
        family,pts,group,index,cfg=prepare_family(
            SceneIndexes([BUILDING]),general,BOUNDS,POINTS,"heavy",CFG,42,"vegetation")
        self.assertEqual(family,"fallback")
        self.assertIsNone(group)
        self.assertCountEqual(pts,POINTS)
        self.assertIs(index,general)
        self.assertIs(cfg,CFG)

    def test_terrain_bank_preserves_angular_search(self):
        base=plan_camera_field(42,1,32,(18,35),(10,25),(4,16))[0]
        terrain_cfg=profile(CFG,"terrain")
        result=family_fields(base,OccluderIndex([]),BOUNDS,POINTS,"heavy",
                             terrain_cfg,42,"terrain")
        self.assertEqual(len(result),len(POINTS))
        for old,new in zip(base,result[0]):
            self.assertEqual(old["avf_ring"],new["avf_ring"])
            self.assertEqual(old["azimuth_deg"],new["azimuth_deg"])
            self.assertEqual(old["horizontal_distance"],new["horizontal_distance"])
            self.assertTrue(4<=new["height_above_ground"]<=6.4)
        self.assertEqual(family_fields(base,OccluderIndex([]),BOUNDS,POINTS,"open",CFG,42,"explore"),
                         [base]*len(POINTS))

    def test_opening_requires_two_real_edges_and_continuous_model(self):
        env=[.02,.002,.002,.02]*4
        model=[.01]*16
        self.assertEqual(aperture_scanlines(env,model,4,4),4)
        self.assertEqual(aperture_scanlines([.02,.02,.002,.002]*4,model,4,4),0)
        self.assertEqual(aperture_scanlines(env,[.01,0.,.01,.01]*4,4,4),0)
        self.assertEqual(aperture_scanlines([.01001,.00999,.00999,.01001]*4,model,4,4),0)
        self.assertEqual(read_aperture_hits("fixed_group_aperture_hits:i=4\n",True),4)
        with self.assertRaises(ValueError):
            read_aperture_hits("fixed_group_aperture_hits:i=4\n",False)

    def test_family_stats_distinguish_attempt_and_measured_result(self):
        stats={}
        count_family(stats,"building","heavy")
        count_family(stats,"building","heavy","open",False)
        count_family(stats,"building","heavy")
        count_family(stats,"building","heavy","heavy",True)
        self.assertEqual(stats["building"],{"attempts":2,"validated":2,
            "target_matches":1,"quota_gain_attempts":1,"actual_bins":{"open":1,"heavy":1}})

    def test_terrain_patch_reversible(self):
        original="\n".join(old for old,_ in EDITS)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"capture.cpp"
            path.write_text(original,encoding="utf-8")
            self.assertTrue(install_terrain_family(path))
            text=path.read_text(encoding="utf-8")
            self.assertEqual(normalize_terrain_family(text),original)
            self.assertFalse(install_terrain_family(path))

    def test_aperture_successor_round_trip(self):
        for edits in (renderer_edits("header"),renderer_edits("render"),CAPTURE_EDITS):
            old="\n".join(a for a,_ in edits)
            new=apply_successors(old,edits)
            self.assertNotEqual(new,old)
            self.assertEqual(apply_successors(new,edits),new)
            self.assertEqual(reverse_successors(new,edits),old)
