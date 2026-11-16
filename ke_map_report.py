"""Local tank-placement coverage report for KolobanovEye.

Reads existing frame.blk files and future metadata.jsonl manifests. The report
never guesses map/image alignment: a background requires explicit world bounds.
No game assets are uploaded to GitHub or external services.
"""
import argparse
import base64
import html
import json
import math
import re
import struct
from collections import defaultdict
from pathlib import Path

FLOAT = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
POINT3 = re.compile(r"^\s*tank_world:p3\s*=\s*(" + FLOAT + r")\s*,\s*(" + FLOAT + r")\s*,\s*(" + FLOAT + r")", re.M)


def field(text, name):
    match = re.search(r'^\s*' + re.escape(name) + r':t\s*=\s*"([^"]*)"', text, re.M)
    return match.group(1) if match else None


def map_from_level(level):
    return Path(str(level).replace("\\", "/")).stem if level else None


def parse_frame_blk(path):
    content = path.read_text(encoding="utf-8-sig", errors="replace")
    found = POINT3.search(content)
    if not found:
        raise ValueError(f"Missing tank_world:p3 in {path}")
    x, y, z = map(float, found.groups())
    image = path.with_name("frame.png")
    return [{"x": x, "y": y, "z": z,
             "map": map_from_level(field(content, "level")),
             "model": field(content, "model"),
             "frame_id": path.parent.name,
             "image": str(image) if image.is_file() else None,
             "status": "saved" if image.is_file() else "missing_image"}]


def position_dict(value):
    if isinstance(value, dict):
        return float(value["x"]), float(value.get("y", 0)), float(value["z"])
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return tuple(map(float, value))
    raise ValueError("Position must be an {x,y,z} object or three coordinates")


def parse_jsonl(path):
    records = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            tanks = item.get("tanks")
            if tanks is None:
                tanks = [item["tank"]] if "tank" in item else [item] if "tank_world" in item else []
            for tank in tanks:
                pos = tank.get("position", tank.get("tank_world"))
                x, y, z = position_dict(pos)
                if not all(map(math.isfinite, (x, y, z))):
                    raise ValueError("Non-finite position")
                records.append({
                    "x": x, "y": y, "z": z,
                    "map": item.get("map") or item.get("map_id") or map_from_level(item.get("level")),
                    "model": tank.get("model") or item.get("model"),
                    "frame_id": str(item.get("frame_id", f"{path.stem}:{line_no}")),
                    "image": None,
                    "status": str(item.get("status", "saved")),
                })
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError(f"{path}:{line_no}: invalid tank metadata: {exc}") from exc
    return records


def collect(sources):
    paths = set()
    for source in sources:
        if not source.exists():
            raise ValueError(f"Input does not exist: {source}")
        if source.is_dir():
            paths.update(source.rglob("frame.blk"))
            paths.update(source.rglob("metadata.jsonl"))
        else:
            paths.add(source)
    records = []
    for path in sorted(paths):
        if path.name == "frame.blk":
            records.extend(parse_frame_blk(path))
        elif path.suffix.lower() == ".jsonl":
            records.extend(parse_jsonl(path))
        else:
            raise ValueError(f"Unsupported input file: {path}")
    return records


def determine_bounds(records, explicit):
    if explicit:
        xmin, zmin, xmax, zmax = explicit
        if not all(map(math.isfinite, explicit)) or xmin >= xmax or zmin >= zmax:
            raise ValueError("Bounds must be finite: xmin < xmax and zmin < zmax")
        return tuple(explicit)
    xs = [r["x"] for r in records]
    zs = [r["z"] for r in records]
    xmin, xmax, zmin, zmax = min(xs), max(xs), min(zs), max(zs)
    pad_x = max((xmax - xmin) * 0.10, 1.0)
    pad_z = max((zmax - zmin) * 0.10, 1.0)
    return xmin - pad_x, zmin - pad_z, xmax + pad_x, zmax + pad_z


def background_png(path):
    if path is None:
        return 1200, 850, ""
    with path.open("rb") as stream:
        head = stream.read(24)
    if head[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
        raise ValueError("Map image must be a PNG; export/convert the local map to PNG first")
    width, height = struct.unpack(">II", head[16:24])
    if not 1 <= width <= 16000 or not 1 <= height <= 16000:
        raise ValueError("Invalid map-image dimensions")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return width, height, f'<image href="data:image/png;base64,{encoded}" width="{width}" height="{height}"/>'


def build_report(records, map_name, bounds, image_path, output, x_left=False, z_down=False):
    if not records:
        raise ValueError("No tank positions found; check your input paths")
    names = {r["map"] for r in records if r["map"]}
    if map_name is None:
        if len(names) > 1:
            raise ValueError("Several maps present; pass --map-name to select one")
        map_name = next(iter(names), "unknown_map")
    selected = [r for r in records if r["map"] in (None, map_name) or r["map"] == map_name]
    if not selected:
        raise ValueError(f"No positions for map {map_name}")
    if image_path and bounds is None:
        raise ValueError("--background requires --bounds: image alignment cannot be inferred")
    for record in selected:
        if not all(map(math.isfinite, (record["x"], record["y"], record["z"]))):
            raise ValueError("Non-finite tank position")
    xmin, zmin, xmax, zmax = determine_bounds(selected, bounds)
    width, height, bg = background_png(image_path)
    groups = defaultdict(list)
    outside = []
    for record in selected:
        u = (record["x"] - xmin) / (xmax - xmin)
        v = (record["z"] - zmin) / (zmax - zmin)
        if not 0 <= u <= 1 or not 0 <= v <= 1:
            outside.append(record)
            continue
        if x_left:
            u = 1 - u
        if not z_down:
            v = 1 - v
        key = (round(record["x"]), round(record["z"]))
        groups[key].append((record, u * width, v * height))
    markers, details = [], []
    for i, (_, items) in enumerate(sorted(groups.items())):
        x = sum(p[1] for p in items) / len(items)
        y = sum(p[2] for p in items) / len(items)
        info = {"count": len(items), "records": [p[0] for p in items]}
        details.append(info)
        has_failure = any(p[0]["status"] != "saved" for p in items)
        color = "#e8795d" if has_failure else "#45d5a1"
        radius = min(16, 5 + math.sqrt(len(items)) * 1.4)
        markers.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius:.2f}" fill="{color}" '
                       f'stroke="#091726" stroke-width="2" onclick="showDetails({i})">'
                       f'<title>{len(items)} records, X={items[0][0]["x"]:.1f}, Z={items[0][0]["z"]:.1f}</title></circle>')
    grid = ""
    if not bg:
        for k in range(1, 10):
            xx, yy = width * k / 10, height * k / 10
            grid += f'<path d="M {xx} 0 V {height} M 0 {yy} H {width}" stroke="#36536a" stroke-width="1"/>'
    safe_json = json.dumps(details, ensure_ascii=False).replace("<", "\\u003c")
    title = html.escape(map_name)
    calibration = "CALIBRATED IMAGE" if image_path else "SCHEMATIC ONLY - NOT A GAME MINIMAP"
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>KolobanovEye map coverage — {title}</title>
<style>
body{{background:#0b1421;color:#dce7f1;font:15px system-ui;margin:0;padding:26px}}
h1{{margin:0 0 8px;font-size:26px}}small{{color:#9eb5cb}}
main{{display:grid;grid-template-columns:minmax(0,3fr) minmax(260px,1fr);gap:20px;margin-top:20px}}
svg{{width:100%;height:auto;background:#152638;border:1px solid #34506a;border-radius:10px}}
circle{{cursor:pointer;opacity:.85}}circle:hover{{stroke:#fff;opacity:1}}
aside{{background:#132337;border:1px solid #34506a;border-radius:10px;padding:20px;word-break:break-word}}
pre{{white-space:pre-wrap;font-size:12px}}a{{color:#8fd4ff}}
@media(max-width:850px){{main{{grid-template-columns:1fr}}}}
</style></head><body>
<h1>KolobanovEye · {title}</h1>
<small>{calibration} · {len(selected)} tank records · {len(groups)} position groups · {len(outside)} outside bounds</small>
<main><svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
{bg}{grid}{''.join(markers)}</svg>
<aside><h2>Position details</h2><div id="details">Select a marker to inspect its frames.</div></aside></main>
<script type="application/json" id="positions">{safe_json}</script>
<script>
const positions=JSON.parse(document.getElementById('positions').textContent);
function showDetails(i){{
 const group=positions[i], root=document.getElementById('details');
 root.replaceChildren();
 const count=document.createElement('p'); count.textContent=group.count+' tank records';root.append(count);
 for(const p of group.records){{
  const row=document.createElement('p');
  row.textContent=(p.frame_id||'?')+' · '+(p.model||'unknown')+
     ' · X='+p.x.toFixed(2)+' Z='+p.z.toFixed(2)+' · '+p.status;
  root.append(row);
 }}
}}
</script></body></html>
"""
    output.mkdir(parents=True, exist_ok=True)
    (output / "placements.html").write_text(page, encoding="utf-8")
    summary = {"map": map_name, "calibrated": image_path is not None,
               "bounds_xz": [xmin, zmin, xmax, zmax],
               "x_left": x_left, "z_down": z_down,
               "records": selected, "outside_bounds": len(outside),
               "groups": len(groups)}
    (output / "placements.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", type=Path, help="Run folders, frame.blk, or metadata.jsonl")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--map-name", help="Select one map when records span several maps")
    parser.add_argument("--background", type=Path, help="Local PNG image, not committed or uploaded")
    parser.add_argument("--bounds", nargs=4, type=float, metavar=("XMIN", "ZMIN", "XMAX", "ZMAX"))
    parser.add_argument("--x-left", action="store_true", help="World X increases leftward in image")
    parser.add_argument("--z-down", action="store_true", help="World Z increases downward in image")
    args = parser.parse_args(argv)
    try:
        report = build_report(collect(args.sources), args.map_name, args.bounds, args.background,
                              args.out, args.x_left, args.z_down)
        print(f'[KE] MAP_COVERAGE_OK map={report["map"]} frames={len(report["records"])} '
              f'positions={report["groups"]} outside={report["outside_bounds"]}')
        print(f'[KE] Report: {args.out / "placements.html"}')
        return 0
    except (OSError, ValueError) as exc:
        parser.exit(1, f"[KE] ERROR {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
