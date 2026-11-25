"""GPU fixed-camera reduction, preserving V1–V5 migration anchors.

The full environment D32 texture is immutable within a group. GPU mode
reads it once for CPU world-edge proposals, and never maps model depth.
Verification uses the same rendered textures for both implementations.
"""
MARKER="// [KE] FIXED_GPU_V6"

def change(s,old,new):
    if s.count(old)!=1:
        raise ValueError("Fixed GPU anchor missing/ambiguous: "+repr(old[:100]))
    return s.replace(old,new,1)


def scene_gpu(old):
    s=change(old,"int &world_count, int &environment_passes)",
        "int &world_count, int &environment_passes, int backend, int &gpu_submitted, int &gpu_verified)")
    s=change(s,"    environment_passes = 0;",
        """    // [KE] FIXED_GPU_V6
    gpu_submitted=gpu_verified=0;
    if (backend<0 || backend>2 || perspective.zn!=.2f || perspective.zf!=20000.f ||
        perspective.ox!=0.f || perspective.oy!=0.f)
      return false;
    environment_passes = 0;""")
    s=change(s,'        deferred_mrt_cnt, deferred_mrt_fmts, TEXFMT_DEPTH32);\n      curView.itm',
        '''        deferred_mrt_cnt, deferred_mrt_fmts, TEXFMT_DEPTH32);
      auto modelTarget = eastl::make_unique<DeferredRenderTarget>("deferred_shadow_to_buffer",
        "ke_fixed_model_depth", width, height, DeferredRT::StereoMode::MonoOrMultipass, 0,
        deferred_mrt_cnt, deferred_mrt_fmts, TEXFMT_DEPTH32);
      curView.itm''')
    s=change(s,"      auto read = [&](Tab<float> &out) -> bool {\n        d3d::set_render_target();\n        Texture *texture = fixedTarget->getDepth();",
        "      auto read = [&](Texture *texture, Tab<float> &out) -> bool {")
    s=change(s,"      auto clear = [&]() {\n        fixedTarget->setRt();",
        "      auto clear = [&](DeferredRenderTarget &target) {\n        target.setRt();")
    # Keep the exact CPU depth-edge and aperture reference. Move rendering
    # and readback out of that loop, so GPU poses are submitted as a group.
    begin=s.index("      Tab<float> sceneDepth(tmpmem), modelDepth(tmpmem);")
    edge_start=s.index("      // [KE] FIXED_DEPTH_EDGES_V2",begin)
    edge_end=s.index("      for (int pose=0; pose<pose_count; ++pose)",edge_start)
    edge_code=s[edge_start:edge_end]
    cpu_start=s.index("        int amodal=0, visible=0, invalid=0;",edge_end)
    world_start=s.index("          // [KE] FIXED_WORLD_EDGES_V4",cpu_start)
    world_stats_end=s.index("          const float cx=sumX/amodal",world_start)
    world_end=s.index('          debug("[KE] FIXED_DEPTH_POSE',world_stats_end)
    cpu=s[cpu_start:world_start]
    # CPU code declares amodal/visible/invalid and hits/openings inside
    # the validity branch. Export a summary for same-input comparison.
    cpu=change(cpu,"          fractions[pose]=float(visible)/amodal;","          // Reference only; publish after GPU verification.")
    cpu=change(cpu,"          edge_hits[pose]=hits;","          ref.edges=hits;")
    cpu=change(cpu,"          aperture_hits[pose]=openings;","          ref.apertures=openings;")
    cpu=change(cpu,'          debug("[KE] FIXED_APERTURE pose=%d scanlines=%d",pose,openings);\n','')
    cpu+=r'''          for (int y=0;y<height;++y)
            for (int x=0;x<width;++x)
              if (validModel(modelDepth[y*width+x]))
              {
                ref.sumX+=x; ref.sumY+=y; ref.sumZ+=linearDepth(modelDepth[y*width+x]);
                ref.minX=min(ref.minX,x); ref.minY=min(ref.minY,y);
                ref.maxX=max(ref.maxX,x); ref.maxY=max(ref.maxY,y);
              }
        }
        ref.amodal=amodal; ref.visible=visible; ref.invalid=invalid;
        return ref;
      };
'''
    world=s[world_stats_end:world_end]
    world=change(world,"const float cx=sumX/amodal,cy=sumY/amodal,z=sumZ/amodal;",
        "const float cx=float(result.sumX)/amodal+.5f,cy=float(result.sumY)/amodal+.5f,z=float(result.sumZ/amodal);")
    world=change(world,"max(maxX-minX+1,maxY-minY+1)",
        "max(result.maxX-result.minX+1,result.maxY-result.minY+1)")
    tail=s.index("    }();",world_end)
    s=s[:begin]+SUBMIT+edge_code+SUMMARY+cpu+COLLECT+world+r'''          debug("[KE] FIXED_DEPTH_POSE pose=%d amodal=%d visible=%d edge_hits=%d",
            pose,result.amodal,result.visible,result.edges);
          debug("[KE] FIXED_APERTURE pose=%d scanlines=%d",pose,result.apertures);
        }
      }
      debug("[KE] FIXED_GPU_GROUP backend=%d submitted=%d verified=%d env_reads=1 model_reads=%d",
        backend,gpu_submitted,gpu_verified,modelReads);
      return true;
'''+s[tail:]
    return s


SUBMIT=r'''      Tab<float> sceneDepth(tmpmem), modelDepth(tmpmem);
      Tab<float> cpuModels[6];
      bool drawn[6]={};
      int modelReads=0;
      const int gx=(width+7)/8,gy=(height+7)/8;
      const int groupCount=gx*gy,words=12+groupCount;
      // Unmanaged buffers avoid the managed-name refcount bug in AVF V3.
      struct KeFixedGpuGroup
      {
        PROGRAM program=BAD_PROGRAM;
        BufPtr counts[6];
        d3d::EventQuery *events[6]={};
        ~KeFixedGpuGroup()
        {
          for (auto &event:events)
            if (event) d3d::release_event_query(event);
          if (program!=BAD_PROGRAM) d3d::delete_program(program);
        }
      } gpu;
      if (backend)
      {
        static const dx11::SimpleHeader header{sizeof(keFixedReduceBytecode),-1,-1,1,1,0};
        ShaderSourceExt source{};
        source.compressedData=make_span_const(keFixedReduceBytecode,sizeof(keFixedReduceBytecode));
        source.uncompressedSize=sizeof(keFixedReduceBytecode);
        source.metadata=make_span_const(reinterpret_cast<const uint8_t *>(&header),sizeof(header));
        gpu.program=d3d::create_program_cs(source,CSPreloaded::No);
        if (gpu.program==BAD_PROGRAM) return false;
      }
      // One environment draw for every pose. Never clear this target again.
      clear(*fixedTarget);
      renderGeomOpaque();
      ++environment_passes;
      d3d::set_render_target();
      Texture *sceneTex=fixedTarget->getDepth();
      if (!sceneTex) return false;
      request_readback_nosyslock(sceneTex,0);
      for (int pose=0;pose<pose_count;++pose)
      {
        if (!prepare_model(pose)) continue;
        clear(*modelTarget);
        if (globalFrameBlockId!=-1)
          ShaderGlobal::setBlock(globalFrameBlockId,ShaderGlobal::LAYER_FRAME);
        render_model(Point3::ZERO);
        d3d::set_render_target();
        Texture *modelTex=modelTarget->getDepth();
        if (!modelTex) return false;
        if (backend)
        {
          static const char *const names[6]={"ke_fixed_counts_0","ke_fixed_counts_1",
            "ke_fixed_counts_2","ke_fixed_counts_3","ke_fixed_counts_4","ke_fixed_counts_5"};
          gpu.counts[pose]=dag::buffers::create_ua_structured_readback(sizeof(uint32_t),words,names[pose]);
          gpu.events[pose]=d3d::create_event_query();
          auto *buf=gpu.counts[pose].get();
          if (!buf || !gpu.events[pose] || !d3d::zero_rwbufi(buf)) return false;
          const bool bound=d3d::set_program(gpu.program) &&
            d3d::set_tex(STAGE_CS,0,sceneTex) && d3d::set_tex(STAGE_CS,1,modelTex) &&
            d3d::set_rwbuffer(STAGE_CS,0,buf);
          const bool sent=bound && d3d::dispatch(gx,gy,1);
          d3d::set_rwbuffer(STAGE_CS,0,nullptr);
          d3d::set_tex(STAGE_CS,0,nullptr);
          d3d::set_tex(STAGE_CS,1,nullptr);
          d3d::set_program(BAD_PROGRAM);
          ShaderElement::invalidate_cached_state_block();
          if (!sent || !issue_readback_query(gpu.events[pose],buf)) return false;
          ++gpu_submitted;
        }
        // CPU/verify read the original pose texture before the next draw.
        // GPU-only never requests a full model-depth readback.
        if (backend!=1)
        {
          request_readback_nosyslock(modelTex,0);
          if (!read(modelTex,cpuModels[pose])) return false;
          ++modelReads;
        }
        drawn[pose]=true;
      }
      if (!read(sceneTex,sceneDepth)) return false;
'''

SUMMARY=r'''      struct FixedSummary
      {
        int amodal=0,visible=0,invalid=0,edges=0,apertures=0;
        uint32_t sumX=0,sumY=0;
        double sumZ=0.;
        int minX=0,minY=0,maxX=-1,maxY=-1;
        bool valid() const { return amodal>=16 && invalid==0; }
      };
      auto cpuSummary=[&](int pose) {
        FixedSummary ref;
        ref.minX=width; ref.minY=height;
        modelDepth=cpuModels[pose];
'''

COLLECT=r'''      // Group boundary: all pose dispatches already submitted.
      for (int pose=0;pose<pose_count;++pose)
      {
        if (!drawn[pose]) continue;
        FixedSummary result;
        if (backend)
        {
          const auto count=lock_sbuffer<const uint32_t>(gpu.counts[pose].get(),0,words,VBLOCK_READONLY);
          if (!count || count[11]!=groupCount || count[0]>width*height ||
              count[1]>count[0] || count[2]>count[0]-count[1] ||
              count[3]>2*count[0] || count[4]>width+height ||
              count[5]>count[0]*(width-1) || count[6]>count[0]*(height-1) ||
              count[7]>width || count[8]>height || count[9]>width || count[10]>height)
            return false;
          result.amodal=count[0]; result.visible=count[1]; result.invalid=count[2];
          result.edges=count[3]; result.apertures=count[4];
          result.sumX=count[5]; result.sumY=count[6];
          result.minX=width-int(count[9]); result.minY=height-int(count[10]);
          result.maxX=int(count[7])-1; result.maxY=int(count[8])-1;
          for (int k=0;k<groupCount;++k)
          {
            const uint32_t bits=count[12+k];
            float metric=0.f;
            memcpy(&metric,&bits,sizeof(metric));
            if (!std::isfinite(metric) || metric<0.f || metric>64.f*perspective.zf) return false;
            result.sumZ+=metric;
          }
          if (result.amodal && (result.minX>result.maxX || result.minY>result.maxY ||
              result.sumZ<result.amodal*perspective.zn*.999 || result.sumZ>result.amodal*perspective.zf*1.001))
            return false;
          if (backend==2)
          {
            const auto ref=cpuSummary(pose);
            // Invalid/undersampled silhouettes have no topology or centroid
            // contract in the CPU reference; always verify the basic counts.
            if (result.amodal!=ref.amodal || result.visible!=ref.visible || result.invalid!=ref.invalid ||
                (ref.valid() && (result.edges!=ref.edges || result.apertures!=ref.apertures ||
                result.sumX!=ref.sumX || result.sumY!=ref.sumY ||
                result.minX!=ref.minX || result.minY!=ref.minY ||
                result.maxX!=ref.maxX || result.maxY!=ref.maxY ||
                fabs(result.sumZ-ref.sumZ)>max(0.001,0.00001*ref.sumZ))))
            {
              debug("[KE] FIXED_GPU_VERIFY_FAIL pose=%d cpu=%d,%d,%d,%d,%d gpu=%d,%d,%d,%d,%d",
                pose,ref.amodal,ref.visible,ref.invalid,ref.edges,ref.apertures,
                result.amodal,result.visible,result.invalid,result.edges,result.apertures);
              return false;
            }
            ++gpu_verified;
            debug("[KE] FIXED_GPU_VERIFY_OK pose=%d amodal=%d visible=%d edges=%d apertures=%d",
              pose,result.amodal,result.visible,result.edges,result.apertures);
            result=ref; // identical reference world proposals in verify
          }
        }
        else result=cpuSummary(pose);
        if (result.valid())
        {
          const int amodal=result.amodal;
          fractions[pose]=float(result.visible)/amodal;
          edge_hits[pose]=result.edges;
          aperture_hits[pose]=result.apertures;
'''


def renderer_edits(key):
    from tools.ke_dagor_aperture import renderer_edits as aperture
    out=[]
    for _,old in aperture(key):
        if key=="header":
            new=change(old,"Point3 *, int &, int &)","Point3 *, int &, int &, int, int &, int &)")
        elif "bool probeKolobanovEyeFixed(const TMatrix" in old:
            new=scene_gpu(old)
        else:
            new=change(old,"int &world_count, int &passes) override",
                "int &world_count, int &passes, int backend, int &submitted, int &verified) override")
            new=change(new,"world_edges,world_count,passes);","world_edges,world_count,passes,backend,submitted,verified);")
        out.append((old,new))
    return tuple(out)


CAPTURE_EDITS=(
    ("  int keFixedApertureHits=0;\n",
     """  int keFixedApertureHits=0;
  // [KE] FIXED_GPU_V6
  const char *keFixedBackend=config.getStr("avf_backend","gpu");
  const int keFixedBackendId=strcmp(keFixedBackend,"gpu")==0?1:(strcmp(keFixedBackend,"verify")==0?2:0);
  int keFixedGpuSubmitted=0,keFixedGpuVerified=0;
"""),
    ("              keFixedFound,prepare,fractions,edges,apertures,keWorldEdges,keWorldEdgeCount,keFixedPasses);",
     """              keFixedFound,prepare,fractions,edges,apertures,keWorldEdges,keWorldEdgeCount,keFixedPasses,
              keFixedBackendId,keFixedGpuSubmitted,keFixedGpuVerified);
            if (!measured && keFixedBackendId==2 && keFixedGpuSubmitted)
              return fail("FIXED_GPU_VERIFY","Fixed-depth GPU reference failed");"""),
    ('    result.setInt("fixed_group_aperture_hits",keFixedApertureHits);\n',
     '''    result.setInt("fixed_group_aperture_hits",keFixedApertureHits);
    result.setStr("fixed_group_backend",keFixedBackend);
    result.setInt("fixed_group_gpu_submitted",keFixedGpuSubmitted);
    result.setInt("fixed_group_gpu_verified",keFixedGpuVerified);
'''),
)
