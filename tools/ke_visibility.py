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
    # Both callers supply validated binary 0/1 arrays. Scan rows with
    # bytes.find/rfind in C instead of constructing a Python pixel-index list.
    left, right, top, bottom = width, -1, None, -1
    for y, start in enumerate(range(0, len(mask), width)):
        row = mask[start:start + width]
        lo = row.find(b"\x01")
        if lo >= 0:
            left = min(left, lo)
            right = max(right, row.rfind(b"\x01"))
            top = y if top is None else top
            bottom = y
    return None if top is None else [left, top, right - left + 1, bottom - top + 1]


def compare_reverse_z_depth(scene_depth, tank_depth, width, height, *,
                            absolute_tolerance=1e-7, relative_tolerance=1e-4):
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


def mask_decoder_name():
    try:
        from PIL import Image
    except ImportError:
        return "python"
    return "pillow"


def _load_mask_rgb(path, width, height):
    # Strict structural/CRC/stream validation still runs on either path.
    from tools.ke_png_fast import validate_rgb_png
    validate_rgb_png(path, width, height)
    try:
        from PIL import Image
    except ImportError:
        from tools.ke_pixel_debug import load_png
        return load_png(path)
    with Image.open(path) as image:
        if image.mode != "RGB" or image.size != (width, height):
            raise ValueError("Unexpected mask mode or dimensions")
        return width, height, image.tobytes()


def read_capture_masks(raw, image_name, blk, width, height):
    """Verify real exported masks and native counts before publishing raw.

    Diagnostic-only: alpha-tested opaque depth is measured; off-frame silhouette,
    translucent foreground, and target visibility quotas are not yet measured.
    """
    import re
    def field(name, kind):
        match = re.search(r"^" + re.escape(name) + ":" + kind + r"=([^\r\n]+)", blk, re.MULTILINE)
        if not match:
            raise ValueError(f"Native visibility field missing: {name}")
        return match.group(1).strip().strip('"')

    method = field("visibility_method", "t")
    if method != "reverse_z_opaque_depth_compare_v1":
        raise ValueError("Unexpected native visibility method")
    paths = {kind: f"masks/{kind}/{image_name}" for kind in ("amodal", "visible")}
    masks = {}
    for kind, relative in paths.items():
        path = raw / relative
        w, h, rgb = _load_mask_rgb(path, width, height)
        if (w, h) != (width, height):
            raise ValueError("Unexpected mask dimensions")
        red = bytes(rgb[0::3])
        if not (red == rgb[1::3] == rgb[2::3]) or red.count(0) + red.count(255) != len(red):
            raise ValueError("Mask must be binary black and white")
        masks[kind] = red.translate(bytes([0] * 255 + [1]))

    amodal, visible = masks["amodal"], masks["visible"]
    total, actual = amodal.count(1), visible.count(1)
    # Exact subset check on the validated 0/1 bytes, using native big-int bitwise operations.
    if total == 0 or int.from_bytes(visible, "little") & ~int.from_bytes(amodal, "little"):
        raise ValueError("Invalid mask pair: empty silhouette or visible outside amodal")
    if total != int(field("amodal_pixels", "i")) or actual != int(field("visible_pixels", "i")):
        raise ValueError("Native pixel counts differ from masks")
    missing = int(field("unmatched_depth_pixels", "i"))
    fraction = float(field("visible_fraction", "r"))
    if not 0 <= missing <= min(total - actual, max(4, total // 20)):
        raise ValueError("Too many unmatched depth pixels")
    if not math.isfinite(fraction) or abs(fraction - actual / total) > 1e-5:
        raise ValueError("Native visibility fraction differs from masks")
    bin_name = visibility_bin(actual / total)
    return {"method": method, "amodal_mask": paths["amodal"], "visible_mask": paths["visible"],
            "amodal_pixels": total, "visible_pixels": actual, "unmatched_depth_pixels": missing,
            "visible_fraction": actual / total, "visibility_bin": bin_name,
            "bbox_amodal_xywh": _bbox(amodal, width), "bbox_visible_xywh": _bbox(visible, width),
            "frame_fraction": None, "training_eligible": bin_name in VISIBILITY_BINS,
            "diagnostic_only": True}
