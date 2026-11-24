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
            self.assertEqual(report["groups"],2)
            self.assertEqual(report["by_category_hint"],{"building":1,"vegetation":1,"other":1})
            for name in ("objects-map.png","density-map.png","groups-map.png"):
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
