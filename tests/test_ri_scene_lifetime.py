import unittest
from tools.ke_ri_scene_lifetime import upgrade_scene, DESTRUCTOR_OLD, CLEAR_OLD


class SceneLifetimeTests(unittest.TestCase):
    def test_wait_before_terrain_and_self_release(self):
        original=DESTRUCTOR_OLD+"}\n"+CLEAR_OLD+"  del_it(lmeshMgr);\n  rendinst::clearRIGen();\n}\n"
        patched=upgrade_scene(original)
        self.assertEqual(upgrade_scene(patched),patched)
        self.assertLess(patched.index("clear();"),patched.index("self = NULL;"))
        self.assertLess(patched.index("while (!rendinst::isRIGenPrepareFinished())"),patched.index("del_it(lmeshMgr);"))
        self.assertEqual(patched.count("rendinst::clearRIGen();"),1)

    def test_partial_installation_fails(self):
        with self.assertRaises(ValueError):
            upgrade_scene(CLEAR_OLD+"// [KE] RI_SCENE_LIFETIME_V1\n")
