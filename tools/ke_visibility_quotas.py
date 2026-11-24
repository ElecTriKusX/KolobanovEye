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


def choose_candidates(records, targets, map_targets=None):
    """Exact global visibility AND map totals, using integral maximum flow.

    A map can contribute any mixture of bins. Residual edges let us exchange
    earlier choices instead of rejecting a feasible allocation greedily.
    Production callers supply the planned maps, including maps with no valid
    candidates. The optional default is for standalone selection of a pool.
    """
    bins = (*VISIBILITY_BINS, "reject_too_occluded")
    if set(targets) != set(VISIBILITY_BINS) or any(
            type(n) is not int or n < 0 for n in targets.values()):
        raise ValueError("Invalid visibility targets")
    total = sum(targets.values())
    if map_targets is None:
        maps = sorted({r["map_id"] for r in records})
        if not maps and total:
            raise ValueError("Explicit map targets required for an empty pool")
        base, extra = divmod(total, len(maps)) if maps else (0, 0)
        map_targets = {m: base + (i < extra) for i, m in enumerate(maps)}
    if (any(not isinstance(m, str) or type(n) is not int or n < 0
            for m, n in map_targets.items()) or sum(map_targets.values()) != total):
        raise ValueError("Map targets must sum to visibility targets")
    maps = sorted(map_targets)
    available = Counter()
    by_bin = defaultdict(lambda: defaultdict(deque))
    seen = set()
    for record in records:
        image_id = record["image_id"]
        if image_id in seen:
            raise ValueError("Duplicate candidate image ID")
        seen.add(image_id)
        mid = record["map_id"]
        if mid not in map_targets:
            raise ValueError("Candidate from an unplanned map")
        measurement = record["visibility_measurement"]
        name = measurement["visibility_bin"]
        if name not in bins:
            raise ValueError("Unexpected candidate visibility bin")
        available[name] += 1
        if name in VISIBILITY_BINS and measurement["training_eligible"]:
            by_bin[name][mid].append(image_id)

    # Source -> visibility bins -> maps -> sink. At most four bin nodes
    # and one node per map; capacities are counts, not individual images.
    source, sink = ("source",), ("sink",)
    residual = defaultdict(dict)
    def edge(u, v, cap):
        residual[u][v] = cap
        residual[v][u] = 0

    order = sorted(VISIBILITY_BINS, key=lambda k: (
        sum(len(q) for q in by_bin[k].values()) / targets[k] if targets[k] else math.inf,
        VISIBILITY_BINS.index(k)))
    for name in order:
        b = ("bin", name)
        edge(source, b, targets[name])
        for mid in maps:
            edge(b, ("map", mid), len(by_bin[name][mid]))
    for mid in maps:
        edge(("map", mid), sink, map_targets[mid])
    while True:
        parent = {source: None}
        queue = deque([source])
        while queue and sink not in parent:
            u = queue.popleft()
            for v, cap in residual[u].items():
                if cap > 0 and v not in parent:
                    parent[v] = u
                    queue.append(v)
        if sink not in parent:
            break
        amount = total
        v = sink
        while v != source:
            u = parent[v]
            amount = min(amount, residual[u][v])
            v = u
        v = sink
        while v != source:
            u = parent[v]
            residual[u][v] -= amount
            residual[v][u] += amount
            v = u

    # A new unit of capacity on bin -> map increases the maximum flow iff
    # its tail is reachable from source and its head can reach sink in the
    # current residual graph. This includes exchanges across already filled
    # maps/bins, not only the cells with two nonzero marginal deficits.
    def reachable(start, reverse=False):
        seen_nodes = {start}
        todo = deque([start])
        while todo:
            u = todo.popleft()
            for v in residual[u]:
                cap = residual[v][u] if reverse else residual[u][v]
                if cap > 0 and v not in seen_nodes:
                    seen_nodes.add(v)
                    todo.append(v)
        return seen_nodes

    from_source = reachable(source)
    to_sink = reachable(sink, reverse=True)
    useful = {mid: [name for name in VISIBILITY_BINS
                    if ("bin", name) in from_source and ("map", mid) in to_sink
                    and len(by_bin[name][mid]) < min(targets[name], map_targets[mid])]
              for mid in maps}

    selected = set()
    actual = Counter()
    per_map = Counter()
    matrix = {}
    capacity = {}
    for mid in maps:
        matrix[mid] = {}
        capacity[mid] = {}
        for name in VISIBILITY_BINS:
            n = residual[("map", mid)][("bin", name)]
            matrix[mid][name] = n
            capacity[mid][name] = len(by_bin[name][mid])
            actual[name] += n
            per_map[mid] += n
            selected.update(list(by_bin[name][mid])[:n])
    deficits = {k: targets[k] - actual[k] for k in VISIBILITY_BINS}
    map_deficits = {m: map_targets[m] - per_map[m] for m in maps}
    report = {
        "method": "bounded_candidate_pool_balanced_flow_v2",
        "target": dict(targets),
        "available": {k: available[k] for k in bins},
        "accepted": {k: actual[k] for k in VISIBILITY_BINS},
        "deficit": deficits,
        "complete": not any(deficits.values()) and not any(map_deficits.values()),
        "candidates": len(records),
        "selected": len(selected),
        "rejected": len(records) - len(selected),
        "selected_by_map": {m: per_map[m] for m in maps},
        "target_by_map": dict(map_targets),
        "deficit_by_map": map_deficits,
        "available_by_map_and_bin": capacity,
        "selected_by_map_and_bin": matrix,
        "useful_next_bins_by_map": useful,
    }
    return selected, report
