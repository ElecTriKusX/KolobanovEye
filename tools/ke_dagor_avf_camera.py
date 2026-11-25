"""Opt-in native AVF candidate ranking for the synthetic screenshot generator.

The original collision estimate is preserved for diagnostics, including when
a GPU probe is unavailable. Invalid GPU estimates never become zero visibility.
Final strict quotas remain based exclusively on the existing full-size masks.
"""
from pathlib import Path

MARKER = "// [KE] AVF_CAMERA_V1"
SETUP_OLD = "  Point3 eye;\n  float cameraGround"
SETUP_NEW = r'''  // [KE] AVF_CAMERA_V1
  const int keProbeSize = config.getInt("avf_probe_size", 0);
  if (keProbeSize != 0 && keProbeSize != 64 && keProbeSize != 128 && keProbeSize != 256)
    return fail("CONFIG", "Invalid AVF probe size");
  if (keProbeSize && (!keSearchOcclusion || !config.getBool("smart_placement", false) ||
      !config.getBool("capture_visibility_masks", false)))
    return fail("CONFIG", "AVF requires smart placement, occlusion search and masks");
  int keProbeAttempts = 0, keProbeValid = 0;
  int keBestProbeAmodal = 0, keBestProbeVisible = 0, keBestProbeMissing = 0;
  float keBestProbe = -1.f;
''' + SETUP_OLD

CHOICE_OLD = r'''      const float score = fabsf(estimate - keTargetCenters[keOcclusionTarget]);
      if (score >= keBestScore)
        continue;
      keBestScore = score;
      keBestProxy = estimate;
'''
CHOICE_NEW = r'''      float score = fabsf(estimate - keTargetCenters[keOcclusionTarget]);
      float probeFraction = -1.f;
      int probeAmodal = 0, probeVisible = 0, probeMissing = 0;
      if (keProbeSize)
      {
        ++keProbeAttempts;
        const auto probeStart = std::chrono::steady_clock::now();
        // Candidate camera is already checked against terrain and the output
        // projection. Prepare its local streamed geometry before GPU sampling.
        scene.update(candidateEye, 0.f);
        perform_delayed_actions();
        const auto deadline = std::chrono::steady_clock::now()+std::chrono::milliseconds(100);
        auto ready = [&]() {
          return rendinst::isRIGenPrepareFinished() &&
            (!scene.getSsm() || !scene.getSsm()->isLoading());
        };
        while (!ready() && std::chrono::steady_clock::now() < deadline)
        {
          cpujobs::release_done_jobs();
          perform_delayed_actions();
          sleep_msec(1);
        }
        cpujobs::release_done_jobs();
        perform_delayed_actions();
        const int longSide = width > height ? width : height;
        const int probeW = int(float(keProbeSize)*width/longSide+.5f);
        const int probeH = int(float(keProbeSize)*height/longSide+.5f);
        if (ready() && probeW >= 16 && probeH >= 16)
        {
          model.setDisableAutoChooseLod(false);
          model.beforeRender(candidateEye);
          const int probeLod = model.getCurrentLodNo();
          if (probeLod >= 0 && probeLod < lods->lods.size() && lods->lods[probeLod].scene)
          {
            model.setDisableAutoChooseLod(true);
            plugin->setKolobanovEyeModel(&model);
            const Driver3dPerspective probePerspective(candidateWk, candidateWk*float(width)/height,
              .2f, 20000.f, 0.f, 0.f);
            const bool probeOk = renderer->probeKolobanovEyeOpaque(&model, candidateCamera, probePerspective,
              probeW, probeH, probeLod, probeAmodal, probeVisible, probeMissing);
            if (probeOk && plugin->getKolobanovEyeDrawCount() && ready() &&
                get_managed_textures_streaming_pending_count() == 0)
              probeFraction = float(probeVisible)/probeAmodal;
            plugin->setKolobanovEyeModel(nullptr);
            model.setDisableAutoChooseLod(false);
          }
        }
        if (probeFraction >= 0.f)
        {
          ++keProbeValid;
          score = fabsf(probeFraction-keTargetCenters[keOcclusionTarget]);
        }
        else
          // Explicitly prefer any valid GPU probe over the collision fallback.
          score += 2.f;
        const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
          std::chrono::steady_clock::now()-probeStart).count();
        console->addMessage(ILogWriter::NOTE,
          "[KE] AVF_PROBE index=%d attempt=%d size=%d valid=%d fraction=%.6f amodal=%d visible=%d missing=%d elapsed_ms=%.3f",
          frameIndex, cameraAttempt, keProbeSize, int(probeFraction>=0.f), probeFraction,
          probeAmodal, probeVisible, probeMissing, float(elapsed)*.001f);
      }
      if (score >= keBestScore)
        continue;
      keBestScore = score;
      keBestProxy = estimate;
      keBestProbe = probeFraction;
      keBestProbeAmodal = probeAmodal;
      keBestProbeVisible = probeVisible;
      keBestProbeMissing = probeMissing;
'''

STOP_OLD = "    if (!keSearchOcclusion)\n      break;\n"
STOP_NEW = r'''    if (!keSearchOcclusion || (keProbeSize && keBestProbe >= 0.f &&
        fabsf(keBestProbe-keTargetCenters[keOcclusionTarget]) <= .04f))
      break;
'''

META_OLD = '    result.setInt("occlusion_proxy_candidates", keProxyCandidates);\n'
META_NEW = META_OLD + r'''    if (keProbeSize)
    {
      result.setInt("avf_probe_size", keProbeSize);
      result.setInt("avf_probe_attempts", keProbeAttempts);
      result.setInt("avf_probe_valid", keProbeValid);
      result.setStr("avf_probe_method", keBestProbe >= 0.f ? "opaque_probe_v1" : "collision_fallback");
      result.setReal("avf_probe_fraction", keBestProbe);
      result.setInt("avf_probe_amodal_pixels", keBestProbeAmodal);
      result.setInt("avf_probe_visible_pixels", keBestProbeVisible);
      result.setInt("avf_probe_missing_pixels", keBestProbeMissing);
    }
'''

# The old occlusion installer verifies its original snippets on reruns.
# Normalize only this exact successor, never arbitrary edits.
EDITS = ((SETUP_OLD, SETUP_NEW), (CHOICE_OLD, CHOICE_NEW),
         (STOP_OLD, STOP_NEW), (META_OLD, META_NEW))



FIELD_MARKER = "// [KE] AVF_FIELD_V2"
FIELD_SETUP_OLD = "  float keBestProbe = -1.f;\n"
FIELD_SETUP_NEW = FIELD_SETUP_OLD + r'''  // [KE] AVF_FIELD_V2
  int keFieldRing[64] = {}, keRingCount = 0;
  float keFieldAngle[64] = {}, keFieldValue[64];
  bool keFieldTried[64] = {};
  for (int i=0; i<cameraAttempts; ++i)
  {
    keFieldValue[i] = -1.f;
    if (!keProbeSize) continue;
    char ringKey[48], angleKey[48];
    snprintf(ringKey, sizeof(ringKey), "avf_ring_%d_%d", frameIndex, i);
    snprintf(angleKey, sizeof(angleKey), "avf_angle_%d_%d", frameIndex, i);
    keFieldRing[i] = config.getInt(ringKey, -1);
    keFieldAngle[i] = config.getReal(angleKey, -1000.f);
    if (keFieldRing[i]<0 || keFieldRing[i]>2 || !std::isfinite(keFieldAngle[i]) ||
        fabsf(keFieldAngle[i])>180.f)
      return fail("AVF_FIELD", "Missing or invalid camera field plan");
    keRingCount = max(keRingCount, keFieldRing[i]+1);
  }
  auto keFieldNext = [&](int iteration) -> int {
    int first = -1;
    for (int i=0; i<cameraAttempts; ++i)
      if (!keFieldTried[i]) { first=i; break; }
    // First cover four broad directions per ring, then reserve periodic
    // exploration. This prevents a single observed boundary monopolizing the search.
    if (!keProbeSize || iteration<4*keRingCount || iteration%4==0)
      return first;
    int best = first;
    float bestRank = 1e9f;
    const float target = keTargetCenters[keOcclusionTarget];
    for (int i=0; i<cameraAttempts; ++i)
    {
      if (keFieldTried[i]) continue;
      float left=361.f, right=361.f, lv=-1.f, rv=-1.f;
      for (int j=0; j<cameraAttempts; ++j)
      {
        if (keFieldRing[i]!=keFieldRing[j] || keFieldValue[j]<0.f) continue;
        const float cw=fmodf(keFieldAngle[j]-keFieldAngle[i]+360.f,360.f);
        const float ccw=fmodf(keFieldAngle[i]-keFieldAngle[j]+360.f,360.f);
        if (cw<right) { right=cw; rv=keFieldValue[j]; }
        if (ccw<left) { left=ccw; lv=keFieldValue[j]; }
      }
      if (lv<0.f || rv<0.f || left+right<=0.f) continue;
      const bool bracket=fminf(lv,rv)<=target && fmaxf(lv,rv)>=target;
      const float predicted=(lv*right+rv*left)/(left+right);
      const float rank=(bracket?0.f:2.f)+fabsf(predicted-target)+.02f*fabsf(left-right)/(left+right);
      if (rank<bestRank) { bestRank=rank; best=i; }
    }
    return best;
  };
'''

FIELD_LOOP_OLD = "  for (int cameraAttempt = 0; cameraAttempt < cameraAttempts; ++cameraAttempt)\n  {\n"
FIELD_LOOP_NEW = r'''  for (int keIteration=0; keIteration<cameraAttempts; ++keIteration)
  {
    const int cameraAttempt = keProbeSize ? keFieldNext(keIteration) : keIteration;
    if (cameraAttempt<0 || cameraAttempt>=cameraAttempts)
      return fail("AVF_FIELD", "Invalid adaptive camera index");
    keFieldTried[cameraAttempt] = true;
'''
FIELD_VALUE_OLD = "          ++keProbeValid;\n"
FIELD_VALUE_NEW = FIELD_VALUE_OLD + "          keFieldValue[cameraAttempt] = probeFraction;\n"
FIELD_EDITS = ((FIELD_SETUP_OLD, FIELD_SETUP_NEW), (FIELD_LOOP_OLD, FIELD_LOOP_NEW),
               (FIELD_VALUE_OLD, FIELD_VALUE_NEW))


def normalize_field(src):
    if FIELD_MARKER not in src:
        return src
    for old, new in FIELD_EDITS:
        if src.count(new) != 1:
            raise ValueError("Partial adaptive camera field")
        src = src.replace(new, old, 1)
    return src


def normalize_capture(src):
    src = normalize_field(src)
    if MARKER not in src:
        return src
    for old, new in EDITS:
        if src.count(new) != 1:
            raise ValueError("Partial AVF camera installation")
        src = src.replace(new, old, 1)
    return src


def install_avf_camera(source: Path):
    raw = source.read_bytes()
    src = raw.replace(b"\r\n", b"\n").decode("utf-8")
    if MARKER in src:
        normalize_capture(src)
        if FIELD_MARKER in src:
            return False
        edits = FIELD_EDITS
    else:
        edits = EDITS + FIELD_EDITS
    for old, new in edits:
        if src.count(old) != 1:
            raise ValueError(f"AVF camera anchor missing/ambiguous: {old[:90]!r}")
        src = src.replace(old, new, 1)
    source.write_bytes(src.encode("utf-8").replace(b"\n", b"\r\n" if b"\r\n" in raw else b"\n"))
    print("[KE] Installed opt-in native AVF camera selection", flush=True)
    return True
