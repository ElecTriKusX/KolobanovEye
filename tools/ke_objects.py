"""Stage 2.7 diagnostic: inventory actual loaded game-scene RendInst objects.

Separate from dataset/raw. No tank or training image is generated.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import os
import re
from pathlib import Path
import subprocess
import uuid

from ke_generate import DEFAULTS
from ke_single_editor import make_command, read_editor_log, explain_batch_failure
from kolobanoveye import dagor_path, editor_failure
from tools.ke_maps import scan_maps, available_whitelist
from tools.ke_pixel_debug import load_png
from tools.ke_review import write_rgb_png
from tools.ke_object_layers import build_index, LAYERS, COLORS as LAYER_COLORS

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
    return build_index(bounds, objects, cell)


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
    def px(x):
        return max(0,min(w,round((x-xmin)/(xmax-xmin)*w)))
    def py(z):
        return max(0,min(h,round((zmax-z)/(zmax-zmin)*h)))
    def paint(rgb,x0,y0,x1,y1,color,alpha):
        if x0>=x1 or y0>=y1:return
        lut=[bytes(round(v*(1-alpha)+c*alpha) for v in range(256)) for c in color]
        for y in range(y0,y1):
            a,b=(y*w+x0)*3,(y*w+x1)*3
            for k in range(3):
                rgb[a+k:b+k:3]=rgb[a+k:b+k:3].translate(lut[k])
    obj_image=bytearray(base)
    for o in objects:
        layer=o['analysis_layer']
        if layer not in LAYERS or o['duplicate_of'] is not None:continue
        x,z=o['position'][0],o['position'][2]
        ix,iy=min(w-1,px(x)),min(h-1,py(z))
        obj_image[(iy*w+ix)*3:(iy*w+ix)*3+3]=bytes(LAYER_COLORS[layer])
    write_rgb_png(out/"objects-map.png",w,h,obj_image)
    # Each layer uses the same absolute 0..1 projected area proxy on all maps.
    specs={"density-map.png":("combined",(245,112,48)),
           "large-obstacles-map.png":("large",(232,149,57)),
           "vegetation-map.png":("vegetation",LAYER_COLORS["vegetation"]),
           "small-obstacles-map.png":("small",LAYER_COLORS["small"]),
           "unknown-obstacles-map.png":("unknown",LAYER_COLORS["unknown"]),
           "low-density-map.png":("low",(151,203,139))}
    images={name:bytearray(base) for name in specs}
    groups=bytearray(base)
    cell=grid['cell_size_m']
    cells={(c['x'],c['z']):c for c in grid['cells']}
    peak=max((len(c['center_ids']) for c in grid['cells']),default=0)
    for z in range(grid['nz']):
        for x in range(grid['nx']):
            c=cells.get((x,z),{})
            sc=c.get('layer_scores',{})
            density=c.get('density',0.)
            box=(px(xmin+x*cell),py(min(zmax,zmin+(z+1)*cell)),
                 px(min(xmax,xmin+(x+1)*cell)),py(zmin+z*cell))
            values={**sc,"combined":density,"large":max(sc.get('building',0.),sc.get('rock',0.)),
                    "low":1.-density if density<=.1 else 0.}
            for name,(key,color) in specs.items():
                value=values.get(key,0.)
                if value>0:
                    paint(images[name],*box,color,.12+.78*math.sqrt(value))
            memberships=c.get('groups_by_layer',{})
            if memberships:
                layer=max(memberships,key=lambda k:(sc.get(k,0.),k))
                gid=memberships[layer]
                # Same layer hue with stable per-group brightness variation.
                factor=.65+.35*((gid*37)%11)/10
                color=tuple(round(v*factor) for v in LAYER_COLORS[layer])
                paint(groups,*box,color,.8)
    for name,image in images.items():
        write_rgb_png(out/name,w,h,image)
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
            "category_colors":LAYER_COLORS,"category_method":"resource_name_and_render_AABB_dimensions_v2",
            "analysis_version":2,"analysis_layer_counts":grid["layer_counts"],
            "duplicate_records":grid["duplicate_records"],"eligible_unique":grid["eligible_unique"],
            "groups_by_layer":dict(Counter(g["layer"] for g in grid["groups"])),
            "largest_group_objects":max((len(g["object_ids"]) for g in grid["groups"]),default=0),
            "density_method":"max_over_layers_of_capped_AABB_subcell_max_area_fraction_absolute_0_1",
            "group_method":"4_connected_semantic_root_cells_bounded_sectors",
            "sector_size_m":grid["sector_size_m"],
            "low_density_threshold":.1,"low_density_is_not_validated_placement":True,
            "raw_index_includes_all_objects":True,
            "limitations":["render_AABB_proxy_not_mesh_or_collision", "semantic_hints_not_asset_metadata",
                           "groups_are_local_sampling_regions_not_city_boundaries"]}
    write_json(out/"objects-report.json",report)
    print(f'[KE] OBJECT_REPORT {out.name} objects={len(objects)} groups={len(grid["groups"])} background={background}',flush=True)
    return report


def read_scan_log(run):
    """Prefer the actual per-process native debug to the legacy batch log.

    A new run directory belongs to exactly one editor process. An empty or
    incomplete legacy log must not hide native progress or fatal assertions.
    Unlike the normal capture reader, preserve an empty native marker stream
    so that the error handler can still report a pre-command crash.
    """
    debug = list((run / ".logs").glob("daEditor3x-dev.exe-*/debug"))
    if debug:
        source = max(debug, key=lambda p: p.stat().st_mtime_ns)
        messages = []
        with source.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                match = re.search(r"\bCON: (\[KE\] .*)$", line.rstrip("\r\n"))
                if match:
                    messages.append(match.group(1))
        return "\n".join(messages), source
    legacy = run / "probe.dcmd.log"
    if legacy.is_file():
        return read_editor_log(run)
    return "", run / ".logs"


def check_scan_completion(run, code, expected):
    log, source = read_scan_log(run)
    lines = log.splitlines()
    scans = [line for line in lines if line.startswith("[KE] OBJECT_SCAN_OK ")]
    batches = [line for line in lines if line.startswith("[KE] MAP_BATCH_OK ")]
    failure = next((line for line in lines if "[KE] FAIL " in line), None)
    if code == 0 and not failure and batches == [f"[KE] MAP_BATCH_OK maps={expected}"] and len(scans) == expected:
        return log
    detail = (f"DaEditor exit={code} (0x{code & 0xffffffff:08X}); "
              f"object scans={len(scans)}/{expected}; "
              f"batch markers={batches}; last KE={lines[-1] if lines else 'none'}")
    if not any("OBJECT_SCAN_BEGIN" in line for line in lines) and any("MODEL_BEGIN" in line or "FRAME_OK" in line for line in lines):
        detail += "; object diagnostic not entered: check that the rebuilt DaEditor supports object_inventory"
    if failure:
        detail += "; " + explain_batch_failure(run, log, source)
    else:
        detail += f"; debug: {source}"
    raise ValueError(editor_failure(run, detail))


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
    report = {"mode":"objects_only","status":"RUNNING","selected_maps":mids,
              "editor_processes":1,"maps":{},"command":[str(arg) for arg in cmd]}
    write_json(out/"manifest.json", report)
    try:
        if executor is None:
            with (out/"host-stdout.log").open("wb") as host:
                code=subprocess.run(cmd,cwd=out,stdout=host,stderr=subprocess.STDOUT,timeout=timeout,
                                    env={**os.environ,"KE_OOZ_FAILURE_DIR":str(out/"ooz-failures")}).returncode
        else:
            code=executor(cmd)
        report["editor_exit_code"] = code
        check_scan_completion(out, code, len(mids))
        for mid in mids:
            folder=out/Path(mid).stem
            report["maps"][mid]=analyze_map(folder,out/"maps"/(Path(mid).name+"-tank.png"),resolution)
        report["status"] = "COMPLETE"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report["status"] = "FAILED"
        report["error"] = str(exc)
        log, source = read_scan_log(out)
        report["debug_log"] = str(source)
        report["last_native_messages"] = log.splitlines()[-20:]
        write_json(out/"manifest.json", report)
        print(f"[KE] OBJECT_ANALYSIS_FAILED preserved={out}",flush=True)
        raise
    write_json(out/"manifest.json",report)
    print(f"[KE] OBJECT_ANALYSIS_OK {out}",flush=True)
    return out


def recover_run(run, resolution=1024):
    """Build reports from completed native scans without starting the editor.

    Explicit offline recovery only: an editor exit failure remains a failure.
    Require one completed scan per capture, matching map order, tile/object
    counts and complete native TSV footers. Never label teardown as successful.
    """
    run = Path(run).resolve()
    captures = sorted((run / "capture").glob("*.blk"))
    mids = []
    for capture in captures:
        match = re.search(r'^map_id:t="(levels/avg_[a-zA-Z0-9_-]+\.bin)"$', capture.read_text(encoding="ascii"), re.M)
        if not match:
            raise ValueError(f"Invalid capture map id: {capture}")
        mids.append(match.group(1))
    if not mids or len(set(mids)) != len(mids):
        raise ValueError("Missing or duplicate recovery maps")
    log, source = read_scan_log(run)
    lines = log.splitlines()
    begins = [i for i,line in enumerate(lines) if line.startswith("[KE] MAP_BEGIN ")]
    if (len(begins) != len(mids) or
            [line for line in lines if line.startswith("[KE] MAP_BATCH_OK ")] != [f"[KE] MAP_BATCH_OK maps={len(mids)}"] or
            any("[KE] FAIL " in line for line in lines)):
        raise ValueError("Recovery requires a completed native map batch")
    for i,mid in enumerate(mids):
        block = lines[begins[i]:begins[i+1] if i+1<len(begins) else len(lines)]
        if not block[0].replace("\\","/").endswith("/"+mid):
            raise ValueError("Recovery map order mismatch")
        matches = [re.fullmatch(r"\[KE\] OBJECT_SCAN_OK tiles=(\d+) objects=(\d+) invalid=(\d+)", line) for line in block]
        matches = [match for match in matches if match]
        if len(matches)!=1:
            raise ValueError(f"Missing or duplicate scan success: {mid}")
        folder = run/Path(mid).stem
        _, objects, tiles, invalid = read_inventory(folder/"objects-native.tsv")
        if tuple(map(int,matches[0].groups())) != (len(tiles),len(objects),invalid):
            raise ValueError(f"Inventory/log count mismatch: {mid}")
        overview = run/"maps"/(Path(mid).name+"-tank.png")
        if load_png(overview)[:2] != (resolution,resolution):
            raise ValueError(f"Recovery overview resolution mismatch: {mid}")
    reports = {}
    for mid in mids:
        reports[mid] = analyze_map(run/Path(mid).stem,run/"maps"/(Path(mid).name+"-tank.png"),resolution)
    report = {"mode":"objects_only","status":"RECOVERED_OFFLINE","native_shutdown":"not_certified",
              "source_debug":str(source),"maps":reports,"editor_processes_started":0}
    # Keep the original failed manifest and raw inventory for investigation.
    write_json(run/"recovery-manifest.json",report)
    print(f"[KE] OBJECT_ANALYSIS_RECOVERED {run}",flush=True)
    return run


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recover-run",type=Path)
    parser.add_argument("--dagor")
    parser.add_argument("--game")
    parser.add_argument("--output")
    parser.add_argument("--map",dest="maps",action="append")
    parser.add_argument("--resolution",type=int,default=1024)
    parser.add_argument("--timeout",type=int,default=1800)
    args=parser.parse_args()
    try:
        if args.recover_run:
            if any((args.dagor,args.game,args.output,args.maps)):
                parser.error("--recover-run must be used without native scan arguments")
            recover_run(args.recover_run,args.resolution)
        else:
            if not all((args.dagor,args.game,args.output,args.maps)):
                parser.error("Native scan requires --dagor, --game, --output and --map")
            run_scan(args.dagor,args.game,args.output,args.maps,args.resolution,args.timeout)
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        parser.exit(1,f"[KE] ERROR OBJECT_ANALYSIS: {exc}\n")


if __name__=="__main__":
    main()
