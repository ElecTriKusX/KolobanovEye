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

# Preserve the V1 literal for existing Windows checkouts. V2 detects true
# projected depth boundaries and counts only edges crossing the model's
# visible/occluded silhouette, rather than unrelated background edges.
EDGE_MARKER = "// [KE] FIXED_DEPTH_EDGES_V2"
def edge_scene_method():
    text=scene_method()
    text=text.replace("float *fractions, int &environment_passes)",
                      "float *fractions, int *edge_hits, int &environment_passes)")
    text=text.replace("    if (!fractions || pose_count < 2 || pose_count > 6)",
                      "    if (!fractions || !edge_hits || pose_count < 2 || pose_count > 6)")
    text=text.replace("    for (int i=0; i<pose_count; ++i) fractions[i]=-1.f;",
                      "    for (int i=0; i<pose_count; ++i) { fractions[i]=-1.f; edge_hits[i]=0; }")
    anchor="""      if (!read(sceneDepth))
        return false;
      for (int pose=0; pose<pose_count; ++pose)"""
    replacement=r'''      if (!read(sceneDepth))
        return false;
      // [KE] FIXED_DEPTH_EDGES_V2
      // Reconstruct metric view-space depth before thresholding reverse-Z.
      // This preserves useful distance discontinuities far from the camera.
      Tab<unsigned char> edgeFlags(tmpmem);
      edgeFlags.resize(width*height);
      memset(edgeFlags.data(),0,width*height);
      auto validEnv=[](float z) { return std::isfinite(z) && z>=0.f && z<=1.f; };
      auto validModel=[](float z) { return std::isfinite(z) && z>0.f && z<=1.f; };
      auto linearDepth=[&](float z) {
        return perspective.zn*perspective.zf/(perspective.zn+z*(perspective.zf-perspective.zn));
      };
      auto depthEdge=[&](int i,int j) {
        const float a=sceneDepth[i],b=sceneDepth[j];
        if (!validEnv(a) || !validEnv(b)) return false;
        const float za=linearDepth(a),zb=linearDepth(b);
        return fabsf(za-zb)>max(.6f,.06f*min(za,zb));
      };
      int environmentEdges=0;
      for (int y=0;y<height;++y)
        for (int x=0;x<width;++x)
        {
          const int i=y*width+x;
          if (x+1<width && depthEdge(i,i+1)) { edgeFlags[i]|=1; ++environmentEdges; }
          if (y+1<height && depthEdge(i,i+width)) { edgeFlags[i]|=2; ++environmentEdges; }
        }
      auto unobstructed=[&](int i) {
        const float m=modelDepth[i],e=sceneDepth[i];
        return e<=m+max(1e-7f,1e-4f*max(m,e));
      };
      debug("[KE] FIXED_DEPTH_EDGES width=%d height=%d edges=%d",width,height,environmentEdges);
      for (int pose=0; pose<pose_count; ++pose)'''
    if text.count(anchor)!=1:
        raise ValueError("Depth edge anchor missing")
    text=text.replace(anchor,replacement,1)
    anchor="""        if (amodal>=16 && invalid==0)
          fractions[pose]=float(visible)/amodal;"""
    replacement=r'''        if (amodal>=16 && invalid==0)
        {
          fractions[pose]=float(visible)/amodal;
          int hits=0;
          // Both pixels must lie on the isolated tank. Count only a real
          // environment depth boundary separating its visible and hidden parts.
          auto crossing=[&](int i,int j) {
            return validModel(modelDepth[i]) && validModel(modelDepth[j]) &&
              validEnv(sceneDepth[i]) && validEnv(sceneDepth[j]) && unobstructed(i)!=unobstructed(j);
          };
          for (int y=0;y<height;++y)
            for (int x=0;x<width;++x)
            {
              const int i=y*width+x;
              if ((edgeFlags[i]&1) && crossing(i,i+1)) ++hits;
              if ((edgeFlags[i]&2) && crossing(i,i+width)) ++hits;
            }
          edge_hits[pose]=hits;
          debug("[KE] FIXED_DEPTH_POSE pose=%d amodal=%d visible=%d edge_hits=%d",pose,amodal,visible,hits);
        }'''
    if text.count(anchor)!=1:
        raise ValueError("Depth edge output anchor missing")
    return text.replace(anchor,replacement,1)


HEADER_V2=HEADER.replace("float *, int &)","float *, int *, int &)")
SERVICE_V2=SERVICE.replace("float *fractions, int &passes)", "float *fractions, int *edges, int &passes)").replace("},fractions,passes);","},fractions,edges,passes);")

def edge_edits(key):
    return {"header":((HEADER,HEADER_V2),),
            "render":((scene_method(),edge_scene_method()),(SERVICE,SERVICE_V2))}[key]


def normalize_edge_renderer(src,key):
    for old,new in reversed(edge_edits(key)):
        if src.count(new)==1:
            src=src.replace(new,old,1)
        elif src.count(old)!=1:
            raise ValueError(f"Partial fixed depth edges renderer: {key}")
    return src


def renderer_edits(key):
    return {"header":((HEADER_ANCHOR,HEADER_ANCHOR+HEADER),),
            "render":((SCENE_ANCHOR,scene_method()+SCENE_ANCHOR),
                      (SERVICE_ANCHOR,SERVICE+SERVICE_ANCHOR))}[key]


def normalize_fixed_renderer(src,key):
    if MARKER not in src and "FIXED_CAMERA_DEPTH_SERVICE_V1" not in src:
        return src
    src=normalize_edge_renderer(src,key)
    for old,new in reversed(renderer_edits(key)):
        if src.count(new)!=1:
            raise ValueError("Partial fixed camera renderer")
        src=src.replace(new,old,1)
    return src


def add_fixed_renderer(src,key):
    if MARKER in src or "FIXED_CAMERA_DEPTH_SERVICE_V1" in src:
        src=normalize_fixed_renderer(src,key)
    for old,new in renderer_edits(key):
        if src.count(new)==1:
            continue
        if src.count(old)!=1:
            raise ValueError(f"Fixed renderer anchor missing/ambiguous: {key}")
        src=src.replace(old,new,1)
    for old,new in edge_edits(key):
        if src.count(old)!=1:
            raise ValueError(f"Missing fixed-depth edge anchor: {key}")
        src=src.replace(old,new,1)
    return src


def install_fixed_renderer(dagor: Path):
    paths={"header":dagor/BASE/"include/de3_dynRenderService.h",
           "render":dagor/BASE/"services/dynRenderSrv/dynRender.cpp"}
    prepared={}
    for key,path in paths.items():
        raw=path.read_bytes()
        src=raw.replace(b"\r\n",b"\n").decode("utf-8")
        src=add_fixed_renderer(src,key)
        prepared[key]=(raw,src.encode("utf-8").replace(b"\n",b"\r\n" if b"\r\n" in raw else b"\n"))
    changed=False
    for key,path in paths.items():
        raw,new=prepared[key]
        if raw!=new:
            path.write_bytes(new)
            changed=True
    return changed
