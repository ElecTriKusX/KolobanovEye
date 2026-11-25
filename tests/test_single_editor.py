"""Offline end-to-end simulation: one DaEditor invocation for several maps."""
import json
import tempfile
import unittest
from pathlib import Path

from ke_single_editor import jobs_for, extract_map_logs, make_command, read_editor_log, explain_batch_failure
from ke_dataset import run_dataset
from ke_generate import DEFAULTS
from tools.ke_analysis import analyze_dataset
from kolobanoveye import editor_failure


class SingleEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / "game"
        levels = self.game / "levels"
        levels.mkdir(parents=True)
        for name in ("avg_finland.bin", "avg_normandy.bin"):
            (levels / name).write_bytes(b"DBLD3x64" + b"\0"*8)
        self.dagor = self.root / "dagor"
        editor = self.dagor / "tools" / "dagor_cdk" / "windows-x86_64"
        editor.mkdir(parents=True)
        (editor / "daEditor3x-dev.exe").touch()
        self.dataset = self.root / "dataset"
        self.config = {**DEFAULTS, "game": str(self.game), "dagor": str(self.dagor),
            "output": str(self.dataset), "count": 3, "seed": 42,
            "width": 64, "height": 64, "map_margin_percent": 0.0,
            "tank_map_resolution": 256, "capture_tank_map": True,
            "placement_mode": "tank_map",
            "map_ids": ["levels/avg_normandy.bin", "levels/avg_finland.bin"],
            "timeout": 10, "visibility_weights":{"open":1,"light":0,"moderate":0,"heavy":0}}
        from tools.ke_smart_positions import build_pool, digest
        cache=self.root/"fixture-cache"
        for mid in self.config["map_ids"]:
            folder=cache/Path(mid).stem
            folder.mkdir(parents=True)
            source=folder/"objects-native.tsv"; source.write_bytes(b"fixture")
            bank=build_pool((0,128,0,128),[])
            bank.update(map_id=mid,source_sha256=digest(source))
            (folder/"placement-candidates.json").write_text(json.dumps(bank))
            (folder/"objects.jsonl").write_text("")
        self.config["smart_placement_cache"]=str(cache)

    def test_native_fastfail_reports_last_debug_and_pre_map_stage(self):
        out = self.root / "failure"
        debug = out / ".logs" / "daEditor3x-dev.exe-2026.11.19-02.59.15" / "debug"
        debug.parent.mkdir(parents=True)
        debug.write_text(
            "daEditorX has loaded and ready for interaction\n"
            "97.65 [D] CON: exec command 'wait.frame 8'\n"
            "97.85 [D]\n",
            encoding="utf-8")
        message = editor_failure(out, "daEditor3x-dev.exe exited with 3221226505")
        self.assertIn("0xC0000409", message)
        self.assertIn("before the first map", message)
        self.assertIn("Last command:", message)
        self.assertIn("wait.frame 8", message)
        self.assertNotIn("Last useful debug line: '97.85 [D]'", message)
        self.assertIn(str(debug), message)

    def test_online_queue_one_native_command(self):
        run=self.root/"many"; run.mkdir()
        command=make_command(self.config,run,run/"online")
        self.assertEqual(Path(command[0]).name,"daEditor3x-dev.exe")
        script=(run/"probe.dcmd").read_text(encoding="ascii")
        self.assertEqual(script.splitlines(),[
            f"kolobanoveye.batch {(run/'map-batch.blk').as_posix()}","exit no"])
        self.assertIn('online_dir:t=',(run/"map-batch.blk").read_text())
        self.assertNotIn("map_count:",(run/"map-batch.blk").read_text())

    def test_two_maps_one_editor_no_duplicate_png_or_coco(self):
        from tests.test_online_capture import FakeOnlineEditor
        processes=[]
        def execute(cmd):
            p=FakeOnlineEditor(cmd,lambda *args:"open")
            processes.append(p)
            return p
        result=run_dataset(self.config,self.dataset,executor=execute)
        self.assertEqual(len(processes),1)
        self.assertEqual(result["editor_processes"],1)
        self.assertEqual(result["images"],3)
        raw=self.dataset/"raw"
        coco=json.loads((raw/"annotations/instances.json").read_text())
        self.assertEqual(len(coco["images"]),3)
        self.assertEqual(len(coco["annotations"]),3)
        self.assertEqual({x["map_id"] for x in coco["images"]},set(self.config["map_ids"]))
        self.assertTrue(all((raw/x["file_name"]).is_file() for x in coco["images"]))
        self.assertTrue(all(x["bbox_kind"]=="visible_opaque" for x in coco["annotations"]))
        run,=list((self.dataset/"runs").glob("run-*"))
        self.assertFalse((run/"images").exists())
        self.assertFalse((run/"coco").exists())
        self.assertFalse(list(run.glob("**/journals/*.jsonl")))

    def test_visibility_failure_includes_native_reason(self):
        run = self.root / "failed-visibility"
        run.mkdir()
        debug = run / "debug"
        debug.write_text("[KE] VISIBILITY_FAIL reason=depth_pass_mismatch missing=90 amodal=200\n", encoding="utf-8")
        error = explain_batch_failure(run, "[KE] FAIL VISIBILITY: Same-camera mask capture failed", debug)
        self.assertIn("reason=depth_pass_mismatch", error)

    def test_replace_preserves_old_raw_when_batch_fails(self):
        from tests.test_online_capture import FakeOnlineEditor
        fake_editor=lambda cmd: FakeOnlineEditor(cmd,lambda *args:"open")
        run_dataset(self.config, self.dataset, executor=fake_editor)
        old_manifest = (self.dataset / "raw" / "manifest.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "--replace-raw"):
            run_dataset(self.config, self.dataset, executor=fake_editor)
        with self.assertRaises((OSError,ValueError)):
            run_dataset(self.config, self.dataset, replace_raw=True,
                        executor=lambda cmd: FakeOnlineEditor(cmd,lambda *args:"open",corrupt=True))
        self.assertEqual((self.dataset / "raw" / "manifest.json").read_bytes(), old_manifest)
        self.assertTrue(any(self.dataset.glob(".raw-staging-*")))
        result = run_dataset({**self.config, "count": 1}, self.dataset,
                             replace_raw=True, executor=fake_editor)
        self.assertEqual(result["images"], 1)
        self.assertEqual(len(list((self.dataset / "raw" / "images").glob("*.png"))), 1)

    def test_scenes_and_metadata_no_splits(self):
        from tests.test_online_capture import FakeOnlineEditor
        run_dataset(self.config,self.dataset,executor=lambda cmd: FakeOnlineEditor(cmd,lambda *args:"open"))
        raw = self.dataset / "raw"
        rows = [json.loads(l) for l in (raw / "metadata" / "images.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(len(r["sha256"]) == 64 for r in rows))
        self.assertTrue((raw / "metadata" / "scenes.jsonl").is_file())
        self.assertTrue((raw / "metadata" / "maps.json").is_file())
        self.assertNotIn("game", json.loads((raw / "metadata" / "generation_config.json").read_text()))
        self.assertFalse((raw / "train").exists())

    def test_analysis_is_generated_only_on_request(self):
        from tests.test_online_capture import FakeOnlineEditor
        run_dataset(self.config,self.dataset,executor=lambda cmd: FakeOnlineEditor(cmd,lambda *args:"open"))
        self.assertFalse((self.dataset / "analysis").exists())
        report = analyze_dataset(self.dataset)
        self.assertEqual(len(report["maps"]), 2)
        self.assertEqual(sum(m["images"] for m in report["maps"]), 3)
        self.assertTrue((self.dataset / "analysis" / "index.html").is_file())
        pages = sorted((self.dataset / "analysis" / "maps").glob("*.html"))
        self.assertEqual(len(pages), 2)
        for page in pages:
            html = page.read_text(encoding="utf-8")
            self.assertIn("const points=", html)
            self.assertIn("tank.png", html)
        self.assertFalse((self.dataset / "analysis" / "images").exists())

    def test_async_debug_log_is_source_of_truth_without_dcmd_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            debug = run / ".logs" / "daEditor3x-dev.exe-2026.11.19-06.04.53" / "debug"
            debug.parent.mkdir(parents=True)
            debug.write_text(
                '  7.73 [D] CON: [KE] MAP_BATCH_BEGIN maps=1\n'
                '  7.83 [D] CON: [KE] MAP_TASK_BEGIN index=1/1 capture=test.blk\n'
                '  7.83 [D] CON: [KE] MAP_BEGIN D:/game/levels/avg_normandy.bin\n'
                '  7.83 [D] [KE] MAP_BEGIN D:/game/levels/avg_normandy.bin\n'
                '  7.93 [D] CON: [KE] MAP_OK terrain_y=183.75\n'
                '  7.93 [D] CON: [KE] MODEL_OK tank=pzkpfw_VI_ausf_h1_tiger\n'
                '  7.93 [D] CON: [KE] PLACEMENT_OK x=0 z=0\n'
                '  7.93 [D] CON: [KE] WATER_GROUND_FILTER enabled=1\n'
                '  7.94 [D] CON: [KE] FRAME_OK index=0\n'
                '  7.94 [D] CON: [KE] FRAME_STABILITY_FINAL enabled=1 stable=1\n'
                '  8.00 [D] CON: [KE] SEQUENCE_OK frames=1\n'
                '  8.00 [D] CON: [KE] MAP_TASK_OK index=1/1\n'
                '  8.00 [D] CON: [KE] MAP_BATCH_OK maps=1\n',
                encoding="utf-8")
            log, source = read_editor_log(run)
            self.assertEqual(source, debug)
            self.assertEqual(log.count("[KE] MAP_BEGIN "), 1)
            self.assertIn("[KE] MAP_BATCH_OK maps=1", log)
            self.assertEqual(len(extract_map_logs(
                log, [{"map_id": "levels/avg_normandy.bin", "count": 1}],
                stability=True)), 1)

    def test_async_log_fails_with_actionable_ooz_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            dirname = run / ".logs" / "daEditor3x-dev.exe-2026.11.19-06.04.53"
            dirname.mkdir(parents=True)
            (dirname / "debug").write_text(
                "  7.73 [D] CON: [KE] MAP_BATCH_BEGIN maps=1\n"
                "  7.83 [D] CON: [KE] MAP_BEGIN D:/game/levels/avg_abandoned_factory.bin\n"
                "  8.90 [E] CON: [KE] FAIL MAP: Binary dump did not produce a terrain renderer\n"
                "  8.90 [E] CON: [KE] FAIL MAP_TASK index=1/1\n", encoding="utf-8")
            (dirname / "logerr").write_text(
                "  7.93 [E] ooz: decode failed in 'D:/game/levels/avg_abandoned_factory.bin' "
                "(stream type=12, packed=3630624, expected=13009598, result=-1)\n",
                encoding="utf-8")
            failure = run / "ooz-failures"
            failure.mkdir()
            (failure / "failed-ooz.bin").write_bytes(b"\\x8c\\x0c")
            (run / "host-stdout.log").write_text(
                "[KE] OOZ_STEP_FAIL decoded_offset=131072 packed_remaining=44512\\n",
                encoding="utf-8")
            log, source = read_editor_log(run)
            error = explain_batch_failure(run, log, source)
            self.assertIn("OOZ_INPUT_CAPTURED", error)
            self.assertIn("OOZ_STEP_FAIL decoded_offset=131072", error)
            self.assertIn("FAIL MAP", error)
            self.assertIn("avg_abandoned_factory.bin", error)
            self.assertIn("stream type=12", error)
            self.assertNotIn("File missing", error)
            with self.assertRaisesRegex(ValueError, "ooz: decode failed"):
                from ke_single_editor import collect_candidates
                collect_candidates(self.config, run, run / "raw", [{"map_id": "levels/avg_abandoned_factory.bin"}])

    def test_batch_detects_incomplete_map(self):
        tasks = [{"map_id": "levels/avg_a.bin", "count": 2}]
        with self.assertRaisesRegex(ValueError, "SEQUENCE_OK"):
            extract_map_logs("[KE] MAP_BEGIN D:/game/levels/avg_a.bin\n[KE] MAP_OK\n", tasks)

    def test_no_whitelist_fails(self):
        with self.assertRaises(ValueError):
            jobs_for({**self.config, "map_ids": ["levels/avg_missing.bin"]}, [])


if __name__ == "__main__":
    unittest.main()
