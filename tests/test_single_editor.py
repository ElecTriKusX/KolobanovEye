"""Offline end-to-end simulation: one DaEditor invocation for several maps."""
import json
import re
import tempfile
import unittest
from pathlib import Path

from ke_single_editor import jobs_for, extract_map_logs
from ke_dataset import run_dataset
from ke_generate import DEFAULTS
from tools.ke_review import write_rgb_png


def fake_editor(cmd):
    """Mimic one editor processing every capture command from the same .dcmd."""
    editor = Path(cmd[0])
    if editor.name != "daEditor3x-dev.exe":
        raise AssertionError(f"Expected DaEditor executable, got {editor}")
    run = Path(next(s.split(":", 1)[1] for s in cmd if str(s).startswith("-rootdir:")))
    queue = (run / "probe.dcmd").read_text(encoding="ascii")
    capture_commands = [line for line in queue.splitlines() if line.startswith("kolobanoveye.frame ")]
    log = []
    for command in capture_commands:
        path = Path(command.partition(" ")[2])
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
            '[KE] MODEL_OK lod=0 nodes=206', '[KE] PLACEMENT_OK',
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
                       f'camera_attempt:i=0\ncomposition_index:i={idx // int(value("frames_per_position"))}\n')
                payload = blk.encode("utf-8")
                stream.write(f"KEFRAME {idx} {len(payload)}\n".encode("ascii") + payload)
                log += ["[KE] FRAME_OK ", "[KE] FRAME_STABILITY_FINAL enabled=1 stable=1"]
        log.append(f"[KE] SEQUENCE_OK frames={n}")
        map_image = run / "maps" / (Path(mid).name + "-tank.png")
        write_rgb_png(map_image, 256, 256, bytes([7, 11, 13]) * (256 * 256))
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
        self.assertEqual(q.count("kolobanoveye.frame "), 2)
        self.assertEqual(q.count("exit no"), 1)

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

    def test_batch_detects_incomplete_map(self):
        tasks = [{"map_id": "levels/avg_a.bin", "count": 2}]
        with self.assertRaisesRegex(ValueError, "SEQUENCE_OK"):
            extract_map_logs("[KE] MAP_BEGIN D:/game/levels/avg_a.bin\n[KE] MAP_OK\n", tasks)

    def test_no_whitelist_fails(self):
        with self.assertRaises(ValueError):
            jobs_for({**self.config, "map_ids": ["levels/avg_missing.bin"]}, [])


if __name__ == "__main__":
    unittest.main()
