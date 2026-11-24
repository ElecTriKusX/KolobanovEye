"""Offline protocol integration with a live fake DaEditor and real PNG/BLK."""
import json
import re
import threading
import time
import unittest
from pathlib import Path

from ke_dataset import run_dataset
from tools.ke_review import write_rgb_png
from tests import test_single_editor


class FakeOnlineEditor:
    def __init__(self, cmd, oracle, corrupt=False):
        self.run = Path(next(s.split(":", 1)[1] for s in cmd if str(s).startswith("-rootdir:")))
        self.oracle = oracle
        self.corrupt = corrupt
        self.frames = 0
        self.tasks = 0
        self.code = None
        self.error = None
        self.cancel = threading.Event()
        self.worker = threading.Thread(target=self.loop, daemon=True)
        self.worker.start()

    def read(self, path):
        end = time.monotonic() + 5
        while not path.is_file():
            if self.cancel.is_set() or time.monotonic() > end:
                raise RuntimeError(f"fake timed out: {path}")
            time.sleep(.005)
        return path.read_text(encoding="ascii")

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        self.worker.join(timeout)
        if self.worker.is_alive():
            import subprocess
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.code

    def terminate(self):
        self.cancel.set()

    kill = terminate

    def loop(self):
        log = ["[KE] ONLINE_BATCH_BEGIN"]
        try:
            mailbox = self.run / "online"
            while True:
                index = self.tasks + 1
                command = self.read(mailbox / f"task_{index}.blk")
                if "stop:b=yes" in command:
                    break
                capture = re.search(r'^capture:t="([^"]+)"$', command, re.M).group(1)
                self.tasks += 1
                log.append(f"[KE] MAP_TASK_BEGIN index={index}/0")
                self.render(Path(capture), log)
                log.append(f"[KE] MAP_TASK_OK index={index}/0")
                (mailbox / f"done_{index}.txt").write_bytes(b"ok\n")
            log.append(f"[KE] MAP_BATCH_OK maps={self.tasks}")
            self.code = 0
        except Exception as exc:
            self.error = exc
            self.code = 9
        finally:
            (self.run / "probe.dcmd.log").write_text("\n".join(log), encoding="utf-8")

    def render(self, path, log):
        data = path.read_text(encoding="ascii")
        def value(name):
            match = re.search(rf'^{name}:[a-z][0-9]?=(.*)$', data, re.MULTILINE)
            if not match:
                raise AssertionError(f"missing {name} in {path}")
            return match.group(1).strip().strip('"')
        mid, n = value("map_id"), int(value("frame_count"))
        feedback = Path(value("online_feedback_dir"))
        rendered = 0
        journal = Path(value("metadata_journal"))
        log += [
            f'[KE] MAP_BEGIN D:/game/{mid}', '[KE] MAP_OK terrain_y=123.456',
            '[KE] MODEL_OK lod=0 nodes=206', '[KE] PLACEMENT_OK x=1 z=2',
            '[KE] WATER_GROUND_FILTER enabled=1',
            '[KE] TANK_BOUNDS min_x=0.000 max_x=4096.000 min_z=0.000 max_z=4096.000',
        ]
        with journal.open("wb") as stream:
            for idx in range(n):
                decision = int(self.read(feedback / f"decision_{idx}.txt"))
                if decision < 0:
                    break
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
                        if self.oracle(mid, decision, self.frames) == "open" or (self.oracle(mid, decision, self.frames) == "moderate" and x == 2) or (self.oracle(mid, decision, self.frames) == "heavy" and x == 2 and y == 2):
                            visible[off:off+3] = b'\xff\xff\xff'
                    write_rgb_png(Path(value(f"mask_amodal_{idx}")), width, height, amodal)
                    write_rgb_png(Path(value(f"mask_visible_{idx}")), width, height, visible)
                    actual = visible.count(255) // 3
                    blk += ('visibility_method:t="reverse_z_opaque_depth_compare_v1"\n'
                            f'amodal_pixels:i=4\nvisible_pixels:i={actual}\n'
                            f'unmatched_depth_pixels:i=0\nvisible_fraction:r={actual / 4}\n')
                if value("search_occlusion") == "yes":
                    target = ("open", "light", "moderate", "heavy")[decision]
                    blk += (f'occlusion_target_bin:t="{target}"\n'
                            'occlusion_proxy_fraction:r=0.5\nocclusion_proxy_candidates:i=2\n')
                payload = blk.encode("utf-8")
                stream.write(f"KEFRAME {idx} {len(payload)}\n".encode("ascii") + payload)
                stream.flush()
                log += ["[KE] FRAME_OK ", "[KE] FRAME_STABILITY_FINAL enabled=1 stable=1"]
                rendered += 1
                self.frames += 1
                if self.corrupt:
                    Path(value(f"mask_visible_{idx}")).write_bytes(b"broken")
                (feedback / f"ready_{idx}.txt").write_bytes(b"ok\n")
        log.append(f"[KE] SEQUENCE_OK frames={rendered}")
        map_image = self.run / "maps" / (Path(mid).name + "-tank.png")
        write_rgb_png(map_image, 256, 256, bytes([7, 11, 13]) * (256 * 256))


class OnlineCaptureTests(unittest.TestCase):
    setUp = test_single_editor.SingleEditorTests.setUp

    def config_for(self, **changes):
        return {**self.config, "count": 4, "capture_visibility_masks": True,
                "enforce_visibility_quotas": True, "search_occlusion": True,
                "visibility_quota_mode": "online", "refill_batch_per_map": 8,
                "visibility_candidate_multiplier": 5, "timeout": 10,
                "visibility_weights": {"open": 1, "light": 0, "moderate": 1, "heavy": 0},
                **changes}

    def test_one_process_exact_target_and_live_targets(self):
        processes = []
        def execute(cmd):
            p = FakeOnlineEditor(cmd, lambda mid, target, i: ("open", "light", "moderate", "heavy")[target])
            processes.append(p)
            return p
        cfg = self.config_for()
        result = run_dataset(cfg, self.dataset, executor=execute)
        self.assertEqual(len(processes), 1)
        self.assertEqual(processes[0].frames, 4)
        self.assertEqual(result["editor_processes"], 1)
        self.assertEqual(result["images"], 4)
        self.assertEqual(result["visibility_quotas"]["selected_by_map"], dict.fromkeys(cfg["map_ids"], 2))
        self.assertEqual(result["visibility_bins"]["open"], 2)
        self.assertEqual(result["visibility_bins"]["moderate"], 2)
        self.assertEqual(len(list((self.dataset / "raw/images").glob("*.png"))), 4)
        run, = (self.dataset / "runs").iterdir()
        progress = [json.loads(l) for l in (run / "online-progress.jsonl").read_text().splitlines()]
        self.assertEqual(len(progress), 4)
        self.assertEqual({x["requested_bin"] for x in progress}, {"open", "moderate"})
        self.assertTrue(any(p.read_text().strip() == "-1" for p in run.glob("online/feedback_*/decision_*.txt")))
        self.assertEqual(len(list((run / "maps").glob("*.png"))), 2)

    def test_budget_incomplete_does_not_publish(self):
        cfg = self.config_for(count=2, visibility_candidate_multiplier=2,
                              visibility_weights={"open": 1, "light": 0, "moderate": 0, "heavy": 0})
        processes = []
        def execute(cmd):
            p = FakeOnlineEditor(cmd, lambda *args: "moderate")
            processes.append(p)
            return p
        with self.assertRaisesRegex(ValueError, "candidate_budget"):
            run_dataset(cfg, self.dataset, executor=execute)
        self.assertEqual(len(processes), 1)
        self.assertEqual(processes[0].frames, 4)
        self.assertFalse((self.dataset / "raw").exists())
        staging, = self.dataset.glob(".raw-staging-*")
        self.assertEqual(len(list((staging / "images").glob("*.png"))), 4)

    def test_corrupt_mask_aborts_before_next_frame(self):
        cfg = self.config_for()
        processes = []
        def execute(cmd):
            p = FakeOnlineEditor(cmd, lambda *args: "open", corrupt=True)
            processes.append(p)
            return p
        with self.assertRaises((OSError, ValueError)):
            run_dataset(cfg, self.dataset, executor=execute)
        self.assertEqual(processes[0].frames, 1)
        self.assertFalse((self.dataset / "raw").exists())
        run, = (self.dataset / "runs").iterdir()
        self.assertEqual(json.loads((run / "manifest.json").read_text())["status"], "FAILED")
