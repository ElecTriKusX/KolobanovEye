"""Stage 2.7 diagnostic: inventory actual loaded game-scene RendInst objects.

Separate from dataset/raw. No tank or training image is generated.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import uuid

from ke_generate import DEFAULTS
from ke_single_editor import make_command, read_editor_log, explain_batch_failure
from kolobanoveye import dagor_path
from tools.ke_maps import scan_maps, available_whitelist
from tools.ke_pixel_debug import load_png
from tools.ke_review import write_rgb_png

CELL = 16.0
COLORS = {"building": (232, 149, 57), "vegetation": (63, 173, 105), "other": (87, 159, 229)}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def category(name):
    # For diagnostics only. Never use these guessed names as collision truth.
    n = name.casefold()
    if any(x in n for x in ("tree", "bush", "forest", "pine", "birch", "oak", "spruce", "palm", "shrub", "vegetation")):
        return "vegetation"
    if any(x in n for x in ("house", "building", "hangar", "factory", "barn", "barrack", "warehouse", "church", "hut")):
        return "building"
    return "other"


def read_inventory(path):
    objects, tiles, counts = [], {}, Counter()
    with path.open("r", encoding="ascii") as stream:
        head = stream.readline().rstrip("\r\n").split("\t")
        if len(head) != 7 or head[:2] != ["KEOBJECTS", "1"]:
            raise ValueError("Invalid object inventory header")
        bounds = tuple(map(float, head[2:6]))
        xmin, xmax, zmin, zmax = bounds
        n = int(head[6])
        if not all(map(math.isfinite, bounds)) or not (xmin < xmax and zmin < zmax and 1 <= n <= 128):
            raise ValueError("Invalid inventory bounds")
        if abs((xmax-xmin)-(zmax-zmin)) > .05 or n != math.ceil((xmax-xmin)/128 - 1e-6) and abs((xmax-xmin)-128*n) > .05:
            raise ValueError("Inventory square/tiles mismatch")
        end = None
        tile_start = 0
        for line in stream:
            parts = line.rstrip("\r\n").split("\t")
            if end is not None:
                raise ValueError("Data after inventory footer")
            if parts[0] == "O" and len(parts) == 15:
                kind, layer, pool = parts[1], int(parts[2]), int(parts[3])
                coords = list(map(float, parts[4:13]))
                root, lo, hi = coords[:3], coords[3:6], coords[6:9]
                if kind not in ("ri_tm", "ri_pos", "ri_extra") or min(layer,pool) < 0 or not all(map(math.isfinite, coords)):
                    raise ValueError("Invalid object record")
                if not (xmin-.001 <= root[0] <= xmax+.001 and zmin-.001 <= root[2] <= zmax+.001) or any(x > y for x,y in zip(lo,hi)):
                    raise ValueError("Object outside inventory bounds or invalid bbox")
                if parts[13] not in ("0","1"):
                    raise ValueError("Invalid collision flag")
                name = bytes.fromhex(parts[14]).decode("utf-8", errors="replace")
                item = {"id": len(objects)+1, "source": kind, "layer": layer, "pool": pool,
                        "resource": name, "category_hint": category(name), "position": root,
                        "bbox_min": lo, "bbox_max": hi, "has_collision_resource": parts[13]=="1"}
                objects.append(item)
                counts[kind] += 1
                if len(objects) > 1000000:
                    raise ValueError("Object inventory exceeds cap")
            elif parts[0] == "T" and len(parts) == 5:
                x,z,count,loaded = map(int, parts[1:])
                if not (0 <= x < n and 0 <= z < n and loaded >= 0 and count == len(objects)-tile_start) or (x,z) in tiles:
                    raise ValueError("Invalid tile record")
                x0,x1 = xmin+(xmax-xmin)*x/n,xmin+(xmax-xmin)*(x+1)/n
                z0,z1 = zmin+(zmax-zmin)*z/n,zmin+(zmax-zmin)*(z+1)/n
                if any(not (x0-.002 <= o["position"][0] <= x1+.002 and z0-.002 <= o["position"][2] <= z1+.002)
                       for o in objects[tile_start:]):
                    raise ValueError("Object root outside owning tile")
                tiles[x,z] = {"x":x,"z":z,"objects":count,"loaded_ri":loaded}
                tile_start = len(objects)
            elif parts[0] == "END" and len(parts) == 4:
                end = tuple(map(int,parts[1:]))
            else:
                raise ValueError("Invalid or truncated object inventory record")
    if end is None or end[:2] != (n*n,len(objects)) or end[2] < 0 or len(tiles) != n*n or tile_start != len(objects):
        raise ValueError("Incomplete object inventory")
    return bounds, objects, list(tiles.values()), end[2]


def spatial_index(bounds, objects, cell=CELL):
    if not math.isfinite(cell) or cell <= 0:
        raise ValueError("Invalid cell size")
    xmin,xmax,zmin,zmax = bounds
    nx,nz = math.ceil((xmax-xmin)/cell), math.ceil((zmax-zmin)/cell)
    if nx*nz > 1048576:
        raise ValueError("Spatial grid exceeds limit")
    def index(x,z):
        return max(0,min(nx-1,int((x-xmin)//cell))), max(0,min(nz-1,int((z-zmin)//cell)))
    centers, footprints = defaultdict(list), defaultdict(list)
    for o in objects:
        centers[index(o["position"][0],o["position"][2])].append(o["id"])
        lo,hi = o["bbox_min"],o["bbox_max"]
        if hi[0] < xmin or lo[0] > xmax or hi[2] < zmin or lo[2] > zmax:
            continue
        x0,z0 = index(lo[0],lo[2]); x1,z1 = index(hi[0],hi[2])
        for z in range(z0,z1+1):
            for x in range(x0,x1+1):
                footprints[x,z].append(o["id"])
    # Connected components on occupied AABB cells (8-neighbor). No semantic
    # city/forest claim; a bridge of adjacent cells may connect large regions.
    remaining = set(footprints)
    groups, group_by_cell = [], {}
    for seed in sorted(remaining, key=lambda p:(p[1],p[0])):
        if seed not in remaining:
            continue
        remaining.remove(seed)
        stack, members, ids = [seed], [], set()
        while stack:
            x,z = stack.pop()
            members.append((x,z))
            ids.update(footprints[x,z])
            for dx,dz in ((-1,-1),(0,-1),(1,-1),(-1,0),(1,0),(-1,1),(0,1),(1,1)):
                neighbor = x+dx,z+dz
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    stack.append(neighbor)
        gid = len(groups)+1
        for p in members:
            group_by_cell[p] = gid
        groups.append({"id":gid, "cells":len(members), "object_ids":sorted(ids),
                       "cell_bounds":[min(x for x,z in members), max(x for x,z in members),
                                      min(z for x,z in members), max(z for x,z in members)]})
    cells = [{"x":x,"z":z,"center_ids":centers.get((x,z),[]),
              "bbox_ids":footprints.get((x,z),[]),"group":group_by_cell.get((x,z))}
             for x,z in sorted(set(centers)|set(footprints),key=lambda p:(p[1],p[0]))]
    return {"cell_size_m":cell,"nx":nx,"nz":nz,"bounds":bounds,"cells":cells,"groups":groups}


def render_maps(out, bounds, objects, grid, overview, resolution):
    if overview.is_file():
        w,h,base = load_png(overview)
        if w!=resolution or h!=resolution:
            raise ValueError("Overview resolution mismatch")
        background = "dagor_tank_square"
    else:
        w=h=resolution
        base = bytearray(bytes((35,44,50))*w*h)
        background = "plain_background_overview_missing"
    xmin,xmax,zmin,zmax=bounds
    def pix(x,z):
        return (max(0,min(w-1,int((x-xmin)/(xmax-xmin)*w))),
                max(0,min(h-1,int((zmax-z)/(zmax-zmin)*h))))
    def paint(rgb,x0,y0,x1,y1,color,alpha=.65):
        for y in range(y0,y1+1):
            for x in range(x0,x1+1):
                i=(y*w+x)*3
                rgb[i:i+3]=bytes(int(rgb[i+k]*(1-alpha)+color[k]*alpha) for k in range(3))
    obj_image=bytearray(base)
    # Draw clipped AABB footprint and a center pixel, not a filled exact mesh.
    for o in objects:
        lo,hi=o["bbox_min"],o["bbox_max"]
        if hi[0]>=xmin and lo[0]<=xmax and hi[2]>=zmin and lo[2]<=zmax:
            x0,y1=pix(lo[0],lo[2]); x1,y0=pix(hi[0],hi[2])
            color=COLORS[o["category_hint"]]
            paint(obj_image,x0,y0,x1,y1,color,.55)
        x,y=pix(o["position"][0],o["position"][2])
        obj_image[(y*w+x)*3:(y*w+x)*3+3]=bytes(COLORS[o["category_hint"]])
    write_rgb_png(out/"objects-map.png",w,h,obj_image)
    density,groups=bytearray(base),bytearray(base)
    peak=max((len(c["center_ids"]) for c in grid["cells"]),default=0)
    palette=((89,196,225),(238,169,65),(192,125,227),(85,207,132),(231,112,117),(215,212,92))
    cell=grid["cell_size_m"]
    for c in grid["cells"]:
        x,z=c["x"],c["z"]
        x0,y1=pix(xmin+x*cell,zmin+z*cell); x1,y0=pix(min(xmax,xmin+(x+1)*cell),min(zmax,zmin+(z+1)*cell))
        count=len(c["center_ids"])
        if count:
            t=math.log1p(count)/math.log1p(peak)
            paint(density,x0,y0,x1,y1,(255,int(210*(1-t)),int(90*(1-t))),.65)
        if c["group"]:
            paint(groups,x0,y0,x1,y1,palette[(c["group"]-1)%len(palette)],.60)
    write_rgb_png(out/"density-map.png",w,h,density)
    write_rgb_png(out/"groups-map.png",w,h,groups)
    return background,peak


def analyze_map(out, overview, resolution=1024):
    bounds,objects,tiles,invalid=read_inventory(out/"objects-native.tsv")
    grid=spatial_index(bounds,objects)
    with (out/"objects.jsonl").open("w",encoding="utf-8") as stream:
        for item in objects:
            stream.write(json.dumps(item,ensure_ascii=False)+"\n")
    write_json(out/"spatial-index.json",grid)
    background,peak=render_maps(out,bounds,objects,grid,overview,resolution)
    report={"status":"SCANNED_UNVERIFIED" if objects else "EMPTY_UNVERIFIED",
            "coverage":"loaded_rendinst_snapshot_per_tile_not_proven_complete",
            "static_scene_objects":"not_enumerated", "tile_size_max_m":128,
            "tile_count":len(tiles),"empty_tiles":sum(t["objects"]==0 for t in tiles),
            "tiles":tiles,"bounds":bounds,"objects":len(objects),"invalid_skipped":invalid,
            "by_source":dict(Counter(o["source"] for o in objects)),
            "by_category_hint":dict(Counter(o["category_hint"] for o in objects)),
            "collision_resource_count":sum(o["has_collision_resource"] for o in objects),
            "cell_size_m":CELL,"occupied_cells":len(grid["cells"]),"groups":len(grid["groups"]),
            "density_peak_objects_per_cell":peak,"background":background,
            "category_colors":COLORS,"category_method":"resource_name_heuristic",
            "density_method":"root_count_per_cell_log_color_scaled_per_map",
            "group_method":"8_connected_projected_render_AABB_cells"}
    write_json(out/"objects-report.json",report)
    print(f'[KE] OBJECT_REPORT {out.name} objects={len(objects)} groups={len(grid["groups"])} background={background}',flush=True)
    return report


def run_scan(dagor,game,output,selected,resolution=1024,timeout=1800,executor=None):
    if resolution not in (256,512,1024,2048,4096) or timeout <= 0:
        raise ValueError("Invalid resolution or timeout")
    mids=available_whitelist(scan_maps(game),selected)
    if not mids or len(mids)!=len(selected):
        raise ValueError("Select valid installed avg maps")
    out=Path(output).resolve()/"object-analysis"/(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8])
    out.mkdir(parents=True)
    for d in ("maps","capture","environment"):
        (out/d).mkdir()
    (out/"probe.level.blk").write_text("lastUniqueId:i=1\n",encoding="ascii")
    (out/"probe.level.local.blk").write_text('viewport0 { currentProjection:t="Perspective"; orthogonalProjection:b=no; wireframe:b=no }\nlocal_plugin_data { bin_scene_view { visible:b=no } }\n',encoding="ascii")
    (out/"environment/environment.plugin.blk").write_text('enviName:t="day"\nweatherType:t="clear"\n',encoding="ascii")
    cfg={**DEFAULTS,"dagor":str(Path(dagor).resolve()),"game":str(Path(game).resolve()),
         "keep_open":False,"capture_tank_map":True,"tank_map_resolution":resolution,"render_debug":False}
    tasks=[]
    for i,mid in enumerate(mids):
        folder=out/Path(mid).stem
        folder.mkdir()
        capture=out/"capture"/f"{i:03d}.blk"
        capture.write_text(f'level:t="{dagor_path(Path(game).resolve()/mid)}"\nmap_id:t="{mid}"\n'
                           'model:t="pzkpfw_VI_ausf_h1_tiger"\n'
                           f'output:t="{dagor_path(folder/"unused.png")}"\nmetadata:t="{dagor_path(folder/"unused.blk")}"\n'
                           f'object_inventory:t="{dagor_path(folder/"objects-native.tsv")}"\nframe_count:i=1\n',encoding="ascii")
        tasks.append({"capture":capture})
    print(f"[KE] OBJECT_ANALYSIS_START {out} maps={len(mids)}",flush=True)
    cmd=make_command(cfg,out,tasks)
    if executor is None:
        with (out/"host-stdout.log").open("wb") as host:
            code=subprocess.run(cmd,cwd=out,stdout=host,stderr=subprocess.STDOUT,timeout=timeout,
                                env={**os.environ,"KE_OOZ_FAILURE_DIR":str(out/"ooz-failures")}).returncode
    else:
        code=executor(cmd)
    log,source=read_editor_log(out)
    if code or f"[KE] MAP_BATCH_OK maps={len(mids)}" not in log or log.count("[KE] OBJECT_SCAN_OK ")!=len(mids):
        raise ValueError(explain_batch_failure(out,log,source))
    reports={}
    for mid in mids:
        folder=out/Path(mid).stem
        reports[mid]=analyze_map(folder,out/"maps"/(Path(mid).name+"-tank.png"),resolution)
    write_json(out/"manifest.json",{"mode":"objects_only","maps":reports,"editor_processes":1})
    print(f"[KE] OBJECT_ANALYSIS_OK {out}",flush=True)
    return out


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dagor",required=True)
    parser.add_argument("--game",required=True)
    parser.add_argument("--output",required=True)
    parser.add_argument("--map",dest="maps",action="append",required=True)
    parser.add_argument("--resolution",type=int,default=1024)
    parser.add_argument("--timeout",type=int,default=1800)
    args=parser.parse_args()
    try:
        run_scan(args.dagor,args.game,args.output,args.maps,args.resolution,args.timeout)
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        parser.exit(1,f"[KE] ERROR OBJECT_ANALYSIS: {exc}\n")


if __name__=="__main__":
    main()
