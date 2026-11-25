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


def read_occlusion_proxy(blk, target, attempts, visibility, *, probe_size=0, probe_region=None, probe_backend=None, probe_batch_size=None):
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
        region = field('avf_probe_region', 't') if probe_region is not None else None
        if probe_region is not None and (region not in ('roi', 'full') or
                (probe_region == 'full' and region != 'full')):
            raise ValueError('Invalid AVF probe region')
        backend = field('avf_backend', 't') if probe_backend is not None else None
        batch = int(field('avf_batch_size', 'i')) if probe_backend is not None else None
        if probe_backend is not None and (backend != probe_backend or batch != (probe_batch_size if backend == 'gpu' else 1)):
            raise ValueError('Invalid AVF backend/batch telemetry')
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
        result['avf'] = {'method': method, 'size': size, 'region': region, 'requested_region': probe_region,
                         'backend': backend, 'batch_size': batch,
                         'attempts': tried, 'valid': valid,
                         'estimated_fraction': estimate, 'amodal_pixels': amodal,
                         'visible_pixels': visible, 'missing_pixels': missing,
                         'absolute_error': abs(estimate-visibility['visible_fraction']) if estimate is not None else None}
    return result


def summarize_search(records):
    by_target = {name: {"attempted": 0, "matched": 0} for name in VISIBILITY_BINS}
    errors = []
    avf_errors, avf_attempts, avf_valid, avf_fallbacks = [], 0, 0, 0
    avf_regions = {}
    avf_by_target = {name: {'valid_selected': 0, 'predicted_target': 0, 'matched': 0,
                            'fallbacks': 0} for name in VISIBILITY_BINS}
    for record in records:
        item = record["occlusion_search"]
        group = by_target[item["target"]]
        group["attempted"] += 1
        group["matched"] += int(item["target_match"])
        errors.append(item["absolute_error"])
        avf = item.get("avf")
        if avf is not None:
            region_key = avf.get("region") or "unknown"
            group_region = avf_regions.setdefault(region_key, {"selected": 0, "valid": 0, "error_sum": 0.0})
            group_region["selected"] += 1
            if avf["absolute_error"] is not None:
                group_region["valid"] += 1
                group_region["error_sum"] += avf["absolute_error"]
            avf_attempts += avf["attempts"]
            avf_valid += avf["valid"]
            group = avf_by_target[item["target"]]
            if avf["estimated_fraction"] is None:
                avf_fallbacks += 1
                group["fallbacks"] += 1
            else:
                avf_errors.append(avf["absolute_error"])
                group["valid_selected"] += 1
                group["predicted_target"] += int(visibility_bin(avf["estimated_fraction"]) == item["target"])
                group["matched"] += int(item["target_match"])
    result = {"method": "collision_proxy_v1", "by_target": by_target,
              "candidates": len(errors), "mean_absolute_error": sum(errors) / len(errors) if errors else None}
    if avf_attempts:
        result["avf"] = {"method": "opaque_probe_v1", "probes_attempted": avf_attempts,
                         "probes_valid": avf_valid, "fallback_frames": avf_fallbacks,
                         "valid_selected_frames": len(avf_errors), "by_target": avf_by_target,
                         "by_region": {k: {"selected": v["selected"], "valid": v["valid"],
                                           "mean_absolute_error": v["error_sum"]/v["valid"] if v["valid"] else None}
                                       for k, v in avf_regions.items()},
                         "mean_absolute_error": sum(avf_errors)/len(avf_errors) if avf_errors else None}
    return result
