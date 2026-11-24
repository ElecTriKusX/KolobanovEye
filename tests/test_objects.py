import tempfile
import unittest
from pathlib import Path
from tools.ke_objects import read_inventory, analyze_map, spatial_index
from tools.ke_pixel_debug import load_png


def row(kind, pool, pos, lo, hi, name):
    return "\t".join(map(str,["O",kind,0,pool,*pos,*lo,*hi,1,name.encode().hex()]))+"\n"


class ObjectInventoryTests(unittest.TestCase):
    def test_reports_and_maps(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/"objects-native.tsv").write_text("KEOBJECTS\t1\t0\t128\t0\t128\t1\n"+
                row("ri_tm",1,(10,3,10),(6,3,6),(14,10,14),"house_a")+
                row("ri_pos",2,(19,3,10),(17,3,8),(21,12,12),"tree_a")+
                row("ri_extra",3,(100,3,100),(98,3,98),(102,6,102),"rock")+
                "T\t0\t0\t3\t3\nEND\t1\t3\t0\n",encoding="ascii")
            report=analyze_map(p,p/"missing.png",256)
            self.assertEqual(report["objects"],3)
            self.assertEqual(report["groups"],3)
            self.assertEqual(report["by_category_hint"],{"building":1,"vegetation":1,"other":1})
            for name in ("objects-map.png","density-map.png","groups-map.png","large-obstacles-map.png",
                         "vegetation-map.png","small-obstacles-map.png","unknown-obstacles-map.png","low-density-map.png",
                         "placement-candidates-map.png"):
                self.assertEqual(load_png(p/name)[:2],(256,256))
            self.assertEqual(report["background"],"plain_background_overview_missing")

    def test_incomplete_and_wrong_tile_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"objects.tsv"
            p.write_text("KEOBJECTS\t1\t0\t128\t0\t128\t1\nT\t0\t0\t0\t0\n",encoding="ascii")
            with self.assertRaises(ValueError):
                read_inventory(p)
            p.write_text("KEOBJECTS\t1\t0\t256\t0\t256\t2\n"+
                         row("ri_extra",1,(200,0,200),(199,0,199),(201,1,201),"rock")+
                         "T\t0\t0\t1\t1\n",encoding="ascii")
            with self.assertRaisesRegex(ValueError,"owning tile"):
                read_inventory(p)

    def test_footprint_spans_cells_and_boundary(self):
        grid=spatial_index((0,32,0,32),[{"id":1,"position":[32,0,32],"bbox_min":[10,0,10],"bbox_max":[35,1,35]}])
        self.assertEqual(len(grid["cells"]),4)
        self.assertEqual(len(grid["groups"]),1)
        self.assertEqual(sum(len(c["center_ids"]) for c in grid["cells"]),1)


    def test_separate_native_batch_without_dataset_raw(self):
        from tests.test_single_editor import SingleEditorTests
        from tools.ke_objects import run_scan
        from tools.ke_review import write_rgb_png
        import json
        import re
        fixture = SingleEditorTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        cfg = fixture.config
        dataset = fixture.dataset
        dataset.mkdir(parents=True, exist_ok=True)
        sentinel = dataset/"raw"
        sentinel.mkdir(exist_ok=True)
        (sentinel/"keep.txt").write_text("untouched")
        mids = cfg["map_ids"]
        def executor(cmd):
            run=Path(next(s.split(":",1)[1] for s in cmd if str(s).startswith("-rootdir:")))
            batch=(run/"map-batch.blk").read_text(encoding="ascii")
            paths=re.findall(r'^map_\d+:t="([^"]+)"$',batch,re.M)
            logs=[]
            for mid,path in zip(mids,paths):
                text=Path(path).read_text(encoding="ascii")
                inventory=Path(re.search(r'^object_inventory:t="([^"]+)"$',text,re.M).group(1))
                inventory.write_text("KEOBJECTS\t1\t0\t128\t0\t128\t1\n"+
                    row("ri_extra",1,(10,0,10),(8,0,8),(12,2,12),"house")+
                    "T\t0\t0\t1\t1\nEND\t1\t1\t0\n",encoding="ascii")
                write_rgb_png(run/"maps"/(Path(mid).name+"-tank.png"),256,256,bytes([20,30,40])*256*256)
                logs.append("[KE] OBJECT_SCAN_OK tiles=1 objects=1 invalid=0")
            logs.append(f"[KE] MAP_BATCH_OK maps={len(paths)}")
            (run/"probe.dcmd.log").write_text("\n".join(logs),encoding="ascii")
            return 0
        out=run_scan(cfg["dagor"],cfg["game"],dataset,mids,256,executor=executor)
        manifest=json.loads((out/"manifest.json").read_text())
        self.assertEqual(manifest["editor_processes"],1)
        self.assertEqual(set(manifest["maps"]),set(mids))
        self.assertEqual((sentinel/"keep.txt").read_text(),"untouched")
        self.assertEqual(manifest["maps"][mids[0]]["background"],"dagor_tank_square")


    def test_native_debug_wins_over_empty_legacy(self):
        from tools.ke_objects import check_scan_completion
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp)
            (run/"probe.dcmd.log").write_text("",encoding="ascii")
            native=run/".logs"/"daEditor3x-dev.exe-test"/"debug"
            native.parent.mkdir(parents=True)
            native.write_text("1.0 [D] CON: [KE] OBJECT_SCAN_OK tiles=1 objects=2 invalid=0\n"
                              "1.1 [D] CON: [KE] MAP_BATCH_OK maps=1\n",encoding="ascii")
            self.assertIn("OBJECT_SCAN_OK",check_scan_completion(run,0,1))

    def test_native_failure_not_hidden_by_legacy_success(self):
        from tools.ke_objects import check_scan_completion
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp)
            (run/"probe.dcmd.log").write_text("[KE] OBJECT_SCAN_OK tiles=1 objects=2 invalid=0\n"
                                             "[KE] MAP_BATCH_OK maps=1\n",encoding="ascii")
            native=run/".logs"/"daEditor3x-dev.exe-test"/"debug"
            native.parent.mkdir(parents=True)
            native.write_text("1.0 [D] CON: [KE] OBJECT_SCAN_BEGIN tiles=1 side=128.0\n"
                              "1.1 [E] CON: [KE] FAIL OBJECT_SCAN: RendInst tile preparation timed out\n",encoding="ascii")
            with self.assertRaisesRegex(ValueError,"RendInst tile preparation timed out"):
                check_scan_completion(run,1,1)

    def test_missing_native_scan_marker_reports_exit(self):
        from tools.ke_objects import check_scan_completion
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp)
            (run/"probe.dcmd.log").write_text("[KE] MAP_BATCH_OK maps=1\n",encoding="ascii")
            with self.assertRaisesRegex(ValueError,"object scans=0/1"):
                check_scan_completion(run,0,1)
            with self.assertRaisesRegex(ValueError,"exit=13"):
                check_scan_completion(run,13,1)


    def test_offline_recovery_checks_native_counts_and_preserves_manifest(self):
        from tools.ke_objects import recover_run
        from tools.ke_review import write_rgb_png
        import json
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp)
            for name in ("capture","maps","avg_finland"):
                (run/name).mkdir()
            (run/"capture"/"000.blk").write_text('map_id:t="levels/avg_finland.bin"\n',encoding="ascii")
            (run/"avg_finland"/"objects-native.tsv").write_text(
                "KEOBJECTS\t1\t0\t128\t0\t128\t1\n"+
                row("ri_tm",1,(10,0,10),(8,0,8),(12,2,12),"house")+
                "T\t0\t0\t1\t1\nEND\t1\t1\t0\n",encoding="ascii")
            write_rgb_png(run/"maps"/"avg_finland.bin-tank.png",256,256,bytes([20,30,40])*256*256)
            (run/"manifest.json").write_text('{"status":"FAILED"}',encoding="ascii")
            native=run/".logs"/"daEditor3x-dev.exe-test"/"debug"
            native.parent.mkdir(parents=True)
            msg=("1.0 [D] CON: [KE] MAP_BEGIN D:/game/levels/avg_finland.bin\n"
                 "1.1 [D] CON: [KE] OBJECT_SCAN_OK tiles=1 objects=1 invalid=0\n"
                 "1.2 [D] CON: [KE] MAP_BATCH_OK maps=1\n"
                 "1.3 [F] C0000005\n")
            native.write_text(msg.replace("objects=1","objects=2"),encoding="ascii")
            with self.assertRaisesRegex(ValueError,"count mismatch"):
                recover_run(run,256)
            self.assertFalse((run/"recovery-manifest.json").exists())
            native.write_text(msg,encoding="ascii")
            recover_run(run,256)
            self.assertEqual(json.loads((run/"manifest.json").read_text())["status"],"FAILED")
            report=json.loads((run/"recovery-manifest.json").read_text())
            self.assertEqual(report["status"],"RECOVERED_OFFLINE")
            self.assertEqual(report["editor_processes_started"],0)
            self.assertTrue((run/"avg_finland"/"groups-map.png").is_file())
