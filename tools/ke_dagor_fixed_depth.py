"""Scoped environment-only depth for several neutral model poses.

Use a separate CPU reference pass. One camera/projection, one scene draw,
one depth read of the environment, then isolated model draws. The cache is
a local Tab, never persisted across frames, scene updates or map changes.
"""
from pathlib import Path
from tools.ke_dagor_avf_probe import SCENE, BASE

MARKER = "// [KE] FIXED_CAMERA_DEPTH_V1"
HEADER_ANCHOR = "  virtual bool collectKolobanovEyeProbe(int, int &, int &, int &) { return false; }\n"
HEADER = """  // [KE] FIXED_CAMERA_DEPTH_V1
  virtual bool probeKolobanovEyeFixed(DynamicRenderableSceneInstance *, const TMatrix &,
    const Driver3dPerspective &, int, int, int, dag::FunctionRef<bool(int)>, float *, int &) { return false; }
"""
SCENE_ANCHOR = "  // [KE] AVF_OPAQUE_PROBE_V1\n  bool probeKolobanovEyeOpaque("
SERVICE_ANCHOR = "  bool probeKolobanovEyeOpaque(DynamicRenderableSceneInstance *model, const TMatrix &camera,\n"

def scene_method():
    # Reuse the established state-save/restore shell, not the scene+model
    # comparison (an environment-only depth texture has different semantics).
    text = SCENE
    start = text.index("  {\n")
    text = """  // [KE] FIXED_CAMERA_DEPTH_V1
  bool probeKolobanovEyeFixed(const TMatrix &camera, const Driver3dPerspective &perspective,
    int width, int height, int pose_count, dag::FunctionRef<bool(int)> prepare_model,
    dag::FunctionRef<void(const Point3 &)> render_model, float *fractions, int &environment_passes)
""" + text[start:]
    text = text.replace("    amodal = visible = missing = 0;\n",
        """    environment_passes = 0;
    if (!fractions || pose_count < 2 || pose_count > 6)
      return false;
    for (int i=0; i<pose_count; ++i) fractions[i]=-1.f;
""",1)
    old = """      if (!keProbeTarget || keProbeTarget->getWidth() != width || keProbeTarget->getHeight() != height)
      {
        keProbeTarget.reset();
        keProbeTarget = eastl::make_unique<DeferredRenderTarget>("deferred_shadow_to_buffer",
          "ke_avf_probe", width, height, DeferredRT::StereoMode::MonoOrMultipass, 0,
          deferred_mrt_cnt, deferred_mrt_fmts, TEXFMT_DEPTH32);
      }"""
    new = """      // Dedicated scratch target: do not resize the existing paired AVF targets.
      auto fixedTarget = eastl::make_unique<DeferredRenderTarget>("deferred_shadow_to_buffer",
        "ke_fixed_depth", width, height, DeferredRT::StereoMode::MonoOrMultipass, 0,
        deferred_mrt_cnt, deferred_mrt_fmts, TEXFMT_DEPTH32);"""
    if text.count(old)!=1:
        raise ValueError("Fixed depth allocation anchor mismatch")
    text=text.replace(old,new,1).replace("keProbeTarget->","fixedTarget->")
    first=text.index("      Tab<float> sceneDepth(tmpmem), modelDepth(tmpmem);")
    last=text.index("    }();",first)
    text=text[:first]+r'''      Tab<float> sceneDepth(tmpmem), modelDepth(tmpmem);
      // The caller guarantees the synthetic tank is NOT bound to the scene.
      clear();
      renderGeomOpaque();
      ++environment_passes;
      if (!read(sceneDepth))
        return false;
      for (int pose=0; pose<pose_count; ++pose)
      {
        if (!prepare_model(pose))
          continue;
        clear();
        if (globalFrameBlockId != -1)
          ShaderGlobal::setBlock(globalFrameBlockId, ShaderGlobal::LAYER_FRAME);
        render_model(Point3::ZERO);
        if (!read(modelDepth))
          continue;
        int amodal=0, visible=0, invalid=0;
        for (int i=0; i<width*height; ++i)
        {
          const float model=modelDepth[i], env=sceneDepth[i];
          if (!std::isfinite(model) || model<=0.f || model>1.f)
            continue;
          ++amodal;
          if (!std::isfinite(env) || env<0.f || env>1.f)
          {
            ++invalid;
            continue;
          }
          // Reverse-Z: clear depth (0) is unobstructed, as is any
          // environment surface behind the model. No scene+model equality test.
          const float tolerance=max(1e-7f,1e-4f*max(model,env));
          visible+=int(env<=model+tolerance);
        }
        if (amodal>=16 && invalid==0)
          fractions[pose]=float(visible)/amodal;
      }
      return true;
'''+text[last:]
    return text

SERVICE = r'''  // [KE] FIXED_CAMERA_DEPTH_SERVICE_V1
  bool probeKolobanovEyeFixed(DynamicRenderableSceneInstance *model, const TMatrix &camera,
    const Driver3dPerspective &perspective, int width, int height, int count,
    dag::FunctionRef<bool(int)> prepare, float *fractions, int &passes) override
  {
    if (!dynScene || !model)
      return false;
    d3d::GpuAutoLock lock;
    return dynScene->probeKolobanovEyeFixed(camera,perspective,width,height,count,prepare,
      [&](const Point3 &) {
        const int shaderVar=get_shader_variable_id("editor_dynmodel_render",true);
        const int old=shaderVar>=0?ShaderGlobal::get_int(shaderVar):0;
        if (shaderVar>=0) ShaderGlobal::set_int(shaderVar,1);
        renderOneDynModelInstance(model,Stage::STG_RENDER_DYNAMIC_OPAQUE,nullptr,false);
        if (shaderVar>=0) ShaderGlobal::set_int(shaderVar,old);
      },fractions,passes);
  }

'''
def install_fixed_renderer(dagor: Path):
    paths={"header":dagor/BASE/"include/de3_dynRenderService.h",
           "render":dagor/BASE/"services/dynRenderSrv/dynRender.cpp"}
    edits={"header":((HEADER_ANCHOR,HEADER_ANCHOR+HEADER),),
           "render":((SCENE_ANCHOR,scene_method()+SCENE_ANCHOR),
                     (SERVICE_ANCHOR,SERVICE+SERVICE_ANCHOR))}
    prepared={}
    for key,path in paths.items():
        raw=path.read_bytes()
        src=raw.replace(b"\r\n",b"\n").decode("utf-8")
        for old,new in edits[key]:
            if src.count(new)==1:
                continue
            if src.count(old)!=1:
                raise ValueError(f"Fixed renderer anchor missing/ambiguous: {key}")
            src=src.replace(old,new,1)
        prepared[key]=(raw,src.encode("utf-8").replace(b"\n",b"\r\n" if b"\r\n" in raw else b"\n"))
    changed=False
    for key,path in paths.items():
        raw,new=prepared[key]
        if raw!=new:
            path.write_bytes(new)
            changed=True
    return changed
