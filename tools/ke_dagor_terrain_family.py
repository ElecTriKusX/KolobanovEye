"""Live heightmap ranking of virtual game screenshot cameras.

Only changes search order for the terrain family. Full AVF and full-resolution
masks still measure every candidate and decide acceptance.
"""
from pathlib import Path

MARKER="// [KE] TERRAIN_FAMILY_V1"
ANCHOR="  auto keFieldNext = [&](int iteration) -> int {\n"
SETUP=r'''  // [KE] TERRAIN_FAMILY_V1
  const bool keTerrainFamily=keGuided &&
    strcmp(keSmartCandidates.getStr("guided_scene_family",""),"terrain")==0;
  float keTerrainRank[64];
  int keTerrainMeasured=0;
  if (keTerrainFamily)
  {
    const float modelHeight=bounds[1].y-bounds[0].y;
    const float bottom=aim.y-modelHeight*.5f;
    for (int i=0;i<cameraAttempts;++i)
    {
      keTerrainRank[i]=1e9f;
      char key[64];
      snprintf(key,sizeof(key),"guided_offset_%d_%d",selectedCandidate,i);
      const Point3 offset=keSmartCandidates.getPoint3(key,Point3::ZERO);
      snprintf(key,sizeof(key),"guided_height_%d_%d",selectedCandidate,i);
      const float above=keSmartCandidates.getReal(key,-1.f);
      float cameraTerrain=0.f;
      Point3 n;
      if (!finite_point(offset) || !std::isfinite(above) || above<minCameraClearance ||
          modelHeight<=.1f ||
          !scene.getHeightmapAtPoint(aim.x+offset.x,aim.z+offset.z,cameraTerrain,&n) ||
          !std::isfinite(cameraTerrain))
        continue;
      const Point3 e(aim.x+offset.x,cameraTerrain+above,aim.z+offset.z);
      float horizon=-1e9f;
      bool known=true;
      // Sample the terrain surface between camera and model. This is a
      // vertical-envelope heuristic, not an occlusion label or collision test.
      for (int j=1;j<12;++j)
      {
        const float t=float(j)/12.f;
        const Point3 p=e+(aim-e)*t;
        float terrain=0.f;
        if (!scene.getHeightmapAtPoint(p.x,p.z,terrain,&n) || !std::isfinite(terrain))
        { known=false; break; }
        horizon=max(horizon,e.y+(terrain-e.y)/t);
      }
      if (!known) continue;
      const float estimate=1.f-fmaxf(0.f,fminf(1.f,(horizon-bottom)/modelHeight));
      keTerrainRank[i]=fabsf(estimate-keTargetCenters[keOcclusionTarget]);
      ++keTerrainMeasured;
    }
    debug("[KE] TERRAIN_FAMILY index=%d ranked=%d total=%d",frameIndex,keTerrainMeasured,cameraAttempts);
  }
'''
NEXT_ANCHOR="      if (!keFieldTried[i]) { first=i; break; }\n"
NEXT=r'''    if (keTerrainFamily && keTerrainMeasured && iteration<8 && iteration%2==0)
    {
      int best=-1;
      float rank=1e9f;
      for (int i=0;i<cameraAttempts;++i)
        if (!keFieldTried[i] && keTerrainRank[i]<rank)
        { best=i; rank=keTerrainRank[i]; }
      if (best>=0) return best;
    }
'''
EDITS=((ANCHOR,SETUP+ANCHOR),(NEXT_ANCHOR,NEXT_ANCHOR+NEXT))

def normalize_terrain_family(src):
    if MARKER not in src:
        return src
    for old,new in reversed(EDITS):
        if src.count(new)!=1:
            raise ValueError("Partial terrain family patch")
        src=src.replace(new,old,1)
    return src


def install_terrain_family(source: Path):
    raw=source.read_bytes()
    src=raw.replace(b"\r\n",b"\n").decode("utf-8")
    if MARKER in src:
        normalize_terrain_family(src)
        return False
    for old,new in EDITS:
        if src.count(old)!=1:
            raise ValueError(f"Missing terrain family anchor: {old[:80]!r}")
        src=src.replace(old,new,1)
    source.write_bytes(src.encode("utf-8").replace(b"\n",b"\r\n" if b"\r\n" in raw else b"\n"))
    return True
