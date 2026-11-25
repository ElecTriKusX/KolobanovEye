"""Retain the measured fixed camera as candidate zero of the final AVF.

The old AVF still measures this view with the real selected model and may
choose another camera. This patch never bypasses the final masks.
"""
from pathlib import Path

MARKER = "// [KE] FIXED_CAMERA_FINAL_V2"
EDITS = (
    ("  float keFixedEstimate=-1.f;\n",
     """  float keFixedEstimate=-1.f;
  // [KE] FIXED_CAMERA_FINAL_V2
  Point3 keFixedEye(0.f,0.f,0.f),keFixedLookAt(0.f,0.f,0.f);
"""),
    ("                  bestScore=score; best=i; keFixedEstimate=fractions[i];\n",
     """                  bestScore=score; best=i; keFixedEstimate=fractions[i];
                  keFixedEye=fixedEye; keFixedLookAt=fixedAim;
"""),
    ("    const Point3 candidateForward = normalize(keShiftedLookAt(candidateEye) - candidateEye);\n",
     """    const bool keIsFixedView=keFixedEstimate>=0.f && cameraAttempt==0;
    const Point3 candidateForward = normalize((keIsFixedView ? keFixedLookAt : keShiftedLookAt(candidateEye)) - candidateEye);
"""),
    ("    if (!finite_point(candidateEye) || lengthSq(aim - candidateEye) < 0.01f ||\n",
     """    if (keFixedEstimate>=0.f && cameraAttempt==0)
      candidateEye=keFixedEye;
    if (!finite_point(candidateEye) || lengthSq(aim - candidateEye) < 0.01f ||
"""),
    ("    if (desiredHeight >= 0.f)\n      candidateEye.y = candidateGround + desiredHeight;\n",
     """    if (desiredHeight >= 0.f && !(keFixedEstimate>=0.f && cameraAttempt==0))
      candidateEye.y = candidateGround + desiredHeight;
"""),
    ("        if (keFieldRing[i]!=keFieldRing[j] || keFieldValue[j]<0.f) continue;\n",
     """        // The fixed view does not belong to an interpolable angular ring.
        if ((keFixedEstimate>=0.f && j==0) || keFieldRing[i]!=keFieldRing[j] || keFieldValue[j]<0.f) continue;
"""),
    ("  const Point3 lookAt = keShiftedLookAt(eye);\n",
     """  const bool keFixedSelected=keFixedEstimate>=0.f && cameraAttemptUsed==0;
  const Point3 lookAt = keFixedSelected ? keFixedLookAt : keShiftedLookAt(eye);
"""),
    ('    result.setReal("fixed_group_estimate",keFixedEstimate);\n',
     '''    result.setReal("fixed_group_estimate",keFixedEstimate);
    result.setBool("fixed_camera_selected",keFixedSelected);
    if (keFixedEstimate>=0.f)
    {
      result.setPoint3("fixed_group_eye",keFixedEye);
      result.setPoint3("fixed_group_look_at",keFixedLookAt);
    }
'''),
)


EDGE_MARKER = "// [KE] FIXED_EDGE_RANK_V3"
EDGE_EDITS = (
    ("  Point3 keFixedEye(0.f,0.f,0.f),keFixedLookAt(0.f,0.f,0.f);\n",
     """  Point3 keFixedEye(0.f,0.f,0.f),keFixedLookAt(0.f,0.f,0.f);
  // [KE] FIXED_EDGE_RANK_V3
  int keFixedEdgeHits=0;
"""),
    ("""        const Point3 fixedAim(anchor.x,aimGround+keFixedPlaces[0].point.y-bounds[0].y+
          (bounds[0].y+bounds[1].y)*.5f,anchor.y);
        if (lengthSq(Point3(0,1,0)%(fixedAim-fixedEye))>.01f)
        {""",
     """        Point3 fixedAim(anchor.x,aimGround+keFixedPlaces[0].point.y-bounds[0].y+
          (bounds[0].y+bounds[1].y)*.5f,anchor.y);
        if (lengthSq(Point3(0,1,0)%(fixedAim-fixedEye))>.01f)
        {
          // Apply the planned composition to the group's anchor. Individual
          // translated poses have different actual screen positions.
          char screenKey[48];
          snprintf(screenKey,sizeof(screenKey),"screen_offset_%d",sceneIndex);
          const Point2 offset=config.getPoint2(screenKey,Point2(0.f,0.f));
          if (!std::isfinite(offset.x) || !std::isfinite(offset.y) ||
              fabsf(offset.x)>.5f || fabsf(offset.y)>.5f)
            return fail("FIXED_CAMERA","Invalid screen composition");
          const Point3 toward=normalize(fixedAim-fixedEye);
          const Point3 horizontal=normalize(Point3(0,1,0)%toward);
          const Point3 up=toward%horizontal;
          const float halfWidth=tanf(fov*DEG_TO_RAD*.5f)*length(fixedAim-fixedEye);
          const float halfHeight=halfWidth*float(height)/width;
          fixedAim=fixedAim-horizontal*(2.f*offset.x*halfWidth)+up*(2.f*offset.y*halfHeight);"""),
    ("""            float fractions[6]={-1.f,-1.f,-1.f,-1.f,-1.f,-1.f};
            const bool measured=renderer->probeKolobanovEyeFixed(&fixedModel,fixedCamera,perspective,pw,ph,
              keFixedFound,prepare,fractions,keFixedPasses);""",
     """            float fractions[6]={-1.f,-1.f,-1.f,-1.f,-1.f,-1.f};
            int edges[6]={};
            const bool measured=renderer->probeKolobanovEyeFixed(&fixedModel,fixedCamera,perspective,pw,ph,
              keFixedFound,prepare,fractions,edges,keFixedPasses);"""),
    ("                const float score=fabsf(fractions[i]-centers[keSmartTarget]);\n",
     """                // Prefer a measured real depth boundary only as a small
                // tie-breaker. Visibility remains the primary criterion.
                const float score=fabsf(fractions[i]-centers[keSmartTarget])-
                  fminf(.015f,.001f*float(edges[i]));
"""),
    ("                  keFixedEye=fixedEye; keFixedLookAt=fixedAim;\n",
     """                  keFixedEye=fixedEye; keFixedLookAt=fixedAim;
                  keFixedEdgeHits=edges[i];
"""),
    ('    result.setBool("fixed_camera_selected",keFixedSelected);\n',
     '''    result.setBool("fixed_camera_selected",keFixedSelected);
    result.setInt("fixed_group_edge_hits",keFixedEdgeHits);
'''),
)

def normalize_edge_capture(src):
    from tools.ke_dagor_depth_proposals import MARKER as WORLD_MARKER, CAPTURE_EDITS, reverse_successors
    from tools.ke_dagor_aperture import MARKER as APERTURE_MARKER, CAPTURE_EDITS as aperture_edits
    if APERTURE_MARKER in src:
        src=reverse_successors(src,aperture_edits)
    if WORLD_MARKER in src:
        src=reverse_successors(src,CAPTURE_EDITS)
    if EDGE_MARKER not in src:
        return src
    for old,new in reversed(EDGE_EDITS):
        if src.count(new)!=1:
            raise ValueError("Partial fixed depth edge capture")
        src=src.replace(new,old,1)
    return src


def normalize_fixed_final(src):
    src=normalize_edge_capture(src)
    if MARKER not in src:
        return src
    for old,new in reversed(EDITS):
        if src.count(new)!=1:
            raise ValueError("Partial fixed-camera final integration")
        src=src.replace(new,old,1)
    return src


def install_fixed_final(source: Path):
    raw=source.read_bytes()
    src=raw.replace(b"\r\n",b"\n").decode("utf-8")
    if MARKER in src:
        normalize_fixed_final(src)
    else:
        for old,new in EDITS:
            if src.count(old)!=1:
                raise ValueError(f"Missing fixed final anchor: {old[:100]!r}")
            src=src.replace(old,new,1)
    src=normalize_edge_capture(src)
    for old,new in EDGE_EDITS:
        if src.count(old)!=1:
            raise ValueError(f"Missing fixed edge capture anchor: {old[:100]!r}")
        src=src.replace(old,new,1)
    from tools.ke_dagor_depth_proposals import CAPTURE_EDITS, apply_successors
    src=apply_successors(src,CAPTURE_EDITS)
    from tools.ke_dagor_aperture import CAPTURE_EDITS as aperture_edits
    src=apply_successors(src,aperture_edits)
    updated=src.encode("utf-8").replace(b"\n",b"\r\n" if b"\r\n" in raw else b"\n")
    if updated==raw:
        return False
    source.write_bytes(updated)
    return True
