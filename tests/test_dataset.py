"""The raw dataset is replaced only after all maps have been validated."""
import json
import tempfile
import unittest
from pathlib import Path

from ke_dataset import run_dataset
from ke_generate import DEFAULTS
from tools.ke_review import write_rgb_png
from kolobanoveye import ensure_level_settings_accessor, has_last_clip_bounds


def fake_editor(argv):
    def get(name):
        return argv[argv.index(name) + 1]
    n = int(get("--count"))
    mid = get("--level")
    run = Path(get("--out"))
    (run / "images").mkdir(parents=True)
    records = []
    for i in range(n):
        write_rgb_png(run / "images" / f"{i:06d}.png", 64, 64,
                      bytes((11, 55, 99)) * (64 * 64))
        blk = (f'width:i=64\nheight:i=64\nlevel:t="{mid}"\n'
               f'tank_world:p3={100 + i},0,100\n')
        records.append({"frame_index": i, "composition_index": 0,
                        "image": f"images/{i:06d}.png",
                        "annotation": {"class": "tank", "bbox_kind": "approximate_amodal",
                                       "bbox_method": "projected_aabb", "bbox_xywh": [4, 5, 20, 25]},
                        "dagor_frame_metadata_blk": blk})
    (run / "metadata.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in records), encoding="utf-8")
    (run / "plan.json").write_text(json.dumps({"cameras": [{}] * n}), encoding="utf-8")
    return 0


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.game = self.root / "game"
        levels = self.game / "levels"
        levels.mkdir(parents=True)
        for name in ("avg_normandy.bin", "avg_finland.bin"):
            (levels / name).write_bytes(b"DBLD3x64" + b"\0" * 8)
        self.dagor = self.root / "dagor"
        self.dagor.mkdir()
        self.dataset = self.root / "dataset"
        self.config = {**DEFAULTS, "dagor": str(self.dagor), "game": str(self.game),
                       "output": str(self.dataset), "count": 3, "seed": 42,
                       "width": 64, "height": 64,
                       "map_ids": ["levels/avg_normandy.bin", "levels/avg_finland.bin"]}

    def test_pack_raw_without_splits(self):
        report = run_dataset(self.config, self.dataset, executor=fake_editor)
        raw = self.dataset / "raw"
        self.assertEqual(report["images"], 3)
        self.assertEqual(report["maps"], 2)
        self.assertTrue((raw / "images" / "00000001.png").is_file())
        self.assertTrue((raw / "images" / "00000003.png").is_file())
        self.assertFalse((raw / "train").exists())
        self.assertFalse((raw / "val").exists())
        self.assertFalse((raw / "test").exists())
        coco = json.loads((raw / "annotations" / "instances.json").read_text(encoding="utf-8"))
        self.assertEqual(len(coco["images"]), 3)
        self.assertEqual(len(coco["annotations"]), 3)
        self.assertEqual(len({img["map_id"] for img in coco["images"]}), 2)
        self.assertTrue(all((raw / img["file_name"]).is_file() for img in coco["images"]))
        rows = (raw / "metadata" / "images.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(json.loads(row)["sha256"] for row in rows))
        self.assertTrue((raw / "metadata" / "scenes.jsonl").is_file())
        self.assertTrue((raw / "metadata" / "maps.json").is_file())
        config = json.loads((raw / "metadata" / "generation_config.json").read_text(encoding="utf-8"))
        self.assertNotIn("game", config)
        self.assertNotIn("dagor", config)

    def test_existing_raw_requires_explicit_replace_and_preserves_failed_run(self):
        run_dataset(self.config, self.dataset, executor=fake_editor)
        raw = self.dataset / "raw"
        previous = (raw / "manifest.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "replace-raw"):
            run_dataset(self.config, self.dataset, executor=fake_editor)
        with self.assertRaisesRegex(RuntimeError, "кодом 9"):
            run_dataset(self.config, self.dataset, replace_raw=True, executor=lambda cmd: 9)
        self.assertEqual((raw / "manifest.json").read_bytes(), previous)
        second = {**self.config, "count": 1, "map_ids": ["levels/avg_normandy.bin"]}
        result = run_dataset(second, self.dataset, replace_raw=True, executor=fake_editor)
        self.assertEqual(result["images"], 1)
        self.assertEqual(len(list((raw / "images").glob("*.png"))), 1)
        self.assertFalse(any(self.dataset.glob(".raw-backup-*")))
        self.assertFalse(any(self.dataset.glob(".raw-staging-*")))



class LegacyClipBoundsTests(unittest.TestCase):
    def test_detects_installed_last_clip_bounds_even_after_successor(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "kolobanovEye.cpp"
            source.write_text('''
  const auto cellOrigin = scene.lmeshMgr->getCellOrigin();
  const float lastClipX0 = cellOrigin.x * cellSize;
  const float lastClipZ1 = 256.f;
  return fail("LAST_CLIP_BOUNDS", "Invalid fixed clip bounds");
  result.setPoint3("last_clip_min", Point3(lastClipX0, 0.f, 0.f));
  result.setPoint3("last_clip_max", Point3(256.f, 0.f, lastClipZ1));
  result.setPoint3("tank_map_min", Point3(0.f, 0.f, 0.f));
''', encoding='utf-8')
            self.assertTrue(has_last_clip_bounds(source))
            source.write_text('just baseline source', encoding='utf-8')
            self.assertFalse(has_last_clip_bounds(source))

    def test_rejects_partial_last_clip_bounds(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "kolobanovEye.cpp"
            source.write_text('  result.setPoint3("last_clip_min", Point3(0,0,0));', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Partial last-clip bounds'):
                has_last_clip_bounds(source)

class DagorAccessorTests(unittest.TestCase):
    def test_safe_injection_accepts_modified_header_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            header = Path(temp) / "strmlevel.h"
            for eol in (b"\n", b"\r\n"):
                original = eol.join((b"class AcesScene {", b"public:",
                                     b"  void extraPatchChanges();",
                                     b"  LandMeshManager *lmeshMgr;",
                                     b"protected:", b"  DataBlock levelSettingsBlk;", b"};")) + eol
                header.write_bytes(original)
                self.assertTrue(ensure_level_settings_accessor(header))
                self.assertFalse(ensure_level_settings_accessor(header))
                updated = header.read_bytes()
                self.assertEqual(updated.count(b"getKolobanovEyeLevelSettings"), 1)
                self.assertEqual(updated.count(b"\n"), original.count(b"\n") + 2)
                if eol == b"\r\n":
                    self.assertNotIn(b"\n", updated.replace(b"\r\n", b""))


if __name__ == "__main__":
    unittest.main()
