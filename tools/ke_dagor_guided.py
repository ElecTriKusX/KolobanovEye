"""Install the opt-in per-placement camera plan and guarded early stop.

All edits are reversible in memory for existing AVF predecessor checks.
"""
from pathlib import Path

MARKER = "// [KE] OCCLUDER_GUIDED_V1"
EDITS = (
    ('  const int keProbeSize = config.getInt("avf_probe_size", 0);\n',
     '''  const int keProbeSize = config.getInt("avf_probe_size", 0);
  // [KE] OCCLUDER_GUIDED_V1
  const bool keGuided = keProbeSize && keSmartPlacement &&
    keSmartCandidates.getBool("guided_sampling", false);
  auto keGuidedHit = [&](float fraction) {
    // Stay away from the final mask bin boundaries. Full masks are still
    // the only source of quota credit.
    const float low[] = {.94f,.69f,.34f,.14f};
    const float high[] = {1.f,.86f,.61f,.26f};
    return fraction >= low[keOcclusionTarget] && fraction <= high[keOcclusionTarget];
  };
'''),
    ('''    keFieldRing[i] = config.getInt(ringKey, -1);
    keFieldAngle[i] = config.getReal(angleKey, -1000.f);
''',
     '''    if (keGuided)
    {
      snprintf(ringKey,sizeof(ringKey),"guided_ring_%d_%d",selectedCandidate,i);
      snprintf(angleKey,sizeof(angleKey),"guided_angle_%d_%d",selectedCandidate,i);
      keFieldRing[i] = keSmartCandidates.getInt(ringKey,-1);
      keFieldAngle[i] = keSmartCandidates.getReal(angleKey,-1000.f);
    }
    else
    {
      keFieldRing[i] = config.getInt(ringKey, -1);
      keFieldAngle[i] = config.getReal(angleKey, -1000.f);
    }
'''),
    ('    Point3 candidateEye = aim + config.getPoint3(offsetKey, legacy);\n',
     '''    Point3 candidateEye = aim + config.getPoint3(offsetKey, legacy);
    if (keGuided)
    {
      char key[64];
      snprintf(key,sizeof(key),"guided_offset_%d_%d",selectedCandidate,cameraAttempt);
      if (!keSmartCandidates.paramExists(key))
        return fail("GUIDED", "Missing guided camera offset");
      candidateEye = aim + keSmartCandidates.getPoint3(key,Point3::ZERO);
    }
'''),
    ('    const float desiredHeight = config.getReal(heightKey, -1.f);\n',
     '''    float desiredHeight = config.getReal(heightKey, -1.f);
    if (keGuided)
    {
      char key[64];
      snprintf(key,sizeof(key),"guided_height_%d_%d",selectedCandidate,cameraAttempt);
      if (!keSmartCandidates.paramExists(key))
        return fail("GUIDED", "Missing guided camera height");
      desiredHeight = keSmartCandidates.getReal(key,-1.f);
      if (desiredHeight < 0.f)
        return fail("GUIDED", "Invalid guided camera height");
    }
'''),
    ('''    if (keGpuProbe && keQueuedCount==0 && keBestProbe>=0.f &&
        fabsf(keBestProbe-keTargetCenters[keOcclusionTarget])<=.04f)
''',
     '''    if (keGpuProbe && keQueuedCount==0 && keBestProbe>=0.f &&
        (keGuided ? keGuidedHit(keBestProbe) : fabsf(keBestProbe-keTargetCenters[keOcclusionTarget])<=.04f))
'''),
    ('''    if (!keSearchOcclusion || (keProbeSize && keBestProbe >= 0.f &&
        fabsf(keBestProbe-keTargetCenters[keOcclusionTarget]) <= .04f))
''',
     '''    if (!keSearchOcclusion || (keProbeSize && keBestProbe >= 0.f &&
         (keGuided ? keGuidedHit(keBestProbe) : fabsf(keBestProbe-keTargetCenters[keOcclusionTarget]) <= .04f)))
'''),
    ('      result.setInt("avf_probe_size", keProbeSize);\n',
     '''      result.setInt("avf_probe_size", keProbeSize);
      if (keGuided)
      {
        result.setBool("guided_camera_plan",true);
        result.setInt("guided_placement_index",selectedCandidate);
      }
'''),
)


def normalize_guided(src):
    from tools.ke_dagor_terrain_family import normalize_terrain_family
    src = normalize_terrain_family(src)
    from tools.ke_dagor_fixed_final import normalize_fixed_final
    src = normalize_fixed_final(src)
    from tools.ke_guided_prerender import normalize_prerender
    src = normalize_prerender(src)
    if MARKER not in src:
        return src
    for old,new in reversed(EDITS):
        if src.count(new)!=1:
            raise ValueError("Partial guided camera installation")
        src=src.replace(new,old,1)
    return src


def install_guided(source: Path):
    raw=source.read_bytes()
    src=raw.replace(b"\r\n",b"\n").decode("utf-8")
    if MARKER in src:
        normalize_guided(src)
        return False
    for old,new in EDITS:
        if src.count(old)!=1:
            raise ValueError(f"Guided camera anchor missing/ambiguous: {old[:90]!r}")
        src=src.replace(old,new,1)
    source.write_bytes(src.encode("utf-8").replace(b"\n",b"\r\n" if b"\r\n" in raw else b"\n"))
    print("[KE] Installed occluder-guided camera plan",flush=True)
    return True
