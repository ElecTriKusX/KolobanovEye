"""Install an opt-in collision proxy for synthetic game screenshot composition.

The proxy only ranks camera candidates. Actual visibility and acceptance are
still measured by the existing RGB/isolated-depth masks.
"""
from pathlib import Path

MARKER = "// [KE] OCCLUSION_SEARCH_V1"

SETUP = """  // [KE] OCCLUSION_SEARCH_V1
  const bool keSearchOcclusion = config.getBool("search_occlusion", false);
  char keOcclusionKey[48];
  snprintf(keOcclusionKey, sizeof(keOcclusionKey), "occlusion_target_%d", frameIndex);
  const int keOcclusionTarget = config.getInt(keOcclusionKey, 0);
  if (keSearchOcclusion && (keOcclusionTarget < 0 || keOcclusionTarget > 3))
    return fail("CONFIG", "Invalid occlusion target");
  const float keTargetCenters[] = {0.97f, 0.775f, 0.475f, 0.2f};
  const char *keTargetNames[] = {"open", "light", "moderate", "heavy"};
  float keBestScore = 1e9f, keBestProxy = -1.f;
  int keProxyCandidates = 0;
  // Collision is an approximation of opaque rendered coverage. Sample inside
  // the neutral model envelope; the isolated GPU mask remains authoritative.
  auto keEstimateVisibility = [&](const Point3 &from) -> float {
    int clear = 0, samples = 0;
    for (int iy = 0; iy < 3; ++iy)
      for (int iz = 0; iz < 3; ++iz)
        for (int ix = 0; ix < 3; ++ix)
        {
          const Point3 local(
            bounds[0].x + (bounds[1].x - bounds[0].x) * (float(ix) + 0.5f) / 3.f,
            bounds[0].y + (bounds[1].y - bounds[0].y) * (float(iy) + 0.5f) / 3.f,
            bounds[0].z + (bounds[1].z - bounds[0].z) * (float(iz) + 0.5f) / 3.f);
          const Point3 delta = world * local - from;
          const float dist = length(delta);
          if (!std::isfinite(dist) || dist <= 0.1f)
            continue;
          const Point3 dir = delta / dist;
          const float limit = dist - 0.05f;
          float t = limit;
          bool blocked = scene.tracerayNormalizedHeightmap(from, dir, t, nullptr);
          t = limit;
          if (!blocked && scene.hasStaticScene())
            blocked = scene.tracerayNormalizedStaticScene(from, dir, t, nullptr, nullptr);
          t = limit;
          Point3 normal(0.f, 0.f, 0.f);
          if (!blocked)
            blocked = rendinst::traceRayRendInstsNormalized(from, dir, t, normal, false, true, nullptr, true);
          ++samples;
          clear += int(!blocked);
        }
    return samples ? float(clear) / float(samples) : -1.f;
  };
"""

OLD_CHOICE = """    eye = candidateEye;
    cameraGround = candidateGround;
    cameraClearance = clearance;
    cameraAttemptUsed = cameraAttempt;
    break;
"""
NEW_CHOICE = """    if (keSearchOcclusion)
    {
      const float estimate = keEstimateVisibility(candidateEye);
      if (estimate < 0.f)
        continue;
      ++keProxyCandidates;
      const float score = fabsf(estimate - keTargetCenters[keOcclusionTarget]);
      if (score >= keBestScore)
        continue;
      keBestScore = score;
      keBestProxy = estimate;
    }
    eye = candidateEye;
    cameraGround = candidateGround;
    cameraClearance = clearance;
    cameraAttemptUsed = cameraAttempt;
    if (!keSearchOcclusion)
      break;
"""
LOG = """  if (keSearchOcclusion)
    console->addMessage(ILogWriter::NOTE,
      "[KE] OCCLUSION_PROXY index=%d target=%s estimate=%.5f valid=%d selected=%d",
      frameIndex, keTargetNames[keOcclusionTarget], keBestProxy, keProxyCandidates, cameraAttemptUsed);
"""
METADATA = """  if (keSearchOcclusion)
  {
    result.setStr("occlusion_target_bin", keTargetNames[keOcclusionTarget]);
    result.setReal("occlusion_proxy_fraction", keBestProxy);
    result.setInt("occlusion_proxy_candidates", keProxyCandidates);
  }
"""
EDITS = (
    ("  Point3 eye;\n  float cameraGround", SETUP + "  Point3 eye;\n  float cameraGround"),
    (OLD_CHOICE, NEW_CHOICE),
    ("  if (frameCount > 1)\n    console->addMessage(ILogWriter::NOTE, \"[KE] ORBIT_FRAME", LOG +
     "  if (frameCount > 1)\n    console->addMessage(ILogWriter::NOTE, \"[KE] ORBIT_FRAME"),
    ('  result.setStr("screen_bin_planned", keScreenBin);\n', '  result.setStr("screen_bin_planned", keScreenBin);\n' + METADATA),
)


def install_occlusion_search(source: Path) -> bool:
    raw = source.read_bytes()
    text = raw.replace(b"\r\n", b"\n").decode("utf-8")
    if MARKER in text:
        if not all(text.count(new) == 1 for _, new in EDITS):
            raise ValueError(f"Partial occlusion-search installation: {source}")
        return False
    for old, new in EDITS:
        if text.count(old) != 1:
            raise ValueError(f"Occlusion search anchor missing or ambiguous: {old[:90]!r}")
        text = text.replace(old, new, 1)
    source.write_bytes(text.encode("utf-8").replace(b"\n", b"\r\n" if b"\r\n" in raw else b"\n"))
    print("[KE] Installed experimental collision-based occlusion search", flush=True)
    return True
