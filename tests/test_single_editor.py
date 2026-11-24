"""Offline end-to-end simulation: one DaEditor invocation for several maps."""
import json
import re
import tempfile
import unittest
from pathlib import Path

from ke_single_editor import jobs_for, extract_map_logs, make_command, read_editor_log, explain_batch_failure
from ke_dataset import run_dataset
from ke_generate import DEFAULTS
from tools.ke_review import write_rgb_png
from tools.ke_analysis import analyze_dataset
from kolobanoveye import editor_failure


def fake_editor(cmd):
    """Mimic one native batch command that runs all maps in one process."""
    editor = Path(cmd[0])
    if editor.name != "daEditor3x-dev.exe":
        raise AssertionError(f"Expected DaEditor executable, got {editor}")
    run = Path(next(s.split(":", 1)[1] for s in cmd if str(s).startswith("-rootdir:")))
    queue = (run / "probe.dcmd").read_text(encoding="ascii")
    batch_commands = [line for line in queue.splitlines() if line.startswith("kolobanoveye.batch ")]
    assert len(batch_commands) == 1, queue
    batch_file = Path(batch_commands[0].partition(" ")[2])
    config = batch_file.read_text(encoding="ascii")
    count = int(re.search(r"^map_count:i=(\d+)$", config, re.MULTILINE).group(1))
    capture_paths = re.findall(r'^map_\d+:t="([^"]+)"$', config, re.MULTILINE)
    assert len(capture_paths) == count
    log = [f"[KE] MAP_BATCH_BEGIN maps={count}"]
    for map_index, capture_path in enumerate(capture_paths, 1):
        path = Path(capture_path)
        log.append(f"[KE] MAP_TASK_BEGIN index={map_index}/{count}")
        data = path.read_text(encoding="ascii")
        def value(name):
            match = re.search(rf'^{name}:[a-z][0-9]?=(.*)$', data, re.MULTILINE)
            if not match:
                raise AssertionError(f"missing {name} in {path}")
            return match.group(1).strip().strip('"')
        mid, n = value("map_id"), int(value("frame_count"))
        journal = Path(value("metadata_journal"))
        log += [
            f'[KE] MAP_BEGIN D:/game/{mid}', '[KE] MAP_OK terrain_y=123.456',
            '[KE] MODEL_OK lod=0 nodes=206', '[KE] PLACEMENT_OK x=1 z=2',
            '[KE] WATER_GROUND_FILTER enabled=1',
            '[KE] TANK_BOUNDS min_x=0.000 max_x=4096.000 min_z=0.000 max_z=4096.000',
        ]
        with journal.open("wb") as stream:
            for idx in range(n):
                image = Path(value(f"output_{idx}"))
                width, height = int(value("width")), int(value("height"))
                write_rgb_png(image, width, height, bytes([10, 20, 30]) * (width * height))
                blk = (f'level:t="{mid}"\nclass:t="tank"\nwidth:i={width}\nheight:i={height}\n'
                       f'tank_world:p3={100+idx},123,100\nbbox_amodal_aabb_xywh:p4=4,5,20,25\n'
                       f'camera_attempt:i=0\ncomposition_index:i={idx // int(value("frames_per_position"))}\n'
                       f'screen_bin_planned:t="{value(f"screen_bin_{idx}")}"\n'
                       f'screen_offset_uv:p2={value(f"screen_offset_{idx}")}\n')
                if value("capture_visibility_masks") == "yes":
                    amodal = bytearray(width * height * 3)
                    visible = bytearray(width * height * 3)
                    for x, y in ((2, 2), (3, 2), (2, 3), (3, 3)):
                        off = (y * width + x) * 3
                        amodal[off:off+3] = b'\xff\xff\xff'
                        if idx % 2 == 0 and x == 2:
                            visible[off:off+3] = b'\xff\xff\xff'
                    write_rgb_png(Path(value(f"mask_amodal_{idx}")), width, height, amodal)
                    write_rgb_png(Path(value(f"mask_visible_{idx}")), width, height, visible)
                    actual = 2 if idx % 2 == 0 else 0
                    blk += ('visibility_method:t="reverse_z_opaque_depth_compare_v1"\n'
                            f'amodal_pixels:i=4\nvisible_pixels:i={actual}\n'
                            f'unmatched_depth_pixels:i=0\nvisible_fraction:r={actual / 4}\n')
                payload = blk.encode("utf-8")
                stream.write(f"KEFRAME {idx} {len(payload)}\n".encode("ascii") + payload)
                log += ["[KE] FRAME_OK ", "[KE] FRAME_STABILITY_FINAL enabled=1 stable=1"]
        log.append(f"[KE] SEQUENCE_OK frames={n}")
        log.append(f"[KE] MAP_TASK_OK index={map_index}/{count}")
        map_image = run / "maps" / (Path(mid).name + "-tank.png")
        write_rgb_png(map_image, 256, 256, bytes([7, 11, 13]) * (256 * 256))
    log.append(f"[KE] MAP_BATCH_OK maps={count}")
    (run / "probe.dcmd.log").write_text("\n".join(log), encoding="utf-8")
    return 0


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
            "map_ids": ["levels/avg_normandy.bin", "levels/avg_finland.bin"]}

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

    def test_sixty_maps_still_queue_one_native_command(self):
        run = self.root / "many"
        run.mkdir()
        tasks = [{"capture": run / "capture" / f"{i:03d}.blk"} for i in range(60)]
        command = make_command(self.config, run, tasks)
        self.assertEqual(Path(command[0]).name, "daEditor3x-dev.exe")
        script = (run / "probe.dcmd").read_text(encoding="ascii")
        self.assertEqual(script.splitlines(), [
            f"kolobanoveye.batch {(run / 'map-batch.blk').as_posix()}",
            "exit no"])
        self.assertNotIn("wait.frame", script)
        manifest = (run / "map-batch.blk").read_text(encoding="ascii")
        self.assertIn("map_count:i=60", manifest)
        self.assertIn("map_59:t=", manifest)
        self.assertNotIn("kolobanoveye.frame", script)

    def test_two_maps_one_editor_no_duplicate_png_or_coco(self):
        commands = []
        def single_invocation(cmd):
            commands.append(cmd)
            return fake_editor(cmd)
        result = run_dataset(self.config, self.dataset, executor=single_invocation)
        self.assertEqual(len(commands), 1)
        self.assertEqual(result["editor_processes"], 1)
        self.assertEqual(result["images"], 3)
        raw = self.dataset / "raw"
        self.assertEqual(len(list((raw / "images").glob("*.png"))), 3)
        self.assertTrue((raw / "images" / "00000001.png").is_file())
        coco = json.loads((raw / "annotations" / "instances.json").read_text())
        self.assertEqual(len(coco["images"]), 3)
        self.assertEqual({x["map_id"] for x in coco["images"]},
                         {"levels/avg_normandy.bin", "levels/avg_finland.bin"})
        self.assertTrue(all((raw / x["file_name"]).is_file() for x in coco["images"]))
        run, = list((self.dataset / "runs").glob("run-*"))
        self.assertFalse((run / "images").exists())
        self.assertFalse((run / "coco").exists())
        self.assertFalse((run / "runs").exists())
        self.assertEqual(len(list((run / "maps").glob("*.png"))), 2)
        self.assertEqual(len(list((run / "capture").glob("*.blk"))), 2)
        self.assertEqual(len(list((run / "plans").glob("*.json"))), 2)
        self.assertFalse(list((run / "journals").glob("*.jsonl")))
        q = (run / "probe.dcmd").read_text()
        self.assertEqual(q.count("kolobanoveye.batch "), 1)
        self.assertEqual(q.count("kolobanoveye.frame "), 0)
        self.assertIn("map_count:i=2", (run / "map-batch.blk").read_text())
        self.assertEqual(q.count("exit no"), 1)

    def test_visibility_capture_exports_mask_boxes_and_hidden_frame(self):
        cfg = {**self.config, "count": 2, "map_ids": ["levels/avg_normandy.bin"],
               "capture_visibility_masks": True}
        result = run_dataset(cfg, self.dataset, executor=fake_editor)
        raw = self.dataset / "raw"
        self.assertTrue(result["visibility_diagnostic"])
        self.assertEqual(result["images"], 2)
        self.assertEqual(result["annotations"], 1)
        self.assertEqual(result["visibility_bins"]["moderate"], 1)
        self.assertEqual(result["visibility_bins"]["reject_too_occluded"], 1)
        coco = json.loads((raw / "annotations/instances.json").read_text())
        ann, = coco["annotations"]
        self.assertEqual(ann["bbox_kind"], "visible_opaque")
        self.assertEqual(ann["bbox"], [2, 2, 1, 2])
        self.assertEqual(ann["ke_visibility"]["visible_fraction"], .5)
        self.assertTrue((raw / ann["ke_visibility"]["amodal_mask"]).is_file())

    def test_quota_selection_publishes_only_validated_accepted_frames(self):
        cfg = {**self.config, "count": 2, "map_ids": ["levels/avg_normandy.bin"],
               "capture_visibility_masks": True, "enforce_visibility_quotas": True,
               "visibility_candidate_multiplier": 2,
               "visibility_weights": {"open": 0, "light": 0, "moderate": 1, "heavy": 0}}
        result = run_dataset(cfg, self.dataset, executor=fake_editor)
        raw = self.dataset / "raw"
        self.assertEqual(result["images"], 2)
        self.assertEqual(result["annotations"], 2)
        self.assertEqual(result["visibility_quotas"]["candidates"], 4)
        self.assertTrue(result["visibility_quotas"]["complete"])
        self.assertEqual(result["visibility_bins"]["moderate"], 2)
        self.assertEqual(len(list((raw / "images").glob("*.png"))), 2)
        self.assertEqual(len(list((raw / "masks" / "visible").glob("*.png"))), 2)
        coco = json.loads((raw / "annotations/instances.json").read_text())
        self.assertEqual({i["id"] for i in coco["images"]}, {1, 3})
        self.assertEqual({a["image_id"] for a in coco["annotations"]}, {1, 3})

    def test_quota_shortfall_preserves_old_raw_and_candidates(self):
        run_dataset(self.config, self.dataset, executor=fake_editor)
        old = (self.dataset / "raw/manifest.json").read_bytes()
        cfg = {**self.config, "count": 2, "map_ids": ["levels/avg_normandy.bin"],
               "capture_visibility_masks": True, "enforce_visibility_quotas": True,
               "visibility_candidate_multiplier": 2,
               "visibility_weights": {"open": 1, "light": 0, "moderate": 0, "heavy": 0}}
        with self.assertRaisesRegex(ValueError, "Не набраны квоты"):
            run_dataset(cfg, self.dataset, replace_raw=True, executor=fake_editor)
        self.assertEqual((self.dataset / "raw/manifest.json").read_bytes(), old)
        staging, = self.dataset.glob(".raw-staging-*")
        self.assertEqual(len(list((staging / "images").glob("*.png"))), 4)
        report, = (self.dataset / "runs").glob("*/visibility-quotas.json")
        self.assertEqual(json.loads(report.read_text())["deficit"]["open"], 2)

    def test_replace_preserves_old_raw_when_batch_fails(self):
        run_dataset(self.config, self.dataset, executor=fake_editor)
        old_manifest = (self.dataset / "raw" / "manifest.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "--replace-raw"):
            run_dataset(self.config, self.dataset, executor=fake_editor)
        with self.assertRaisesRegex(RuntimeError, "кодом 9"):
            run_dataset(self.config, self.dataset, replace_raw=True, executor=lambda _: 9)
        self.assertEqual((self.dataset / "raw" / "manifest.json").read_bytes(), old_manifest)
        self.assertFalse(any(self.dataset.glob(".raw-staging-*")))
        result = run_dataset({**self.config, "count": 1}, self.dataset,
                             replace_raw=True, executor=fake_editor)
        self.assertEqual(result["images"], 1)
        self.assertEqual(len(list((self.dataset / "raw" / "images").glob("*.png"))), 1)

    def test_scenes_and_metadata_no_splits(self):
        run_dataset(self.config, self.dataset, executor=fake_editor)
        raw = self.dataset / "raw"
        rows = [json.loads(l) for l in (raw / "metadata" / "images.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(len(r["sha256"]) == 64 for r in rows))
        self.assertTrue((raw / "metadata" / "scenes.jsonl").is_file())
        self.assertTrue((raw / "metadata" / "maps.json").is_file())
        self.assertNotIn("game", json.loads((raw / "metadata" / "generation_config.json").read_text()))
        self.assertFalse((raw / "train").exists())

    def test_analysis_is_generated_only_on_request(self):
        run_dataset(self.config, self.dataset, executor=fake_editor)
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
                from ke_single_editor import finalize
                finalize(self.config, run, run / "raw", [{"map_id": "levels/avg_abandoned_factory.bin"}])

    def test_batch_detects_incomplete_map(self):
        tasks = [{"map_id": "levels/avg_a.bin", "count": 2}]
        with self.assertRaisesRegex(ValueError, "SEQUENCE_OK"):
            extract_map_logs("[KE] MAP_BEGIN D:/game/levels/avg_a.bin\n[KE] MAP_OK\n", tasks)

    def test_no_whitelist_fails(self):
        with self.assertRaises(ValueError):
            jobs_for({**self.config, "map_ids": ["levels/avg_missing.bin"]}, [])


if __name__ == "__main__":
    unittest.main()
