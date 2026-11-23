"""Deterministic screen-space composition plan for Dagor capture.

The labels are *requested* camera compositions, not measured clipping. In
particular near_clip asks for a target close to a border; a small tank may
still fit completely in frame. Actual visibility/clipping is measured later.
"""
import math
import random

BINS = ("center", "offset", "edge", "near_clip")
DEFAULT_WEIGHTS = {"center": 15, "offset": 65, "edge": 15, "near_clip": 5}


def validate_weights(weights):
    if not isinstance(weights, dict) or set(weights) != set(BINS):
        raise ValueError("screen_position_weights: specify center, offset, edge, near_clip")
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(v) or v < 0 for v in weights.values()):
        raise ValueError("screen_position_weights: weights must be finite and nonnegative")
    if sum(weights.values()) <= 0:
        raise ValueError("screen_position_weights: at least one weight must be positive")
    return {key: float(weights[key]) for key in BINS}


def plan_screen_positions(seed, count, weights=None):
    """Produce exactly count scheduled screen centers (u,v), using largest remainder.

    U increases rightwards and V increases downwards; both are normalized to
    image width/height. The camera *position* is not changed by this plan.
    """
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 1000:
        raise ValueError("count must be an integer 1..1000")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    weights = validate_weights(DEFAULT_WEIGHTS if weights is None else weights)
    total = sum(weights.values())
    exact = {name: count * weights[name] / total for name in BINS}
    quotas = {name: math.floor(exact[name]) for name in BINS}
    missing = count - sum(quotas.values())
    ranked = sorted(BINS, key=lambda name: (-(exact[name] - quotas[name]), BINS.index(name)))
    for name in ranked[:missing]:
        quotas[name] += 1

    rng = random.Random(f"KolobanovEye-screen-v1:{seed}")
    sequence = [name for name in BINS for _ in range(quotas[name])]
    rng.shuffle(sequence)

    result = []
    for name in sequence:
        if name == "center":
            u, v = rng.uniform(.44, .56), rng.uniform(.44, .56)
        elif name == "offset":
            while True:
                u, v = rng.uniform(.16, .84), rng.uniform(.16, .84)
                if abs(u - .5) > .15 or abs(v - .5) > .15:
                    break
        else:
            border = rng.uniform(.85, .94) if name == "edge" else rng.uniform(.965, .985)
            if rng.randrange(2):
                border = 1.0 - border
            other = rng.uniform(.18, .82)
            if rng.randrange(2):
                u, v = border, other
            else:
                u, v = other, border
        result.append({"bin": name, "target_uv": [round(u, 6), round(v, 6)],
                       "offset_uv": [round(u - .5, 6), round(v - .5, 6)]})
    return result
