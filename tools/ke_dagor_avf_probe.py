"""Install an experimental opaque-only probe into the pinned Dagor editor.

This path is separate from final RGB/mask capture. It uses the same opaque
geometry shaders, including alpha test, but does not resolve lighting, sky,
shadows or post-processing. It currently reads two small depth buffers back
synchronously; asynchronous GPU reduction is a later optimization.
"""
from pathlib import Path
from tools.ke_dagor_visibility import BASE

MARKER = "// [KE] AVF_OPAQUE_PROBE_V1"

HEADER = r'''  // [KE] AVF_OPAQUE_PROBE_V1
  virtual bool probeKolobanovEyeOpaque(DynamicRenderableSceneInstance *, const TMatrix &,
    const Driver3dPerspective &, int, int, int, int &, int &, int &) { return false; }
'''

SCENE = r'''  // [KE] AVF_OPAQUE_PROBE_V1
  bool probeKolobanovEyeOpaque(const TMatrix &camera, const Driver3dPerspective &perspective,
    int width, int height, int &amodal, int &visible, int &missing,
    dag::FunctionRef<void(const Point3 &)> render_model)
  {
    amodal = visible = missing = 0;
    if (rtype != RTYPE_DYNAMIC_DEFERRED || width < 16 || height < 16 ||
        width > 512 || height > 512 || !d3d::validatepersp(perspective))
      return false;

    int oldX, oldY, oldW, oldH;
    float oldMin, oldMax;
    d3d::getview(oldX, oldY, oldW, oldH, oldMin, oldMax);
    const DagorCurView oldView = curView, oldGlobalView = ::grs_cur_view;
    const auto oldTexCtx = currentTexCtx;
    const bool oldMatrixOk = renderMatrixOk;
    const int oldFrameBlock = ShaderGlobal::getBlock(ShaderGlobal::LAYER_FRAME);
    const int oldSceneBlock = ShaderGlobal::getBlock(ShaderGlobal::LAYER_SCENE);
    static int zn_zfarVarId = get_shader_variable_id("zn_zfar", true);
    const Color4 oldWorldPos = ShaderGlobal::get_float4(worldViewPosVarId);
    const Color4 oldZnZfar = ShaderGlobal::get_float4(zn_zfarVarId);
    mat44f oldWorld;
    d3d::gettm(TM_WORLD, oldWorld);
    Driver3dPerspective oldPerspective;
    const bool oldPerspectiveValid = d3d::getpersp(oldPerspective);
    TMatrix4 oldProjection;
    d3d::gettm(TM_PROJ, &oldProjection);

    const bool ok = [&]() -> bool {
      ScopeViewProjMatrix scopeViewProj;
      SCOPE_RENDER_TARGET;
      if (!keProbeTarget || keProbeTarget->getWidth() != width || keProbeTarget->getHeight() != height)
      {
        keProbeTarget.reset();
        keProbeTarget = eastl::make_unique<DeferredRenderTarget>("deferred_shadow_to_buffer",
          "ke_avf_probe", width, height, DeferredRT::StereoMode::MonoOrMultipass, 0,
          deferred_mrt_cnt, deferred_mrt_fmts, TEXFMT_DEPTH32);
      }
      curView.itm = camera;
      curView.tm = orthonormalized_inverse(camera);
      curView.pos = camera.getcol(3);
      ::grs_cur_view = curView;
      TMatrix4 projection;
      d3d::settm(TM_VIEW, curView.tm);
      if (!d3d::setpersp(perspective, &projection))
        return false;
      d3d::settm(TM_WORLD, TMatrix::IDENT);
      gatherRendSrv();
      // Includes clipmap/impostor/visibility preparation. Skipping this
      // would compare against stale geometry from another camera.
      beforeRender();
      if (!renderMatrixOk)
        return false;
      d3d::settm(TM_VIEW, curView.tm);
      if (!d3d::setpersp(perspective, &projection))
        return false;
      ::set_viewvecs_to_shader(curView.tm, projection);
      currentTexCtx = TexStreamingContext(perspective, width);
      auto read = [&](Tab<float> &out) -> bool {
        d3d::set_render_target();
        Texture *texture = keProbeTarget->getDepth();
        TextureInfo info{};
        if (!texture || !texture->getinfo(info) || (info.cflg & TEXFMT_MASK) != TEXFMT_DEPTH32)
          return false;
        const auto depth = lock_texture<const float>(texture, 0, TEXLOCK_READ);
        if (!depth || depth.getWidthInElems() != width || depth.getHeightInElems() != height)
          return false;
        out.resize(width * height);
        for (int y = 0; y < height; ++y)
          memcpy(out.data() + y * width,
            reinterpret_cast<const char *>(depth.get()) + y * depth.getByteStride(),
            width * sizeof(float));
        return true;
      };
      auto clear = [&]() {
        keProbeTarget->setRt();
        d3d::setview(0, 0, width, height, 0.f, 1.f);
        d3d::clearview(CLEAR_TARGET | CLEAR_ZBUFFER | CLEAR_STENCIL, 0, 0, 0);
        d3d::settm(TM_WORLD, TMatrix::IDENT);
      };
      Tab<float> sceneDepth(tmpmem), modelDepth(tmpmem);
      clear();
      renderGeomOpaque();
      if (!read(sceneDepth))
        return false;
      clear();
      // Reset the frame block just as the existing isolated-model pass does.
      if (globalFrameBlockId != -1)
        ShaderGlobal::setBlock(globalFrameBlockId, ShaderGlobal::LAYER_FRAME);
      render_model(Point3::ZERO);
      if (!read(modelDepth))
        return false;
      for (int i = 0; i < width * height; ++i)
      {
        const float model = modelDepth[i], scene = sceneDepth[i];
        if (!std::isfinite(model) || model <= 0.f || model > 1.f)
          continue;
        ++amodal;
        const bool sceneValid = std::isfinite(scene) && scene > 0.f && scene <= 1.f;
        const float tolerance = max(1e-7f, 1e-4f * max(model, scene));
        visible += int(sceneValid && fabsf(model - scene) <= tolerance);
        missing += int(!sceneValid || scene + tolerance < model);
      }
      // An undersampled or inconsistent probe is unknown, not heavy overlap.
      return amodal >= 16 && missing <= max(4, amodal / 20);
    }();

    curView = oldView;
    ::grs_cur_view = oldGlobalView;
    currentTexCtx = oldTexCtx;
    renderMatrixOk = oldMatrixOk;
    ShaderGlobal::setBlock(oldFrameBlock, ShaderGlobal::LAYER_FRAME);
    ShaderGlobal::setBlock(oldSceneBlock, ShaderGlobal::LAYER_SCENE);
    ShaderGlobal::set_float4(worldViewPosVarId, oldWorldPos);
    ShaderGlobal::set_float4(zn_zfarVarId, oldZnZfar);
    d3d::settm(TM_WORLD, oldWorld);
    if (oldPerspectiveValid)
      d3d::setpersp(oldPerspective);
    else
      d3d::settm(TM_PROJ, &oldProjection);
    d3d::setview(oldX, oldY, oldW, oldH, oldMin, oldMax);
    return ok;
  }

'''

SERVICE = r'''  bool probeKolobanovEyeOpaque(DynamicRenderableSceneInstance *model, const TMatrix &camera,
    const Driver3dPerspective &perspective, int width, int height, int lod,
    int &amodal, int &visible, int &missing) override
  {
    if (!dynScene || !model || model->getCurrentLodNo() != lod)
      return false;
    d3d::GpuAutoLock gpuLock;
    const bool ok = dynScene->probeKolobanovEyeOpaque(camera, perspective, width, height,
      amodal, visible, missing, [&](const Point3 &) {
        const int shaderVar = get_shader_variable_id("editor_dynmodel_render", true);
        const int old = shaderVar >= 0 ? ShaderGlobal::get_int(shaderVar) : 0;
        if (shaderVar >= 0)
          ShaderGlobal::set_int(shaderVar, 1);
        renderOneDynModelInstance(model, Stage::STG_RENDER_DYNAMIC_OPAQUE, nullptr, false);
        if (shaderVar >= 0)
          ShaderGlobal::set_int(shaderVar, old);
      });
    return ok && model->getCurrentLodNo() == lod;
  }

'''

# Insert after established methods without changing predecessor patch anchors.
EDITS = {
    "header": (("  virtual void beforeProjectSceneReset() {}\n", None),),
    "render": (
        ("  void closeMicroDetails()\n", SCENE),
        ("  bool isEnvironmentLightingReady() override { return !dynScene || !dynScene->isEnviProbeUpdatePending(); }\n", SERVICE),
        ("  eastl::unique_ptr<DeferredRenderTarget> isolatedTarget;\n",
         "  eastl::unique_ptr<DeferredRenderTarget> keProbeTarget;\n"),
    ),
}


def install_avf_probe(dagor: Path) -> bool:
    paths = {"header": dagor / BASE / "include/de3_dynRenderService.h",
             "render": dagor / BASE / "services/dynRenderSrv/dynRender.cpp"}
    raw = {k: p.read_bytes() for k, p in paths.items()}
    src = {k: v.replace(b"\r\n", b"\n").decode("utf-8") for k, v in raw.items()}
    # Append virtual at the end of the existing KolobanovEye mask ABI.
    anchor = "bool &depthMismatch) { depthMismatch = false; return false; }\n"
    changes = {"header": ((anchor, anchor + HEADER),),
               "render": tuple((old, new + old) for old, new in EDITS["render"]) +
                         (("  void closeIsolatedTargets()\n  {\n",
                           "  void closeIsolatedTargets()\n  {\n    keProbeTarget.reset();\n"),)}
    marked = [MARKER in v for v in src.values()]
    if all(marked):
        if not all(src[k].count(new) == 1 for k, pairs in changes.items() for _, new in pairs):
            raise ValueError("Partial AVF probe installation")
        return False
    if any(marked):
        raise ValueError("Partial AVF probe across renderer files")
    for k, pairs in changes.items():
        for old, new in pairs:
            if src[k].count(old) != 1:
                raise ValueError(f"AVF probe anchor missing/ambiguous: {k} {old[:90]!r}")
            src[k] = src[k].replace(old, new, 1)
    for k, p in paths.items():
        p.write_bytes(src[k].encode("utf-8").replace(b"\n", b"\r\n" if b"\r\n" in raw[k] else b"\n"))
    print("[KE] Installed experimental opaque-only AVF probe", flush=True)
    return True
