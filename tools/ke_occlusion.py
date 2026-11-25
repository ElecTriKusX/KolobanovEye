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


def read_occlusion_proxy(blk, target, attempts, visibility, *, probe_size=0):
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
    result = {"target": target, "estimated_fraction": fraction, "estimated_bin": visibility_bin(fraction),
              "valid_candidates": candidates, "measured_bin": measured,
              "target_match": target == measured,
              "absolute_error": abs(fraction - visibility["visible_fraction"])}
    if probe_size:
        size = int(field('avf_probe_size', 'i'))
        tried = int(field('avf_probe_attempts', 'i'))
        valid = int(field('avf_probe_valid', 'i'))
        method = field('avf_probe_method', 't')
        estimate = float(field('avf_probe_fraction', 'r'))
        amodal = int(field('avf_probe_amodal_pixels', 'i'))
        visible = int(field('avf_probe_visible_pixels', 'i'))
        missing = int(field('avf_probe_missing_pixels', 'i'))
        if size != probe_size or not 0 <= valid <= tried == candidates <= attempts or not 0 <= visible <= amodal or not 0 <= missing <= amodal-visible:
            raise ValueError('Invalid AVF probe telemetry')
        if method == 'opaque_probe_v1':
            if not valid or amodal < 16 or missing > max(4, amodal//20) or not math.isfinite(estimate) or abs(estimate-visible/amodal) > 1e-5:
                raise ValueError('Inconsistent valid AVF estimate')
            estimate = visible/amodal
        elif method == 'collision_fallback':
            if valid or estimate != -1:
                raise ValueError('Invalid AVF fallback')
            estimate = None
        else:
            raise ValueError('Unknown AVF method')
        result['avf'] = {'method': method, 'size': size, 'attempts': tried, 'valid': valid,
                         'estimated_fraction': estimate, 'amodal_pixels': amodal,
                         'visible_pixels': visible, 'missing_pixels': missing,
                         'absolute_error': abs(estimate-visibility['visible_fraction']) if estimate is not None else None}
    return result


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
