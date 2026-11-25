import unittest
from ke_generate import validate_dataset_config
from tools.ke_pipeline import PIPELINE, SCENE_DEFAULTS, migrated_keys
from ke_gui import FIELDS

class DatasetPipelineTests(unittest.TestCase):
    def test_mandatory_pipeline_migrates_obsolete_options(self):
        raw={"smart_placement_cache":"cache","placement_mode":"around_point",
             "frames_per_position":6,"capture_visibility_masks":False,
             "enforce_visibility_quotas":False,"search_occlusion":False,
             "avf_probe_size":0,"guided_sampling":"off","guided_prerender":"conservative",
             "render_debug":True,"keep_open":True}
        c=validate_dataset_config(raw)
        for k,v in PIPELINE.items():
            self.assertEqual(c[k],v)
        self.assertEqual(c["avf_backend"],"gpu")
        self.assertIn("guided_prerender",migrated_keys(raw,c))
        self.assertEqual(validate_dataset_config(c),c)

    def test_user_scene_parameters_and_weights_are_preserved(self):
        c=validate_dataset_config({"smart_placement_cache":"cache","distance_max":50.,
            "count":600,"seed":7,"scene_cache":"hints",
            "visibility_weights":{"open":40,"light":25,"moderate":25,"heavy":10}})
        self.assertEqual(c["distance_max"],50.)
        self.assertEqual(c["count"],600)
        self.assertEqual(c["seed"],7)
        self.assertEqual(c["scene_cache"],"hints")
        self.assertEqual(c["camera_attempts"],SCENE_DEFAULTS["camera_attempts"])

    def test_no_fallback_without_object_cache(self):
        for raw in ({},{"smart_placement_cache":"  "},{"smart_placement_cache":False}):
            with self.assertRaises(ValueError):
                validate_dataset_config(raw)

    def test_only_same_texture_verification_remains_as_backend_diagnostic(self):
        for backend,expected in (("gpu","gpu"),("cpu","verify"),("verify","verify")):
            self.assertEqual(validate_dataset_config({"smart_placement_cache":"cache","avf_backend":backend})["avf_backend"],expected)
        with self.assertRaises(ValueError):
            validate_dataset_config({"smart_placement_cache":"cache","avf_backend":"unknown"})

    def test_gui_does_not_expose_pipeline_switches(self):
        fields={key for key,_ in FIELDS}
        self.assertFalse(fields & set(PIPELINE))
        self.assertIn("smart_placement_cache",fields)
        self.assertIn("scene_cache",fields)
        self.assertIn("count",fields)
