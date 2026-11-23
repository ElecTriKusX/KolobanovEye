"""CPU reference for comparing actual rendered reverse-Z depth buffers.

The renderer must supply two depth passes with exactly the same camera, pose,
LOD and resolution: scene depth WITH tank and isolated tank-only depth.
This reference does not synthesize masks from approximate 3D bounding boxes.
"""
import math

VISIBILITY_BINS = ("open", "light", "moderate", "heavy")
DEFAULT_VISIBILITY_WEIGHTS = {"open": 40, "light": 25, "moderate": 25, "heavy": 10}


def visibility_bin(fraction):
    if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not math.isfinite(fraction):
        raise ValueError("visible fraction must be finite")
    if not 0 <= fraction <= 1:
        raise ValueError("visible fraction outside [0,1]")
    if fraction >= .9:
        return "open"
    if fraction >= .65:
        return "light"
    if fraction >= .3:
        return "moderate"
    if fraction >= .1:
        return "heavy"
    return "reject_too_occluded"


def _bbox(mask, width):
    indexes = [i for i, v in enumerate(mask) if v]
    if not indexes:
        return None
    left = min(i % width for i in indexes)
    right = max(i % width for i in indexes)
    top = min(i // width for i in indexes)
    bottom = max(i // width for i in indexes)
    return [left, top, right - left + 1, bottom - top + 1]


def compare_reverse_z_depth(scene_depth, tank_depth, width, height, *,
                            absolute_tolerance=1e-5, relative_tolerance=1e-3):
    """Return 0/1 amodal+visible masks, pixel counts and visible bbox.

    Zero is clear depth in Dagor's reverse-Z projection. NaN/inf are invalid.
    The tank-only pass must clear its own depth buffer and render opaque model
    geometry; transparent materials not writing depth are not represented.
    """
    if isinstance(width, bool) or isinstance(height, bool) or not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise ValueError("Invalid dimensions")
    if len(scene_depth) != width * height or len(tank_depth) != width * height:
        raise ValueError("Depth buffers must have same dimensions")
    if not all(isinstance(x, (int, float)) and math.isfinite(x) and x >= 0
               for x in (absolute_tolerance, relative_tolerance)):
        raise ValueError("Depth tolerances must be finite and nonnegative")

    amodal = bytearray(width * height)
    visible = bytearray(width * height)
    for i, (scene, tank) in enumerate(zip(scene_depth, tank_depth)):
        if not math.isfinite(tank) or tank <= 0 or tank > 1:
            continue
        amodal[i] = 1
        if not math.isfinite(scene) or scene <= 0 or scene > 1:
            continue
        tol = max(absolute_tolerance, relative_tolerance * max(scene, tank))
        # Depth equality, not scene <= tank: the latter can mark unmatched
        # pixels visible when no actual tank fragment reached the scene depth.
        if abs(scene - tank) <= tol:
            visible[i] = 1
    total = sum(amodal)
    actual = sum(visible)
    return {
        "schema_version": 1,
        "method": "reverse_z_opaque_depth_compare_v1",
        "width": width,
        "height": height,
        "amodal_mask": bytes(amodal),
        "visible_mask": bytes(visible),
        "amodal_pixels": total,
        "visible_pixels": actual,
        "visible_fraction": actual / total if total else None,
        "visibility_bin": visibility_bin(actual / total) if total else "reject_empty_amodal",
        "bbox_amodal_xywh": _bbox(amodal, width),
        "bbox_visible_xywh": _bbox(visible, width),
        "limit": "opaque_depth_only; requires validated same-camera scene and isolated passes",
    }
