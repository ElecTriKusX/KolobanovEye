"""Online-only bounded rejection of exhausted terrain placement searches.

Emit one explicit journal outcome per planned slot in the failed composition.
No RGB or masks are produced. Use the same feedback handshake as a rendered
frame and honor a map switch/stop before consuming another slot.
"""
from pathlib import Path

MARKER = "// [KE] PLACEMENT_REJECT_V1"
OLD = '''  if (!placementFound)
    return fail("PLACEMENT", "No candidate passed the heightmap checks");
'''
NEW = r'''  if (!placementFound)
  {
    // [KE] PLACEMENT_REJECT_V1
    const char *feedbackDir = config.getStr("online_feedback_dir", "");
    if (!*feedbackDir || !*metadataJournal)
      return fail("PLACEMENT", "No candidate passed the heightmap checks");
    for (int rejectedIndex = sceneIndex * framesPerPosition;
         rejectedIndex < frameCount && rejectedIndex < (sceneIndex + 1) * framesPerPosition; ++rejectedIndex)
    {
      int decision = 0;
      if (!keOnlineDecision(feedbackDir, rejectedIndex, decision) || decision < 0)
        return fail("ONLINE_FEEDBACK", "Missing placement rejection decision");
      DataBlock rejected;
      rejected.setStr("level", level);
      rejected.setStr("map_id", config.getStr("map_id", ""));
      rejected.setStr("placement_rejection", "heightmap_candidates_exhausted");
      rejected.setInt("placement_attempts", candidateCount);
      rejected.setInt("composition_index", sceneIndex);
      DynamicMemGeneralSaveCB buffer(tmpmem, 0, 4096);
      if (!rejected.saveToTextStream(buffer) || buffer.size() <= 0 || buffer.size() > 65536)
        return fail("METADATA", "Failed to serialize placement rejection");
      FILE *journal = fopen(metadataJournal, "ab");
      if (!journal)
        return fail("METADATA", "Failed to open placement rejection journal");
      const size_t size = size_t(buffer.size());
      const bool written = fprintf(journal, "KEFRAME %d %d\n", rejectedIndex, int(size)) > 0 &&
        fwrite(buffer.data(), 1, size, journal) == size;
      const bool closed = fclose(journal) == 0;
      if (!written || !closed)
        return fail("METADATA", "Failed to append placement rejection");
      console->addMessage(ILogWriter::NOTE,
        "[KE] FRAME_REJECT index=%d reason=heightmap_candidates_exhausted", rejectedIndex);
      if (!keOnlineDone(feedbackDir, "ready_", rejectedIndex))
        return fail("ONLINE_FEEDBACK", "Could not acknowledge placement rejection");
      if (rejectedIndex + 1 < frameCount)
      {
        int nextDecision = 0;
        if (!keOnlineDecision(feedbackDir, rejectedIndex + 1, nextDecision))
          return fail("ONLINE_FEEDBACK", "Timed out after placement rejection");
        if (nextDecision < 0)
        {
          console->addMessage(ILogWriter::NOTE, "[KE] ONLINE_SEQUENCE_STOP frames=%d", rejectedIndex + 1);
          console->addMessage(ILogWriter::NOTE, "[KE] SEQUENCE_OK frames=%d", rejectedIndex + 1);
          return true;
        }
      }
    }
    continue; // next independent planned composition; keep map and model loaded
  }
'''


def install_placement_reject(source: Path) -> bool:
    raw = source.read_bytes()
    text = raw.replace(b"\r\n", b"\n").decode("utf-8")
    if MARKER in text:
        if text.count(NEW) != 1:
            raise ValueError("Partial online placement rejection; do not reset Dagor")
        return False
    if text.count(OLD) != 1:
        raise ValueError("Missing or ambiguous placement rejection anchor; do not reset Dagor")
    text = text.replace(OLD, NEW, 1)
    source.write_bytes(text.encode("utf-8").replace(b"\n", b"\r\n" if b"\r\n" in raw else b"\n"))
    print("[KE] Installed online placement exhaustion rejection", flush=True)
    return True
