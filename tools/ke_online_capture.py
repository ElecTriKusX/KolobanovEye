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
from tools.ke_frame_journal import read_journal


def publish(path, content):
    """A newly created immutable command, visible only after close/rename."""
    if path.exists():
        raise ValueError(f"Duplicate online command: {path}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="ascii")
    os.replace(temporary, path)


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
        collect_frame, collect_candidates, export_candidates, map_seed)
    from tools.ke_maps import scan_maps
    from kolobanoveye import (inspect, require_file, run_logged, parse_resources,
        choose_resources, dagor_path, editor_failure, BIN, GRP)

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
    write_json(run / "generation-config.json", cfg)
    write_json(run / "manifest.json", report)
    tasks, validated = [], {}
    records = []
    no_gain = 0
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
        while request is not None:
            mid, target = request
            number = len(tasks) + 1
            cap = min(cfg["refill_batch_per_map"], budget[mid] - planner.attempts[mid] + 1)
            task = write_capture(cfg, mid, cap, map_seed(f'{cfg["seed"]}:online:{number}', mid),
                                 run, raw, number, model, skeleton, next_id)
            next_id += cap
            feedback = mailbox / f"feedback_{number}"
            feedback.mkdir()
            with task["capture"].open("a", encoding="ascii") as stream:
                stream.write(f'online_feedback_dir:t="{dagor_path(feedback)}"\n')
            publish(feedback / "decision_0.txt", f"{VISIBILITY_BINS.index(target)}\n")
            publish(mailbox / f"task_{number}.blk", f'capture:t="{dagor_path(task["capture"])}"\n')
            tasks.append(task)
            report["tasks"] = number
            for idx in range(cap):
                ready = feedback / f"ready_{idx}.txt"
                def completed_record():
                    if not ready.is_file() or ready.read_bytes() != b"ok\n":
                        return None
                    # Native acknowledgement follows PNG and journal close.
                    # A ready marker with a missing record is a protocol error.
                    journal = read_journal(task["journal"])
                    if idx not in journal:
                        raise ValueError("Ready marker has no matching frame journal record")
                    return journal[idx]
                blk = await_value(process, completed_record, cfg["timeout"], f"task {number} frame {idx}")
                if cfg["search_occlusion"]:
                    task["occlusion_targets"][idx] = target
                candidate = collect_frame(cfg, run, raw, task, idx, blk)
                record = candidate[0]
                validated[record["image_id"]] = candidate
                records.append(record)
                before = planner.report["selected"]
                planner.finish(record)
                after = planner.report["selected"]
                no_gain = no_gain + 1 if after == before else 0
                with (run / "candidate-pool.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                report["candidate_frames"] = report["validated_candidates"] = len(records)
                for item in report["maps"]:
                    item["actual"] = planner.report["selected_by_map"][item["map_id"]]
                with (run / "online-progress.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"task": number, "index": idx, "image_id": record["image_id"],
                        "requested_bin": target, "measured_bin": record["visibility_measurement"]["visibility_bin"],
                        "map_id": mid, "selected": after, "deficit": planner.report["deficit"],
                        "deficit_by_map": planner.report["deficit_by_map"]}) + "\n")
                write_json(run / "manifest.json", report)
                print(f'[KE] ONLINE_FRAME task={number} index={idx} map={mid} '
                      f'accepted={after}/{cfg["count"]} candidates={len(records)}', flush=True)
                if planner.report["complete"]:
                    stop_reason, request = "exact_target", None
                elif no_gain >= stall_limit:
                    stop_reason, request = "stall", None
                else:
                    request = planner.next_request(current_map=mid)
                    if request is None:
                        stop_reason = "candidate_budget"
                same = request is not None and request[0] == mid and idx + 1 < cap
                if idx + 1 < cap:
                    publish(feedback / f"decision_{idx+1}.txt",
                            f"{VISIBILITY_BINS.index(request[1]) if same else -1}\n")
                if not same:
                    task["count"] = idx + 1
                    task["outputs"] = task["outputs"][:idx+1]
                    break
                target = request[1]
            plan_path = run / "plans" / f'{task["index"]:03d}-{task["stem"]}.json'
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["online_completed_frames"] = task["count"]
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
        # Verify native task sequence/stability/overview even though each
        # RGB/mask/BLK was already validated before the next decision.
        pool = collect_candidates(cfg, run, raw, tasks, prevalidated=validated)
        selected, quota = choose_candidates(pool[0], targets, map_targets)
        if len(selected) != planner.report["selected"]:
            raise ValueError("Online planner disagrees with final full-pool selection")
        quota.update(status="COMPLETE" if quota["complete"] else "INCOMPLETE",
                     stop_reason=stop_reason, attempts_by_map=dict(planner.attempts),
                     candidate_budget=sum(budget.values()), tasks=len(tasks))
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
        report.update(status="COMPLETE", complete=True)
        for item in report["maps"]:
            item["status"] = "verified"
        write_json(run / "manifest.json", report)
        print(f'[KE] ONLINE_OK frames={result["images"]} candidates={len(records)} tasks={len(tasks)} instances=1', flush=True)
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
