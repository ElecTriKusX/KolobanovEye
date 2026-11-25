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



ROI_MARKER = "// [KE] AVF_ROI_V3"
ROI_SETUP_OLD = "  int keProbeAttempts = 0, keProbeValid = 0;\n"
ROI_SETUP_NEW = r'''  // [KE] AVF_ROI_V3
  const bool keProbeRoi = config.getBool("avf_roi", true);
  bool keBestProbeRoi = false;
  auto keCropProjection = [&](const Point4 &bbox, Driver3dPerspective &p) -> bool {
    // Crop in the original screenshot pixel coordinates. Keep camera,
    // near/far and depth convention unchanged. Padding covers AABB edge
    // rasterization and small discrepancies in the approximate envelope.
    const float pad = fmaxf(4.f, .15f*fmaxf(bbox.z,bbox.w));
    const float x0 = fmaxf(0.f, bbox.x-pad), y0 = fmaxf(0.f, bbox.y-pad);
    const float x1 = fminf(float(width), bbox.x+bbox.z+pad);
    const float y1 = fminf(float(height), bbox.y+bbox.w+pad);
    const float sx = (x1-x0)/width, sy = (y1-y0)/height;
    const float cx = (x0+x1)/width-1.f, cy = 1.f-(y0+y1)/height;
    if (!(sx>0.f && sy>0.f) || sx>1.f || sy>1.f)
      return false;
    TMatrix4 base, unit, actual;
    if (!d3d::calcproj(p,base)) return false;
    Driver3dPerspective cropped = p;
    cropped.wk /= sx; cropped.hk /= sy;
    cropped.ox = cropped.oy = 1.f;
    if (!d3d::calcproj(cropped,unit)) return false;
    // Determine Dagor's actual offset sign rather than assuming a DX
    // convention. Verify the generated matrix before it is ever used.
    const float signX = unit[2][0], signY = unit[2][1];
    if (fabsf(fabsf(signX)-1.f)>1e-5f || fabsf(fabsf(signY)-1.f)>1e-5f)
      return false;
    cropped.ox = (base[2][0]-cx)/(sx*signX);
    cropped.oy = (base[2][1]-cy)/(sy*signY);
    if (!d3d::calcproj(cropped,actual)) return false;
    for (int row=0; row<4; ++row)
      for (int col=0; col<4; ++col)
      {
        float want=base[row][col];
        if (col==0) want=(want-cx*base[row][3])/sx;
        if (col==1) want=(want-cy*base[row][3])/sy;
        if (!std::isfinite(actual[row][col]) ||
            fabsf(actual[row][col]-want)>1e-5f*fmaxf(1.f,fabsf(want)))
          return false;
      }
    p=cropped;
    return true;
  };
''' + ROI_SETUP_OLD

ROI_SIZE_OLD = r'''        const int probeW = int(float(keProbeSize)*width/longSide+.5f);
        const int probeH = int(float(keProbeSize)*height/longSide+.5f);
'''
ROI_SIZE_NEW = r'''        int probeW = int(float(keProbeSize)*width/longSide+.5f);
        int probeH = int(float(keProbeSize)*height/longSide+.5f);
        Driver3dPerspective probePerspective(candidateWk, candidateWk*float(width)/height,
          .2f, 20000.f, 0.f, 0.f);
        probeUsedRoi = keProbeRoi && keCropProjection(candidateBbox,probePerspective);
        // Fixed square ROI avoids reallocating targets on each candidate.
        // Anisotropic resampling affects sampling density, not the depth test.
        if (probeUsedRoi)
          probeW = probeH = keProbeSize;
        else if (keProbeRoi)
          debug("[KE] AVF_ROI_FALLBACK index=%d attempt=%d", frameIndex, cameraAttempt);
'''
ROI_EDITS = (
    ("#include <cmath>\n", "#include <drv/3d/dag_decl.h>\n#include <drv/3d/dag_matricesAndPerspective.h>\n#include <math/dag_TMatrix4.h>\n#include <cmath>\n"),
    (ROI_SETUP_OLD, ROI_SETUP_NEW),
    ("      float probeFraction = -1.f;\n", "      float probeFraction = -1.f;\n      bool probeUsedRoi = false;\n"),
    (ROI_SIZE_OLD, ROI_SIZE_NEW),
    ("            const Driver3dPerspective probePerspective(candidateWk, candidateWk*float(width)/height,\n              .2f, 20000.f, 0.f, 0.f);\n", ""),
    ('size=%d valid=%d fraction=%.6f', 'size=%d region=%s valid=%d fraction=%.6f'),
    ("frameIndex, cameraAttempt, keProbeSize, int(probeFraction>=0.f)", 'frameIndex, cameraAttempt, keProbeSize, probeUsedRoi ? "roi" : "full", int(probeFraction>=0.f)'),
    ("      keBestProbe = probeFraction;\n", "      keBestProbe = probeFraction;\n      keBestProbeRoi = probeUsedRoi;\n"),
    ('      result.setInt("avf_probe_size", keProbeSize);\n', '      result.setInt("avf_probe_size", keProbeSize);\n      result.setStr("avf_probe_region", keBestProbeRoi ? "roi" : "full");\n'),
)


def normalize_roi(src):
    if ROI_MARKER not in src:
        return src
    # A deletion has no unique reverse anchor; restore it at the call site.
    call = "            const bool probeOk = renderer->probeKolobanovEyeOpaque"
    for old, new in reversed(ROI_EDITS):
        if not new:
            if src.count(call) != 1:
                raise ValueError("Partial AVF ROI projection")
            src = src.replace(call, old + call, 1)
        else:
            if src.count(new) != 1:
                raise ValueError("Partial AVF ROI installation")
            src = src.replace(new, old, 1)
    return src


def normalize_capture(src):
    from tools.ke_dagor_guided import normalize_guided
    from tools.ke_dagor_avf_batch import normalize_batch
    src = normalize_field(normalize_roi(normalize_batch(normalize_guided(src))))
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
        if ROI_MARKER in src:
            return False
        edits = ROI_EDITS if FIELD_MARKER in src else FIELD_EDITS + ROI_EDITS
    else:
        edits = EDITS + FIELD_EDITS + ROI_EDITS
    for old, new in edits:
        if src.count(old) != 1:
            raise ValueError(f"AVF camera anchor missing/ambiguous: {old[:90]!r}")
        src = src.replace(old, new, 1)
    source.write_bytes(src.encode("utf-8").replace(b"\n", b"\r\n" if b"\r\n" in raw else b"\n"))
    print("[KE] Installed opt-in native AVF camera selection", flush=True)
    return True
