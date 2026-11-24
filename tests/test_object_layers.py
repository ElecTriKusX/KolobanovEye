import unittest
from tools.ke_object_layers import build_index, classify


def obj(i,name,p=(8,0,8),size=(4,4,4)):
    return {"id":i,"resource":name,"position":list(p),"bbox_min":[p[0]-size[0]/2,p[1],p[2]-size[2]/2],
            "bbox_max":[p[0]+size[0]/2,p[1]+size[1],p[2]+size[2]/2],
            "source":"ri_tm","layer":0,"pool":1,"has_collision_resource":True}


class ObjectLayerTests(unittest.TestCase):
    def test_names_and_dimensions(self):
        for name,size,expected in [
            ("rails_sleeper_concrete_a",(3,.3,1),"detail"),
            ("pavement_border_a_secondlayer",(2,.6,2),"detail"),
            ("ground_plant_beech_a",(2,1,2),"detail"),
            ("finland_stone_small_a",(5,4,5),"rock"),
            ("af_cc_wall_ext_side_ab",(8,5,2),"building"),
            ("af_factory_small_building_i",(8,5,8),"building"),
            ("tree_fir_snow_bush_a",(3,3,3),"vegetation"),
            ("abandoned_ropeway_rope",(800,38,458),"linear"),
            ("finland_clsn_cliff_on_a",(95,64,93),"collision_only"),
            ("icefloe_a",(45,3,44),"water_surface"),
        ]:
            self.assertEqual(classify(obj(1,name,size=size)),expected,name)

    def test_duplicates_and_decor_do_not_inflate_density(self):
        house=obj(1,"house_a")
        base=build_index((0,128,0,128),[house])
        rows=[house,obj(2,"house_a"),obj(3,"rails_sleeper_concrete_a",size=(100,.3,1))]
        grid=build_index((0,128,0,128),rows)
        self.assertEqual(grid["duplicate_records"],1)
        self.assertEqual(rows[1]["duplicate_of"],1)
        self.assertEqual(grid["eligible_unique"],1)
        cell=next(c for c in grid["cells"] if c["x"]==c["z"]==0)
        self.assertEqual(cell["density"],base["cells"][0]["density"])
        self.assertEqual(cell["center_ids"],[1,2,3])
        self.assertIn(3,cell["bbox_ids"])

    def test_semantic_and_sector_boundaries(self):
        rows=[obj(i+1,"tree_a",p=(8+16*i,0,8)) for i in range(16)]
        rows.append(obj(17,"house_a",p=(8,0,8)))
        grid=build_index((0,256,0,256),rows)
        self.assertEqual(len(grid["groups"]),3)
        self.assertEqual(sorted(len(g["object_ids"]) for g in grid["groups"]),[1,8,8])
        self.assertEqual(sum(len(g["object_ids"]) for g in grid["groups"]),17)

    def test_large_bbox_cannot_bridge_groups_or_fill_map(self):
        rows=[obj(1,"abandoned_ropeway_rope",p=(64,0,64),size=(120,10,120)),
              obj(2,"house_a",p=(8,0,8)),obj(3,"house_a",p=(120,0,120))]
        grid=build_index((0,128,0,128),rows)
        self.assertEqual(len(grid["groups"]),2)
        self.assertTrue(any(c["density"]==0 and 1 in c["bbox_ids"] for c in grid["cells"]))
