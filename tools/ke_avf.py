"""Bounded, renderer-independent AVF search for offline synthetic scenes.

One search represents one immutable object pose and scene. The callback
evaluates batches of virtual views; None means invalid/unavailable, not
fully occluded. No estimate grants quota credit. This is a reference search,
not a Dagor GPU implementation.
"""
from dataclasses import dataclass
import math
import random

BANDS = {"open": (.9, 1.), "light": (.65, .9),
         "moderate": (.3, .65), "heavy": (.1, .3)}


@dataclass(frozen=True, order=True)
class View:
    ring: int
    azimuth_deg: float


@dataclass(frozen=True)
class Probe:
    view: View
    fraction: float | None


@dataclass(frozen=True)
class SearchResult:
    best: Probe | None
    matched: bool
    probes: tuple[Probe, ...]


def search_visibility(probe_batch, target, *, rings=3, coarse=12,
                      budget=64, batch_size=8, seed=0, min_step=1.):
    """Sample cyclic angular rings, then refine promising intervals.

    The caller defines each ring's camera distance/elevation and checks
    feasibility. The callback must preserve input order. No RGB is requested
    by this routine. The callback may implement a CPU or GPU estimator.
    """
    if target not in BANDS:
        raise ValueError("Unknown visibility target")
    if any(type(v) is not int or v < 1 for v in
           (rings, coarse, budget, batch_size)):
        raise ValueError("Invalid search dimensions")
    if coarse < 3 or rings * coarse > budget or not 0 < min_step < 180:
        raise ValueError("Invalid coarse grid or angular step")
    lo, hi = BANDS[target]
    center = (lo + hi) / 2
    rng = random.Random(seed)
    phase = rng.uniform(0, 360 / coarse)
    seen = {}
    ordered = []

    def evaluate(views):
        for i in range(0, len(views), batch_size):
            group = tuple(views[i:i+batch_size])
            values = list(probe_batch(group))
            if len(values) != len(group):
                raise ValueError("Probe result count mismatch")
            for view, value in zip(group, values):
                if value is not None and (isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(value) or not 0 <= value <= 1):
                    raise ValueError("Invalid visibility fraction")
                seen[view] = value
                ordered.append(Probe(view, value))

    def matches(value):
        return value is not None and lo <= value and (
            value < hi or (target == "open" and value == hi))

    def distance(value):
        return abs(value - center) if value is not None else math.inf

    evaluate([View(r, round((phase + 360*i/coarse) % 360, 9))
              for i in range(coarse) for r in range(rings)])
    while len(ordered) < budget and not any(matches(p.fraction) for p in ordered):
        intervals = []
        for r in range(rings):
            keys = sorted(v for v in seen if v.ring == r)
            for a, b in zip(keys, keys[1:] + keys[:1]):
                width = (b.azimuth_deg - a.azimuth_deg) % 360
                if width / 2 < min_step:
                    continue
                mid = View(r, round((a.azimuth_deg + width/2) % 360, 9))
                if mid in seen:
                    continue
                va, vb = seen[a], seen[b]
                crossing = va is not None and vb is not None and (
                    min(va, vb) <= center <= max(va, vb))
                proximity = min(distance(va), distance(vb))
                intervals.append((not crossing, proximity, -width, mid))
        if not intervals:
            break
        intervals.sort()
        n = min(batch_size, budget-len(ordered), len(intervals))
        chosen = []
        # Reserve one of each multi-probe batch for exploration of a wide
        # interval. This reduces (but does not eliminate) narrow-region misses.
        if n > 1:
            explorer = min(intervals, key=lambda x: (x[2], x[3]))
            chosen.append(explorer[3])
        for entry in intervals:
            if len(chosen) == n:
                break
            if entry[3] not in chosen:
                chosen.append(entry[3])
        evaluate(chosen)
    valid = [p for p in ordered if p.fraction is not None]
    best = min(valid, key=lambda p: (not matches(p.fraction),
                                    distance(p.fraction), p.view)) if valid else None
    return SearchResult(best, bool(best and matches(best.fraction)), tuple(ordered))


def plan_camera_field(seed, count, attempts, distance_range, elevation_range, height_range):
    """Deterministic finite angular bank for the native adaptive scheduler.

    Rings share fixed horizontal distance and requested height above ground.
    Far-apart directions are listed first. The renderer may reorder indices,
    but metadata always refers to the original immutable bank.
    """
    if not 1 <= attempts <= 64 or count < 0:
        raise ValueError("Invalid field dimensions")
    for lo, hi in (distance_range, elevation_range, height_range):
        if not math.isfinite(lo) or not math.isfinite(hi) or lo > hi:
            raise ValueError("Invalid field range")
    if not 2 <= distance_range[0] <= distance_range[1] <= 10000:
        raise ValueError("Invalid distance range")
    if not 0 <= elevation_range[0] <= elevation_range[1] <= 80 or height_range[0] < 0:
        raise ValueError("Invalid camera height/elevation")
    rng = random.Random(f"avf-bank-v1:{seed}")
    rings = min(3, max(1, attempts // 8))
    sizes = [attempts // rings + int(r < attempts % rings) for r in range(rings)]

    def stratified_angles(n):
        # Maximize angular distance to the already proposed directions.
        remaining = list(range(n))
        chosen = []
        while remaining:
            i = max(remaining, key=lambda k: (
                min((min(abs(k-j), n-abs(k-j)) for j in chosen), default=n),
                -k))
            chosen.append(i)
            remaining.remove(i)
        return chosen

    orders = [stratified_angles(n) for n in sizes]
    frames = []
    for _ in range(count):
        phase = rng.uniform(-180, 180)
        ring_data = []
        heights = list(range(rings))
        rng.shuffle(heights)
        for r in range(rings):
            d = distance_range[0] + (distance_range[1]-distance_range[0])*(r+rng.random())/rings
            h = height_range[0] + (height_range[1]-height_range[0])*(heights[r]+rng.random())/rings
            e = rng.uniform(*elevation_range)
            ring_data.append((round(d, 4), round(h, 5), round(e, 4)))
        views = []
        for j in range(max(sizes)):
            for r, n in enumerate(sizes):
                if j >= n:
                    continue
                azimuth = round((phase+360*orders[r][j]/n+180) % 360-180, 4)
                d, h, e = ring_data[r]
                angle = math.radians(azimuth)
                views.append({"avf_ring": r, "azimuth_deg": azimuth,
                              "horizontal_distance": d, "elevation_deg": e,
                              "height_above_ground": h,
                              "offset": (round(d*math.sin(angle), 6),
                                         round(d*math.tan(math.radians(e)), 6),
                                         round(d*math.cos(angle), 6))})
        frames.append(views)
    return frames
