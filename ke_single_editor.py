"""One DaEditor process for all selected maps; no per-map editor relaunches.

The dataset is generated directly into the raw staging images directory.
The technical run contains a single editor workspace, capture scripts, plans,
logs and map overviews, but never duplicate RGB frames or COCO files.
"""
import hashlib
import json
import random
import re
import subprocess
from pathlib import Path

from ke_generate import validate_config
from tools.ke_maps import scan_maps, available_whitelist, plan_equal_allocation, parse_tank_bounds_log
from tools.ke_coco import group_for, scene_metadata
from tools.ke_png_fast import validate_rgb_png
from tools.ke_annotation import parse_annotation
from tools.ke_frame_journal import read_journal
from kolobanoveye import (
    BIN, GRP, inspect, choose_resources, parse_resources, require_file,
    dagor_path, run_logged, numbers, plan_articulation, plan_camera_orbits,
    plan_map_compositions, plan_compositions, select_camera_attempt,
    read_articulation, verify_png, editor_failure,
)


def map_seed(base, map_id):
    digest = hashlib.sha256(f"KolobanovEye2:{base}:{map_id.casefold()}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


def jobs_for(config, discovered):
    cfg = validate_config(config)
    selected = cfg["map_ids"] or [cfg["map_id"]]
    valid = available_whitelist(discovered, selected)
    if not valid or len(valid) != len(selected):
        raise ValueError("Выберите одну или более доступных avg-карт")
    return [(mid, n) for mid, n in plan_equal_allocation(cfg["count"], valid) if n]


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_capture(cfg, map_id, count, seed, run, raw, index, model, skeleton):
    """Write map-specific capture config; workspace and editor belong to the batch."""
    level = Path(cfg["game"]) / map_id
    frames_per = cfg["frames_per_position"]
    if cfg["placement_mode"] == "tank_map":
        candidates = plan_map_compositions(seed, cfg["map_margin_percent"],
            cfg["placement_attempts"], count, frames_per)
    elif cfg["placement_mode"] == "around_point":
        candidates = plan_compositions(seed, (cfg["tank_x"], cfg["tank_z"]),
            cfg["placement_radius"], cfg["placement_attempts"], count, frames_per)
    else:
        raise ValueError("Для единого запуска поддерживаются tank_map и around_point")
    orbits = plan_camera_orbits(seed, count * cfg["camera_attempts"],
        (cfg["distance_min"], cfg["distance_max"]),
        (cfg["elevation_min"], cfg["elevation_max"]))
    hrng = random.Random(f"camera-ground-height:{seed}")
    for camera in orbits:
        camera["height_above_ground"] = round(hrng.uniform(
            cfg["camera_height_min"], cfg["camera_height_max"]), 5)
    cameras = [orbits[i * cfg["camera_attempts"]:(i + 1) * cfg["camera_attempts"]] for i in range(count)]
    poses = plan_articulation(seed, count,
        (cfg["turret_yaw_min"], cfg["turret_yaw_max"]),
        (cfg["gun_pitch_min"], cfg["gun_pitch_max"]))
    stem = Path(map_id).stem
    capture = run / "capture" / f"{index:03d}-{stem}.blk"
    journal = run / "journals" / f"{index:03d}-{stem}.jsonl"
    outputs = [raw / "images" / f"{index:03d}-{i:06d}.png" for i in range(count)]
    lines = [
        f'level:t="{dagor_path(level)}"', f'map_id:t="{map_id}"',
        f'model:t="{model}"', f'expected_grp:t="{GRP.name}"', f'skeleton:t="{skeleton}"',
        f'output:t="{dagor_path(outputs[0])}"', f'metadata:t="{dagor_path(journal)}"',
        f'width:i={cfg["width"]}', f'height:i={cfg["height"]}', f'fov:r={numbers([cfg["fov"]])}',
        f'camera_clearance:r={numbers([cfg["camera_clearance"]])}',
        f'max_height_range:r={numbers([cfg["max_height_range"]])}',
        f'minimum_ground_y:r={numbers([cfg["minimum_ground_y"]])}',
        f'candidate_count:i={len(candidates[0])}',
        "placement_whole_map:b=no",
        f'placement_tank_map:b={"yes" if cfg["placement_mode"] == "tank_map" else "no"}',
        f'frames_per_position:i={frames_per}',
        f'min_warmup_renders:i={cfg["min_warmup_renders"]}',
        f'wait_sky_convergence:b={"yes" if cfg["wait_sky_convergence"] else "no"}',
        f'wait_frame_stability:b={"yes" if cfg["wait_frame_stability"] else "no"}',
        f'yaw:r={numbers([cfg["tank_yaw"]])}',
        f'tank:p3={numbers([cfg["tank_x"], cfg["ground_offset"], cfg["tank_z"]])}',
        f"frame_count:i={count}", f'camera_attempts:i={cfg["camera_attempts"]}',
        f'metadata_journal:t="{dagor_path(journal)}"',
    ]
    if poses:
        lines.append("articulate_tank:b=yes")
        lines.extend(f"articulation_{i}:p2={numbers(pose)}" for i, pose in enumerate(poses))
    for scene_idx, points in enumerate(candidates):
        for idx, (x, z) in enumerate(points):
            lines.append(f"candidate_{scene_idx}_{idx}:p3={numbers((x, cfg['ground_offset'], z))}")
    for i, options in enumerate(cameras):
        lines.append(f'output_{i}:t="{dagor_path(outputs[i])}"')
        for j, cam in enumerate(options):
            lines.append(f"camera_offset_{i}_{j}:p3={numbers(cam['offset'])}")
            lines.append(f"camera_height_{i}_{j}:r={numbers([cam['height_above_ground']])}")
        if cfg["render_debug"]:
            debug = run / "render-debug" / f"{index:03d}-{stem}" / f"{i:06d}"
            debug.mkdir(parents=True, exist_ok=True)
            lines.append(f'render_debug_{i}:t="{dagor_path(debug)}"')
    capture.write_text("\n".join(lines) + "\n", encoding="ascii")
    write_json(run / "plans" / f"{index:03d}-{stem}.json", {
        "map_id": map_id, "seed": seed, "cameras": cameras,
        "placement_candidates_xz": candidates, "tank_articulation_deg": poses,
        "frames_per_position": frames_per, "placement_mode": cfg["placement_mode"],
    })
    return {"map_id": map_id, "count": count, "seed": seed, "index": index, "capture": capture,
            "journal": journal, "outputs": outputs, "cameras": cameras, "poses": poses,
            "stem": stem, "frames_per_position": frames_per}


def make_command(cfg, run, tasks):
    """Exactly one DaEditor invocation for the complete map queue."""
    dagor, game = Path(cfg["dagor"]).resolve(), Path(cfg["game"]).resolve()
    cdk = (game / "WarThunderCDK").resolve()
    editor = require_file(dagor / BIN / "daEditor3x-dev.exe")
    local = editor.parent.parent / ".local"
    local.mkdir(exist_ok=True)
    workspace = local / "workspace.blk"
    if not workspace.is_file():
        workspace.write_text("// Workspaces\n", encoding="ascii")
    name = "KolobanovEye-" + hashlib.sha256(dagor_path(cdk).lower().encode()).hexdigest()[:12]
    if name.encode() not in workspace.read_bytes():
        with workspace.open("ab") as stream:
            stream.write((f'\nworkspace {{\n name:t="{name}"\n application_dir:t="{dagor_path(cdk)}/"\n'
                          f' application_path:t="{dagor_path(cdk / "application.blk")}"\n}}\n').encode("ascii"))
    script = run / "probe.dcmd"
    commands = ["wait.frame 8"]
    for task in tasks:
        commands.append(f'kolobanoveye.frame {dagor_path(task["capture"])}')
        commands.append("wait.frame 8")
    if not cfg["keep_open"]:
        commands.append("exit no")
    script.write_text("\n".join(commands) + "\n", encoding="ascii")
    args = [editor, "-quiet", "-drv:dx11", "-no_src_assets", "-no_dabuild",
        "-async_batch", "-kolobanoveye-probe", f"-rootdir:{dagor_path(run)}",
        f"-addpath:{dagor_path(game)}", f"-addpath:{dagor_path(cdk)}",
        "-enable:Scene view", "-disable:Landscape", r"-exclude_dll_re:heightmapLand-aces-dev\.dll$",
        f"-ws:{name}", run / "probe.level.blk", script]
    if cfg["keep_open"]:
        args.remove("-quiet")
    args.insert(-2, "-ke-clipmap-legacy-color")
    if cfg["clipmap_feedback"] == "software":
        args.insert(-2, "-ke-clipmap-software")
    if cfg["capture_tank_map"]:
        args.insert(-2, f'-ke-tank-map-output-dir:{dagor_path(run / "maps")}')
        args.insert(-2, f'-ke-tank-map-size:{cfg["tank_map_resolution"]}')
    if cfg["render_debug"]:
        args.insert(-2, f'-ke-render-debug:{dagor_path(run / "render-debug")}')
    return args


