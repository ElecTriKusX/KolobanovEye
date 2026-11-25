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


def normalize_capture(src):
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
        return False
    for old, new in EDITS:
        if src.count(old) != 1:
            raise ValueError(f"AVF camera anchor missing/ambiguous: {old[:90]!r}")
        src = src.replace(old, new, 1)
    source.write_bytes(src.encode("utf-8").replace(b"\n", b"\r\n" if b"\r\n" in raw else b"\n"))
    print("[KE] Installed opt-in native AVF camera selection", flush=True)
    return True
