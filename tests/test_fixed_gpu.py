import unittest
from pathlib import Path

from tools.ke_dagor_fixed_gpu import renderer_edits, CAPTURE_EDITS
from tools.ke_dagor_fixed_depth import add_fixed_renderer, normalize_fixed_renderer, HEADER_ANCHOR, SCENE_ANCHOR, SERVICE_ANCHOR
from tools.ke_dagor_depth_proposals import apply_successors, reverse_successors
from tools.ke_fixed_camera import read_fixed_gpu_metadata


class FixedGpuTests(unittest.TestCase):
    def test_full_renderer_chain_and_v5_upgrade(self):
        for key,base in (("header",HEADER_ANCHOR),
                         ("render",SCENE_ANCHOR+"\n"+SERVICE_ANCHOR)):
            latest=add_fixed_renderer(base,key)
            edits=renderer_edits(key)
            v5=reverse_successors(latest,edits)
            self.assertNotEqual(latest,v5)
            self.assertEqual(add_fixed_renderer(v5,key),latest)
            self.assertEqual(add_fixed_renderer(latest,key),latest)
            self.assertEqual(normalize_fixed_renderer(latest,key),base)
            self.assertEqual(normalize_fixed_renderer(v5,key),base)

    def test_environment_once_and_gpu_group_before_readback(self):
        method=renderer_edits("render")[0][1]
        self.assertEqual(method.count("renderGeomOpaque();"),1)
        self.assertIn("clear(*fixedTarget);",method)
        self.assertIn("clear(*modelTarget);",method)
        self.assertIn("d3d::set_tex(STAGE_CS,0,sceneTex)",method)
        self.assertIn("BufPtr counts[6]",method)
        self.assertLess(method.index("d3d::dispatch(gx,gy,1)"),method.index("lock_sbuffer<const uint32_t>"))
        self.assertIn("if (backend!=1)",method)
        self.assertEqual(method.count("read(modelTex,cpuModels[pose])"),1)
        self.assertEqual(method.count("read(sceneTex,sceneDepth)"),1)
        self.assertIn("FIXED_GPU_VERIFY_FAIL",method)
        self.assertIn("result=ref;",method)

    def test_capture_successor_round_trip(self):
        old="\n".join(a for a,_ in CAPTURE_EDITS)
        new=apply_successors(old,CAPTURE_EDITS)
        self.assertEqual(apply_successors(new,CAPTURE_EDITS),new)
        self.assertEqual(reverse_successors(new,CAPTURE_EDITS),old)
        self.assertIn('return fail("FIXED_GPU_VERIFY"',new)

    def test_backend_and_evidence(self):
        def blk(backend,submitted,verified):
            return (f'fixed_group_backend:t="{backend}"\n'
                    f'fixed_group_gpu_submitted:i={submitted}\n'
                    f'fixed_group_gpu_verified:i={verified}\n')
        fixed={"valid_placements":6,"valid_probes":4,"environment_passes":1}
        for backend,s,v in (("cpu",0,0),("gpu",6,0),("verify",6,6)):
            self.assertEqual(read_fixed_gpu_metadata(blk(backend,s,v),fixed,backend)["gpu_verified"],v)
        for backend,s,v in (("cpu",1,0),("gpu",2,0),("gpu",6,1),("verify",6,5),("verify",7,7)):
            with self.assertRaises(ValueError):
                read_fixed_gpu_metadata(blk(backend,s,v),fixed,backend)
        with self.assertRaises(ValueError):
            read_fixed_gpu_metadata(blk("gpu",6,0),fixed,"verify")
        with self.assertRaises(ValueError):
            read_fixed_gpu_metadata(blk("gpu",6,0),dict(fixed,environment_passes=0),"gpu")

    def test_shader_uses_environment_only_reverse_z_and_group_witness(self):
        shader=(Path(__file__).resolve().parents[1]/"engine/shaders/ke_fixed_reduce.hlsl").read_text()
        self.assertIn("d >= 0.0",shader)
        self.assertIn("e <= m + max",shader)
        self.assertNotIn("abs(model - scene)",shader)
        self.assertIn("InterlockedAdd(counts[11],1)",shader)
        self.assertIn("counts[12+group.y",shader)
        self.assertIn("opening(p,uint2(1,0),w)",shader)
