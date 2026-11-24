"""Plan and validate requested occlusion categories for game-render candidates."""
import math
import random
import re
from tools.ke_visibility import VISIBILITY_BINS, visibility_bin
from tools.ke_visibility_quotas import visibility_targets


def plan_occlusion_targets(seed, count, weights):
    quotas = visibility_targets(count, weights)
    plan = [name for name in VISIBILITY_BINS for _ in range(quotas[name])]
    random.Random(f"KolobanovEye-occlusion-v1:{seed}").shuffle(plan)
    return plan


def read_occlusion_proxy(blk, target, attempts, visibility):
    def field(name, kind):
        match = re.search(r"^" + name + ":" + kind + r"=([^\r\n]+)", blk, re.MULTILINE)
        if not match:
            raise ValueError(f"Native occlusion search metadata missing: {name}")
        return match.group(1).strip().strip('"')
    actual_target = field("occlusion_target_bin", "t")
    fraction = float(field("occlusion_proxy_fraction", "r"))
    candidates = int(field("occlusion_proxy_candidates", "i"))
    if actual_target != target or not math.isfinite(fraction) or not 0 <= fraction <= 1 or not 1 <= candidates <= attempts:
        raise ValueError("Invalid native occlusion search metadata")
    measured = visibility["visibility_bin"]
    return {"target": target, "estimated_fraction": fraction, "estimated_bin": visibility_bin(fraction),
            "valid_candidates": candidates, "measured_bin": measured,
            "target_match": target == measured,
            "absolute_error": abs(fraction - visibility["visible_fraction"])}


def summarize_search(records):
    by_target = {name: {"attempted": 0, "matched": 0} for name in VISIBILITY_BINS}
    errors = []
    for record in records:
        item = record["occlusion_search"]
        group = by_target[item["target"]]
        group["attempted"] += 1
        group["matched"] += int(item["target_match"])
        errors.append(item["absolute_error"])
    return {"method": "collision_proxy_v1", "by_target": by_target,
            "candidates": len(errors), "mean_absolute_error": sum(errors) / len(errors) if errors else None}
