"""Object-guided camera proposals for an offline synthetic dataset.

Render AABBs only propose directions. Live placement checks and full masks
remain authoritative. No map objects are modified.
"""
import json
import math
from pathlib import Path
import random

LAYERS = {"building", "rock", "vegetation", "small", "unknown"}
CELL = 32.0


def angle(x, z):
    return math.degrees(math.atan2(x, z))


def wrap(a):
    return (a + 180.0) % 360.0 - 180.0


class OccluderIndex:
    def __init__(self, objects):
        self.cells = {}
        for o in objects:
            if o.get("duplicate_of") is not None or o.get("analysis_layer") not in LAYERS:
                continue
            lo, hi = o["bbox_min"], o["bbox_max"]
            if len(lo) != 3 or len(hi) != 3 or not all(map(math.isfinite, (*lo, *hi))):
                continue
            if any(a > b for a, b in zip(lo, hi)) or hi[1]-lo[1] < .5:
                continue
            # Very large aggregate bounds are poor local edge proposals.
            if max(hi[0]-lo[0], hi[2]-lo[2]) > 64:
                continue
            box = (lo[0], hi[0], lo[2], hi[2], hi[1]-lo[1])
            for ix in range(math.floor(lo[0]/CELL), math.floor(hi[0]/CELL)+1):
                for iz in range(math.floor(lo[2]/CELL), math.floor(hi[2]/CELL)+1):
                    self.cells.setdefault((ix, iz), []).append(box)

    @classmethod
    def load(cls, root, map_id):
        path = Path(root)/Path(map_id).stem/"objects.jsonl"
        with path.open(encoding="utf-8") as stream:
            return cls(json.loads(line) for line in stream if line.strip())

    def nearby(self, x, z, radius):
        found = set()
        for ix in range(math.floor((x-radius)/CELL), math.floor((x+radius)/CELL)+1):
            for iz in range(math.floor((z-radius)/CELL), math.floor((z+radius)/CELL)+1):
                found.update(self.cells.get((ix, iz), ()))
        def distance(b):
            a,c,d,e,_ = b
            return math.hypot(max(a-x, 0, x-c), max(d-z, 0, z-e))
        return sorted((b for b in found if 2 <= distance(b) < radius),
                      key=lambda b: (distance(b), b))

    def edges(self, x, z, maximum):
        """Angular silhouette edges of nearby horizontal AABBs.

        Each proposal has (bearing, minimum camera distance, object height).
        The camera must lie beyond the proposed edge, not inside its box.
        """
        for a,b,c,d,h in self.nearby(x,z,maximum):
            cx,cz = (a+b)/2-x, (c+d)/2-z
            center = angle(cx,cz)
            corners = [(u-x,v-z) for u in (a,b) for v in (c,d)]
            sides = sorted((wrap(angle(u,v)-center),u,v) for u,v in corners)
            for off,u,v in (sides[0],sides[-1]):
                minimum = math.hypot(u,v)+2
                if minimum <= maximum:
                    yield wrap(center+off), minimum, h



def ranked_edges(index, x, z, cfg):
    # Approximate a low line of sight across a level patch. The true terrain
    # height and silhouette are deliberately left to native validation.
    def rank(edge):
        _, minimum, height = edge
        distance = max(cfg["distance_min"], minimum)
        sight = 1.5 + (cfg["camera_height_min"]-1.5)*max(0.,minimum-2.)/distance
        return (min(height-sight, 5.), -minimum)
    return sorted(index.edges(x,z,cfg["distance_max"]),key=rank,reverse=True)


def prioritize_points(index, bounds, points, target, cfg):
    """Prefer feasible obstacle directions before costly native placement.

    Leave the last quarter in their original order as a fallback. No point
    is discarded by this approximate horizontal scoring.
    """
    if target == "open" or len(points)<4:
        return points
    xmin,xmax,zmin,zmax = bounds
    def score(point):
        u,v,_ = point
        edges = ranked_edges(index,xmin+u*(xmax-xmin),zmin+v*(zmax-zmin),cfg)
        if not edges:
            return (0,0.)
        return (1, min(len(edges),12) + min(edges[0][2],5.)/10.)
    count = len(points)*3//4
    ranked = sorted(range(len(points)),key=lambda i:score(points[i]),reverse=True)[:count]
    chosen = set(ranked)
    return [points[i] for i in ranked]+[p for i,p in enumerate(points) if i not in chosen]


def guided_field(base, index, x, z, target, cfg, seed):
    """Replace up to two thirds of a camera bank with edge-oriented probes.

    The rest stays broad exploration. A ring retains one distance and height,
    so the native angular interpolation is still meaningful. No fabricated
    visibility labels: all proposals must be measured by AVF.
    """
    if target == "open" or len(base) < 8:
        return base
    edges = ranked_edges(index,x,z,cfg)
    if not edges:
        return base
    rng = random.Random(seed)
    # Randomize within a small nearest-obstacle shortlist rather than always
    # generating the same view of the closest building.
    edges = edges[:12]
    rng.shuffle(edges)
    ring_ids = sorted({c["avf_ring"] for c in base})
    # One ring still reserves at least a quarter of its samples for exploration.
    replace_rings = ring_ids[:max(1,len(ring_ids)-1)]
    result = [dict(c) for c in base]
    for k,r in enumerate(replace_rings):
        ids = [i for i,c in enumerate(base) if c["avf_ring"]==r]
        bearing, minimum, _ = edges[k % len(edges)]
        lo = max(cfg["distance_min"], minimum)
        hi = cfg["distance_max"]
        dist = rng.uniform(lo, hi)
        # Lower views are more likely to retain overlap; stay inside user bounds.
        h0,h1 = cfg["camera_height_min"],cfg["camera_height_max"]
        height = rng.uniform(h0, h0+(h1-h0)*(.35 if target=="light" else .2))
        elevation = base[ids[0]]["elevation_deg"]
        # AABB tangent is a starting guess, not an exact silhouette boundary.
        # Straddle it at several scales, retaining uniform angular exploration.
        offsets = (0., -4., 4., -9., 9., -18., 18., -32., 32.)
        directed = min(len(offsets), max(1,len(ids)*3//4))
        for j,i in enumerate(ids):
            a = wrap(bearing+offsets[j]) if j<directed else base[i]["azimuth_deg"]
            a = round(a,4)
            radians = math.radians(a)
            result[i] = {"avf_ring":r, "azimuth_deg":a,
                         "horizontal_distance":round(dist,4),
                         "elevation_deg":elevation,
                         "height_above_ground":round(height,5),
                         "offset":(round(dist*math.sin(radians),6),
                                   round(dist*math.tan(math.radians(elevation)),6),
                                   round(dist*math.cos(radians),6))}
    return result


def candidate_fields(base, index, bounds, points, target, cfg, seed):
    xmin,xmax,zmin,zmax = bounds
    return [guided_field(base,index,xmin+u*(xmax-xmin),zmin+v*(zmax-zmin),
                         target,cfg,f"{seed}:{j}")
            for j,(u,v,_) in enumerate(points)]


def fields_block(fields):
    rows = []
    for p, field in enumerate(fields):
        for i,c in enumerate(field):
            rows += [f'guided_offset_{p}_{i}:p3='+",".join(f"{v:.6f}" for v in c["offset"]),
                     f'guided_height_{p}_{i}:r={c["height_above_ground"]}',
                     f'guided_ring_{p}_{i}:i={c["avf_ring"]}',
                     f'guided_angle_{p}_{i}:r={c["azimuth_deg"]}']
    return "\n".join(rows)+"\n"
