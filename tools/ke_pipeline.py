"""Single supported dataset pipeline. Historical config flags are migrated."""
PIPELINE = {
    "placement_mode": "tank_map", "frames_per_position": 1,
    "tank_x": 0., "tank_z": 0., "placement_radius": 0.,
    "turret_yaw_min": 0., "turret_yaw_max": 0.,
    "gun_pitch_min": 0., "gun_pitch_max": 0.,
    "capture_tank_map": True, "capture_last_clip": False,
    "capture_visibility_masks": True, "enforce_visibility_quotas": True,
    "visibility_quota_mode": "online", "search_occlusion": True,
    "wait_frame_stability": True, "keep_open": False, "render_debug": False,
    "avf_probe_size": 128, "avf_region": "roi", "avf_reuse_map": True,
    "avf_batch_size": 4, "guided_sampling": "diverse", "guided_prerender": "off",
}
# Successful 600-frame run; user-adjustable scene parameters.
SCENE_DEFAULTS = {
    "distance_min": 18., "distance_max": 45.,
    "camera_height_min": .5, "camera_height_max": 10.,
    "camera_clearance": .3, "camera_attempts": 32, "placement_attempts": 24,
    "map_margin_percent": 0., "visibility_candidate_multiplier": 5,
}


def pipeline_config(raw):
    if not isinstance(raw, dict):
        raise ValueError("Настройки должны быть JSON-объектом")
    backend=raw.get("avf_backend","gpu")
    if backend not in ("cpu","gpu","verify"):
        raise ValueError("AVF: допустима диагностика verify или штатный GPU")
    # Old standalone CPU choice migrates to same-texture verification.
    result={**SCENE_DEFAULTS,**raw,**PIPELINE,
            "avf_backend":"verify" if backend in ("cpu","verify") else "gpu"}
    cache=result.get("smart_placement_cache","")
    if not isinstance(cache,str) or not cache.strip():
        raise ValueError("Укажите кэш анализа объектов для выбранных карт")
    return result


def migrated_keys(raw,effective):
    return sorted(k for k in (*PIPELINE,"avf_backend")
                  if k in raw and raw[k]!=effective[k])
