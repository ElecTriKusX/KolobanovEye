"""Reject only isolated, fully loaded but temporally unstable online shots.

The original streaming timeout and all quality thresholds remain intact.
This insertion precedes (and preserves) the streaming-grace failure block.
"""
from pathlib import Path
from tools.ke_streaming_grace import EDITS

MARKER = "// [KE] STABILITY_REJECT_V1"
OLD = EDITS[-1][1]
INSERT = r'''  // [KE] STABILITY_REJECT_V1
  if (!ready && *keOnlineDir && *metadataJournal && waitFrameStability &&
      frame && frames >= minWarmupRenders && keHavePreviousSample &&
      keStableChecks >= 0 && keStableChecks < 3 &&
      std::isfinite(keMeanDelta) && std::isfinite(keMaxDelta) &&
      keMeanDelta >= 0.f && keMaxDelta >= 0.f &&
      pendingBefore == 0 && pendingAfter == 0 && texturesIdle() &&
      !mapLoading && !(scene.getSsm() && scene.getSsm()->isLoading()) &&
      skyReady && (!skies || (skies->areCloudTexturesReady() && skies->isLightingConverged())) &&
      renderer->isEnvironmentLightingReady())
  {
    DataBlock rejected;
    rejected.setStr("level", level);
    rejected.setStr("map_id", config.getStr("map_id", ""));
    rejected.setStr("stability_rejection", "frame_stability_timeout");
    rejected.setInt("composition_index", sceneIndex);
    rejected.setInt("warmup_passes", frames);
    rejected.setInt("warmup_elapsed_ms", elapsedMs);
    rejected.setInt("stable_checks", keStableChecks);
    rejected.setReal("stability_mean_delta", keMeanDelta);
    rejected.setReal("stability_max_delta", keMaxDelta);
    rejected.setInt("pending_textures", 0);
    rejected.setBool("resources_ready", true);
    DynamicMemGeneralSaveCB buffer(tmpmem, 0, 4096);
    if (!rejected.saveToTextStream(buffer) || buffer.size() <= 0 || buffer.size() > 65536)
      return fail("METADATA", "Failed to serialize stability rejection");
    FILE *journal = fopen(metadataJournal, "ab");
    if (!journal)
      return fail("METADATA", "Failed to open stability rejection journal");
    const size_t size = size_t(buffer.size());
    const bool written = fprintf(journal, "KEFRAME %d %d\n", frameIndex, int(size)) > 0 &&
      fwrite(buffer.data(), 1, size, journal) == size;
    const bool closed = fclose(journal) == 0;
    if (!written || !closed)
      return fail("METADATA", "Failed to append stability rejection");
    console->addMessage(ILogWriter::NOTE,
      "[KE] FRAME_REJECT index=%d reason=frame_stability_timeout", frameIndex);
    if (!keOnlineDone(keOnlineDir, "ready_", frameIndex))
      return fail("ONLINE_FEEDBACK", "Could not acknowledge stability rejection");
    if (frameIndex + 1 < frameCount)
    {
      int nextDecision = 0;
      if (!keOnlineDecision(keOnlineDir, frameIndex + 1, nextDecision))
        return fail("ONLINE_FEEDBACK", "Timed out after stability rejection");
      if (nextDecision < 0)
      {
        console->addMessage(ILogWriter::NOTE, "[KE] ONLINE_SEQUENCE_STOP frames=%d", frameIndex + 1);
        console->addMessage(ILogWriter::NOTE, "[KE] SEQUENCE_OK frames=%d", frameIndex + 1);
        return true;
      }
    }
    continue; // no RGB/masks: next planned camera
  }
'''
NEW = INSERT + OLD


def install_stability_reject(source: Path) -> bool:
    raw = source.read_bytes()
    text = raw.replace(b"\r\n", b"\n").decode("utf-8")
    if MARKER in text:
        if text.count(NEW) != 1:
            raise ValueError("Partial stability rejection; do not reset Dagor")
        return False
    if text.count(OLD) != 1:
        raise ValueError("Missing or ambiguous streaming timeout anchor")
    text = text.replace(OLD, NEW, 1)
    source.write_bytes(text.encode("utf-8").replace(b"\n", b"\r\n" if b"\r\n" in raw else b"\n"))
    print("[KE] Installed online fully-loaded stability-timeout rejection", flush=True)
    return True
