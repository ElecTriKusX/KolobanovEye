"""Group building-like Dagor instances into candidate urban zones."""
import json
import math
import re
from collections import defaultdict

BUILDING_NAME = re.compile(
    r"(?:^|[/_. -])(?:house|building|barn|church|farmhouse|warehouse|hangar|chapel|hut)s?\d*(?:$|[/_. -])",
    re.IGNORECASE,
)
CELL_SIZE = 128
MIN_BUILDINGS = 3


def write_report(instances, output):
    cells = defaultdict(set)
    scanned = 0
    matched = set()
    with instances.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            parts = line.rstrip("\r\n").split("\t", 2)
            if len(parts) != 3:
                continue
            x, z = map(float, parts[:2])
            if not math.isfinite(x) or not math.isfinite(z):
                continue
            scanned += 1
            name = parts[2]
            if BUILDING_NAME.search(name):
                point = (round(x, 2), round(z, 2))
                matched.add(point)
                cells[math.floor(x / CELL_SIZE), math.floor(z / CELL_SIZE)].add(point)

    remaining = {key for key, points in cells.items() if len(points) >= MIN_BUILDINGS}
    zones = []
    while remaining:
        queue = [min(remaining)]
        remaining.remove(queue[0])
        points = set()
        for ix, iz in queue:
            points.update(cells[ix, iz])
            for neighbor in ((ix - 1, iz), (ix + 1, iz), (ix, iz - 1), (ix, iz + 1)):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
        xs, zs = zip(*sorted(points))
        zones.append({
            "center_xz": [round(sum(xs) / len(xs), 2), round(sum(zs) / len(zs), 2)],
            "bounds_xz": [min(xs), max(xs), min(zs), max(zs)],
            "candidate_buildings": len(points),
        })
    zones.sort(key=lambda zone: (-zone["candidate_buildings"], zone["center_xz"]))
    report = {
        "method": "loaded_rendinst_names_and_connected_dense_cells",
        "scanned_instances": scanned,
        "matched_positions": len(matched),
        "cell_size": CELL_SIZE,
        "minimum_buildings_per_cell": MIN_BUILDINGS,
        "zones": zones,
    }
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
