"""GUI-only pipeline policy; the legacy CLI remains unchanged until cleanup."""

RECOMMENDED = {
    "placement_mode": "tank_map", "frames_per_position": 1,
    "turret_yaw_min": 0., "turret_yaw_max": 0.,
    "gun_pitch_min": 0., "gun_pitch_max": 0.,
    "capture_visibility_masks": True, "enforce_visibility_quotas": True,
    "visibility_quota_mode": "online", "search_occlusion": True,
    "wait_frame_stability": True, "keep_open": False, "render_debug": False,
    "avf_probe_size": 128, "avf_region": "roi", "avf_backend": "gpu",
    "avf_batch_size": 4, "guided_sampling": "diverse", "guided_prerender": "off",
}
# Exact scene ranges from the verified 600-frame run. Applied only on explicit
# preset reset (or initial GUI startup), not on every edit.
TESTED_RANGES = {
    "distance_min": 18., "distance_max": 45.,
    "camera_height_min": .5, "camera_height_max": 10.,
    "camera_clearance": .3, "camera_attempts": 32,
    "placement_attempts": 24, "map_margin_percent": 0.,
    "visibility_candidate_multiplier": 5,
}
ARTICULATION = ("turret_yaw_min", "turret_yaw_max", "gun_pitch_min", "gun_pitch_max")


def effective_config(data, recommended=False):
    """Ignore inactive controls without deleting their saved UI values."""
    c = dict(data)
    if recommended:
        c.update(RECOMMENDED)
    if c["placement_mode"] == "tank_map":
        c.update(tank_x=0., tank_z=0., placement_radius=0.)
    else:
        c["map_margin_percent"] = 0.
    if not c["capture_visibility_masks"]:
        c.update(enforce_visibility_quotas=False, search_occlusion=False)
    if not c["enforce_visibility_quotas"]:
        c["visibility_quota_mode"] = "refill"
    else:
        c["keep_open"] = False
    eligible = (c["placement_mode"] == "tank_map" and
                str(c["frames_per_position"]) == "1" and
                c["enforce_visibility_quotas"] and c["visibility_quota_mode"] == "online"
                and c["search_occlusion"] and c["capture_visibility_masks"])
    if not eligible:
        c["smart_placement_cache"] = ""
    if c["smart_placement_cache"]:
        c.update(dict.fromkeys(ARTICULATION, 0.))
    if not c["smart_placement_cache"] or c["render_debug"]:
        c["avf_probe_size"] = 0
    # Recommended mode requires a cache rather than silently degrading.
    if recommended:
        c.update(RECOMMENDED)
    if str(c["avf_probe_size"]) == "0":
        c["guided_sampling"] = "off"
    if c["guided_sampling"] == "off":
        c["guided_prerender"] = "off"
    if c["guided_sampling"] not in ("fixed_camera", "diverse"):
        c["scene_cache"] = ""
    return c


def visible_rows(data, recommended=False):
    """Return rows whose controls are meaningful for the current GUI state."""
    c = effective_config(data, recommended)
    hidden = set()
    if c["placement_mode"] == "tank_map":
        hidden.update(("tank_x", "placement_radius"))
    else:
        hidden.add("map_margin_percent")
    eligible = (c["placement_mode"] == "tank_map" and str(c["frames_per_position"]) == "1"
                and c["enforce_visibility_quotas"] and c["visibility_quota_mode"] == "online"
                and c["search_occlusion"] and c["capture_visibility_masks"])
    if not eligible:
        hidden.add("smart_placement_cache")
    if c["smart_placement_cache"] or recommended:
        hidden.update(("turret_yaw_min", "gun_pitch_min"))
    if not c["capture_visibility_masks"]:
        hidden.update(("enforce_visibility_quotas", "search_occlusion"))
    if not c["enforce_visibility_quotas"]:
        hidden.update(("online_quotas", "visibility_candidate_multiplier"))
    else:
        hidden.add("keep_open")
    if not c["smart_placement_cache"] or c["render_debug"]:
        hidden.add("avf_probe_size")
    if str(c["avf_probe_size"]) == "0":
        hidden.update(("avf_region", "avf_backend", "avf_batch_size", "guided_sampling", "guided_prerender"))
    elif c["guided_sampling"] == "off":
        hidden.add("guided_prerender")
    if c["guided_sampling"] not in ("fixed_camera", "diverse"):
        hidden.add("scene_cache")
    if recommended:
        hidden.update(("placement_mode", "frames_per_position", "capture_visibility_masks",
            "enforce_visibility_quotas", "online_quotas", "search_occlusion",
            "wait_frame_stability", "render_debug", "keep_open", "avf_probe_size",
            "avf_region", "avf_backend", "avf_batch_size", "guided_sampling", "guided_prerender"))
    return hidden
