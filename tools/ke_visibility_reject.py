"""Allow a bounded, explicit candidate rejection for depth-pass mismatch only."""
from pathlib import Path
from tools.ke_dagor_visibility import (BASE, HEADER_METHOD, SCENE_METHOD,
    SERVICE_METHOD, CAPTURE_MASKS, CAPTURE_METADATA)
from tools.ke_dagor_online import FRAME_OK

MARKER = "// [KE] VISIBILITY_REJECT_V1"
HEADER = HEADER_METHOD.replace("int &, int &, int &) { return false; }",
                               "int &, int &, int &, bool &depthMismatch) { depthMismatch = false; return false; }")
SCENE = SCENE_METHOD.replace("int &missingCount,\n", "int &missingCount, bool &depthMismatch,\n").replace(
    "    amodalCount = visibleCount = missingCount = 0;",
    "    depthMismatch = false;\n    amodalCount = visibleCount = missingCount = 0;").replace(
    '      debug("[KE] VISIBILITY_FAIL reason=depth_pass_mismatch missing=%d amodal=%d", missingCount, amodalCount);',
    r'''      int clear = 0, farther = 0, minX = width, minY = height, maxX = -1, maxY = -1;
      for (int i = 0; i < width * height; ++i)
      {
        const float tank = tankDepth[i], scene = mainDepth[i];
        if (!std::isfinite(tank) || tank <= 0.f || tank > 1.f)
          continue;
        const bool sceneValid = std::isfinite(scene) && scene > 0.f && scene <= 1.f;
        const float tolerance = max(1e-7f, 1e-4f * max(tank, scene));
        if (sceneValid && scene + tolerance >= tank)
          continue;
        clear += int(!sceneValid);
        farther += int(sceneValid);
        minX = min(minX, i % width); maxX = max(maxX, i % width);
        minY = min(minY, i / width); maxY = max(maxY, i / width);
      }
      debug("[KE] VISIBILITY_MISMATCH missing=%d amodal=%d visible=%d clear=%d farther=%d bounds=%d,%d,%d,%d",
        missingCount, amodalCount, visibleCount, clear, farther, minX, minY, maxX, maxY);
      depthMismatch = true;''')
SERVICE = SERVICE_METHOD.replace("int &missingCount) override", "int &missingCount, bool &depthMismatch) override").replace(
    "    if (!dynScene || !model", "    depthMismatch = false;\n    if (!dynScene || !model").replace(
    "amodalCount, visibleCount, missingCount, [&](const Point3 &offset)",
    "amodalCount, visibleCount, missingCount, depthMismatch, [&](const Point3 &offset)").replace(
    "    return captured && zeroOffset && model->getCurrentLodNo() == lod;",
    r'''    if (!zeroOffset || model->getCurrentLodNo() != lod)
    {
      depthMismatch = false;
      debug("[KE] VISIBILITY_FAIL reason=isolated_offset_or_lod_changed offset_ok=%d expected_lod=%d actual_lod=%d",
        int(zeroOffset), lod, model->getCurrentLodNo());
      return false;
    }
    return captured;''')
CAPTURE = r'''  // [KE] VISIBILITY_REJECT_V1
  int keAmodalPixels = 0, keVisiblePixels = 0, keMissingPixels = 0;
  bool keDepthMismatch = false, keMaskRejected = false;
  if (keCaptureMasks && !renderer->captureKolobanovEyeMasks(&model, camera, width, height, lod,
      keAmodalPath, keVisiblePath, keAmodalPixels, keVisiblePixels, keMissingPixels, keDepthMismatch))
  {
    if (!*keOnlineDir || !keDepthMismatch)
      return fail("VISIBILITY", "Same-camera mask capture failed; inspect VISIBILITY_FAIL in debug log");
    keMaskRejected = true;
  }

'''
METADATA = r'''  if (keMaskRejected)
  {
    result.setStr("visibility_rejection", "depth_pass_mismatch");
    result.setInt("rejected_amodal_pixels", keAmodalPixels);
    result.setInt("rejected_visible_pixels", keVisiblePixels);
    result.setInt("rejected_unmatched_pixels", keMissingPixels);
  }
  else
''' + CAPTURE_METADATA
FRAME_OUTCOME = r'''  if (keMaskRejected)
    console->addMessage(ILogWriter::NOTE, "[KE] FRAME_REJECT index=%d reason=depth_pass_mismatch", frameIndex);
  else
''' + FRAME_OK
EDITS = {
    "header": ((HEADER_METHOD, HEADER),),
    "render": ((SCENE_METHOD, SCENE), (SERVICE_METHOD, SERVICE)),
    "capture": ((CAPTURE_MASKS, CAPTURE), (CAPTURE_METADATA, METADATA), (FRAME_OK, FRAME_OUTCOME)),
}


def install_visibility_reject(dagor: Path) -> bool:
    paths = {"header": dagor / BASE / "include/de3_dynRenderService.h",
             "render": dagor / BASE / "services/dynRenderSrv/dynRender.cpp",
             "capture": dagor / BASE / "kolobanovEye.cpp"}
    raw = {k: p.read_bytes() for k, p in paths.items()}
    src = {k: v.replace(b"\r\n", b"\n").decode("utf-8") for k, v in raw.items()}
    if MARKER in src["capture"]:
        if not all(src[k].count(new) == 1 for k, edits in EDITS.items() for _, new in edits):
            raise ValueError("Partial visibility-rejection upgrade; do not reset Dagor")
        return False
    for k, edits in EDITS.items():
        for old, new in edits:
            if old == new or src[k].count(old) != 1:
                raise ValueError(f"Visibility rejection anchor missing or ambiguous: {k} {old[:80]!r}")
            src[k] = src[k].replace(old, new, 1)
    for k, p in paths.items():
        p.write_bytes(src[k].encode("utf-8").replace(b"\n", b"\r\n" if b"\r\n" in raw[k] else b"\n"))
    print("[KE] Installed online depth-mismatch rejection (unchanged mask threshold)", flush=True)
    return True
