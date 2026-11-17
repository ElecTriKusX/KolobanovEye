"""File-only map discovery and allowlist tests (no Windows/Dagor required)."""
import json
import tempfile
import unittest
from pathlib import Path

from ke_maps import (
    scan_maps, load_whitelist, save_whitelist, initial_whitelist,
    available_whitelist, plan_equal_allocation,
)


class MapDiscoveryTests(unittest.TestCase):
    def test_scan_only_top_level_avg_bin_and_sort(self):
        with tempfile.TemporaryDirectory() as folder:
            game = Path(folder)
            levels = game / "levels"
            levels.mkdir()
            (levels / "avg_zulu.bin").write_bytes(b"DBLD3x64" + b"\0" * 8)
            (levels / "avg_normandy.bin").write_bytes(b"DBLD3x64" + b"\0" * 8)
            (levels / "avg_broken.bin").write_bytes(b"NOT_DBLD")
            (levels / "avn_ship.bin").write_bytes(b"DBLD3x64")
            (levels / "avg_other.txt").write_bytes(b"DBLD3x64")
            (levels / "nested").mkdir()
            (levels / "nested" / "avg_invisible.bin").write_bytes(b"DBLD3x64")
            result = scan_maps(game)
            self.assertEqual([m["id"] for m in result], [
                "levels/avg_broken.bin", "levels/avg_normandy.bin", "levels/avg_zulu.bin"
            ])
            self.assertEqual([m["status"] for m in result], [
                "invalid_header", "header_ok", "header_ok"
            ])
            self.assertEqual(initial_whitelist(result), ["levels/avg_normandy.bin"])

    def test_missing_levels_and_empty_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            game = Path(folder)
            with self.assertRaisesRegex(ValueError, "Папка карт"):
                scan_maps(game)
            (game / "levels").mkdir()
            self.assertEqual(scan_maps(game), [])
            self.assertEqual(initial_whitelist([]), [])


class MapAllowlistTests(unittest.TestCase):
    def test_save_load_empty_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "maps.json"
            self.assertIsNone(load_whitelist(file))
            save_whitelist([], file)
            self.assertEqual(load_whitelist(file), [])
            chosen = ["levels/avg_normandy.bin", "levels/avg_finland.bin"]
            save_whitelist(chosen, file)
            self.assertEqual(load_whitelist(file), chosen)
            self.assertEqual(json.loads(file.read_text(encoding="utf-8"))["version"], 1)

    def test_block_path_traversal_duplicates_and_invalid_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "maps.json"
            for names in (["../avg_normandy.bin"], ["levels/../avg_b.bin"],
                          ["levels/avn_ship.bin"],
                          ["levels/avg_a.bin", "levels/AVG_A.BIN"]):
                with self.subTest(names=names), self.assertRaises(ValueError):
                    save_whitelist(names, file)
            file.write_text('{"version": 2, "maps": []}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_whitelist(file)

    def test_missing_and_invalid_maps_are_not_usable(self):
        discovered = [
            {"id": "levels/avg_invalid.bin", "status": "invalid_header"},
            {"id": "levels/avg_normandy.bin", "status": "header_ok"},
        ]
        selected = [
            "levels/avg_normandy.bin", "levels/avg_invalid.bin",
            "levels/avg_removed.bin",
        ]
        self.assertEqual(available_whitelist(discovered, selected),
                         ["levels/avg_normandy.bin"])

    def test_equal_allocation_with_zero_quota(self):
        ids = [f"levels/avg_{n}.bin" for n in "abcde"]
        self.assertEqual([q for _, q in plan_equal_allocation(3, ids)],
                         [1, 1, 1, 0, 0])
        self.assertEqual([q for _, q in plan_equal_allocation(7, ids[:3])],
                         [3, 2, 2])
        self.assertEqual(plan_equal_allocation(0, []), [])
        with self.assertRaises(ValueError):
            plan_equal_allocation(3, [])


if __name__ == "__main__":
    unittest.main()
