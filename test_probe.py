import struct
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from zlib import compress, crc32

import kolobanoveye as probe


def block(tag, data=b"", flags=0):
    body = tag.rjust(4, b"\0") + data
    return struct.pack("<I", len(body) | flags << 30) + body


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc32(kind + data))


class ProbeTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_level_reports_blocks_and_flags(self):
        data = b"DBLD3x64" + struct.pack("<I", 2) + block(b"lmap", b"1234", 2) + block(b"END")
        report = probe.level_info(self.write("level.bin", data))
        self.assertEqual(report["texture_count_header"], 2)
        self.assertEqual(report["blocks"], [
            {"tag": "lmap", "offset": 12, "bytes": 8, "flags": 2},
            {"tag": "END", "offset": 24, "bytes": 4, "flags": 0},
        ])

    def test_level_rejects_wrong_version_and_bad_block_length(self):
        for data in (b"DBLD3x32" + bytes(4), b"DBLD3x64" + bytes(4) + struct.pack("<I", 200) + b"lmap"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                probe.level_info(self.write("level.bin", data))

    def test_extracts_embedded_oodle_lightmap(self):
        def texture_name(name):
            return struct.pack("<I", len(name)) + name + bytes((-len(name)) % 4)

        payload = bytes((0x8C, 6, 0, 0, 0))
        ddsx = struct.pack("<4s4sIHHBBHHBBII", b"DDSx", b"DXT1", 0x60000000, 4, 4, 1, 0, 0, 4, 0, 3, 8, len(payload))
        outer = b"DBLD3x64" + bytes(4)
        other = block(b"TEX", texture_name(b"avg_normandy_lightmap_extra*?AmQ0-0-0"))
        for name in (b"avg_normandy_lightmap", b"avg_normandy_lightmap*?AmQ0-0-0"):
            with self.subTest(name=name):
                named = texture_name(name)
                tex = block(b"TEX", named + struct.pack("<I", len(ddsx) + len(payload)) + ddsx + payload)
                path = self.write("level.bin", outer + other + tex + block(b"END"))
                info, data = probe.read_normandy_lightmap(path)
                self.assertEqual(data, payload)
                self.assertEqual(info["texture"], "avg_normandy_lightmap")
                self.assertEqual(info["encoded_texture"], name.decode())
                self.assertEqual(info["first_stream_type"], "Kraken")
                self.assertEqual(info["mip_bytes"], 8)
                self.assertEqual(info["payload_offset"], len(outer + other) + 12 + len(named) + len(ddsx))

                truncated = block(b"TEX", named + struct.pack("<I", len(ddsx) + 1) + ddsx + payload)
                path = self.write("level.bin", outer + truncated + block(b"END"))
                with self.assertRaisesRegex(ValueError, "Invalid DDSx sizes"):
                    probe.read_normandy_lightmap(path)

    def test_inventory_accepts_optional_hq_pack_absence(self):
        self.write(probe.LEVEL, b"DBLD3x64" + bytes(4) + block(b"END"))
        self.write(probe.GRP, b"GRP3" + bytes(12))
        self.write("aces.vromfs.bin", b"VRFx")
        self.write("content/base/res/grp_hdr.vromfs.bin", b"VRFs")
        self.write("WarThunderCDK/application.blk", b"SDK {}")
        self.write("WarThunderCDK/tools.ps50.shdump.bin", b"VSPSdump11.3")
        result = probe.inspect(self.root, self.root / "WarThunderCDK")
        self.assertFalse(result["errors"])
        self.assertFalse(result["files"][str(self.root / probe.DXP)]["present"])

    def test_large_oodle_quanta_report_restarts_and_tail(self):
        fill_quantum = bytes.fromhex("8c0c07ffff2a")
        size = 0x40000 + 32
        self.assertEqual(probe.inspect_large_oodle_quanta(fill_quantum * 2, size), {
            "count": 2, "packed_bytes_left": 0, "restart_offsets": [0, 6],
        })
        self.assertEqual(probe.inspect_large_oodle_quanta(fill_quantum * 2 + b"x", size)["packed_bytes_left"], 1)
        self.assertEqual(probe.inspect_large_oodle_quanta(fill_quantum + fill_quantum[:-1], size), {
            "error": "Truncated quantum payload", "packed_offset": 6,
        })

    def test_unoodle_requires_actual_decoded_byte_count(self):
        exe = self.write("unoodle.exe", b"")
        self.write(probe.LEVEL, b"")
        payload = bytes.fromhex("8c0c07ffff2a")
        meta = {"expected_bytes": 8, "first_stream_type": "Leviathan"}
        for reported in (8, 4):
            def run(command, **_):
                self.assertEqual(command[0], exe)
                self.assertEqual(command[2:4], ["--length", "8"])
                self.assertEqual(command[1].read_bytes(), payload)
                self.assertEqual(command[4], "--output")
                command[5].write_bytes(b"*" * 8)
                return subprocess.CompletedProcess(command, 0, f"Extracted {reported} bytes\n".encode(), b"")

            output = StringIO()
            with (self.subTest(reported=reported), redirect_stdout(output),
                  patch.object(probe, "read_normandy_lightmap", side_effect=lambda _: (meta.copy(), payload)),
                  patch.object(probe.subprocess, "run", side_effect=run)):
                if reported == 8:
                    probe.probe_ooz(self.root, exe, unoodle=True)
                    self.assertIn("OOZ_LIGHTMAP_OK", output.getvalue())
                else:
                    with self.assertRaisesRegex(ValueError, "expected decoded byte count"):
                        probe.probe_ooz(self.root, exe, unoodle=True)

    def test_incompatible_shader_version_is_reported(self):
        path = self.write("tools.ps50.shdump.bin", b"VSPSdump10.2")
        with self.assertRaisesRegex(ValueError, "11.3"):
            probe.shader_info(path)

    def test_choose_does_not_confuse_pack_and_resource_type(self):
        data = (
            '  4: "tiger" cls=0xB4B7D9C4 ofs=0x00000010\n'
            '     [ 0] tiger_skeleton\n'
            '  6: "tiger_skeleton" cls=0x56F81B6D ofs=0x00000020\n'
            '  9: "tiger_alt" cls=0xB4B7D9C4 ofs=0x00000030\n'
        )
        res = probe.parse_resources(data)
        self.assertEqual(probe.choose_resources(res, "tiger", None, None), ("tiger", "tiger_skeleton"))
        with self.assertRaisesRegex(ValueError, "Set --model"):
            probe.choose_resources(res, "other", None, None)
        with self.assertRaisesRegex(ValueError, "not a DynModel"):
            probe.choose_resources(res, "tiger", "tiger_skeleton", None)

    def test_tiger_selects_the_base_model_and_its_skeleton(self):
        stem = "pzkpfw_VI_ausf_h1_tiger"
        res = {
            stem: (probe.DYNMODEL, []),
            stem + "_dmg": (probe.DYNMODEL, []),
            stem + "_east": (probe.DYNMODEL, []),
            stem + "_xray": (probe.DYNMODEL, []),
            stem + "_skeleton": (probe.SKELETON, []),
            stem + "_dm_skeleton": (probe.SKELETON, []),
            stem + "_east_skeleton": (probe.SKELETON, []),
        }
        self.assertEqual(probe.choose_resources(res, "germ_" + stem.lower(), None, None), (stem, stem + "_skeleton"))
        self.assertEqual(probe.choose_resources(res, "germ_" + stem.lower(), stem + "_east", None),
                         (stem + "_east", stem + "_east_skeleton"))

    def test_identifies_oodle_stub_in_native_editor_log(self):
        path = self.write(".logs/daEditor3x-dev.exe-test/fatalerr",
                          b"OodleLoadCB::open not implemented (D:/Games/WarThunder/levels/avg_normandy.bin)\n")
        message = probe.editor_failure(self.root, "Editor exited with 1.")
        self.assertIn("built with the Oodle stub", message)
        self.assertIn(str(path), message)

    def test_rgb_png_dimensions_and_color_type(self):
        for color_type, pixel in ((2, b"\xff\x80\x40"), (6, b"\xff\x80\x40\xff")):
            ihdr = struct.pack(">IIBBBBB", 1, 1, 8, color_type, 0, 0, 0)
            png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compress(b"\0" + pixel)) + chunk(b"IEND", b"")
            path = self.write("probe.png", png)
            if color_type == 2:
                probe.verify_png(path, 1, 1)
            else:
                with self.assertRaisesRegex(ValueError, "RGB"):
                    probe.verify_png(path, 1, 1)


if __name__ == "__main__":
    unittest.main()
