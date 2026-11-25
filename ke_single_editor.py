"""One DaEditor process for all selected maps; no per-map editor relaunches.

The dataset is generated directly into the raw staging images directory.
The technical run contains a single editor workspace, capture scripts, plans,
logs and map overviews, but never duplicate RGB frames or COCO files.
"""
import hashlib
import math
import json
import os
import random
import re
import subprocess
import shutil
from pathlib import Path

from ke_generate import validate_config
from tools.ke_maps import scan_maps, available_whitelist, plan_equal_allocation, parse_tank_bounds_log
from tools.ke_coco import group_for, scene_metadata
from tools.ke_png_fast import validate_rgb_png
from tools.ke_annotation import parse_annotation
from tools.ke_composition import plan_screen_positions
from tools.ke_visibility import read_capture_masks, mask_decoder_name, VISIBILITY_BINS
from tools.ke_occlusion import plan_occlusion_targets, read_occlusion_proxy, summarize_search
from tools.ke_visibility_quotas import candidate_allocation, visibility_targets, choose_candidates
from tools.ke_frame_journal import read_journal
from tools.ke_refill import plan_refill_round
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
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def write_capture(cfg, map_id, count, seed, run, raw, index, model, skeleton, first_image_id):
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
    if cfg.get('avf_probe_size', 0):
        from tools.ke_avf import plan_camera_field
        cameras = plan_camera_field(seed, count, cfg["camera_attempts"],
            (cfg["distance_min"], cfg["distance_max"]),
            (cfg["elevation_min"], cfg["elevation_max"]),
            (cfg["camera_height_min"], cfg["camera_height_max"]))
    else:
        orbits = plan_camera_orbits(seed, count * cfg["camera_attempts"],
            (cfg["distance_min"], cfg["distance_max"]),
            (cfg["elevation_min"], cfg["elevation_max"]))
        hrng = random.Random(f"camera-ground-height:{seed}")
        for camera in orbits:
            camera["height_above_ground"] = round(hrng.uniform(
                cfg["camera_height_min"], cfg["camera_height_max"]), 5)
        cameras = [orbits[i * cfg["camera_attempts"]:(i + 1) * cfg["camera_attempts"]] for i in range(count)]
    screen_positions = plan_screen_positions(seed, count, cfg['screen_position_weights'])
    occlusion_targets = (plan_occlusion_targets(seed, count, cfg['visibility_weights'])
                         if cfg['search_occlusion'] else [])
    poses = plan_articulation(seed, count,
        (cfg["turret_yaw_min"], cfg["turret_yaw_max"]),
        (cfg["gun_pitch_min"], cfg["gun_pitch_max"]))
    stem = Path(map_id).stem
    capture = run / "capture" / f"{index:03d}-{stem}.blk"
    journal = run / "journals" / f"{index:03d}-{stem}.jsonl"
    outputs = [raw / "images" / f"{first_image_id + i:08d}.png" for i in range(count)]
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
        f'capture_visibility_masks:b={"yes" if cfg["capture_visibility_masks"] else "no"}',
        f'search_occlusion:b={"yes" if cfg["search_occlusion"] else "no"}',
    ]
    if cfg.get('avf_probe_size', 0):
        lines.append(f'avf_probe_size:i={cfg["avf_probe_size"]}')
        lines.append(f'avf_roi:b={"yes" if cfg.get("avf_region", "roi") == "roi" else "no"}')
    if poses:
        lines.append("articulate_tank:b=yes")
        lines.extend(f"articulation_{i}:p2={numbers(pose)}" for i, pose in enumerate(poses))
    for scene_idx, points in enumerate(candidates):
        for idx, (x, z) in enumerate(points):
            lines.append(f"candidate_{scene_idx}_{idx}:p3={numbers((x, cfg['ground_offset'], z))}")
    for i, options in enumerate(cameras):
        lines.append(f'output_{i}:t="{dagor_path(outputs[i])}"')
        if cfg["capture_visibility_masks"]:
            for kind in ("amodal", "visible"):
                mask_path = raw / "masks" / kind / outputs[i].name
                lines.append(f'mask_{kind}_{i}:t="{dagor_path(mask_path)}"')
        lines.append(f'screen_offset_{i}:p2={numbers(screen_positions[i]["offset_uv"])}')
        lines.append(f'screen_bin_{i}:t="{screen_positions[i]["bin"]}"')
        if occlusion_targets:
            lines.append(f"occlusion_target_{i}:i={VISIBILITY_BINS.index(occlusion_targets[i])}")
        for j, cam in enumerate(options):
            lines.append(f"camera_offset_{i}_{j}:p3={numbers(cam['offset'])}")
            lines.append(f"camera_height_{i}_{j}:r={numbers([cam['height_above_ground']])}")
            if cfg.get('avf_probe_size', 0):
                lines.append(f"avf_ring_{i}_{j}:i={cam['avf_ring']}")
                lines.append(f"avf_angle_{i}_{j}:r={numbers([cam['azimuth_deg']])}")
        if cfg["render_debug"]:
            debug = run / "render-debug" / f"{index:03d}-{stem}" / f"{i:06d}"
            debug.mkdir(parents=True, exist_ok=True)
            lines.append(f'render_debug_{i}:t="{dagor_path(debug)}"')
    capture.write_text("\n".join(lines) + "\n", encoding="ascii")
    write_json(run / "plans" / f"{index:03d}-{stem}.json", {
        "map_id": map_id, "seed": seed, "cameras": cameras,
        "screen_positions": screen_positions, "occlusion_targets": occlusion_targets,
        "placement_candidates_xz": candidates, "tank_articulation_deg": poses,
        "frames_per_position": frames_per, "placement_mode": cfg["placement_mode"],
    })
    return {"map_id": map_id, "count": count, "seed": seed, "index": index, "capture": capture,
            "journal": journal, "outputs": outputs, "cameras": cameras,
            "screen_positions": screen_positions, "poses": poses, "occlusion_targets": occlusion_targets,
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
    if not 1 <= len(tasks) <= 256:
        raise ValueError("Native DaEditor map batch supports 1..256 maps")
    batch = run / "map-batch.blk"
    batch.write_text(
        f'map_count:i={len(tasks)}\n' +
        "".join(f'map_{index}:t="{dagor_path(task["capture"])}"\n'
                for index, task in enumerate(tasks)),
        encoding="ascii")
    script = run / "probe.dcmd"
    # The editor logs "ready for interaction" before invoking its async command queue.
    # Do not yield eight idle frames before processing the first map: a Windows
    # 0xC0000409 crash was observed during this initial wait, before MAP_BATCH_BEGIN.
    commands = [f"kolobanoveye.batch {dagor_path(batch)}"]
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


def read_editor_log(run: Path) -> tuple[str, Path]:
    """Read the actual async DaEditor console log, not a nonexistent .dcmd.log.

    Dagor ConBatch::startLog() counts console messages; it never saves a file.
    Async editor sessions emit those messages to .logs/<process>/debug with
    'CON: ' prefixes. Discard duplicate raw [KE] debug() records to avoid
    counting each MAP_BEGIN twice.
    """
    legacy = run / "probe.dcmd.log"
    if legacy.is_file():
        return legacy.read_text(encoding="utf-8", errors="replace"), legacy
    logs = list((run / ".logs").glob("daEditor3x-dev.exe-*/debug"))
    if not logs:
        raise ValueError(f"DaEditor не создал debug-журнал в {run / '.logs'}")
    current = max(logs, key=lambda p: p.stat().st_mtime_ns)
    raw = current.read_text(encoding="utf-8", errors="replace")
    messages = []
    for line in raw.splitlines():
        match = re.search(r"\bCON: (\[KE\] .*)$", line)
        if match:
            messages.append(match.group(1))
    if not messages:
        raise ValueError(f"DaEditor не выполнил ни одной команды KolobanovEye. Проверьте {current}")
    return "\n".join(messages), current


def explain_batch_failure(run: Path, log: str, source: Path) -> str:
    """Preserve the first actionable native error instead of File missing."""
    failed = next((line for line in log.splitlines() if "[KE] FAIL " in line), None)
    native_errors = []
    for filename in ("logerr", "debug"):
        path = source.parent / filename
        if path.is_file():
            native_errors.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
    errors = [
        line.strip() for line in native_errors if (
            "ooz: decode failed" in line.lower() or
            "Oodle read error" in line or
            "Land dump load error" in line or
            "[KE] VISIBILITY_FAIL" in line
        )
    ]
    unique = list(dict.fromkeys(errors))
    details = "; ".join(unique[:3])
    message = failed or "нет [KE] MAP_BATCH_OK (пакет не завершился)"
    if details:
        message += f"; native errors: {details}"
    # Decoder-stage telemetry is written to stderr by the standalone OOZ C++
    # code, not to CoolConsole's debug journal.
    host = run / "host-stdout.log"
    if host.is_file():
        telemetry = [line.strip() for line in host.read_text(
            encoding="utf-8", errors="replace").splitlines()
            if "[KE] OOZ_" in line and (
                "FAIL" in line or "MISMATCH" in line)]
        if telemetry:
            message += "; decoder stage: " + "; ".join(telemetry[-3:])
    failed = run / "ooz-failures" / "failed-ooz.bin"
    if failed.is_file():
        message += f"; OOZ_INPUT_CAPTURED={failed} (local file; do not publish proprietary game data)"
    return f"{message}; debug: {source}"


def read_impostor_quality(source: Path, count: int) -> list[dict]:
    """Associate native preshadow warnings with map tasks in the editor debug log.

    A skipped shadow-atlas slice is a quality degradation, not a normal render.
    Unlike console [KE] messages, Dagor logwarn() is a raw debug record.
    """
    found = [{"skipped_updates": 0, "assets": set()} for _ in range(count)]
    if source.name != "debug" or not source.is_file():
        return [{"skipped_updates": 0, "assets": []} for _ in range(count)]
    current = None
    for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.search(r"CON: \[KE\] MAP_TASK_BEGIN index=(\d+)/(\d+)", line)
        if match:
            pos = int(match.group(1)) - 1
            current = pos if 0 <= pos < count and int(match.group(2)) in (0, count) else None
        if current is not None and "[KE] IMPOSTOR_SHADOW_SLICE_SKIPPED " in line:
            found[current]["skipped_updates"] += 1
            asset = re.search(r"\basset=(\S+)", line)
            if asset:
                found[current]["assets"].add(asset.group(1))
    return [{"skipped_updates": x["skipped_updates"], "assets": sorted(x["assets"])} for x in found]


def extract_map_logs(log, tasks, *, stability=True):
    """Separate the single console journal into individual map passes.

    Never report a completed batch unless every map reports success.
    """
    starts = [match.start() for match in re.finditer(r"\[KE\] MAP_BEGIN\s+", log)]
    if len(starts) != len(tasks):
        raise ValueError(f"Ожидалось {len(tasks)} загрузок карт, найдено {len(starts)}")
    result = []
    for idx, task in enumerate(tasks):
        section = log[starts[idx]:(starts[idx + 1] if idx + 1 < len(starts) else len(log))]
        first = section.splitlines()[0]
        if Path(task["map_id"]).name.casefold() not in first.casefold():
            raise ValueError(f"Неверный порядок загрузки карты: {first}")
        if f'[KE] SEQUENCE_OK frames={task["count"]}' not in section:
            raise ValueError(f'Нет SEQUENCE_OK для {task["map_id"]}')
        rejected = task.get("rejected_indices", set())
        placement = task.get("placement_rejected_indices", set())
        unstable = task.get("stability_rejected_indices", set())
        native_unstable = [int(n) for n in re.findall(r"\[KE\] FRAME_REJECT index=(\d+) reason=frame_stability_timeout", section)]
        native_rejected = [int(n) for n in re.findall(r"\[KE\] FRAME_REJECT index=(\d+) reason=depth_pass_mismatch", section)]
        native_placement = [int(n) for n in re.findall(r"\[KE\] FRAME_REJECT index=(\d+) reason=heightmap_candidates_exhausted", section)]
        if (len(native_rejected) != len(rejected) or set(native_rejected) != rejected or
                len(native_placement) != len(placement) or set(native_placement) != placement or
                len(native_unstable) != len(unstable) or set(native_unstable) != unstable or
                rejected & placement or rejected & unstable or placement & unstable or
                section.count("[KE] FRAME_OK ") != task["count"] - len(rejected) - len(placement) - len(unstable)):
            raise ValueError(f'Не получены все кадры карты {task["map_id"]}')
        if stability and (section.count("[KE] FRAME_STABILITY_FINAL enabled=1 stable=1") != task["count"] - len(placement) - len(unstable) or
                          section.count("[KE] FRAME_STABILITY_FINAL enabled=1 stable=0") != len(unstable)):
            raise ValueError(f'Нет подтверждения стабильности {task["map_id"]}')
        markers = ("MODEL_OK", "WATER_GROUND_FILTER")
        if task["count"] > len(placement):
            markers += ("MAP_OK", "PLACEMENT_OK")
        for marker in markers:
            if f"[KE] {marker} " not in section:
                raise ValueError(f'Отсутствует {marker}: {task["map_id"]}')
        result.append(section)
    return result


def read_stability_rejection(cfg, raw, task, idx, blk):
    if not re.search(r"^stability_rejection:", blk, re.M):
        return None
    if not cfg["enforce_visibility_quotas"] or cfg["visibility_quota_mode"] != "online" or not cfg["wait_frame_stability"]:
        raise ValueError("Unexpected stability rejection outside online stability mode")
    def field(name, kind, pattern):
        hits = re.findall(rf"^{name}:{kind}=({pattern})\s*$", blk, re.M)
        if len(hits) != 1:
            raise ValueError(f"Invalid stability rejection field: {name}")
        return hits[0]
    if field("stability_rejection", "t", '"[^"\r\n]+"') != '"frame_stability_timeout"':
        raise ValueError("Unknown stability rejection")
    if field("map_id", "t", '"[^"\r\n]+"') != f'"{task["map_id"]}"':
        raise ValueError("Stability rejection map mismatch")
    if int(field("composition_index", "i", r"\d+")) != idx // task["frames_per_position"]:
        raise ValueError("Stability rejection composition mismatch")
    passes = int(field("warmup_passes", "i", r"\d+"))
    elapsed = int(field("warmup_elapsed_ms", "i", r"\d+"))
    checks = int(field("stable_checks", "i", r"\d+"))
    mean = float(field("stability_mean_delta", "r", r"[0-9.eE+-]+"))
    maximum = float(field("stability_max_delta", "r", r"[0-9.eE+-]+"))
    if (passes < cfg["min_warmup_renders"] or elapsed < 20000 or not 0 <= checks < 3 or
            not math.isfinite(mean) or not math.isfinite(maximum) or not 0 <= mean <= maximum <= 255 or
            field("pending_textures", "i", r"\d+") != "0" or
            field("resources_ready", "b", "yes|true") not in ("yes", "true")):
        raise ValueError("Stability rejection without settled resources and verified timeout")
    if re.search(r"^(placement_rejection|visibility_method|visibility_rejection):", blk, re.M):
        raise ValueError("Stability rejection contains conflicting metadata")
    image = task["outputs"][idx]
    if image.exists() or any((raw / "masks" / kind / image.name).exists() for kind in ("amodal", "visible")):
        raise ValueError("Stability rejection unexpectedly has published frame files")
    return {"image_id": int(image.stem), "map_id": task["map_id"], "frame_index": idx,
            "reason": "frame_stability_timeout", "warmup_passes": passes, "elapsed_ms": elapsed,
            "stable_checks": checks, "mean_delta": mean, "max_delta": maximum,
            "dagor_frame_metadata_blk": blk}


def read_placement_rejection(cfg, raw, task, idx, blk):
    if not re.search(r"^placement_rejection:", blk, re.M):
        return None
    if not cfg["enforce_visibility_quotas"] or cfg["visibility_quota_mode"] != "online":
        raise ValueError("Placement rejection outside online mode")
    if re.findall(r'^placement_rejection:t="([^"\r\n]+)"\s*$', blk, re.M) != ["heightmap_candidates_exhausted"]:
        raise ValueError("Unknown placement rejection")
    if re.findall(r'^map_id:t="([^"\r\n]+)"\s*$', blk, re.M) != [task["map_id"]]:
        raise ValueError("Placement rejection map mismatch")
    if re.findall(r"^placement_attempts:i=(\d+)\s*$", blk, re.M) != [str(cfg["placement_attempts"])]:
        raise ValueError("Placement rejection attempt count mismatch")
    if re.findall(r"^composition_index:i=(\d+)\s*$", blk, re.M) != [str(idx // task["frames_per_position"])]:
        raise ValueError("Placement rejection composition mismatch")
    if re.search(r"^(visibility_method|visibility_rejection):", blk, re.M):
        raise ValueError("Placement rejection unexpectedly contains mask metadata")
    image = task["outputs"][idx]
    if image.exists() or any((raw / "masks" / kind / image.name).exists() for kind in ("amodal", "visible")):
        raise ValueError("Placement rejection unexpectedly has rendered files")
    return {"image_id": int(image.stem), "map_id": task["map_id"], "frame_index": idx,
            "reason": "heightmap_candidates_exhausted", "placement_attempts": cfg["placement_attempts"],
            "dagor_frame_metadata_blk": blk}


def read_depth_rejection(cfg, raw, task, idx, blk):
    """Recognize only the explicit native mismatch, never a missing/invalid PNG."""
    if not re.search(r"^visibility_rejection:", blk, re.M):
        return None
    if not cfg["capture_visibility_masks"] or not cfg["enforce_visibility_quotas"] or cfg["visibility_quota_mode"] != "online":
        raise ValueError("Unexpected depth rejection outside online mode")
    if re.findall(r'^visibility_rejection:t="([^"\r\n]+)"\s*$', blk, re.M) != ["depth_pass_mismatch"]:
        raise ValueError("Unknown native visibility rejection")
    def integer(name):
        hits = re.findall(rf"^{name}:i=(\d+)\s*$", blk, re.M)
        if len(hits) != 1:
            raise ValueError(f"Invalid rejected-frame field: {name}")
        return int(hits[0])
    amodal = integer("rejected_amodal_pixels")
    visible = integer("rejected_visible_pixels")
    missing = integer("rejected_unmatched_pixels")
    if not (0 < amodal <= cfg["width"] * cfg["height"] and
            max(4, amodal // 20) < missing <= amodal - visible):
        raise ValueError("Invalid rejected-frame depth counts")
    if re.search(r"^visibility_method:", blk, re.M):
        raise ValueError("Rejected frame also claims a valid mask")
    image_path = task["outputs"][idx]
    verify_png(image_path, cfg["width"], cfg["height"])
    validate_rgb_png(image_path, cfg["width"], cfg["height"])
    level = re.findall(r'^level:t="([^"\r\n]+)"', blk, re.M)
    if len(level) != 1 or Path(level[0]).name.casefold() != Path(task["map_id"]).name.casefold():
        raise ValueError("Rejected-frame map mismatch")
    if any((raw / "masks" / kind / image_path.name).exists() for kind in ("amodal", "visible")):
        raise ValueError("Rejected frame unexpectedly has mask files")
    return {"image_id": int(image_path.stem), "image": f"images/{image_path.name}",
            "map_id": task["map_id"], "frame_index": idx, "reason": "depth_pass_mismatch",
            "amodal_pixels": amodal, "visible_pixels": visible, "unmatched_pixels": missing,
            "dagor_frame_metadata_blk": blk}


def collect_frame(cfg, run, raw, task, idx, blk):
    """Validate one completed frame; shared by fixed and online capture."""
    image_path = task["outputs"][idx]
    verify_png(image_path, cfg["width"], cfg["height"])
    validate_rgb_png(image_path, cfg["width"], cfg["height"])
    level_match = re.search(r'^level:t="([^"\r\n]+)"', blk, re.MULTILINE)
    if not level_match or Path(level_match.group(1)).name.casefold() != Path(task["map_id"]).name.casefold():
        raise ValueError(f'Несовпадение карты в кадре {idx}: {task["map_id"]}')
    annotation = parse_annotation(blk)["annotations"][0]
    attempt, camera = select_camera_attempt(blk, task["cameras"][idx])
    comp = idx // task["frames_per_position"]
    if not re.search(rf"^composition_index:i={comp}\s*$", blk, re.MULTILINE):
        raise ValueError("Неверный индекс расстановки техники")
    articulation = read_articulation(blk, task["poses"][idx]) if task["poses"] else {}
    position = task["screen_positions"][idx]
    screen_bin = re.search(r'^screen_bin_planned:t="([^"\r\n]+)"', blk, re.MULTILINE)
    if not screen_bin or screen_bin.group(1) != position["bin"]:
        raise ValueError("Native Dagor screen-composition metadata missing or mismatched")
    match_uv = re.search(r'^screen_offset_uv:p2=([^\r\n]+)', blk, re.MULTILINE)
    if not match_uv:
        raise ValueError("Native Dagor did not report the camera screen offset")
    actual_uv = [float(q.strip()) for q in match_uv.group(1).split(",")]
    if len(actual_uv) != 2 or any(abs(a - b) > 1e-4 for a, b in
                                  zip(actual_uv, position["offset_uv"])):
        raise ValueError("Native Dagor camera offset differs from the capture plan")
    visibility = (read_capture_masks(raw, image_path.name, blk, cfg["width"], cfg["height"])
                  if cfg["capture_visibility_masks"] else None)
    occlusion = (read_occlusion_proxy(blk, task["occlusion_targets"][idx], cfg["camera_attempts"], visibility,
                                      probe_size=cfg.get('avf_probe_size', 0))
                 if cfg["search_occlusion"] else None)
    image_id = int(image_path.stem)
    scene_id = f'{run.name}:{task["index"]}:{task["stem"]}:{comp}'
    group = group_for(blk, 256)
    record = {
        "image_id": image_id, "image": f"images/{image_path.name}",
        "map_id": task["map_id"], "scene_id": scene_id, "scene_group_id": group,
        "frame_index": idx, "composition_index": comp, "seed": task["seed"],
        "camera_attempt": attempt, "camera_azimuth_deg": camera["azimuth_deg"],
        "camera_horizontal_distance": camera["horizontal_distance"],
        "planned_camera_offset": camera["offset"],
        "screen_position_bin_planned": position["bin"],
        "screen_position_target_uv": position["target_uv"],
        "annotation": annotation, "visibility_measurement": visibility,
        "occlusion_search": occlusion,
        "dagor_frame_metadata_blk": blk,
        "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(), **articulation,
    }
    image = {
        "id": image_id, "file_name": record["image"],
        "width": cfg["width"], "height": cfg["height"], "map_id": task["map_id"],
        "scene_id": scene_id, "scene_group": group, "scene_group_id": group,
        "ke_scene": scene_metadata(blk),
        **({"ke_visibility": visibility} if visibility is not None else {}),
    }
    box = annotation["bbox_xywh"] if visibility is None else visibility["bbox_visible_xywh"]
    coco_annotation = None
    if box is not None:
        x, y, w, h = box
        coco_annotation = {
            "id": image_id, "image_id": image_id, "category_id": 1,
            "bbox": [x, y, w, h], "area": w * h, "iscrowd": 0,
            "bbox_kind": annotation["bbox_kind"] if visibility is None else "visible_opaque",
            "bbox_method": annotation["bbox_method"] if visibility is None else visibility["method"],
            **({"ke_visibility": visibility} if visibility is not None else {}),
        }
    return record, image, coco_annotation


def collect_candidates(cfg, run, raw, tasks, *, prevalidated=None, rejected=None, placement_rejected=None, stability_rejected=None):
    """Validate one native batch once; do not select or delete any files."""
    log, source = read_editor_log(run)
    if f'[KE] MAP_BATCH_OK maps={len(tasks)}' not in log:
        raise ValueError(explain_batch_failure(run, log, source))
    map_logs = extract_map_logs(log, tasks, stability=cfg["wait_frame_stability"])
    shadow_quality = read_impostor_quality(source, len(tasks))
    images, annotations, records, scenes, maps = [], [], [], {}, []
    expected = sum(task["count"] for task in tasks)
    decoder = mask_decoder_name() if cfg["capture_visibility_masks"] else "none"
    print(f"[KE] FINALIZE_BEGIN frames={expected} maps={len(tasks)} mask_decoder={decoder}", flush=True)
    for task, map_log, quality in zip(tasks, map_logs, shadow_quality):
        print(f'[KE] FINALIZE_MAP map={task["map_id"]} completed={len(images)}/{expected}', flush=True)
        journal = read_journal(require_file(task["journal"]))
        if set(journal) != set(range(task["count"])):
            raise ValueError(f'Неполный журнал кадров: {task["map_id"]}')
        bounds = parse_tank_bounds_log(map_log, task["map_id"])
        overview_name = f'{task["stem"]}.bin-tank.png'
        overview = run / "maps" / overview_name
        if cfg["capture_tank_map"]:
            if bounds["status"] != "available":
                raise ValueError(f'Нет координат танкового поля: {task["map_id"]}')
            verify_png(overview, cfg["tank_map_resolution"], cfg["tank_map_resolution"])
        maps.append({"map_id": task["map_id"], "frames": task["count"] - len(task.get("rejected_indices", ())) - len(task.get("placement_rejected_indices", ())) - len(task.get("stability_rejected_indices", ())), "tank_bounds": bounds,
                     "overview": f"maps/{overview_name}" if cfg["capture_tank_map"] else None,
                     "render_quality_flags": (["impostor_preshadow_incomplete"]
                                              if quality["skipped_updates"] else []),
                     "impostor_shadow_skipped_updates": quality["skipped_updates"],
                     "impostor_shadow_assets": quality["assets"]})
        if quality["skipped_updates"]:
            print(f'[KE] RENDER_QUALITY_WARNING map={task["map_id"]} '
                  f'impostor_shadow_skipped={quality["skipped_updates"]} '
                  f'assets={len(quality["assets"])}; visual review required', flush=True)
        for idx, image_path in enumerate(task["outputs"]):
            if idx in task.get("stability_rejected_indices", ()):
                if stability_rejected is None or int(image_path.stem) not in stability_rejected or stability_rejected[int(image_path.stem)] != read_stability_rejection(cfg, raw, task, idx, journal[idx]):
                    raise ValueError("Stability rejection journal changed after online validation")
                continue
            if idx in task.get("placement_rejected_indices", ()):
                if placement_rejected is None or int(image_path.stem) not in placement_rejected or placement_rejected[int(image_path.stem)] != read_placement_rejection(cfg, raw, task, idx, journal[idx]):
                    raise ValueError("Placement rejection journal changed after online validation")
                continue
            if idx in task.get("rejected_indices", ()):
                if rejected is None or int(image_path.stem) not in rejected or rejected[int(image_path.stem)] != read_depth_rejection(cfg, raw, task, idx, journal[idx]):
                    raise ValueError("Rejected-frame journal changed after online validation")
                continue
            if prevalidated is None:
                record, image, annotation = collect_frame(cfg, run, raw, task, idx, journal[idx])
            else:
                record, image, annotation = prevalidated[int(image_path.stem)]
                if record["dagor_frame_metadata_blk"] != journal[idx]:
                    raise ValueError("Frame journal changed after online validation")
            records.append(record)
            images.append(image)
            if annotation is not None:
                annotations.append(annotation)
            scene_id = record["scene_id"]
            scenes.setdefault(scene_id, {
                "scene_id": scene_id, "map_id": task["map_id"],
                "scene_group_id": record["scene_group_id"], "image_ids": [],
            })["image_ids"].append(record["image_id"])
            print(f"[KE] FINALIZE_FRAME {len(images)}/{expected} image={image_path.name}", flush=True)
    return records, images, annotations, scenes, maps


def finalize(cfg, run, raw, tasks):
    """Ordinary single batch: validate, then export."""
    pool = collect_candidates(cfg, run, raw, tasks)
    return export_candidates(cfg, run, raw, pool)


def export_candidates(cfg, run, raw, pool, *, selection=None, editor_processes=1):
    """Export once from the validated accumulated pool."""
    records, images, annotations, scenes, maps = pool
    occlusion_report = summarize_search(records) if cfg["search_occlusion"] else None
    if occlusion_report is not None:
        write_json(run / "occlusion-search.json", occlusion_report)
        print("[KE] OCCLUSION_SEARCH " + json.dumps(occlusion_report), flush=True)
    quota_report = None
    discarded = []
    if cfg["enforce_visibility_quotas"]:
        if selection is None:
            targets = visibility_targets(cfg["count"], cfg["visibility_weights"])
            map_targets = dict(plan_equal_allocation(cfg["count"], [m["map_id"] for m in maps]))
            selected, quota_report = choose_candidates(records, targets, map_targets)
        else:
            selected, quota_report = selection
        write_json(run / "visibility-quotas.json", quota_report)
        print("[KE] VISIBILITY_QUOTAS " + json.dumps(quota_report, ensure_ascii=False), flush=True)
        if not quota_report["complete"]:
            raise ValueError("Не набраны квоты видимости/карт: " + json.dumps(quota_report["deficit"]) +
                             "; дефицит карт: " + json.dumps(quota_report["deficit_by_map"]) +
                             f"; отчёт: {run / 'visibility-quotas.json'}")
        # Retain alternatives until the final metadata has been written.
        # IDs are stable across rounds; COCO IDs need not be contiguous.
        discarded = [r for r in records if r["image_id"] not in selected]
        records = [r for r in records if r["image_id"] in selected]
        images = [i for i in images if i["id"] in selected]
        annotations = [a for a in annotations if a["image_id"] in selected]
        for scene in scenes.values():
            scene["image_ids"] = [i for i in scene["image_ids"] if i in selected]
        scenes = {k: s for k, s in scenes.items() if s["image_ids"]}
        for m in maps:
            m["candidate_frames"] = m["frames"]
            m["frames"] = quota_report["selected_by_map"].get(m["map_id"], 0)
    (raw / "annotations").mkdir()
    (raw / "metadata").mkdir()
    write_json(raw / "annotations" / "instances.json", {
        "info": {"description": ("KolobanovEye raw, experimental opaque depth masks, unsplit"
                                  if cfg["capture_visibility_masks"] else
                                  "KolobanovEye raw, approximate projected 3D AABB, unsplit"),
                 "unsplit": True, "visibility_diagnostic": cfg["capture_visibility_masks"]},
        "images": images, "annotations": annotations, "categories": [{"id": 1, "name": "tank"}],
    })
    for name, rows in (("images.jsonl", records), ("scenes.jsonl", scenes.values())):
        with (raw / "metadata" / name).open("w", encoding="utf-8") as stream:
            for item in rows:
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")
    write_json(raw / "metadata" / "maps.json", maps)
    write_json(raw / "metadata" / "generation_config.json",
        {k: v for k, v in cfg.items() if k not in ("dagor", "game", "output")})
    overview = {
        "schema_version": 2, "kind": "raw", "split": None,
        "run_id": run.name, "images": len(images), "annotations": len(annotations),
        "scenes": len(scenes), "maps": len(maps), "editor_processes": editor_processes,
        "visibility_diagnostic": cfg["capture_visibility_masks"],
        "visibility_quotas": quota_report, "occlusion_search": occlusion_report,
        "visibility_bins": ({name: sum(r["visibility_measurement"]["visibility_bin"] == name for r in records)
                             for name in ("open", "light", "moderate", "heavy", "reject_too_occluded")}
                            if cfg["capture_visibility_masks"] else None),
        "render_quality": {
            "impostor_shadow_skipped_updates": sum(m["impostor_shadow_skipped_updates"] for m in maps),
            "maps_requiring_visual_review": sum(bool(m["render_quality_flags"]) for m in maps),
        },
    }
    write_json(raw / "manifest.json", overview)
    for record in discarded:
        (raw / record["image"]).unlink()
        for kind in ("amodal_mask", "visible_mask"):
            (raw / record["visibility_measurement"][kind]).unlink()
    print(f"[KE] FINALIZE_OK frames={len(images)} annotations={len(annotations)}", flush=True)
    return overview


def _generate_batch(config, run, raw, executor=None, *, planned_jobs=None,
                    first_image_id=1, round_index=0, target_weights=None):
    """One fixed native batch. A refill round only validates and returns its pool."""
    cfg = validate_config(config)
    game, dagor = Path(cfg["game"]).resolve(), Path(cfg["dagor"]).resolve()
    cdk = game / "WarThunderCDK"
    run, raw = Path(run).resolve(), Path(raw).resolve()
    for forbidden in (game, dagor):
        if (run == forbidden or run.is_relative_to(forbidden) or
                raw == forbidden or raw.is_relative_to(forbidden)):
            raise ValueError("Результат не может находиться внутри Dagor или War Thunder")
    target_jobs = jobs_for(cfg, scan_maps(game)) if planned_jobs is None else planned_jobs
    quotas = target_jobs
    if run.exists() or (raw.exists() and not round_index):
        raise ValueError("Рабочий каталог или исходный датасет уже существует")
    run.mkdir(parents=True)
    raw.mkdir(parents=True, exist_ok=bool(round_index))
    (raw / "images").mkdir(exist_ok=bool(round_index))
    if cfg["capture_visibility_masks"]:
        for kind in ("amodal", "visible"):
            (raw / "masks" / kind).mkdir(parents=True, exist_ok=bool(round_index))
    for name in ("capture", "plans", "maps", "journals"):
        (run / name).mkdir()
    write_json(run / "generation-config.json", cfg)
    report = {
        "schema_version": 2, "editor_processes": 1,
        "requested_frames": cfg["count"], "candidate_frames": sum(n for _, n in quotas), "complete": False,
        "maps": [{"map_id": mid, "planned": n, "actual": 0, "status": "pending"} for mid, n in quotas],
    }
    write_json(run / "manifest.json", report)
    try:
        if executor is None:
            inventory = inspect(game, cdk, Path(quotas[0][0]))
            write_json(run / "inventory.json", inventory)
            if inventory["errors"]:
                raise ValueError("Неправильные ресурсы, см. inventory.json")
            dump = require_file(dagor / BIN / "dumpGrp-dev.exe")
            run_logged([dump, game / GRP, "-dumpR"], run / "grp.txt", run, 60)
            resources = parse_resources((run / "grp.txt").read_text(encoding="utf-8", errors="replace"))
            model, skeleton = choose_resources(resources, GRP.stem, None, None)
        else:
            model, skeleton = "pzkpfw_VI_ausf_h1_tiger", "pzkpfw_VI_ausf_h1_tiger_skeleton"
        (run / "probe.level.blk").write_text("lastUniqueId:i=1\n", encoding="ascii")
        (run / "probe.level.local.blk").write_text(
            'viewport0 { currentProjection:t="Perspective"; orthogonalProjection:b=no; wireframe:b=no }\n'
            'local_plugin_data { bin_scene_view { visible:b=no } }\n', encoding="ascii")
        (run / "environment").mkdir()
        (run / "environment" / "environment.plugin.blk").write_text(
            'enviName:t="day"\nweatherType:t="clear"\n', encoding="ascii")
        tasks = []
        next_image_id = first_image_id
        for index, (map_id, count) in enumerate(quotas, 1):
            seed = map_seed(cfg["seed"] if round_index <= 1 else f'{cfg["seed"]}:refill:{round_index}', map_id)
            map_cfg = cfg if not target_weights else {**cfg, "visibility_weights": target_weights[map_id]}
            tasks.append(write_capture(map_cfg, map_id, count, seed,
                run, raw, index, model, skeleton, next_image_id))
            next_image_id += count
        cmd = make_command(cfg, run, tasks)
        if cfg["enforce_visibility_quotas"]:
            print(f'[KE] VISIBILITY_PLAN target={cfg["count"]} candidates={sum(n for _, n in quotas)} '
                  f'quotas={visibility_targets(cfg["count"], cfg["visibility_weights"])}', flush=True)
        print(f'[KE] SINGLE_EDITOR_START maps={len(tasks)} frames={sum(n for _, n in quotas)} instances=1', flush=True)
        if executor is not None:
            if (status := executor(cmd)):
                raise RuntimeError(f"DaEditor завершился с кодом {status}")
        else:
            # Capture only a rejected compressed Oodle stream, on this machine,
            # so the decoder can be tested without 90s DaEditor startup cycles.
            failure_dir = run / "ooz-failures"
            failure_dir.mkdir()
            editor_env = {**os.environ, "KE_OOZ_FAILURE_DIR": str(failure_dir)}
            try:
                run_logged(cmd, run / "host-stdout.log", run,
                           None if cfg["keep_open"] else cfg["timeout"] *
                           (sum(n for _, n in quotas) if round_index else len(tasks)),
                           env=editor_env)
            except (ValueError, subprocess.TimeoutExpired) as exc:
                raise ValueError(editor_failure(run, str(exc))) from exc
        report["render_complete"] = True
        report["staging"] = str(raw)
        write_json(run / "manifest.json", report)
        print(f'[KE] RENDER_COMPLETE staging={raw}; validating RGB, metadata and masks', flush=True)
        result = collect_candidates(cfg, run, raw, tasks) if round_index else finalize(cfg, run, raw, tasks)
        for item in report["maps"]:
            item.update(actual=item["planned"], status="verified")
        report["complete"] = True
        write_json(run / "manifest.json", report)
        if round_index:
            print(f'[KE] REFILL_BATCH_OK round={round_index} candidates={len(result[0])}', flush=True)
        else:
            print(f'[KE] SINGLE_EDITOR_OK frames={result["images"]} maps={result["maps"]} instances=1', flush=True)
        return result
    except Exception as exc:
        report["error"] = str(exc)
        for item in report["maps"]:
            if item["status"] == "pending":
                item["status"] = "failed"
        write_json(run / "manifest.json", report)
        raise




def generate(config, run, raw, executor=None):
    """Dispatch ordinary runs unchanged; strict quotas use Python round feedback."""
    cfg = validate_config(config)
    if not cfg["enforce_visibility_quotas"]:
        return _generate_batch(cfg, run, raw, executor)
    if cfg["visibility_quota_mode"] == "online":
        from tools.ke_online_capture import generate_online
        return generate_online(cfg, run, raw, executor=executor)
    run, raw = Path(run).resolve(), Path(raw).resolve()
    for forbidden in (Path(cfg["game"]).resolve(), Path(cfg["dagor"]).resolve()):
        if any(path == forbidden or path.is_relative_to(forbidden) for path in (run, raw)):
            raise ValueError("Результат не может находиться внутри Dagor или War Thunder")
    if run.exists() or raw.exists():
        raise ValueError("Рабочий каталог или исходный датасет уже существует")
    target_jobs = jobs_for(cfg, scan_maps(Path(cfg["game"])))
    if len(target_jobs) > 256:
        raise ValueError("Refill supports at most 256 selected maps")
    targets = visibility_targets(cfg["count"], cfg["visibility_weights"])
    map_targets = dict(target_jobs)
    budget = cfg["count"] * cfg["visibility_candidate_multiplier"]
    run.mkdir(parents=True)
    (run / "rounds").mkdir()
    (run / "maps").mkdir()
    write_json(run / "generation-config.json", cfg)
    records, images, annotations, scenes = [], [], [], {}
    maps = {}
    selected, quota = choose_candidates([], targets, map_targets)
    attempts = dict.fromkeys(map_targets, 0)
    report = {"schema_version": 2, "mode": "refill_v1", "status": "RUNNING",
              "requested_frames": cfg["count"], "candidate_budget": budget,
              "candidate_frames": 0, "validated_candidates": 0, "editor_processes": 0,
              "complete": False, "preserve_staging": True, "staging": str(raw),
              "rounds": [], "maps": [{"map_id": m, "planned": n, "actual": 0,
                                       "status": "pending"} for m, n in target_jobs]}
    write_json(run / "manifest.json", report)
    stalled = 0
    try:
        for round_index in range(1, cfg["refill_max_rounds"] + 1):
            remaining_budget = budget - report["candidate_frames"]
            jobs, weights = plan_refill_round(quota, attempts, remaining_budget,
                                               cfg["refill_batch_per_map"],
                                               initial=round_index == 1,
                                               initial_multiplier=min(2, cfg["visibility_candidate_multiplier"]))
            if not jobs:
                report["stop_reason"] = "candidate_budget"
                break
            before = len(selected)
            planned = sum(n for _, n in jobs)
            round_run = run / "rounds" / f"round-{round_index:05d}"
            first_id = report["candidate_frames"] + 1
            report["candidate_frames"] += planned
            report["editor_processes"] += 1
            item = {"round": round_index, "jobs": dict(jobs), "planned": planned,
                    "status": "running"}
            report["rounds"].append(item)
            write_json(run / "manifest.json", report)
            print(f'[KE] REFILL_ROUND_BEGIN round={round_index} accepted={before}/{cfg["count"]} '
                  f'candidates={planned} budget_left={remaining_budget}', flush=True)
            batch = _generate_batch(cfg, round_run, raw, executor, planned_jobs=jobs,
                                    first_image_id=first_id, round_index=round_index,
                                    target_weights=weights if round_index > 1 else None)
            batch_records, batch_images, batch_annotations, batch_scenes, batch_maps = batch
            # All native files were validated. Keep alternatives for residual
            # reassignment, not just the subset provisionally selected last round.
            records.extend(batch_records)
            images.extend(batch_images)
            annotations.extend(batch_annotations)
            if scenes.keys() & batch_scenes.keys():
                raise ValueError("Duplicate scene identity across refill rounds")
            scenes.update(batch_scenes)
            for mid, n in jobs:
                attempts[mid] += n
            for m in batch_maps:
                mid = m["map_id"]
                if mid not in maps:
                    maps[mid] = m
                    if m["overview"]:
                        shutil.copy2(round_run / m["overview"], run / m["overview"])
                else:
                    old = maps[mid]
                    if old["tank_bounds"] != m["tank_bounds"]:
                        raise ValueError(f"Map bounds changed between refill rounds: {mid}")
                    old["frames"] += m["frames"]
                    old["impostor_shadow_skipped_updates"] += m["impostor_shadow_skipped_updates"]
                    for key in ("render_quality_flags", "impostor_shadow_assets"):
                        old[key] = sorted(set(old[key]) | set(m[key]))
            # Journal validated candidates; raw remains untouched on incomplete.
            with (run / "candidate-pool.jsonl").open("a", encoding="utf-8") as stream:
                for row in batch_records:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            selected, quota = choose_candidates(records, targets, map_targets)
            report["validated_candidates"] = len(records)
            report["render_complete"] = True
            stalled = stalled + 1 if len(selected) == before else 0
            item.update(status="validated", accepted=len(selected), gain=len(selected)-before,
                        deficit=quota["deficit"], deficit_by_map=quota["deficit_by_map"])
            for m in report["maps"]:
                m["actual"] = quota["selected_by_map"][m["map_id"]]
            quota.update(rounds=round_index, candidate_budget=budget,
                         attempts_by_map=dict(attempts), stalled_rounds=stalled)
            write_json(run / "visibility-quotas.json", quota)
            write_json(run / "manifest.json", report)
            print(f'[KE] REFILL_ROUND_END round={round_index} accepted={len(selected)}/{cfg["count"]} '
                  f'gain={len(selected)-before} candidates_total={len(records)} '
                  f'deficit={json.dumps(quota["deficit"])} '
                  f'map_deficit={json.dumps(quota["deficit_by_map"])}', flush=True)
            if quota["complete"]:
                report["stop_reason"] = "exact_target"
                break
            if report["candidate_frames"] >= budget:
                report["stop_reason"] = "candidate_budget"
                break
            if stalled >= cfg["refill_stall_rounds"]:
                report["stop_reason"] = "stall"
                break
        else:
            report["stop_reason"] = "round_limit"
        if not quota["complete"]:
            report["status"] = "INCOMPLETE"
            quota.update(status="INCOMPLETE", stop_reason=report["stop_reason"])
            write_json(run / "visibility-quotas.json", quota)
            raise ValueError("Не набраны квоты видимости/карт: " + json.dumps(quota["deficit"]) +
                             "; дефицит карт: " + json.dumps(quota["deficit_by_map"]) +
                             f'; причина: {report["stop_reason"]}; отчёт: {run / "visibility-quotas.json"}')
        quota.update(status="COMPLETE", stop_reason="exact_target")
        result = export_candidates(cfg, run, raw,
                                   (records, images, annotations, scenes, list(maps.values())),
                                   selection=(selected, quota), editor_processes=report["editor_processes"])
        report.update(status="COMPLETE", complete=True)
        for m in report["maps"]:
            m["status"] = "verified"
        write_json(run / "manifest.json", report)
        print(f'[KE] REFILL_OK frames={result["images"]} maps={result["maps"]} '
              f'rounds={report["editor_processes"]}', flush=True)
        return result
    except Exception as exc:
        if report["status"] != "INCOMPLETE":
            report["status"] = "FAILED"
        report["error"] = str(exc)
        write_json(run / "manifest.json", report)
        raise
