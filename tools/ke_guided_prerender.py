"""Migration only: remove the retired pre-render rejection from old Dagor checkouts."""
from pathlib import Path

MARKER = "// [KE] GUIDED_PRERENDER_V1"
ANCHOR = "  if (keGpuProbe && keQueuedCount) keDrainProbes();\n"
INSERT = r'''  // [KE] GUIDED_PRERENDER_V1
  if (keGuided && *keOnlineDir && *metadataJournal &&
      keSmartCandidates.getBool("guided_skip_open", false) &&
      keOcclusionTarget != 0 && keProbeAttempts >= 8 && keProbeValid == keProbeAttempts)
  {
    float minimum = 1.f;
    int samples = 0;
    for (int i=0; i<cameraAttempts; ++i)
      if (keFieldValue[i]>=0.f)
      {
        minimum = fminf(minimum,keFieldValue[i]);
        ++samples;
      }
    if (samples == keProbeValid && minimum >= .98f)
    {
      DataBlock rejected;
      rejected.setStr("level", level);
      rejected.setStr("map_id", config.getStr("map_id", ""));
      rejected.setStr("guided_rejection","redundant_open_probe");
      rejected.setInt("composition_index",sceneIndex);
      rejected.setInt("guided_placement_index",selectedCandidate);
      rejected.setInt("guided_target",keOcclusionTarget);
      rejected.setInt("guided_probe_attempts",keProbeAttempts);
      rejected.setInt("guided_probe_valid",keProbeValid);
      rejected.setReal("guided_min_fraction",minimum);
      DynamicMemGeneralSaveCB buffer(tmpmem,0,4096);
      if (!rejected.saveToTextStream(buffer) || buffer.size()<=0 || buffer.size()>65536)
        return fail("METADATA","Failed to serialize guided rejection");
      FILE *journal = fopen(metadataJournal,"ab");
      if (!journal)
        return fail("METADATA","Failed to open guided rejection journal");
      const size_t size = size_t(buffer.size());
      const bool written = fprintf(journal,"KEFRAME %d %d\n",frameIndex,int(size))>0 &&
        fwrite(buffer.data(),1,size,journal)==size;
      const bool closed = fclose(journal)==0;
      if (!written || !closed)
        return fail("METADATA","Failed to append guided rejection");
      console->addMessage(ILogWriter::NOTE,
        "[KE] FRAME_REJECT index=%d reason=redundant_open_probe",frameIndex);
      if (!keOnlineDone(keOnlineDir,"ready_",frameIndex))
        return fail("ONLINE_FEEDBACK","Could not acknowledge guided rejection");
      if (frameIndex+1<frameCount)
      {
        int nextDecision=0;
        if (!keOnlineDecision(keOnlineDir,frameIndex+1,nextDecision))
          return fail("ONLINE_FEEDBACK","Timed out after guided rejection");
        if (nextDecision<0)
        {
          console->addMessage(ILogWriter::NOTE,"[KE] ONLINE_SEQUENCE_STOP frames=%d",frameIndex+1);
          console->addMessage(ILogWriter::NOTE,"[KE] SEQUENCE_OK frames=%d",frameIndex+1);
          return true;
        }
      }
      continue; // no full RGB or masks were produced
    }
  }
'''
REPLACEMENT = ANCHOR + INSERT


def normalize_prerender(src):
    if MARKER not in src:
        return src
    if src.count(REPLACEMENT)!=1:
        raise ValueError("Partial guided pre-render patch")
    return src.replace(REPLACEMENT,ANCHOR,1)



def remove_prerender(source: Path):
    raw=source.read_bytes()
    text=raw.replace(b"\r\n",b"\n").decode("utf-8")
    updated=normalize_prerender(text)
    if updated==text:
        return False
    source.write_bytes(updated.encode("utf-8").replace(b"\n",b"\r\n" if b"\r\n" in raw else b"\n"))
    print("[KE] Removed retired guided pre-render rejection",flush=True)
    return True
