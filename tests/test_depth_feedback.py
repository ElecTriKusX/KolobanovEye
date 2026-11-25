import unittest
from tools.ke_depth_feedback import DepthFeedback, read_world_edges, unproject_plane
from tools.ke_dagor_depth_proposals import renderer_successors, apply_successors, reverse_successors

BOUNDS=(-100.,100.,-100.,100.)
CFG={"distance_min":18.,"distance_max":35.,"map_margin_percent":10.}
GROUP={"count":4,"eye_xz":[0.,-25.],"aim_xz":[0.,0.],"height":5.}
POINTS=[(.2+i*.05,.7,"open") for i in range(8)]


class DepthFeedbackTests(unittest.TestCase):
    def test_symmetric_camera_projection(self):
        basis=((0,0,0),(1,0,0),(0,1,0),(0,0,1))
        self.assertEqual(unproject_plane(100,50,200,100,2,4,20,*basis),(0,0,20))
        self.assertEqual(unproject_plane(150,25,200,100,2,4,20,*basis),(5,2.5,20))

    def test_metadata_validation(self):
        blk="fixed_world_edge_count:i=1\nfixed_world_edge_0:p3=1,2,3\n"
        self.assertEqual(read_world_edges(blk,True),[(1.,2.,3.)])
        for invalid in (blk.replace("1,2,3","nan,2,3"),blk.replace("count:i=1","count:i=2")):
            with self.assertRaises(ValueError):
                read_world_edges(invalid,True)
        with self.assertRaises(ValueError):
            read_world_edges(blk,False)

    def test_local_feedback_is_one_shot_map_target_scoped(self):
        cache=DepthFeedback()
        edges=[(2,1,0),(2.1,2,0),(90,0,90)]
        self.assertEqual(cache.offer("m","heavy",GROUP,edges,BOUNDS,CFG),1)
        self.assertEqual(cache.offer("m","heavy",GROUP,edges,BOUNDS,CFG),0)
        for mid,target in (("other","heavy"),("m","open"),("m","light")):
            self.assertEqual(cache.take(mid,target,POINTS,BOUNDS,CFG),(POINTS,None))
        bank,group=cache.take("m","heavy",POINTS,BOUNDS,CFG)
        self.assertEqual(len(bank),len(POINTS))
        self.assertEqual(group["origin"],"depth_feedback")
        self.assertEqual(group["eye_xz"],GROUP["eye_xz"])
        self.assertEqual(group["proposed_anchor_xz"],[2,0])
        self.assertTrue(all(p[2]=="fixed_depth_edge" for p in bank[:group["count"]]))
        self.assertTrue(all(p in POINTS for p in bank[group["count"]:]))
        self.assertEqual(cache.take("m","heavy",POINTS,BOUNDS,CFG),(POINTS,None))
        self.assertEqual(cache.offer("m","heavy",group,[(4,0,0)],BOUNDS,CFG),0)

    def test_world_projection_native_migration(self):
        for key in ("header","render"):
            edits=renderer_successors(key)
            old="\n".join(a for a,_ in edits)
            new=apply_successors(old,edits)
            self.assertNotEqual(old,new)
            self.assertEqual(apply_successors(new,edits),new)
            self.assertEqual(reverse_successors(new,edits),old)
            if key=="render":
                self.assertIn("camera*local",new)
                self.assertIn("world_count<6",new)
                self.assertIn("min(a,b)>z-.5f",new)
