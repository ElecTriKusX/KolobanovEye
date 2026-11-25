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

def normalize_fixed_final(src):
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
        return False
    for old,new in EDITS:
        if src.count(old)!=1:
            raise ValueError(f"Missing fixed final anchor: {old[:100]!r}")
        src=src.replace(old,new,1)
    source.write_bytes(src.encode("utf-8").replace(b"\n",b"\r\n" if b"\r\n" in raw else b"\n"))
    return True
