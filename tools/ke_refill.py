"""Round-based refill scheduling. Quota credit comes only from validated masks."""
import math

from tools.ke_visibility import VISIBILITY_BINS


def plan_refill_round(report, attempts, budget_left, per_map_cap, *,
                      initial=False, initial_multiplier=2):
    """Return bounded map jobs and proxy-target weights, without changing quotas.

    The residual network identifies useful map/bin pairs, including exchanges
    on a map or visibility bin that is provisionally full. The global budget
    limits candidates; per-map limits only constrain one native batch.
    """
    if budget_left <= 0 or report["complete"]:
        return [], {}
    maps = report["target_by_map"]
    remaining = sum(report["deficit"].values())
    useful = report["useful_next_bins_by_map"]
    eligible = [m for m in sorted(maps) if maps[m] > 0 and useful[m]]
    # Explore rather than silently dropping a map when a joint exchange is needed.
    eligible = eligible or [m for m in sorted(maps) if maps[m] > 0]
    desired, weights = {}, {}
    for mid in eligible:
        bins = useful[mid] or [b for b in VISIBILITY_BINS if report["target"][b]]
        weights[mid] = {b: (max(1, report["deficit"][b]) if b in bins else 0)
                        for b in VISIBILITY_BINS}
        if initial:
            want = maps[mid] * initial_multiplier
        else:
            hits = sum(report["available_by_map_and_bin"][mid][b] for b in bins)
            rate = max(.1, min(1.0, (hits + 1) / (attempts[mid] + 2)))
            # A filled map may supply an exchange edge. Use the remaining
            # global deficit as a cap rather than allocating its entire quota again.
            goal = max(1, report["deficit_by_map"][mid])
            if not report["deficit_by_map"][mid]:
                goal = min(maps[mid], remaining)
            want = math.ceil(min(goal, remaining) / rate)
        desired[mid] = min(per_map_cap, max(1, want))
    # Fair deterministic distribution even when the remaining budget is small.
    jobs = dict.fromkeys(eligible, 0)
    order = sorted(eligible, key=lambda m: (-report["deficit_by_map"][m], attempts[m], m))
    while budget_left and order:
        step = min(budget_left // len(order), min(desired[m] - jobs[m] for m in order))
        if step == 0:
            for mid in order[:budget_left]:
                jobs[mid] += 1
            break
        for mid in order:
            jobs[mid] += step
        budget_left -= step * len(order)
        order = [m for m in order if jobs[m] < desired[m]]
    return [(m, jobs[m]) for m in eligible if jobs[m]], {m: weights[m] for m in eligible if jobs[m]}
