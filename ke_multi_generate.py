"""Run an equal-quota dataset over all selected installed ground maps.

One editor process per map. No invented counts: the manifest records actual success.
The original single-map CLI is preserved; GUI delegates here for multiple maps.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from ke_generate import command, validate_config
from ke_maps import scan_maps, available_whitelist, plan_equal_allocation
from ke_validate import validate
from ke_terrain_audit import audit
from ke_coco import export


def jobs_for(config, discovered):
    """Resolve allowed maps, retain alphabetic inventory order and split exact quotas."""
    c = validate_config(config)
    wanted = c["map_ids"] or [c["map_id"]]
    enabled = available_whitelist(discovered, wanted)
    if len(enabled) != len(wanted):
        missing = sorted(set(wanted) - set(enabled))
        raise ValueError(f"Карты отсутствуют или имеют неподдерживаемый заголовок: {missing}")
    return [(name, count) for name, count in plan_equal_allocation(c["count"], enabled)]


def map_seed(base, map_id):
    """Stable across Python versions, processes and Python hash randomization."""
    digest = hashlib.sha256(f"KolobanovEye2:{base}:{map_id.casefold()}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


def run_multi(config, executor=None):
    c = validate_config(config)
    discovered = scan_maps(c["game"])
    quotas = jobs_for(c, discovered)
    if not quotas or sum(n for _, n in quotas) != c["count"]:
        raise ValueError("Нет карт для генерации")
    root = Path(c["output"]).resolve()
    game = Path(c["game"]).resolve()
    dagor = Path(c["dagor"]).resolve()
    if root == game or root.is_relative_to(game) or root == dagor or root.is_relative_to(dagor):
        raise ValueError("Каталог вывода должен быть вне War Thunder и Dagor")
    if root.exists():
        raise ValueError("Папка результатов уже существует")
    root.mkdir(parents=True)
    (root / "runs").mkdir()
    report = {
        "schema_version": 1, "requested_frames": c["count"], "seed": c["seed"],
        "maps": [{"map_id": mid, "planned": n, "actual": 0,
                  "status": "skipped_zero_quota" if n == 0 else "pending"} for mid, n in quotas],
        "complete": False
    }
    def checkpoint():
        (root / "multi-manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "generation-config.json").write_text(json.dumps(c, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checkpoint()
    successful = []
    run_command = executor if executor is not None else (lambda args: subprocess.call(args, cwd=Path(__file__).resolve().parent))
    try:
        for item in report["maps"]:
            n = item["planned"]
            if not n:
                continue
            map_id = item["map_id"]
            run_path = root / "runs" / Path(map_id).stem
            seed = map_seed(c["seed"], map_id)
            run_cfg = {**c, "map_ids": [], "map_id": map_id, "count": n,
                       "seed": seed, "output": str(run_path)}
            print(f"[KE] MAP_JOB_START map={map_id} frames={n} seed={seed}", flush=True)
            item["status"] = "running"
            checkpoint()
            ret = run_command(command(run_cfg))
            if ret:
                raise RuntimeError(f"Dagor завершился с кодом {ret}: {map_id} — см. {run_path}")
            verified = validate(run_path)
            if verified["frames"] != n:
                raise RuntimeError(f"Неверное число кадров для {map_id}: {verified['frames']} != {n}")
            terrain = audit(run_path, c["max_height_range"])
            (run_path / "terrain-report.json").write_text(
                json.dumps(terrain, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (run_path / "generation-config.json").write_text(
                json.dumps(run_cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            item.update(status="verified", actual=n, run=str(run_path.relative_to(root)))
            checkpoint()
            successful.append(run_path)
            print(f"[KE] MAP_JOB_OK map={map_id} frames={n}", flush=True)
        if sum(item["actual"] for item in report["maps"]) != c["count"]:
            raise RuntimeError("Фактическое число кадров не совпало с заданием")
        export(successful, root / "coco", split=False, seed=c["seed"])
        report["complete"] = True
        checkpoint()
        print(f"[KE] MULTIMAP_OK frames={c['count']} maps={len(successful)} coco={root / 'coco'}", flush=True)
        return report
    except Exception as exc:
        report["error"] = str(exc)
        for item in report["maps"]:
            if item["status"] == "running":
                item["status"] = "failed"
        checkpoint()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.add_mutually_exclusive_group(required=True)
    args.add_argument("--config", type=Path, help="JSON configuration")
    args.add_argument("--config-json", help="JSON configuration supplied by GUI")
    a = parser.parse_args()
    try:
        value = json.loads(a.config.read_text(encoding="utf-8") if a.config else a.config_json)
        run_multi(value)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(f"[KE] ERROR {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
