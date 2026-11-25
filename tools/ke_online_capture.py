"""One DaEditor process; Python sends the next target only after validation.

The native frame loop keeps its loaded map/model until a task ends or the
scheduler changes maps. A task is a bounded preplanned geometry window, NOT
a fixed number of expensive renders. A single immutable command is in flight.
"""
import json
import os
import subprocess
import time
from pathlib import Path

from tools.ke_adaptive_quotas import AdaptiveQuotaPlanner
from tools.ke_visibility import VISIBILITY_BINS
from tools.ke_visibility_quotas import choose_candidates, visibility_targets
from tools.ke_frame_journal import JournalCursor
from tools.ke_smart_positions import load_pool, CandidateSampler, frame_block, verify_frame


def publish(path, content):
    """A newly created immutable command, visible only after close/rename."""
    if path.exists():
        raise ValueError(f"Duplicate online command: {path}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="ascii")
    os.replace(temporary, path)


def read_ready_acknowledgement(path):
    """Retry only immutable ready files; never retry a consumed journal record."""
    try:
        return path.read_bytes() == b"ok\n"
    except (FileNotFoundError, PermissionError):
        return False


def await_value(process, read, timeout, description):
    deadline = time.monotonic() + timeout
    while True:
        value = read()
        if value is not None:
            return value
        if process.poll() is not None:
            raise RuntimeError(f"DaEditor exited ({process.poll()}) before {description}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Online timeout: {description}")
        time.sleep(.025)


def generate_online(cfg, run, raw, *, executor=None):
    # Lazy import avoids circular dependency with the existing renderer.
    from ke_single_editor import (jobs_for, write_capture, make_command, write_json,
        collect_frame, collect_candidates, export_candidates, map_seed, read_depth_rejection, read_placement_rejection, read_stability_rejection)
    from tools.ke_maps import scan_maps
    from kolobanoveye import (inspect, require_file, run_logged, parse_resources,
        choose_resources, dagor_path, editor_failure, BIN, GRP)

    started = time.monotonic()
    run, raw = Path(run).resolve(), Path(raw).resolve()
    game, dagor = Path(cfg["game"]).resolve(), Path(cfg["dagor"]).resolve()
    for forbidden in (game, dagor):
        if any(p == forbidden or p.is_relative_to(forbidden) for p in (run, raw)):
            raise ValueError("Результат не может находиться внутри Dagor или War Thunder")
    if run.exists() or raw.exists():
        raise ValueError("Рабочий каталог или исходный датасет уже существует")
    target_jobs = jobs_for(cfg, scan_maps(game))
    if len(target_jobs) > 256:
        raise ValueError("Online supports at most 256 maps")
    map_targets = dict(target_jobs)
    targets = visibility_targets(cfg["count"], cfg["visibility_weights"])
    budget = {m: n * cfg["visibility_candidate_multiplier"] for m, n in target_jobs}
    planner = AdaptiveQuotaPlanner(targets, map_targets, budget)
    samplers, bank_info, occluders = {}, {}, {}
    guided = cfg.get("guided_sampling", "off") == "obstacle"
    if guided:
        from tools.ke_guided_sampling import OccluderIndex, candidate_fields, fields_block
    if cfg["smart_placement_cache"]:
        for mid in map_targets:
            bank = load_pool(cfg["smart_placement_cache"],mid)
            samplers[mid] = CandidateSampler(bank,map_seed(cfg["seed"],mid),cfg["map_margin_percent"])
            bank_info[mid] = {k:v for k,v in bank.items() if k!="pools"}
            if guided:
                occluders[mid] = OccluderIndex.load(cfg["smart_placement_cache"],mid)
    run.mkdir(parents=True)
    raw.mkdir(parents=True)
    for name in ("images", "masks/amodal", "masks/visible"):
        (raw / name).mkdir(parents=True)
    for name in ("capture", "plans", "maps", "journals", "online", "ooz-failures"):
        (run / name).mkdir()
    mailbox = run / "online"
    report = {"schema_version": 2, "mode": "online_v1", "status": "RUNNING",
              "requested_frames": cfg["count"], "candidate_budget": sum(budget.values()),
              "editor_processes": 0, "complete": False, "preserve_staging": True,
              "staging": str(raw), "candidate_frames": 0, "validated_candidates": 0,
              "maps": [{"map_id": m, "planned": n, "actual": 0, "status": "pending"}
                       for m, n in target_jobs], "tasks": 0}
    if samplers:
        report["smart_placement"] = {"banks":bank_info,"validated_sources":{}}
        write_json(run/"smart-placement-banks.json",bank_info)
    write_json(run / "generation-config.json", cfg)
    write_json(run / "manifest.json", report)
    tasks, validated, rejected, placement_rejected, stability_rejected = [], {}, {}, {}, {}
    records = []
    no_gain = 0
    consecutive_mismatch = 0
    report["depth_mismatch_rejections"] = 0
    report["placement_rejections"] = 0
    report["stability_rejections"] = 0
    consecutive_unstable = 0
    report["attempted_candidates"] = 0
    # A single miss is not stagnation. Allow several map-sized windows of
    # exploration, with a minimum useful number of observations for tiny runs.
    stall_limit = cfg["refill_stall_rounds"] * max(32, min(256, cfg["refill_batch_per_map"]), len(map_targets)*4)
    process = None
    host = None
    try:
        if executor is None:
            cdk = game / "WarThunderCDK"
            inventory = inspect(game, cdk, Path(target_jobs[0][0]))
            write_json(run / "inventory.json", inventory)
            if inventory["errors"]:
                raise ValueError("Неправильные ресурсы, см. inventory.json")
            run_logged([require_file(dagor / BIN / "dumpGrp-dev.exe"), game / GRP, "-dumpR"],
                       run / "grp.txt", run, 60)
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
        # Use the established command, flags and workspace, then switch its
        # batch config to the online protocol before the process starts.
        cmd = make_command(cfg, run, [{"capture": run / "unused.blk"}])
        (run / "map-batch.blk").write_text(f'online_dir:t="{dagor_path(mailbox)}"\n', encoding="ascii")
        request = planner.next_request()
        next_id = 1
        native_started = time.monotonic()
        if executor is None:
            host = (run / "host-stdout.log").open("wb")
            env = {**os.environ, "KE_OOZ_FAILURE_DIR": str(run / "ooz-failures")}
            process = subprocess.Popen(cmd, cwd=run, stdout=host, stderr=subprocess.STDOUT, env=env)
        else:
            # Online test hook returns a live process-like object.
            process = executor(cmd)
        report["editor_processes"] = 1
        print(f'[KE] ONLINE_START target={cfg["count"]} maps={len(map_targets)} instances=1', flush=True)
        stop_reason = None
        residency_map, residency_attempts, residency_misses = None, 0, 0
        report['map_residency_policy'] = {'enabled': bool(cfg.get('avf_probe_size', 0)),
                                          'preferred_attempts': 16, 'max_consecutive_no_gain': 6}
        while request is not None:
            mid, target = request
            if mid != residency_map:
                residency_map, residency_attempts, residency_misses = mid, 0, 0
            number = len(tasks) + 1
            cap = min(cfg["refill_batch_per_map"], budget[mid] - planner.attempts[mid] + 1)
            task = write_capture(cfg, mid, cap, map_seed(f'{cfg["seed"]}:online:{number}', mid),
                                 run, raw, number, model, skeleton, next_id)
            next_id += cap
            feedback = mailbox / f"feedback_{number}"
            feedback.mkdir()
            with task["capture"].open("a", encoding="ascii") as stream:
                stream.write(f'online_feedback_dir:t="{dagor_path(feedback)}"\n')
            sampler = samplers.get(mid)
            if sampler:
                xmin,xmax,zmin,zmax=sampler.bounds
                with task["capture"].open("a",encoding="ascii") as stream:
                    stream.write(f'smart_placement:b=yes\nsmart_bounds_min:p2={xmin:.9f},{zmin:.9f}\n'
                                 f'smart_bounds_max:p2={xmax:.9f},{zmax:.9f}\n')
                task["smart_points"] = {}
                guided_fields = {}
                selected_fields = {}
                def publish_points(frame_index, requested):
                    points = sampler.choose(requested,cfg["placement_attempts"])
                    task["smart_points"][frame_index] = points
                    block = frame_block(frame_index,requested,points,cfg["ground_offset"])
                    if guided:
                        fields = candidate_fields(task["cameras"][frame_index],occluders[mid],sampler.bounds,
                            points,requested,cfg,f'{task["seed"]}:{frame_index}')
                        guided_fields[frame_index] = fields
                        block += "guided_sampling:b=yes\n"+fields_block(fields)
                    publish(feedback/f"smart_{frame_index}.blk",block)
                    with (run/"smart-placement-plans.jsonl").open("a",encoding="utf-8") as stream:
                        stream.write(json.dumps({"task":number,"index":frame_index,"map_id":mid,
                            "target":requested,"points":points})+"\n")
                publish_points(0,target)
            publish(feedback / "decision_0.txt", f"{VISIBILITY_BINS.index(target)}\n")
            attempt_started = time.monotonic()
            publish(mailbox / f"task_{number}.blk", f'capture:t="{dagor_path(task["capture"])}"\n')
            tasks.append(task)
            report["tasks"] = number
            journal_cursor = JournalCursor(task["journal"])
            for idx in range(cap):
                ready = feedback / f"ready_{idx}.txt"
                def completed_record():
                    if not read_ready_acknowledgement(ready):
                        return None
                    # Native acknowledgement follows PNG and journal close.
                    # A ready marker with a missing record is a protocol error.
                    return journal_cursor.read_next(idx)
                wait_started = time.monotonic()
                blk = await_value(process, completed_record, cfg["timeout"], f"task {number} frame {idx}")
                ready_observed = time.monotonic()
                if cfg["search_occlusion"]:
                    task["occlusion_targets"][idx] = target
                unstable = read_stability_rejection(cfg, raw, task, idx, blk)
                placement = read_placement_rejection(cfg, raw, task, idx, blk)
                rejection = read_depth_rejection(cfg, raw, task, idx, blk)
                if unstable is None and placement is None:
                    consecutive_unstable = 0
                rejection_checked = time.monotonic()
                before = planner.report["selected"]
                if unstable is not None:
                    record = unstable
                    stability_rejected[record["image_id"]] = record
                    task.setdefault("stability_rejected_indices", set()).add(idx)
                    planner.finish()
                    consecutive_unstable += 1
                    measured = "reject_stability"
                    journal_name = "rejected-frames.jsonl"
                    print(f'[KE] ONLINE_REJECT task={number} index={idx} reason=frame_stability_timeout '
                          f'passes={record["warmup_passes"]} elapsed_ms={record["elapsed_ms"]}', flush=True)
                elif placement is not None:
                    record = placement
                    placement_rejected[record["image_id"]] = record
                    task.setdefault("placement_rejected_indices", set()).add(idx)
                    planner.finish()
                    measured = "reject_placement"
                    journal_name = "rejected-frames.jsonl"
                    print(f'[KE] ONLINE_REJECT task={number} index={idx} reason=heightmap_candidates_exhausted '
                          f'attempts={record["placement_attempts"]}', flush=True)
                elif rejection is not None:
                    record = rejection
                    rejected[record["image_id"]] = record
                    task.setdefault("rejected_indices", set()).add(idx)
                    planner.finish()
                    consecutive_mismatch += 1
                    measured = "reject_depth_pass_mismatch"
                    journal_name = "rejected-frames.jsonl"
                    print(f'[KE] ONLINE_REJECT task={number} index={idx} reason=depth_pass_mismatch '
                          f'missing={record["unmatched_pixels"]} amodal={record["amodal_pixels"]}', flush=True)
                else:
                    smart = verify_frame(blk,target,task["smart_points"][idx],sampler.bounds) if sampler else None
                    if guided:
                        import re
                        if not re.search(r"^guided_camera_plan:b=(?:yes|true|1)\s*$",blk,re.M):
                            raise ValueError("Missing guided camera metadata; rebuild Dagor")
                        match = re.search(r"^guided_placement_index:i=(\d+)\s*$",blk,re.M)
                        if not match or int(match.group(1)) != smart["attempt"]:
                            raise ValueError("Guided camera bank does not match selected placement")
                        field = guided_fields[idx][smart["attempt"]]
                        task["cameras"][idx] = field
                        selected_fields[idx] = field
                    candidate = collect_frame(cfg, run, raw, task, idx, blk)
                    record = candidate[0]
                    if sampler:
                        record["smart_placement"] = smart
                        candidate[1]["ke_smart_placement"] = smart
                        sources = report["smart_placement"]["validated_sources"]
                        sources[smart["source"]] = sources.get(smart["source"],0)+1
                    validated[record["image_id"]] = candidate
                    records.append(record)
                    planner.finish(record)
                    consecutive_mismatch = 0
                    measured = record["visibility_measurement"]["visibility_bin"]
                    journal_name = "candidate-pool.jsonl"
                if guided:
                    guided_fields.pop(idx,None)
                validation_finished = time.monotonic()
                after = planner.report["selected"]
                no_gain = no_gain + 1 if after == before else 0
                residency_attempts += 1
                residency_misses = residency_misses + 1 if after == before else 0
                prefer_current = bool(cfg.get('avf_probe_size', 0) and
                                      residency_attempts < 16 and residency_misses < 6)
                with (run / journal_name).open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                report["depth_mismatch_rejections"] = len(rejected)
                report["candidate_frames"] = len(records) + len(rejected)
                report["placement_rejections"] = len(placement_rejected)
                report["stability_rejections"] = len(stability_rejected)
                report["attempted_candidates"] = report["candidate_frames"] + len(placement_rejected) + len(stability_rejected)
                report["validated_candidates"] = len(records)
                for item in report["maps"]:
                    item["actual"] = planner.report["selected_by_map"][item["map_id"]]
                attempt_wall_seconds = round(time.monotonic()-attempt_started,4)
                warmup_attempts = report["candidate_frames"] + len(stability_rejected)
                if consecutive_unstable >= 3 or (warmup_attempts >= 30 and 10 * len(stability_rejected) > warmup_attempts):
                    raise ValueError("STABILITY: systematic frame instability (3 consecutive or >10% after 30 warmups)")
                if consecutive_mismatch >= 5 or (report["candidate_frames"] >= 30 and
                        5 * len(rejected) > report["candidate_frames"]):
                    raise ValueError("VISIBILITY: systematic depth-pass mismatch (5 consecutive or >20% after 30 attempts)")
                bookkeeping_finished = time.monotonic()
                if planner.report["complete"]:
                    stop_reason, request = "exact_target", None
                elif no_gain >= stall_limit:
                    stop_reason, request = "stall", None
                else:
                    request = planner.next_request(current_map=mid, prefer_current=prefer_current)
                    if request is None:
                        stop_reason = "candidate_budget"
                same = request is not None and request[0] == mid and idx + 1 < cap
                scheduler_finished = time.monotonic()
                attempt_started = time.monotonic()
                if idx + 1 < cap:
                    if same and sampler:
                        publish_points(idx+1,request[1])
                    publish(feedback / f"decision_{idx+1}.txt",
                            f"{VISIBILITY_BINS.index(request[1]) if same else -1}\n")
                decision_published = time.monotonic()
                # Durable candidate journal, validation, quota credit and abort guards
                # precede the decision. Cosmetic progress and manifest can overlap
                # native work without changing the one-request protocol.
                with (run / "online-progress.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"task": number, "index": idx, "image_id": record["image_id"],
                        "requested_bin": target, "measured_bin": measured,
                        "map_id": mid, "selected": after, "deficit": planner.report["deficit"],
                        "smart_placement":record.get("smart_placement"),
                        "attempt_wall_seconds":attempt_wall_seconds,
                        "deficit_by_map": planner.report["deficit_by_map"]}) + "\n")
                write_json(run / "manifest.json", report)
                print(f'[KE] ONLINE_FRAME task={number} index={idx} map={mid} '
                      f'accepted={after}/{cfg["count"]} candidates={report["candidate_frames"]} '
                      f'depth_rejected={len(rejected)} placement_rejected={len(placement_rejected)} '
                      f'stability_rejected={len(stability_rejected)} attempts={report["attempted_candidates"]}', flush=True)
                reporting_finished = time.monotonic()
                with (run / "online-timing.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"task": number, "index": idx, "map_id": mid,
                        "requested_bin": target, "measured_bin": measured,
                        "native_wait_ms": round(1000*(ready_observed-wait_started), 3),
                        "rejection_checks_ms": round(1000*(rejection_checked-ready_observed), 3),
                        "validation_and_credit_ms": round(1000*(validation_finished-rejection_checked), 3),
                        "bookkeeping_ms": round(1000*(bookkeeping_finished-validation_finished), 3),
                        "scheduler_ms": round(1000*(scheduler_finished-bookkeeping_finished), 3),
                        "decision_publish_ms": round(1000*(decision_published-scheduler_finished), 3),
                        "python_turnaround_ms": round(1000*(decision_published-ready_observed), 3),
                        "post_decision_reporting_ms": round(1000*(reporting_finished-decision_published), 3),
                        "decision_sent": idx + 1 < cap,
                        "residency_attempts": residency_attempts, "residency_misses": residency_misses,
                        "prefer_current_map": prefer_current,
                        "continue_same_task": same, "switch_map": request is not None and request[0] != mid}) + "\n")
                if not same:
                    task["count"] = idx + 1
                    task["outputs"] = task["outputs"][:idx+1]
                    break
                target = request[1]
            plan_path = run / "plans" / f'{task["index"]:03d}-{task["stem"]}.json'
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["online_completed_frames"] = task["count"]
            if guided:
                plan["guided_selected_camera_fields"] = selected_fields
            plan["online_occlusion_targets"] = task["occlusion_targets"][:task["count"]]
            write_json(plan_path, plan)
            done = mailbox / f"done_{number}.txt"
            await_value(process, lambda: True if done.is_file() and done.read_bytes() == b"ok\n" else None,
                        cfg["timeout"], f"task {number} acknowledgement")
        publish(mailbox / f"task_{len(tasks)+1}.blk", "stop:b=yes\n")
        exit_code = process.wait(timeout=cfg["timeout"])
        if exit_code:
            raise RuntimeError(f"DaEditor exited with {exit_code}")
        report["render_complete"] = True
        report["native_wall_seconds"] = round(time.monotonic()-native_started,3)
        # Verify native task sequence/stability/overview even though each
        # RGB/mask/BLK was already validated before the next decision.
        pool = collect_candidates(cfg, run, raw, tasks, prevalidated=validated, rejected=rejected,
                                  placement_rejected=placement_rejected, stability_rejected=stability_rejected)
        selected, quota = choose_candidates(pool[0], targets, map_targets)
        if len(selected) != planner.report["selected"]:
            raise ValueError("Online planner disagrees with final full-pool selection")
        quota.update(status="COMPLETE" if quota["complete"] else "INCOMPLETE",
                     stop_reason=stop_reason, attempts_by_map=dict(planner.attempts),
                     candidate_budget=sum(budget.values()), tasks=len(tasks),
                     depth_mismatch_rejections=len(rejected), placement_rejections=len(placement_rejected),
                     stability_rejections=len(stability_rejected),
                     attempted_candidates=report["attempted_candidates"], rendered_candidates=report["candidate_frames"])
        write_json(run / "visibility-quotas.json", quota)
        report["stop_reason"] = stop_reason
        if not quota["complete"]:
            report["status"] = "INCOMPLETE"
            raise ValueError(f'Не набраны квоты видимости/карт: {stop_reason}; {run / "visibility-quotas.json"}')
        # Merge repeated map visits without changing the planned balance.
        maps = {}
        for item in pool[4]:
            mid = item["map_id"]
            if mid not in maps:
                maps[mid] = item
            else:
                old = maps[mid]
                if old["tank_bounds"] != item["tank_bounds"]:
                    raise ValueError(f"Map bounds changed between online tasks: {mid}")
                old["frames"] += item["frames"]
                old["impostor_shadow_skipped_updates"] += item["impostor_shadow_skipped_updates"]
                for key in ("render_quality_flags", "impostor_shadow_assets"):
                    old[key] = sorted(set(old[key]) | set(item[key]))
        result = export_candidates(cfg, run, raw, (*pool[:4], list(maps.values())),
                                   selection=(selected, quota), editor_processes=1)
        # Rejected RGB is kept in staging on failure, never in published raw.
        for item in rejected.values():
            (raw / item["image"]).unlink()
        report.update(status="COMPLETE", complete=True,
                      total_wall_seconds=round(time.monotonic()-started,3),
                      native_seconds_per_selected_frame=round(report["native_wall_seconds"]/max(1,result["images"]),4))
        for item in report["maps"]:
            item["status"] = "verified"
        write_json(run / "manifest.json", report)
        print(f'[KE] ONLINE_OK frames={result["images"]} candidates={report["candidate_frames"]} tasks={len(tasks)} instances=1', flush=True)
        return result
    except BaseException as exc:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if report["status"] != "INCOMPLETE":
            report["status"] = "FAILED"
        report["error"] = str(exc)
        report["total_wall_seconds"] = round(time.monotonic()-started,3)
        write_json(run / "manifest.json", report)
        if isinstance(exc, (RuntimeError, TimeoutError, subprocess.TimeoutExpired)):
            from ke_single_editor import read_editor_log, explain_batch_failure
            try:
                log, source = read_editor_log(run)
                if "[KE] FAIL " in log:
                    raise ValueError(explain_batch_failure(run, log, source)) from exc
            except ValueError as detail:
                if "[KE] FAIL " in str(detail):
                    raise
            raise ValueError(editor_failure(run, str(exc))) from exc
        raise
    finally:
        if host is not None:
            host.close()
