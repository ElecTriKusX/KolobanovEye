import json
import tempfile
import unittest
from pathlib import Path

import ke_map_report as coverage


class MapCoverageTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_native_frame_blk_and_report(self):
        run = self.root / "run_001"
        run.mkdir()
        (run / "frame.blk").write_text(
            'level:t="C:/WarThunder/levels/avg_normandy.bin"\n'
            'model:t="pzkpfw_VI_ausf_h1_tiger"\n'
            'tank_world:p3=12.5, 17.0, -21.25\n', encoding="utf-8")
        (run / "frame.png").write_bytes(b"test")
        records = coverage.collect([self.root])
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0]["x"], records[0]["z"]), (12.5, -21.25))
        self.assertEqual(records[0]["map"], "avg_normandy")
        report = coverage.build_report(records, None, None, None, self.root / "report")
        self.assertEqual(report["groups"], 1)
        self.assertFalse(report["calibrated"])
        self.assertIn("SCHEMATIC ONLY", (self.root / "report" / "placements.html").read_text())

    def test_multi_tank_jsonl_and_validation(self):
        manifest = self.root / "metadata.jsonl"
        manifest.write_text(json.dumps({
            "map": "avg_normandy", "frame_id": "000262",
            "tanks": [
                {"position": {"x": 1, "y": 3, "z": 2}, "model": "tiger"},
                {"position": [8, 3, 9], "model": "panther"}
            ]
        }) + "\n", encoding="utf-8")
        records = coverage.parse_jsonl(manifest)
        self.assertEqual(len(records), 2)
        summary = coverage.build_report(records, "avg_normandy", [0, 0, 10, 10], None, self.root / "report")
        self.assertEqual(summary["groups"], 2)
        self.assertEqual(summary["outside_bounds"], 0)
        with self.assertRaisesRegex(ValueError, "requires --bounds"):
            coverage.build_report(records, None, None, self.root / "map.png", self.root / "bad")

    def test_map_calibration_and_bounds(self):
        image = self.root / "map.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" +
                          (64).to_bytes(4, "big") + (64).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
        records = [{"x": 1, "y": 0, "z": 2, "map": "normandy", "model": None,
                    "frame_id": "1", "image": None, "status": "saved"}]
        report = coverage.build_report(records, None, [0, 0, 10, 10], image, self.root / "report")
        self.assertTrue(report["calibrated"])
        self.assertIn('cx="6.40"', (self.root / "report" / "placements.html").read_text())
        self.assertIn('cy="51.20"', (self.root / "report" / "placements.html").read_text())
        with self.assertRaisesRegex(ValueError, "Bounds must be finite"):
            coverage.determine_bounds(records, [0, 10, 1, 2])


if __name__ == "__main__":
    unittest.main()
