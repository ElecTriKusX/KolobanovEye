"""Create one unsplit dataset/raw per generation, with one DaEditor instance.

Technical editor workspace is in dataset/runs/run-<id>; no duplicate images
or COCO are retained under runs/. The previous raw is only replaced on success.
"""
import argparse
import json
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ke_generate import validate_config
from ke_single_editor import generate

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "dataset"


def run_dataset(config, dataset_root=DATASET, *, replace_raw=False, executor=None):
    cfg = validate_config(config)
    dataset_root = Path(dataset_root).resolve()
    raw = dataset_root / "raw"
    if raw.exists() and not replace_raw:
        raise ValueError("dataset/raw уже существует; для замены требуется --replace-raw")
    run_id = "run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    work = dataset_root / "runs" / run_id
    dataset_root.mkdir(parents=True, exist_ok=True)
    (dataset_root / "runs").mkdir(exist_ok=True)
    staging = dataset_root / (".raw-staging-" + uuid.uuid4().hex)
    try:
        result = generate({**cfg, "output": str(work)}, work, staging, executor=executor)
    except Exception:
        # Do not erase an expensive completed native render on postprocessing errors.
        # Incomplete native runs are still discarded; neither replaces valid raw.
        manifest = work / "manifest.json"
        rendered = False
        if manifest.is_file():
            try:
                state = json.loads(manifest.read_text(encoding="utf-8"))
                rendered = bool(state.get("render_complete") or state.get("preserve_staging"))
            except (OSError, ValueError):
                pass
        if staging.exists():
            if rendered:
                print(f"[KE] FINALIZE_FAILED staging preserved: {staging}; logs and journals: {work}", flush=True)
            else:
                shutil.rmtree(staging)
        raise
    backup = dataset_root / (".raw-backup-" + uuid.uuid4().hex)
    moved_old = False
    try:
        if raw.exists():
            if not raw.is_dir() or raw.is_symlink():
                raise ValueError("dataset/raw должен быть обычным каталогом")
            raw.rename(backup)
            moved_old = True
        staging.rename(raw)
    except Exception:
        if moved_old and not raw.exists():
            backup.rename(raw)
        raise
    if moved_old:
        shutil.rmtree(backup)
    # Only after raw is safely published are the native journals redundant.
    for journal in work.glob("**/journals/*.jsonl"):
        journal.unlink()
    print(f'[KE] DATASET_RAW_OK frames={result["images"]} maps={result["maps"]} instances={result["editor_processes"]} output={raw}', flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--config", type=Path)
    inputs.add_argument("--config-json")
    parser.add_argument("--replace-raw", action="store_true",
                        help="Allow replacing an existing raw after successful generation")
    args = parser.parse_args()
    try:
        data = json.loads(args.config.read_text(encoding="utf-8") if args.config else args.config_json)
        run_dataset(data, replace_raw=args.replace_raw)
        return 0
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
        print(f"[KE] ERROR {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
