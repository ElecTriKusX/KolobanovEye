"""Read-only comparison of completed or interrupted online AVF runs.

Usage: python -m tools.ke_avf_benchmark RUN_BASELINE RUN_FULL RUN_ROI
No game assets are accessed; incomplete runs are never reported as successes.
"""
import argparse
import json
import math
from pathlib import Path
import re

IGNORED_CONFIG = {"output", "avf_probe_size", "avf_region", "avf_backend", "avf_batch_size", "avf_reuse_map"}
PROBE_RE = re.compile(
    r"\[KE\] AVF_PROBE index=\d+ attempt=\d+ size=(\d+)"
    r"(?: region=(roi|full))? valid=([01]) fraction=([-.\d]+)"
    r" amodal=\d+ visible=\d+ missing=\d+ elapsed_ms=([\d.]+)")


def load_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def load_jsonl(path):
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {"n": 0, "p50": None, "p95": None}
    if any(not math.isfinite(v) or v < 0 for v in ordered):
        raise ValueError("Invalid timing sample")
    def percentile(p):
        i = (len(ordered)-1)*p
        low = math.floor(i)
        high = math.ceil(i)
        return round(ordered[low]+(i-low)*(ordered[high]-ordered[low]), 4)
    return {"n": len(ordered), "p50": percentile(.5), "p95": percentile(.95)}


def summarize_run(path, native_log=None):
    path = Path(path)
    config = load_json(path/"generation-config.json")
    manifest = load_json(path/"manifest.json")
    if not isinstance(config, dict) or not isinstance(manifest, dict):
        raise ValueError(f"Missing run config/manifest: {path}")
    quotas = load_json(path/"visibility-quotas.json", {}) or {}
    timing = load_jsonl(path/"online-timing.jsonl")
    candidates = load_jsonl(path/"candidate-pool.jsonl")
    search = load_json(path/"occlusion-search.json")
    # An interrupted run may not have reached export; summarize its validated
    # candidate journal, not merely the subset selected for training.
    if search is None and config.get("search_occlusion") and candidates:
        from tools.ke_occlusion import summarize_search
        search = summarize_search(candidates)
    complete = manifest.get("status") == "COMPLETE" and manifest.get("complete") is True and quotas.get("complete") is True
    selected = quotas.get("selected")
    attempted = manifest.get("attempted_candidates")
    elapsed = manifest.get("total_wall_seconds")
    rate = (elapsed/selected if complete and selected and elapsed is not None else None)
    probe_stats = None
    loads = None
    fixed_native = None
    fixed_gpu = None
    actual_loads = reuses = None
    if native_log is not None:
        native = Path(native_log).read_text(encoding="utf-8", errors="replace")
        probes = PROBE_RE.findall(native)
        probe_stats = {
            "observed_log_entries": len(probes),
            "valid_log_entries": sum(int(m[2]) for m in probes),
            "latency_ms": distribution([float(m[4]) for m in probes]),
            "roi_entries": sum(m[1] == "roi" for m in probes),
            "gpu_verify_ok": len(re.findall(r"\[KE\] AVF_GPU_VERIFY_OK\b", native)),
            "gpu_verify_fail": len(re.findall(r"\[KE\] AVF_GPU_VERIFY_FAIL\b", native)),
        }
        # Use one supplied native log only: do not aggregate duplicate logs.
        # MAP_BEGIN is emitted twice in debug logs (raw and CON). Prefer the
        # console stream when present; residency markers occur once per task.
        console = [line.split("CON: ", 1)[1] for line in native.splitlines() if "CON: [KE]" in line]
        events = "\n".join(console) if console else native
        loads = len(re.findall(r"\[KE\] MAP_BEGIN\b", events))
        fixed_rows = re.findall(r"\[KE\] FIXED_PRESELECT index=\d+ placements=(\d+) valid=(\d+) env_passes=(\d+) selected=\d+ fraction=[-.\d]+",events)
        if fixed_rows:
            fixed_native = {"groups":len(fixed_rows),"valid_placements":sum(int(x[0]) for x in fixed_rows),
                            "valid_probes":sum(int(x[1]) for x in fixed_rows),
                            "environment_passes":sum(int(x[2]) for x in fixed_rows)}
        gpu_rows = re.findall(r"\[KE\] FIXED_GPU_GROUP backend=(\d+) submitted=(\d+) verified=(\d+) env_reads=(\d+) model_reads=(\d+)",native)
        fixed_gpu = {"successful_groups":len(gpu_rows), "submitted":sum(int(x[1]) for x in gpu_rows),
                     "verified":sum(int(x[2]) for x in gpu_rows), "environment_reads":sum(int(x[3]) for x in gpu_rows),
                     "model_reads":sum(int(x[4]) for x in gpu_rows),
                     "verify_fail":len(re.findall(r"\[KE\] FIXED_GPU_VERIFY_FAIL\b",native)),
                     "fallbacks":len(re.findall(r"\[KE\] FIXED_GPU_FALLBACK\b",native))}
        actions = re.findall(r"\[KE\] MAP_RESIDENCY action=(load|reuse)\b", events)
        if actions:
            actual_loads, reuses = actions.count("load"), actions.count("reuse")
    return {"run": path.name, "status": manifest.get("status"), "complete": complete,
            "requested": manifest.get("requested_frames"), "selected": selected,
            "attempted": attempted, "rendered": manifest.get("candidate_frames"),
            "total_wall_seconds": elapsed,
            "seconds_per_accepted_frame": rate,
            "tasks": manifest.get("tasks"), "map_begin_log_entries": loads,
            "actual_map_loads": actual_loads, "map_reuses": reuses,
            "backend": config.get("avf_backend"), "batch_size": config.get("avf_batch_size"),
            "guided_sampling": config.get("guided_sampling","off"),
            "guided_prerender": config.get("guided_prerender","off"),
            "guided_prerender_rejections": manifest.get("guided_prerender_rejections",0),
            "guided_prerender_probes": manifest.get("guided_prerender_probes",0),
            "guided_prerender_disabled_maps": manifest.get("guided_prerender_disabled_maps",{}),
            "fixed_camera": manifest.get("fixed_camera"),
            "scene_families": manifest.get("scene_families",{}),
            "scene_cache": manifest.get("scene_cache",{"enabled":False}),
            "fixed_camera_native":fixed_native,
            "fixed_gpu_native":fixed_gpu,
            "reuse_map_enabled": config.get("avf_reuse_map"),
            "probe_size": config.get("avf_probe_size", 0), "region": config.get("avf_region"),
            "host_turnaround_ms": distribution([r["python_turnaround_ms"] for r in timing
                                               if r.get("decision_sent", True)]),
            "host_validation_ms": distribution([r["validation_and_credit_ms"] for r in timing]),
            "host_post_decision_reporting_ms": distribution([r["post_decision_reporting_ms"] for r in timing
                                                             if "post_decision_reporting_ms" in r]),
            "probe_log": probe_stats, "occlusion": search}, config


def compare_runs(paths, logs=None, *, compare_guided=False):
    logs = logs if logs is not None else [None]*len(paths)
    if len(logs) != len(paths) or not paths:
        raise ValueError("Specify one log per run, or no logs")
    rows, configs = [], []
    ignored = IGNORED_CONFIG | ({"guided_sampling","guided_prerender","scene_cache"} if compare_guided else set())
    for path, log in zip(paths, logs):
        row, config = summarize_run(path, log)
        rows.append(row)
        config = {"guided_sampling":"off","guided_prerender":"off","scene_cache":"",**config}
        configs.append({k: v for k, v in config.items() if k not in ignored})
    differences = sorted(k for k in set().union(*(c.keys() for c in configs))
                         if any(c.get(k) != configs[0].get(k) for c in configs[1:]))
    baseline = rows[0]["seconds_per_accepted_frame"]
    for row in rows:
        rate = row["seconds_per_accepted_frame"]
        row["speedup_vs_first"] = baseline/rate if not differences and baseline and rate else None
    return {"comparable_config": not differences, "differing_config_keys": differences,
            "compared_guided_modes": compare_guided,
            "all_complete": all(r["complete"] for r in rows), "runs": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, nargs="+")
    parser.add_argument("--native-log", type=Path, action="append",
                        help="One native log for each run, in the same order")
    parser.add_argument("--compare-guided", action="store_true", help="Allow explicit A/B comparison across directed sampling and prefilter modes")
    args = parser.parse_args()
    print(json.dumps(compare_runs(args.runs, args.native_log, compare_guided=args.compare_guided), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
