import json
import tempfile
import unittest
from pathlib import Path

from tools.ke_smart_positions import build_pool, CandidateSampler, BINS, frame_block, verify_frame, digest, load_pool


class SmartPositionsTests(unittest.TestCase):
    def test_raw_bounds_exclusion_and_proximity(self):
        rows=[{"bbox_min":[56,0,56],"bbox_max":[64,8,64],"has_collision_resource":True,
               "analysis_layer":"building","duplicate_of":None}]
        bank=build_pool((0,128,0,128),rows)
        self.assertEqual(sum(bank["counts"].values())+bank["edge_excluded"]+bank["aabb_excluded"],bank["grid_points"])
        self.assertGreater(bank["counts"]["heavy"],0)
        self.assertGreater(bank["counts"]["open"],0)
        for points in bank["pools"].values():
            for x,z in points:
                self.assertFalse(50.5<=x<=69.5 and 50.5<=z<=69.5)
        # A duplicate does not change the result.
        self.assertEqual(bank,build_pool((0,128,0,128),rows+[dict(rows[0],duplicate_of=1)]))

    def test_deterministic_target_margin_and_fallback(self):
        bank=build_pool((0,128,0,128),[])
        a,b=CandidateSampler(bank,42,10),CandidateSampler(bank,42,10)
        points=a.choose("heavy",24)
        self.assertEqual(points,b.choose("heavy",24))
        self.assertEqual(len(set(p[:2] for p in points)),24)
        self.assertTrue(all(.1<=u<=.9 and .1<=v<=.9 for u,v,_ in points))
        self.assertTrue(all(kind=="open" for _,_,kind in points))
        empty=dict(bank,pools=dict.fromkeys(BINS,[]))
        self.assertTrue(all(p[2]=="uniform_fallback" for p in CandidateSampler(empty,3).choose("heavy",8)))

    def test_verify_candidate_metadata(self):
        points=[(.25,.5,"heavy")]
        block=frame_block(4,"heavy",points,.05)
        self.assertIn("candidate_4_0:p3=0.250000000,0.050000000,0.500000000",block)
        metadata=('placement_candidate:i=0\nsmart_placement_target:i=3\n'
                  'smart_placement_source:t="heavy"\nsmart_placement_check:t="ri_collision_bounds_static_rays_v1"\n'
                  'tank_world:p3=32,100,64\n')
        self.assertEqual(verify_frame(metadata,"heavy",points,(0,128,0,128))["attempt"],0)
        with self.assertRaises(ValueError):
            verify_frame(metadata.replace("32,100","34,100"),"heavy",points,(0,128,0,128))

    def test_cache_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/"avg_finland";folder.mkdir()
            source=folder/"objects-native.tsv";source.write_bytes(b"raw")
            bank=build_pool((0,128,0,128),[])
            bank.update(map_id="levels/avg_finland.bin",source_sha256=digest(source))
            (folder/"placement-candidates.json").write_text(json.dumps(bank))
            self.assertEqual(load_pool(tmp,bank["map_id"]),bank)
            source.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError,"Stale"):
                load_pool(tmp,bank["map_id"])
