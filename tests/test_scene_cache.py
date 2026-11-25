import tempfile
import unittest
from pathlib import Path

from tools.ke_scene_cache import SceneCache, scope_key, propose, hint_from_frame

CFG={"distance_min":18.,"distance_max":35.,"camera_height_min":4.,
     "camera_height_max":16.,"camera_clearance":1.,"map_margin_percent":10.}
BOUNDS=(-100.,100.,-100.,100.)
POINTS=[(.2+i*.05,.7,"open") for i in range(8)]
GROUP={"count":4,"eye_xz":[0.,-25.],"aim_xz":[0.,0.],"height":5.}
FIXED={"estimate":.2,"camera":GROUP,"selected_index":0,
       "final_view":{"selected":True,"actual_eye":[0.,5.,-25.]}}
RECORD={"smart_placement":{"position_xz":[0.,0.]},
        "visibility_measurement":{"visibility_bin":"heavy"}}


class SceneCacheTests(unittest.TestCase):
    def test_scope_changes_on_map_assets_and_geometry_settings(self):
        def scope(cfg=CFG,map_digest="map",asset="grp",objects="objects"):
            return scope_key(cfg,"m",{"source_sha256":"bank"},"model","skeleton",map_digest,asset,objects)
        base=scope()
        self.assertEqual(base,scope(dict(CFG,seed=123,output="elsewhere")))
        for other in (scope(dict(CFG,distance_min=19)),scope(map_digest="changed"),
                      scope(asset="changed"),scope(objects="changed")):
            self.assertNotEqual(base,other)

    def test_learn_only_confirmed_fixed_matching_frames(self):
        self.assertIsNotNone(hint_from_frame(RECORD,FIXED,"heavy"))
        self.assertIsNone(hint_from_frame(RECORD,FIXED,"light"))
        self.assertIsNone(hint_from_frame(RECORD,dict(FIXED,estimate=None),"heavy"))
        self.assertIsNone(hint_from_frame(RECORD,dict(FIXED,final_view={"selected":False}),"heavy"))
        self.assertIsNone(hint_from_frame(RECORD,dict(FIXED,camera=dict(GROUP,origin="scene_cache")),"heavy"))

    def test_proposals_are_new_bounded_and_preserve_fallback(self):
        hint=hint_from_frame(RECORD,FIXED,"heavy")
        bank,group=propose(hint,POINTS,BOUNDS,CFG)
        self.assertEqual(len(bank),len(POINTS))
        self.assertGreaterEqual(group["count"],2)
        for u,v,source in bank[:group["count"]]:
            self.assertNotEqual((u,v),(.5,.5))
            self.assertEqual(source,"scene_cache_local")
            self.assertTrue(.1<=u<=.9 and .1<=v<=.9)
        self.assertTrue(all(p in POINTS for p in bank[group["count"]:]))
        self.assertEqual(propose(dict(hint,height=30),POINTS,BOUNDS,CFG),(POINTS,None))

    def test_transactional_persistence_and_one_sector_per_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"hints.sqlite3"
            a=SceneCache(path,{"m":"scope"})
            a.learn("m",RECORD,FIXED,"heavy")
            a.learn("m",RECORD,FIXED,"heavy")
            self.assertEqual(SceneCache(path,{"m":"scope"}).stats["loaded"],0)
            a.commit()
            self.assertEqual(a.stats["inserted_hints"],1)
            b=SceneCache(path,{"m":"scope","other":"different"})
            self.assertEqual(b.stats["loaded"],1)
            self.assertEqual(b.take("other","heavy",POINTS,BOUNDS,CFG),(POINTS,None,None))
            self.assertEqual(b.take("m","open",POINTS,BOUNDS,CFG),(POINTS,None,None))
            bank,group,key=b.take("m","heavy",POINTS,BOUNDS,CFG)
            self.assertIsNotNone(key)
            self.assertEqual(b.take("m","heavy",POINTS,BOUNDS,CFG),(POINTS,None,None))
            b.observe(key)
            b.commit()
            c=SceneCache(path,{"m":"scope"})
            with c.connect() as db:
                self.assertEqual(db.execute("SELECT hits,misses,unmeasured,uses FROM hints_v1").fetchone(),(1,0,1,1))
            _,group,key=c.take("m","heavy",POINTS,BOUNDS,CFG)
            c.observe(key,RECORD,dict(FIXED,camera=group))
            c.commit()
            with c.connect() as db:
                self.assertEqual(db.execute("SELECT hits,misses,unmeasured,uses FROM hints_v1").fetchone(),(2,0,1,2))

    def test_failed_replay_reduces_score_not_quota(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"hints.sqlite3"
            a=SceneCache(path,{"m":"scope"})
            a.learn("m",RECORD,FIXED,"heavy")
            a.commit()
            b=SceneCache(path,{"m":"scope"})
            _,group,key=b.take("m","heavy",POINTS,BOUNDS,CFG)
            b.observe(key,{"visibility_measurement":{"visibility_bin":"open"}},None)
            self.assertEqual(b.stats["misses"],1)
            b.commit()
            with b.connect() as db:
                self.assertEqual(db.execute("SELECT hits,misses,unmeasured,uses FROM hints_v1").fetchone(),(1,1,0,1))
