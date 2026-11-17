"""Assemble one unsplit, standalone dataset/raw from validated Dagor runs.

No train/val/test and no release management. Replace raw only after a successful
generation and complete validation, with rollback on failed publish.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ke_generate import validate_config
from ke_multi_generate import run_multi

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "dataset"


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def link_or_copy(source, dest):
    try:
        os.link(source, dest)
    except OSError:
        shutil.copyfile(source, dest)


def pack_raw(run_root, dest):
    """Publish verified runs as a single portable COCO and metadata package."""
    run_root, dest = Path(run_root).resolve(), Path(dest).resolve()
    if dest.exists():
        raise ValueError(f"Каталог сборки уже существует: {dest}")
    manifest = json.loads((run_root / "multi-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("complete") is not True:
        raise ValueError("Нельзя собрать raw из незавершённого запуска")
    coco = json.loads((run_root / "coco" / "all.json").read_text(encoding="utf-8"))
    rows, maps = [], []
    for item in manifest["maps"]:
        if item["planned"] == 0:
            continue
        if item.get("status") != "verified" or item.get("actual") != item["planned"]:
            raise ValueError(f"Не подтверждена карта {item['map_id']}")
        subrun = (run_root / item["run"]).resolve()
        if not subrun.is_relative_to(run_root):
            raise ValueError("Некорректный путь подпроцесса")
        records = [json.loads(line) for line in (subrun / "metadata.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(records) != item["actual"]:
            raise ValueError("Количество кадров не совпало с метаданными")
        for rec in records:
            source = (subrun / rec["image"]).resolve()
            if not source.is_relative_to((subrun / "images").resolve()) or not source.is_file():
                raise ValueError("Путь изображения отсутствует или выходит за каталог")
            rows.append((item["map_id"], rec, source))
        bound_path = subrun / "tank-map-bounds.json"
        maps.append({"map_id": item["map_id"], "frames": item["actual"],
                     "tank_bounds": json.loads(bound_path.read_text(encoding="utf-8")) if bound_path.is_file() else None})
    if len(rows) != len(coco["images"]) or len(rows) != manifest["requested_frames"]:
        raise ValueError("Неверное количество изображений в COCO")
    annotations = {}
    for ann in coco["annotations"]:
        annotations.setdefault(ann["image_id"], []).append(ann)
    dest.mkdir(parents=True)
    try:
        for dirname in ("images", "annotations", "metadata"):
            (dest / dirname).mkdir()
        images, boxes, image_meta, scenes = [], [], [], {}
        next_ann = 1
        for idx, (old, (map_id, rec, source)) in enumerate(zip(coco["images"], rows), start=1):
            referenced = (run_root / "coco" / old["file_name"]).resolve()
            if referenced != source:
                raise ValueError("Несовпадение COCO image и исходного PNG")
            filename = f"{idx:08d}.png"
            output = dest / "images" / filename
            link_or_copy(source, output)
            scene_id = f"{run_root.name}:{Path(map_id).stem}:{rec.get('composition_index', 0)}"
            group_id = old.get("scene_group")
            images.append({**old, "id": idx, "file_name": f"images/{filename}",
                           "map_id": map_id, "scene_id": scene_id, "scene_group_id": group_id})
            image_meta.append({**rec, "image_id": idx, "image": f"images/{filename}",
                               "map_id": map_id, "scene_id": scene_id,
                               "scene_group_id": group_id, "sha256": sha256(output)})
            scene = scenes.setdefault(scene_id, {"scene_id": scene_id, "map_id": map_id,
                                                  "scene_group_id": group_id, "image_ids": []})
            scene["image_ids"].append(idx)
            for ann in annotations.get(old["id"], []):
                boxes.append({**ann, "id": next_ann, "image_id": idx})
                next_ann += 1
        save_json(dest / "annotations" / "instances.json",
                  {"info": {**coco.get("info", {}), "dataset": "KolobanovEye raw", "unsplit": True},
                   "licenses": coco.get("licenses", []), "images": images,
                   "annotations": boxes, "categories": coco["categories"]})
        with (dest / "metadata" / "images.jsonl").open("w", encoding="utf-8") as out:
            for record in image_meta:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
        with (dest / "metadata" / "scenes.jsonl").open("w", encoding="utf-8") as out:
            for scene in scenes.values():
                out.write(json.dumps(scene, ensure_ascii=False) + "\n")
        save_json(dest / "metadata" / "maps.json", maps)
        cfg = json.loads((run_root / "generation-config.json").read_text(encoding="utf-8"))
        for key in ("game", "dagor", "output"):
            cfg.pop(key, None)
        save_json(dest / "metadata" / "generation_config.json", cfg)
        result = {"schema_version": 1, "kind": "raw", "split": None,
                  "run_id": run_root.name, "images": len(images),
                  "annotations": len(boxes), "scenes": len(scenes), "maps": len(maps),
                  "created_utc": datetime.now(timezone.utc).isoformat()}
        save_json(dest / "manifest.json", result)
        return result
    except Exception:
        shutil.rmtree(dest)
        raise


def run_dataset(config, dataset_root=DATASET, *, replace_raw=False, executor=None):
    """Use a new technical run, then replace raw with a rollback-safe rename."""
    cfg = validate_config(config)
    dataset_root = Path(dataset_root).resolve()
    raw = dataset_root / "raw"
    if raw.exists() and not replace_raw:
        raise ValueError("dataset/raw существует; для замены требуется --replace-raw")
    run_id = "run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    work = dataset_root / "runs" / run_id
    dataset_root.mkdir(parents=True, exist_ok=True)
    (dataset_root / "runs").mkdir(exist_ok=True)
    run_multi({**cfg, "output": str(work)}, executor=executor)
    staging = dataset_root / (".raw-staging-" + uuid.uuid4().hex)
    report = pack_raw(work, staging)
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
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    if moved_old:
        shutil.rmtree(backup)
    print(f"[KE] DATASET_RAW_OK frames={report['images']} maps={report['maps']} output={raw}", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--config", type=Path)
    sources.add_argument("--config-json")
    parser.add_argument("--replace-raw", action="store_true",
                        help="Allow replacing an existing raw after successful generation")
    args = parser.parse_args()
    try:
        raw = json.loads(args.config.read_text(encoding="utf-8") if args.config else args.config_json)
        run_dataset(raw, replace_raw=args.replace_raw)
        return 0
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        print(f"[KE] ERROR {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
