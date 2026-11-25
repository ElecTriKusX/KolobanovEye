"""Conservative pre-render rejection of redundant nearly-open game scenes."""
import math
from pathlib import Path
import re

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


def install_prerender(source):
    raw=source.read_bytes()
    src=raw.replace(b"\r\n",b"\n").decode("utf-8")
    if MARKER in src:
        normalize_prerender(src)
        return False
    if src.count(ANCHOR)!=1:
        raise ValueError("Missing guided pre-render anchor")
    src=src.replace(ANCHOR,REPLACEMENT,1)
    source.write_bytes(src.encode("utf-8").replace(b"\n",b"\r\n" if b"\r\n" in raw else b"\n"))
    return True


def allow_skip(planner, mid, target):
    """Only saturated map/bin cells, with at least every fifth request audited."""
    if target=="open" or planner.attempts[mid]%5==0:
        return False
    cap=min(planner.targets["open"],planner.map_targets[mid])
    return (planner.capacity[mid]["open"]>=cap and
            "open" not in planner.report["useful_next_bins_by_map"][mid])


def read_prerender_rejection(cfg, raw, task, idx, blk):
    if not re.search(r"^guided_rejection:",blk,re.M):
        return None
    if cfg.get("guided_prerender","off")!="conservative" or not task.get("guided_skip",{}).get(idx):
        raise ValueError("Unrequested guided pre-render rejection")
    def field(name, kind):
        matches=re.findall(r"^"+name+":"+kind+r"=([^\r\n]+)",blk,re.M)
        if len(matches)!=1:
            raise ValueError("Missing or duplicate guided rejection field: "+name)
        return matches[0].strip().strip('"')
    if field("guided_rejection","t")!="redundant_open_probe":
        raise ValueError("Unknown guided rejection")
    if Path(field("level","t")).name.casefold()!=Path(task["map_id"]).name.casefold():
        raise ValueError("Guided rejection map mismatch")
    if int(field("composition_index","i"))!=idx:
        raise ValueError("Guided rejection composition mismatch")
    from tools.ke_visibility import VISIBILITY_BINS
    target=task["occlusion_targets"][idx]
    if target=="open" or int(field("guided_target","i"))!=VISIBILITY_BINS.index(target):
        raise ValueError("Guided rejection target mismatch")
    attempts=int(field("guided_probe_attempts","i"))
    valid=int(field("guided_probe_valid","i"))
    minimum=float(field("guided_min_fraction","r"))
    placement=int(field("guided_placement_index","i"))
    if not (8<=attempts==valid<=cfg["camera_attempts"] and
            math.isfinite(minimum) and .98<=minimum<=1 and
            0<=placement<len(task["smart_points"][idx])):
        raise ValueError("Insufficient or invalid guided rejection evidence")
    image=task["outputs"][idx]
    if image.exists() or any((raw/"masks"/kind/image.name).exists() for kind in ("amodal","visible")):
        raise ValueError("Pre-render rejection has published frame files")
    return {"image_id":int(image.stem),"map_id":task["map_id"],"frame_index":idx,
            "reason":"redundant_open_probe","probe_attempts":attempts,
            "probe_valid":valid,"minimum_fraction":minimum,
            "placement_index":placement,"dagor_frame_metadata_blk":blk}
