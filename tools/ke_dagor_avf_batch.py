"""Batch GPU candidate probes while retaining adaptive feedback at group boundaries."""
from pathlib import Path

MARKER = "// [KE] AVF_CAMERA_QUEUE_V4"
SETUP_OLD = "  int cameraAttemptUsed = -1;\n"
SETUP_NEW = SETUP_OLD + r'''  // [KE] AVF_CAMERA_QUEUE_V4
  const char *keBackend = config.getStr("avf_backend", "gpu");
  const bool keGpuProbe = keProbeSize && strcmp(keBackend, "gpu")==0;
  const int keBatchLimit = config.getInt("avf_batch_size", 4);
  if (keProbeSize && ((strcmp(keBackend,"cpu") && strcmp(keBackend,"gpu")) ||
      keBatchLimit<1 || keBatchLimit>4))
    return fail("AVF_QUEUE", "Invalid backend or batch size");
  struct KeCandidateProbe
  {
    Point3 eye;
    float ground, clearance, collision;
    int index;
    bool submitted, ready, roi;
    std::chrono::steady_clock::time_point started;
  };
  KeCandidateProbe keQueued[4];
  int keQueuedCount=0;
  auto keDrainProbes = [&]() {
    for (int slot=0; slot<keQueuedCount; ++slot)
    {
      const auto &q = keQueued[slot];
      int amodal=0, visible=0, missing=0;
      // Drain submitted slots even if streaming changed after the draw.
      const bool counted = q.submitted && renderer->collectKolobanovEyeProbe(slot,amodal,visible,missing);
      const float fraction = counted && q.ready ? float(visible)/amodal : -1.f;
      float score = fabsf(q.collision-keTargetCenters[keOcclusionTarget])+2.f;
      if (fraction>=0.f)
      {
        ++keProbeValid;
        keFieldValue[q.index] = fraction;
        score = fabsf(fraction-keTargetCenters[keOcclusionTarget]);
      }
      const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now()-q.started).count();
      console->addMessage(ILogWriter::NOTE,
        "[KE] AVF_PROBE index=%d attempt=%d size=%d region=%s valid=%d fraction=%.6f amodal=%d visible=%d missing=%d elapsed_ms=%.3f",
        frameIndex,q.index,keProbeSize,q.roi?"roi":"full",int(fraction>=0.f),fraction,
        amodal,visible,missing,float(elapsed)*.001f);
      if (score>=keBestScore) continue;
      keBestScore=score; keBestProxy=q.collision; keBestProbe=fraction;
      keBestProbeAmodal=amodal; keBestProbeVisible=visible; keBestProbeMissing=missing;
      keBestProbeRoi=q.roi;
      eye=q.eye; cameraGround=q.ground; cameraClearance=q.clearance; cameraAttemptUsed=q.index;
    }
    keQueuedCount=0;
  };
'''
EDITS = (
    (SETUP_OLD, SETUP_NEW),
    ("  for (int keIteration=0; keIteration<cameraAttempts; ++keIteration)\n  {\n",
     """  for (int keIteration=0; keIteration<cameraAttempts; ++keIteration)
  {
    if (keGpuProbe && keQueuedCount==0 && keBestProbe>=0.f &&
        fabsf(keBestProbe-keTargetCenters[keOcclusionTarget])<=.04f)
      break;
"""),
    ("        ++keProbeAttempts;\n", "        ++keProbeAttempts;\n        bool keSubmitted=false, keReady=false;\n"),
    ("              probeW, probeH, probeLod, probeAmodal, probeVisible, probeMissing);\n",
     "              probeW, probeH, probeLod, probeAmodal, probeVisible, probeMissing, keGpuProbe ? keQueuedCount : -1);\n"
     "            keSubmitted = keGpuProbe && probeOk;\n"),
    ("""            if (probeOk && plugin->getKolobanovEyeDrawCount() && ready() &&
                get_managed_textures_streaming_pending_count() == 0)
              probeFraction = float(probeVisible)/probeAmodal;
""", """            keReady = plugin->getKolobanovEyeDrawCount() && ready() &&
                get_managed_textures_streaming_pending_count() == 0;
            if (!keGpuProbe && probeOk && keReady)
              probeFraction = float(probeVisible)/probeAmodal;
"""),
    ("        if (probeFraction >= 0.f)\n", """        if (keGpuProbe)
        {
          keQueued[keQueuedCount++] = {candidateEye,candidateGround,clearance,estimate,
            cameraAttempt,keSubmitted,keReady,probeUsedRoi,probeStart};
          if (keQueuedCount>=keBatchLimit)
            keDrainProbes();
          continue;
        }
        if (probeFraction >= 0.f)
"""),
    ("  if (cameraAttemptUsed < 0)\n", "  if (keGpuProbe && keQueuedCount) keDrainProbes();\n  if (cameraAttemptUsed < 0)\n"),
    ('      result.setInt("avf_probe_size", keProbeSize);\n',
     '      result.setInt("avf_probe_size", keProbeSize);\n'
     '      result.setStr("avf_backend", keGpuProbe ? "gpu" : "cpu");\n'
     '      result.setInt("avf_batch_size", keGpuProbe ? keBatchLimit : 1);\n'),
)

def normalize_batch(src):
    if MARKER not in src:
        return src
    for old,new in reversed(EDITS):
        if src.count(new)!=1:
            raise ValueError("Partial AVF camera queue")
        src=src.replace(new,old,1)
    return src

def install_avf_batch(source: Path):
    raw=source.read_bytes()
    src=raw.replace(b"\r\n",b"\n").decode("utf-8")
    if MARKER in src:
        normalize_batch(src)
        return False
    for old,new in EDITS:
        if src.count(old)!=1:
            raise ValueError(f"AVF camera queue anchor missing: {old[:90]!r}")
        src=src.replace(old,new,1)
    source.write_bytes(src.encode("utf-8").replace(b"\n",b"\r\n" if b"\r\n" in raw else b"\n"))
    return True
