"""Deterministic, bounded candidate selection using measured visibility.

Only validated mask measurements are accepted. The budget is a finite
candidate pool, not an adaptive native retry loop.
"""
import math
from collections import Counter, defaultdict, deque

from tools.ke_visibility import VISIBILITY_BINS, DEFAULT_VISIBILITY_WEIGHTS


def validate_visibility_weights(weights):
    if not isinstance(weights, dict) or set(weights) != set(VISIBILITY_BINS):
        raise ValueError("visibility_weights: specify open, light, moderate, heavy")
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(v) or v < 0 for v in weights.values()):
        raise ValueError("visibility_weights: finite nonnegative weights required")
    if sum(weights.values()) <= 0:
        raise ValueError("visibility_weights: at least one positive weight required")
    return {name: float(weights[name]) for name in VISIBILITY_BINS}


def visibility_targets(count, weights=DEFAULT_VISIBILITY_WEIGHTS):
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 1000:
        raise ValueError("visibility target count must be 1..1000")
    weights = validate_visibility_weights(weights)
    # Normalize first to avoid overflow in count * weight.
    scale = max(weights.values())
    normalized = {k: v / scale for k, v in weights.items()}
    total = sum(normalized.values())
    exact = {k: count * normalized[k] / total for k in VISIBILITY_BINS}
    result = {k: math.floor(exact[k]) for k in VISIBILITY_BINS}
    remaining = count - sum(result.values())
    ranked = sorted(VISIBILITY_BINS, key=lambda k: (-(exact[k] - result[k]), VISIBILITY_BINS.index(k)))
    for k in ranked[:remaining]:
        result[k] += 1
    return result


def candidate_allocation(target_jobs, multiplier):
    # The existing native and screen-position planners allow at most 1000
    # candidates per map. Never exceed the configured multiplier or native cap.
    return [(mid, min(1000, n * multiplier)) for mid, n in target_jobs]


def choose_candidates(records, targets):
    """Return accepted image IDs and auditable report, without mutating records.

    Prefer maps with fewer selected images, and process the scarcest bins
    first. Map balance is best-effort; global visibility counts are strict.
    """
    bins = (*VISIBILITY_BINS, "reject_too_occluded")
    available = Counter()
    by_bin = defaultdict(lambda: defaultdict(deque))
    seen = set()
    for record in records:
        image_id = record["image_id"]
        if image_id in seen:
            raise ValueError("Duplicate candidate image ID")
        seen.add(image_id)
        measurement = record["visibility_measurement"]
        name = measurement["visibility_bin"]
        if name not in bins:
            raise ValueError("Unexpected candidate visibility bin")
        available[name] += 1
        if name in VISIBILITY_BINS and measurement["training_eligible"]:
            by_bin[name][record["map_id"]].append(image_id)
    selected = set()
    actual = Counter()
    per_map = Counter()
    order = sorted(VISIBILITY_BINS, key=lambda k: (
        sum(len(q) for q in by_bin[k].values()) / targets[k] if targets[k] else math.inf,
        VISIBILITY_BINS.index(k)))
    for name in order:
        groups = by_bin[name]
        for _ in range(targets[name]):
            choices = [mid for mid, q in groups.items() if q]
            if not choices:
                break
            mid = min(choices, key=lambda m: (per_map[m], m))
            selected.add(groups[mid].popleft())
            per_map[mid] += 1
            actual[name] += 1
    deficits = {k: targets[k] - actual[k] for k in VISIBILITY_BINS}
    report = {
        "method": "bounded_candidate_pool_v1",
        "target": dict(targets),
        "available": {k: available[k] for k in bins},
        "accepted": {k: actual[k] for k in VISIBILITY_BINS},
        "deficit": deficits,
        "complete": not any(deficits.values()),
        "candidates": len(records),
        "selected": len(selected),
        "rejected": len(records) - len(selected),
        "selected_by_map": dict(sorted(per_map.items())),
    }
    return selected, report
