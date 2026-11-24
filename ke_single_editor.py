"""One DaEditor process for all selected maps; no per-map editor relaunches.

The dataset is generated directly into the raw staging images directory.
The technical run contains a single editor workspace, capture scripts, plans,
logs and map overviews, but never duplicate RGB frames or COCO files.
"""
import hashlib
import json
import os
import random
import re
import subprocess
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
            current = pos if 0 <= pos < count and int(match.group(2)) == count else None
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
        if section.count("[KE] FRAME_OK ") != task["count"]:
            raise ValueError(f'Не получены все кадры карты {task["map_id"]}')
        if stability and section.count("[KE] FRAME_STABILITY_FINAL enabled=1 stable=1") != task["count"]:
            raise ValueError(f'Нет подтверждения стабильности {task["map_id"]}')
        for marker in ("MAP_OK", "MODEL_OK", "PLACEMENT_OK", "WATER_GROUND_FILTER"):
            if f"[KE] {marker} " not in section:
                raise ValueError(f'Отсутствует {marker}: {task["map_id"]}')
        result.append(section)
    return result


def finalize(cfg, run, raw, tasks):
    """Validate all images and journals, create the one and only COCO file."""
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
        maps.append({"map_id": task["map_id"], "frames": task["count"], "tank_bounds": bounds,
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
            verify_png(image_path, cfg["width"], cfg["height"])
            validate_rgb_png(image_path, cfg["width"], cfg["height"])
            blk = journal[idx]
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
            occlusion = (read_occlusion_proxy(blk, task["occlusion_targets"][idx], cfg["camera_attempts"], visibility)
                         if cfg["search_occlusion"] else None)
            image_id = len(images) + 1
            scene_id = f'{run.name}:{task["stem"]}:{comp}'
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
            records.append(record)
            images.append({
                "id": image_id, "file_name": record["image"],
                "width": cfg["width"], "height": cfg["height"], "map_id": task["map_id"],
                "scene_id": scene_id, "scene_group": group, "scene_group_id": group,
                "ke_scene": scene_metadata(blk),
                **({"ke_visibility": visibility} if visibility is not None else {}),
            })
            box = annotation["bbox_xywh"] if visibility is None else visibility["bbox_visible_xywh"]
            if box is not None:
                x, y, w, h = box
                annotations.append({
                    "id": image_id, "image_id": image_id, "category_id": 1,
                    "bbox": [x, y, w, h], "area": w * h, "iscrowd": 0,
                    "bbox_kind": annotation["bbox_kind"] if visibility is None else "visible_opaque",
                    "bbox_method": annotation["bbox_method"] if visibility is None else visibility["method"],
                    **({"ke_visibility": visibility} if visibility is not None else {}),
                })
            scenes.setdefault(scene_id, {
                "scene_id": scene_id, "map_id": task["map_id"],
                "scene_group_id": group, "image_ids": [],
            })["image_ids"].append(image_id)
            print(f"[KE] FINALIZE_FRAME {image_id}/{expected} image={image_path.name}", flush=True)
    occlusion_report = summarize_search(records) if cfg["search_occlusion"] else None
    if occlusion_report is not None:
        write_json(run / "occlusion-search.json", occlusion_report)
        print("[KE] OCCLUSION_SEARCH " + json.dumps(occlusion_report), flush=True)
    quota_report = None
    if cfg["enforce_visibility_quotas"]:
        targets = visibility_targets(cfg["count"], cfg["visibility_weights"])
        map_targets = dict(plan_equal_allocation(cfg["count"], [t["map_id"] for t in tasks]))
        selected, quota_report = choose_candidates(records, targets, map_targets)
        write_json(run / "visibility-quotas.json", quota_report)
        print("[KE] VISIBILITY_QUOTAS " + json.dumps(quota_report, ensure_ascii=False), flush=True)
        if not quota_report["complete"]:
            raise ValueError("Не набраны квоты видимости/карт: " + json.dumps(quota_report["deficit"]) +
                             "; дефицит карт: " + json.dumps(quota_report["deficit_by_map"]) +
                             f"; отчёт: {run / 'visibility-quotas.json'}")
        # Keep stable candidate filenames and image IDs. COCO IDs need not be contiguous.
        # Drop RGB and both masks only AFTER complete candidate validation and exact selection.
        for record in records:
            if record["image_id"] not in selected:
                (raw / record["image"]).unlink()
                for kind in ("amodal_mask", "visible_mask"):
                    (raw / record["visibility_measurement"][kind]).unlink()
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
        "scenes": len(scenes), "maps": len(maps), "editor_processes": 1,
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
    print(f"[KE] FINALIZE_OK frames={len(images)} annotations={len(annotations)}", flush=True)
    return overview


def generate(config, run, raw, executor=None):
    """Generate the raw dataset, invoking DaEditor exactly once.

    executor is a low-level test hook: callable(args) -> integer exit status.
    """
    cfg = validate_config(config)
    game, dagor = Path(cfg["game"]).resolve(), Path(cfg["dagor"]).resolve()
    cdk = game / "WarThunderCDK"
    run, raw = Path(run).resolve(), Path(raw).resolve()
    for forbidden in (game, dagor):
        if (run == forbidden or run.is_relative_to(forbidden) or
                raw == forbidden or raw.is_relative_to(forbidden)):
            raise ValueError("Результат не может находиться внутри Dagor или War Thunder")
    target_jobs = jobs_for(cfg, scan_maps(game))
    quotas = (candidate_allocation(target_jobs, cfg["visibility_candidate_multiplier"])
              if cfg["enforce_visibility_quotas"] else target_jobs)
    if run.exists() or raw.exists():
        raise ValueError("Рабочий каталог или исходный датасет уже существует")
    run.mkdir(parents=True)
    raw.mkdir(parents=True)
    (raw / "images").mkdir()
    if cfg["capture_visibility_masks"]:
        for kind in ("amodal", "visible"):
            (raw / "masks" / kind).mkdir(parents=True)
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
        next_image_id = 1
        for index, (map_id, count) in enumerate(quotas, 1):
            tasks.append(write_capture(cfg, map_id, count, map_seed(cfg["seed"], map_id),
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
                           None if cfg["keep_open"] else cfg["timeout"] * len(tasks),
                           env=editor_env)
            except (ValueError, subprocess.TimeoutExpired) as exc:
                raise ValueError(editor_failure(run, str(exc))) from exc
        report["render_complete"] = True
        report["staging"] = str(raw)
        write_json(run / "manifest.json", report)
        print(f'[KE] RENDER_COMPLETE staging={raw}; validating RGB, metadata and masks', flush=True)
        result = finalize(cfg, run, raw, tasks)
        for item in report["maps"]:
            actual = (result["visibility_quotas"]["selected_by_map"].get(item["map_id"], 0)
                      if cfg["enforce_visibility_quotas"] else item["planned"])
            item.update(actual=actual, status="verified")
        report["complete"] = True
        write_json(run / "manifest.json", report)
        print(f'[KE] SINGLE_EDITOR_OK frames={result["images"]} maps={result["maps"]} instances=1', flush=True)
        return result
    except Exception as exc:
        report["error"] = str(exc)
        for item in report["maps"]:
            if item["status"] == "pending":
                item["status"] = "failed"
        write_json(run / "manifest.json", report)
        raise

