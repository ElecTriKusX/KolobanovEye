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
