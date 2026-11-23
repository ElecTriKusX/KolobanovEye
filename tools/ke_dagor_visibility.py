"""Install the opt-in, same-screenshot Dagor depth-mask diagnostic.

Keep ABI additions at the end of IDynRenderService; use the final screenshot
camera rather than reconstructing a projection after screenshot mode resets.
All insertion anchors are checked before any file is written.
"""
from pathlib import Path

MARKER = "// [KE] VISIBILITY_CAPTURE_V1"
BASE = Path("prog/tools/sceneTools/daEditorX")

HEADER_METHOD = """  // [KE] VISIBILITY_CAPTURE_V1
  virtual bool captureKolobanovEyeMasks(DynamicRenderableSceneInstance *, const TMatrix &, int, int, int,
    const char *, const char *, int &, int &, int &) { return false; }
"""

SCREEN_STATE = """  // [KE] VISIBILITY_CAPTURE_V1
  bool keMaskCameraValid = false;
  TMatrix keMaskViewItm = TMatrix::IDENT;
  Driver3dPerspective keMaskPerspective;
  int keMaskWidth = 0, keMaskHeight = 0;

"""

SCREEN_SNAPSHOT = """          if (rt_ready && renderMatrixOk)
          {
            keMaskViewItm = viewItm;
            keMaskPerspective = perspective;
            keMaskWidth = viewportW;
            keMaskHeight = viewportH;
            keMaskCameraValid = true;
          }
"""

SCENE_METHOD = """  bool captureKolobanovEyeMasks(const TMatrix &expectedCamera, int width, int height,
    const char *amodalPath, const char *visiblePath, int &amodalCount, int &visibleCount, int &missingCount,
    dag::FunctionRef<void(const Point3 &)> render_opaque)
  {
    amodalCount = visibleCount = missingCount = 0;
    if (!keMaskCameraValid || keMaskWidth != width || keMaskHeight != height ||
        !deferredTarget || !canRenderIsolated() || !*amodalPath || !*visiblePath)
    {
      debug("[KE] VISIBILITY_FAIL reason=screenshot_or_renderer_not_ready snapshot=%d actual=%dx%d expected=%dx%d isolated=%d",
        int(keMaskCameraValid), keMaskWidth, keMaskHeight, width, height, int(canRenderIsolated()));
      return false;
    }
    // Consume the snapshot exactly once; a later frame must capture its own.
    keMaskCameraValid = false;
    for (int col = 0; col < 4; ++col)
    {
      const float error = lengthSq(keMaskViewItm.getcol(col) - expectedCamera.getcol(col));
      if (!std::isfinite(error) || error > (col == 3 ? 0.01f : 1e-6f))
      {
        debug("[KE] VISIBILITY_FAIL reason=camera_mismatch column=%d squared_error=%g", col, error);
        return false;
      }
    }

    // The main G-buffer still contains the final screenshot. Copy it BEFORE
    // rendering anything else, and honor the driver's row pitch.
    auto readDepth = [&](auto *texture, Tab<float> &out) -> bool {
      TextureInfo info{};
      if (!texture || !texture->getinfo(info) || (info.cflg & TEXFMT_MASK) != TEXFMT_DEPTH32)
        return false;
      const auto depth = lock_texture<const float>(texture, 0, TEXLOCK_READ);
      if (!depth || depth.getWidthInElems() != width || depth.getHeightInElems() != height)
        return false;
      out.resize(width * height);
      for (int y = 0; y < height; ++y)
      {
        const auto *row = reinterpret_cast<const float *>(
          reinterpret_cast<const char *>(depth.get()) + y * depth.getByteStride());
        memcpy(out.data() + y * width, row, width * sizeof(float));
      }
      return true;
    };
    Tab<float> mainDepth(tmpmem), tankDepth(tmpmem);
    if (!readDepth(deferredTarget->getDepth(), mainDepth))
    {
      debug("[KE] VISIBILITY_FAIL reason=scene_depth_readback");
      return false;
    }

    int oldX, oldY, oldW, oldH;
    float oldMin, oldMax;
    d3d::getview(oldX, oldY, oldW, oldH, oldMin, oldMax);
    TexPtr isolated = renderIsolated(width, height, keMaskViewItm, keMaskPerspective,
      [&](const Point3 &offset) {
        d3d::setview(0, 0, width, height, 0.f, 1.f);
        render_opaque(offset);
      });
    d3d::setview(oldX, oldY, oldW, oldH, oldMin, oldMax);
    if (!isolated || !isolatedTarget || !readDepth(isolatedTarget->getDepth(), tankDepth))
    {
      debug("[KE] VISIBILITY_FAIL reason=isolated_depth_readback");
      return false;
    }

    Tab<TexPixel32> amodal(tmpmem), visible(tmpmem);
    amodal.resize(width * height);
    visible.resize(width * height);
    for (int i = 0; i < width * height; ++i)
    {
      const float tank = tankDepth[i], scene = mainDepth[i];
      const bool inTank = std::isfinite(tank) && tank > 0.f && tank <= 1.f;
      const bool sceneValid = std::isfinite(scene) && scene > 0.f && scene <= 1.f;
      // Reverse Z: nearer geometry has larger depth. Require equality rather
      // than assuming a tank pixel absent from the scene depth was visible.
      const float tolerance = max(1e-7f, 1e-4f * max(tank, scene));
      const bool inVisible = inTank && sceneValid && fabsf(tank - scene) <= tolerance;
      amodalCount += int(inTank);
      visibleCount += int(inVisible);
      missingCount += int(inTank && (!sceneValid || scene + tolerance < tank));
      amodal[i].r = amodal[i].g = amodal[i].b = inTank ? 255 : 0;
      visible[i].r = visible[i].g = visible[i].b = inVisible ? 255 : 0;
      amodal[i].a = visible[i].a = 255;
    }
    if (!amodalCount)
    {
      debug("[KE] VISIBILITY_FAIL reason=empty_isolated_depth");
      return false;
    }
    // A nearer scene pixel is normal occlusion; a farther/clear pixel where
    // the tank should draw is a possible pass mismatch. Fail closed if large.
    if (missingCount > max(4, amodalCount / 20))
    {
      debug("[KE] VISIBILITY_FAIL reason=depth_pass_mismatch missing=%d amodal=%d", missingCount, amodalCount);
      return false;
    }
    if (!save_png32(amodalPath, amodal.data(), width, height, width * sizeof(TexPixel32), nullptr, 0, false) ||
        !save_png32(visiblePath, visible.data(), width, height, width * sizeof(TexPixel32), nullptr, 0, false))
      return false;
    debug("[KE] VISIBILITY_OK amodal=%d visible=%d unmatched=%d", amodalCount, visibleCount, missingCount);
    return true;
  }

"""

SERVICE_METHOD = """  bool captureKolobanovEyeMasks(DynamicRenderableSceneInstance *model, const TMatrix &camera,
    int width, int height, int lod, const char *amodal, const char *visible,
    int &amodalCount, int &visibleCount, int &missingCount) override
  {
    if (!dynScene || !model || model->getCurrentLodNo() != lod)
    {
      debug("[KE] VISIBILITY_FAIL reason=lod_or_renderer_mismatch expected_lod=%d actual_lod=%d",
        lod, model ? model->getCurrentLodNo() : -1);
      return false;
    }
    d3d::GpuAutoLock gpuLock;
    bool zeroOffset = true;
    const bool captured = dynScene->captureKolobanovEyeMasks(camera, width, height, amodal, visible,
      amodalCount, visibleCount, missingCount, [&](const Point3 &offset) {
        // This pinned Dagor implementation promises Point3::ZERO.
        if (lengthSq(offset) > 1e-12f)
        {
          zeroOffset = false;
          return;
        }
        const int shaderVar = get_shader_variable_id("editor_dynmodel_render", true);
        if (shaderVar >= 0)
          ShaderGlobal::set_int(shaderVar, 1);
        renderOneDynModelInstance(model, Stage::STG_RENDER_DYNAMIC_OPAQUE, nullptr, false);
        if (shaderVar >= 0)
          ShaderGlobal::set_int(shaderVar, 0);
      });
    return captured && zeroOffset && model->getCurrentLodNo() == lod;
  }

"""

CAPTURE_SETUP = """  // [KE] VISIBILITY_CAPTURE_V1
  char keAmodalKey[48], keVisibleKey[48];
  snprintf(keAmodalKey, sizeof(keAmodalKey), "mask_amodal_%d", frameIndex);
  snprintf(keVisibleKey, sizeof(keVisibleKey), "mask_visible_%d", frameIndex);
  const char *keAmodalPath = config.getStr(keAmodalKey, "");
  const char *keVisiblePath = config.getStr(keVisibleKey, "");
  const bool keCaptureMasks = config.getBool("capture_visibility_masks", false);
  if (keCaptureMasks && (!*keAmodalPath || !*keVisiblePath))
    return fail("VISIBILITY", "Missing mask output paths");
  model.setDisableAutoChooseLod(false);
"""

CAPTURE_MASKS = """  int keAmodalPixels = 0, keVisiblePixels = 0, keMissingPixels = 0;
  if (keCaptureMasks && !renderer->captureKolobanovEyeMasks(&model, camera, width, height, lod,
      keAmodalPath, keVisiblePath, keAmodalPixels, keVisiblePixels, keMissingPixels))
    return fail("VISIBILITY", "Same-camera mask capture failed; inspect VISIBILITY_FAIL in debug log");

"""

CAPTURE_METADATA = """  if (keCaptureMasks)
  {
    result.setStr("visibility_method", "reverse_z_opaque_depth_compare_v1");
    result.setInt("amodal_pixels", keAmodalPixels);
    result.setInt("visible_pixels", keVisiblePixels);
    result.setInt("unmatched_depth_pixels", keMissingPixels);
    result.setReal("visible_fraction", float(keVisiblePixels) / float(keAmodalPixels));
  }
"""


def install_visibility_capture(dagor: Path) -> bool:
    files = {
        "header": dagor / BASE / "include/de3_dynRenderService.h",
        "render": dagor / BASE / "services/dynRenderSrv/dynRender.cpp",
        "capture": dagor / BASE / "kolobanovEye.cpp",
    }
    raw = {key: path.read_bytes() for key, path in files.items()}
    src = {key: value.replace(b"\r\n", b"\n").decode("utf-8") for key, value in raw.items()}
    marked = [MARKER in text for text in src.values()]
    if all(marked):
        required = {"header": ("captureKolobanovEyeMasks",),
                    "render": ("keMaskCameraValid = true", "readDepth(deferredTarget->getDepth()", "readDepth(isolatedTarget->getDepth()", "VISIBILITY_OK"),
                    "capture": ("keCaptureMasks", "setDisableAutoChooseLod(keCaptureMasks)", "unmatched_depth_pixels")}
        if not all(all(anchor in src[key] for anchor in anchors) for key, anchors in required.items()):
            raise ValueError("Partial visibility installation")
        return False
    if any(marked):
        raise ValueError("Partial visibility installation across Dagor files")

    def change(key, old, new):
        count = src[key].count(old)
        if count != 1:
            raise ValueError(f"Visibility insertion failed: {key} {count} hits for {old[:90]!r}")
        src[key] = src[key].replace(old, new, 1)

    anchor = "  virtual void beforeProjectSceneReset() {}\n"
    change("header", anchor, anchor + "\n" + HEADER_METHOD)
    anchor = "  void renderScreenShot(bool force_disable_wireframe)\n  {\n"
    change("render", anchor, SCREEN_STATE + anchor + "    keMaskCameraValid = false;\n")
    anchor = "          const TMatrix viewItm = orthonormalized_inverse(viewTm);\n"
    change("render", anchor, anchor + SCREEN_SNAPSHOT)
    anchor = "  void closeMicroDetails()\n"
    change("render", anchor, SCENE_METHOD + anchor)
    anchor = "  bool isEnvironmentLightingReady() override { return !dynScene || !dynScene->isEnviProbeUpdatePending(); }\n"
    change("render", anchor, SERVICE_METHOD + anchor)
    anchor = "  model.beforeRender(eye);\n"
    change("capture", anchor, CAPTURE_SETUP + anchor)
    anchor = "  const int lod = model.getCurrentLodNo();\n"
    change("capture", anchor, anchor + "  model.setDisableAutoChooseLod(keCaptureMasks);\n")
    anchor = "  DataBlock result;\n"
    change("capture", anchor, CAPTURE_MASKS + anchor + CAPTURE_METADATA)

    for key, path in files.items():
        eol = b"\r\n" if b"\r\n" in raw[key] else b"\n"
        path.write_bytes(src[key].encode("utf-8").replace(b"\n", eol))
    print("[KE] Installed opt-in same-screenshot depth-mask capture", flush=True)
    return True
