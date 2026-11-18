"""Visualize unique tank placement positions on Dagor tank-square overview maps.

Reads the current dataset/raw and its matching dataset/runs/<run_id>/maps.
Only clicking "Провести анализ" creates dataset/analysis/.
"""
import argparse
import html
import json
import math
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

from tools.ke_coco import scene_metadata


def _render_map(image, output, bounds, points, title):
    xmin, xmax, zmin, zmax = bounds
    if not all(math.isfinite(n) for n in bounds) or xmin >= xmax or zmin >= zmax:
        raise ValueError("Invalid tank-square map bounds")
    mapped = []
    for item in points:
        u = (item["x"] - xmin) / (xmax - xmin)
        v = 1 - (item["z"] - zmin) / (zmax - zmin)  # Dagor overview's orientation
        mapped.append({**item, "u": u, "v": v})
    relative = os.path.relpath(image.resolve(), output.resolve().parent).replace(os.sep, "/")
    image_url = html.escape(quote(relative, safe="/"), quote=True)
    title_html = html.escape(title)
    data = json.dumps(mapped, ensure_ascii=True).replace("<", "\\u003c")
    page = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MAP_TITLE — KolobanovEye</title>
<style>
body{background:#171d25;color:#e9edf4;font:15px system-ui,sans-serif;margin:24px}
a{color:#b6d2ff}.map{position:relative;display:inline-block;max-width:min(95vw,1100px)}
.map img{display:block;width:100%;height:auto;border:1px solid #485365}
.dot{position:absolute;transform:translate(-50%,-50%);height:11px;width:11px;border-radius:50%;
background:#e94c4c;border:2px solid white;box-shadow:0 1px 4px #171d25}
.summary{color:#b3bdca}h1{font-size:22px;font-weight:600}
</style>
<h1>MAP_TITLE</h1>
<p class="summary">Красные точки — уникальные позиции танков. Наведи на точку для координат и числа кадров.
Ориентацию обзорной текстуры необходимо подтвердить на реальной карте Dagor.</p>
<div class="map"><img src="MAP_IMAGE" alt="Обзор танкового поля"></div>
<p id="stats" class="summary"></p>
<script>
const points=MAP_POINTS;
const layer=document.querySelector(".map");
let outside=0;
for(const p of points){
 if(p.u<0||p.u>1||p.v<0||p.v>1){outside++;continue;}
 const el=document.createElement("div");
 el.className="dot";
 el.style.left=(p.u*100)+"%";
 el.style.top=(p.v*100)+"%";
 el.title="X="+p.x.toFixed(2)+", Z="+p.z.toFixed(2)+", кадров="+p.frames;
 layer.appendChild(el);
}
document.getElementById("stats").textContent =
 "Позиций: "+points.length+"; за границами карты: "+outside;
</script>
""".replace("MAP_TITLE", title_html).replace("MAP_IMAGE", image_url).replace("MAP_POINTS", data)
    output.write_text(page, encoding="utf-8")
    return {"positions": len(mapped),
            "outside": sum(p["u"] < 0 or p["u"] > 1 or p["v"] < 0 or p["v"] > 1 for p in mapped)}


def analyze_dataset(dataset):
    dataset = Path(dataset).resolve()
    raw = dataset / "raw"
    manifest = json.loads((raw / "manifest.json").read_text(encoding="utf-8"))
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or re.fullmatch(r"run-[A-Za-z0-9T-]+", run_id) is None:
        raise ValueError("Отсутствует допустимый идентификатор последнего запуска")
    run = dataset / "runs" / run_id
    if not run.is_dir():
        raise ValueError(f"Не найден технический запуск {run_id}")
    configs = json.loads((raw / "metadata" / "maps.json").read_text(encoding="utf-8"))
    frames = [json.loads(line) for line in (raw / "metadata" / "images.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if not frames:
        raise ValueError("В исходном датасете нет изображений")
    output = dataset / "analysis"
    staging = dataset / (".analysis-staging-" + uuid.uuid4().hex)
    staging.mkdir(parents=True)
    try:
        (staging / "maps").mkdir()
        summaries, index_links = [], []
        for i, item in enumerate(configs):
            mid = item["map_id"]
            stem = Path(mid).stem
            square = (item.get("tank_bounds") or {}).get("tank_square")
            if not square:
                raise ValueError(f"Нет координат танкового поля {mid}")
            bounds = [square[name] for name in ("min_x", "max_x", "min_z", "max_z")]
            image_name = item.get("overview")
            image = (run / image_name).resolve() if image_name else (run / "maps" / f"{stem}-tank.png").resolve()
            if not image.is_relative_to((run / "maps").resolve()) or not image.is_file():
                raise ValueError(f"Не найдена обзорная карта {mid}: {image}")
            locations = {}
            for frame in frames:
                if frame.get("map_id") != mid:
                    continue
                xyz = scene_metadata(frame["dagor_frame_metadata_blk"])["tank_world_xyz"]
                if xyz is None:
                    raise ValueError(f"Нет координат танка {mid}")
                sid = frame.get("scene_id") or f'{mid}:{frame.get("composition_index", 0)}'
                if sid not in locations:
                    locations[sid] = {"x": xyz[0], "z": xyz[2], "frames": 0}
                locations[sid]["frames"] += 1
            if not locations:
                raise ValueError(f"Нет позиций для карты {mid}")
            name = f"{i+1:02d}-{stem}-positions.html"
            report = _render_map(image, staging / "maps" / name, bounds, list(locations.values()), mid)
            summaries.append({"map_id": mid, "images": sum(p["frames"] for p in locations.values()),
                              **report, "file": f"maps/{name}"})
            index_links.append(f'<li><a href="maps/{html.escape(name)}">{html.escape(mid)}</a>'
                               f' — позиций: {report["positions"]}</li>')
        index = ('<!doctype html><meta charset="utf-8"><title>KolobanovEye — Анализ</title>'
                 '<style>body{font:16px system-ui,sans-serif;max-width:850px;margin:35px auto}</style>'
                 '<h1>Позиции танков на картах</h1><ul>' + "\n".join(index_links) + "</ul>")
        (staging / "index.html").write_text(index, encoding="utf-8")
        report = {"run_id": run_id, "maps": summaries, "total_images": len(frames)}
        (staging / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        if output.exists():
            shutil.rmtree(output)
        staging.rename(output)
        print(f'[KE] ANALYSIS_OK maps={len(summaries)} images={len(frames)} output={output}', flush=True)
        return report
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", type=Path, required=True)
    args = p.parse_args()
    try:
        analyze_dataset(args.dataset)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"[KE] ERROR {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
