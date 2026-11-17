"""Integration-level tests for deterministic equal-quota multi-map batches."""
import json
import tempfile
import unittest
from pathlib import Path

from ke_generate import DEFAULTS
from ke_multi_generate import jobs_for, map_seed, run_multi
from tools.ke_review import write_rgb_png


def installed_maps(game, n):
    levels = game / "levels"
    levels.mkdir(parents=True)
    result = []
    for index in range(n):
        name = f"levels/avg_test{index}.bin"
        (levels / Path(name).name).write_bytes(b"DBLD3x64" + b"\0" * 8)
        result.append({"id": name, "status": "header_ok"})
    return result


class MultiMapTests(unittest.TestCase):
    def test_quotas_preserve_sorted_map_order(self):
        c = {**DEFAULTS, "count": 7, "map_ids": [
            "levels/avg_test2.bin", "levels/avg_test0.bin", "levels/avg_test1.bin"]}
        discovered = [dict(id=f"levels/avg_test{i}.bin", status="header_ok") for i in range(3)]
        self.assertEqual(jobs_for(c, discovered), [
            ("levels/avg_test0.bin", 3), ("levels/avg_test1.bin", 2), ("levels/avg_test2.bin", 2)])
        c["count"] = 2
        self.assertEqual([x[1] for x in jobs_for(c, discovered)], [1, 1, 0])
        self.assertEqual(map_seed(42, discovered[0]["id"]), map_seed(42, discovered[0]["id"]))

    def test_raises_if_any_map_is_missing(self):
        c = {**DEFAULTS, "map_ids": ["levels/avg_missing.bin"]}
        with self.assertRaisesRegex(ValueError, "отсутствуют"):
            jobs_for(c, [])

    def test_three_images_five_maps_end_to_end_without_dagor(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            game = p / "game"; installed_maps(game, 5)
            dagor = p / "dagor"; dagor.mkdir()
            output = p / "output"
            cfg = {**DEFAULTS, "game": str(game), "dagor": str(dagor),
                   "output": str(output), "count": 3, "width": 64, "height": 64,
                   "map_ids": [f"levels/avg_test{i}.bin" for i in range(5)]}
            invoked = []
            def mock_editor(cmd):
                def value(flag):
                    return cmd[cmd.index(flag) + 1]
                mid, n = value("--level"), int(value("--count"))
                self.assertEqual(n, 1)
                run = Path(value("--out"))
                invoked.append(mid)
                (run / "images").mkdir(parents=True)
                records = []
                for i in range(n):
                    write_rgb_png(run / "images" / f"{i:06d}.png", 64, 64,
                                  bytes((12, 44, 90)) * (64 * 64))
                    blk = (f"width:i=64\nheight:i=64\nlevel:t=\"{mid}\"\n"
                           f"tank_world:p3={100+i}, 0, 100\n")
                    records.append({"frame_index": i, "image": f"images/{i:06d}.png",
                                    "annotation": {"class": "tank",
                                                   "bbox_kind": "approximate_amodal",
                                                   "bbox_method": "projected_aabb",
                                                   "bbox_xywh": [5, 6, 20, 20]},
                                    "dagor_frame_metadata_blk": blk})
                (run / "metadata.jsonl").write_text(
                    "".join(json.dumps(x) + "\n" for x in records), encoding="utf-8")
                (run / "plan.json").write_text(
                    json.dumps({"cameras": [{}] * n}), encoding="utf-8")
                return 0
            report = run_multi(cfg, executor=mock_editor)
            self.assertTrue(report["complete"])
            self.assertEqual([x["planned"] for x in report["maps"]], [1, 1, 1, 0, 0])
            self.assertEqual(len(invoked), 3)
            data = json.loads((output / "coco" / "all.json").read_text(encoding="utf-8"))
            self.assertEqual(len(data["images"]), 3)
            self.assertEqual(len({img["scene_group"].split(":")[0] for img in data["images"]}), 3)
            self.assertTrue(all((output / "coco" / img["file_name"]).resolve().is_file()
                                for img in data["images"]))

    def test_failed_map_does_not_fake_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp); game = p / "game"; installed_maps(game, 2)
            dagor = p / "dagor"; dagor.mkdir()
            output = p / "output"
            cfg = {**DEFAULTS, "game": str(game), "dagor": str(dagor),
                   "output": str(output), "count": 3,
                   "map_ids": ["levels/avg_test0.bin", "levels/avg_test1.bin"]}
            with self.assertRaisesRegex(RuntimeError, "кодом 9"):
                run_multi(cfg, executor=lambda cmd: 9)
            manifest = json.loads((output / "multi-manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["complete"])
            self.assertEqual(manifest["maps"][0]["status"], "failed")
            self.assertFalse((output / "coco").exists())


if __name__ == "__main__":
    unittest.main()
