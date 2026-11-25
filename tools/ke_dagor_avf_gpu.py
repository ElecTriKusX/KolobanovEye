"""Add queued DX11 reduction to the AVF renderer, preserving its CPU reference."""
from pathlib import Path
from tools.ke_dagor_visibility import BASE
from tools.ke_dagor_avf_probe import HEADER, SERVICE, paired_scene, PAIRED_EDITS

MARKER = "// [KE] AVF_GPU_QUEUE_V3"
HEADER_NEW = HEADER.replace(
    "int, int, int, int &, int &, int &) { return false; }",
    """int, int, int, int &, int &, int &, int slot = -1) { return false; }
  virtual bool collectKolobanovEyeProbe(int, int &, int &, int &) { return false; }""")

INCLUDES = r'''#include <drv/3d/dag_shader.h>
#include <drv/3d/dag_dispatch.h>
#include <drv/3d/dag_buffers.h>
#include <drv/3d/dag_texture.h>
#include <drv/3d/dag_rwResource.h>
#include <3d/dag_lockSbuffer.h>
#include <drv/shadersMetaData/dx11/compiled_shader_header.h>
#include "ke_avf_reduce_bytecode.h"
'''

MEMBERS = r'''  // [KE] AVF_GPU_QUEUE_V3
  struct KeProbeSlot
  {
    UniqueBuf counters;
    d3d::EventQuery *event = nullptr;
    bool pending = false;
    unsigned pixels = 0, groups = 0;
  };
  KeProbeSlot keProbeSlots[4];
  PROGRAM keProbeProgram = BAD_PROGRAM;

  bool keSubmitProbe(int slot, Texture *scene, Texture *model, int w, int h)
  {
    if (slot < 0 || slot >= 4 || !scene || !model)
      return false;
    KeProbeSlot &q = keProbeSlots[slot];
    if (q.pending)
      return false;
    if (keProbeProgram == BAD_PROGRAM)
    {
      static const dx11::SimpleHeader header{sizeof(keAvfReduceBytecode), -1, -1, 1, 1, 0};
      ShaderSourceExt src{};
      src.compressedData = make_span_const(keAvfReduceBytecode, sizeof(keAvfReduceBytecode));
      src.uncompressedSize = sizeof(keAvfReduceBytecode);
      src.metadata = make_span_const(reinterpret_cast<const uint8_t *>(&header), sizeof(header));
      keProbeProgram = d3d::create_program_cs(src, CSPreloaded::No);
    }
    if (!q.counters)
      q.counters = dag::buffers::create_ua_structured_readback(sizeof(uint32_t), 4, "ke_avf_counts");
    if (!q.event)
      q.event = d3d::create_event_query();
    if (keProbeProgram == BAD_PROGRAM || !q.counters || !q.event)
      return false;
    auto *buf = q.counters.getBuf();
    if (!d3d::zero_rwbufi(buf))
      return false;
    const bool bound = d3d::set_program(keProbeProgram) &&
      d3d::set_tex(STAGE_CS, 0, scene) && d3d::set_tex(STAGE_CS, 1, model) &&
      d3d::set_rwbuffer(STAGE_CS, 0, buf);
    const unsigned gx = (w+7)/8, gy = (h+7)/8;
    const bool sent = bound && d3d::dispatch(gx, gy, 1);
    d3d::set_rwbuffer(STAGE_CS, 0, nullptr);
    d3d::set_tex(STAGE_CS, 0, nullptr);
    d3d::set_tex(STAGE_CS, 1, nullptr);
    d3d::set_program(BAD_PROGRAM);
    ShaderElement::invalidate_cached_state_block();
    if (!sent || !issue_readback_query(q.event, buf))
      return false;
    q.pixels = w*h;
    q.groups = gx*gy;
    q.pending = true;
    return true;
  }

  bool collectKolobanovEyeProbe(int slot, int &amodal, int &visible, int &missing)
  {
    amodal = visible = missing = 0;
    if (slot < 0 || slot >= 4 || !keProbeSlots[slot].pending)
      return false;
    KeProbeSlot &q = keProbeSlots[slot];
    q.pending = false;
    // All cameras in this group have already been submitted. A blocking
    // map here is the explicit group boundary, never a per-camera barrier.
    const auto counts = lock_sbuffer<const uint32_t>(q.counters.getBuf(), 0, 4, VBLOCK_READONLY);
    if (!counts || counts[3] != q.groups || counts[0] > q.pixels ||
        counts[1] > counts[0] || counts[2] > counts[0]-counts[1])
      return false;
    amodal = counts[0]; visible = counts[1]; missing = counts[2];
    return amodal >= 16 && missing <= max(4, amodal/20);
  }

  void closeKeProbeQueue()
  {
    for (auto &q : keProbeSlots)
    {
      q.pending = false;
      if (q.event)
        d3d::release_event_query(q.event);
      q.event = nullptr;
      q.counters.close();
    }
    if (keProbeProgram != BAD_PROGRAM)
      d3d::delete_program(keProbeProgram);
    keProbeProgram = BAD_PROGRAM;
  }

'''
SCENE_OLD = paired_scene()
SCENE_NEW = SCENE_OLD.replace(
    "dag::FunctionRef<void(const Point3 &)> render_model)",
    "dag::FunctionRef<void(const Point3 &)> render_model, int slot)")
SCENE_NEW = SCENE_NEW.replace(
    "      request_readback_nosyslock(sceneTex, 0);",
    "      if (slot < 0) request_readback_nosyslock(sceneTex, 0);")
SCENE_NEW = SCENE_NEW.replace(
    "      request_readback_nosyslock(modelTex, 0);",
    """      if (slot >= 0)
        return keSubmitProbe(slot, sceneTex, modelTex, width, height);
      request_readback_nosyslock(modelTex, 0);""")
# Verification mode consumes the same two textures, not a second rendering.
SCENE_NEW = SCENE_NEW.replace(
    "      // An undersampled or inconsistent probe is unknown, not heavy overlap.\n",
    """      if (slot == -2)
      {
        int ga=0, gv=0, gm=0;
        const bool submitted = keSubmitProbe(0,sceneTex,modelTex,width,height);
        const bool gpuValid = submitted && collectKolobanovEyeProbe(0,ga,gv,gm);
        const bool cpuValid = amodal >= 16 && missing <= max(4,amodal/20);
        if (!submitted || gpuValid != cpuValid || ga!=amodal || gv!=visible || gm!=missing)
        {
          debug("[KE] AVF_GPU_VERIFY_FAIL cpu=%d,%d,%d gpu=%d,%d,%d submitted=%d",
            amodal,visible,missing,ga,gv,gm,int(submitted));
          return false;
        }
        debug("[KE] AVF_GPU_VERIFY_OK amodal=%d visible=%d missing=%d",amodal,visible,missing);
      }
      // An undersampled or inconsistent probe is unknown, not heavy overlap.
""")
SERVICE_NEW = SERVICE.replace("int &amodal, int &visible, int &missing) override",
                              "int &amodal, int &visible, int &missing, int slot) override")
SERVICE_NEW = SERVICE_NEW.replace(
    "      });\n    return ok && model->getCurrentLodNo() == lod;",
    """      }, slot);
    if (ok && slot >= 0 && model->getCurrentLodNo() != lod)
    {
      int a, v, m;
      dynScene->collectKolobanovEyeProbe(slot, a, v, m);
      return false;
    }
    return ok && model->getCurrentLodNo() == lod;""")
SERVICE_NEW += r'''  bool collectKolobanovEyeProbe(int slot, int &amodal, int &visible, int &missing) override
  {
    if (!dynScene) return false;
    d3d::GpuAutoLock lock;
    return dynScene->collectKolobanovEyeProbe(slot, amodal, visible, missing);
  }

'''

# Original marker literals retained for reversible, fail-closed migrations.
EDITS = {
    "header": ((HEADER, HEADER_NEW),),
    "render": (
        ("#include <3d/dag_lockTexture.h>\n", "#include <3d/dag_lockTexture.h>\n" + INCLUDES),
        (SCENE_OLD, SCENE_NEW),
        (SERVICE, SERVICE_NEW),
        (PAIRED_EDITS[1][1], PAIRED_EDITS[1][1] + MEMBERS),
        (PAIRED_EDITS[2][1], PAIRED_EDITS[2][1] + "    closeKeProbeQueue();\n"),
    ),
}

# The first native build exposed two C++ issues. Keep the original V3
# literals for migrating already-patched checkouts and predecessor checks.
BUILD_FIXES = {
    "header": (("int &, int &, int &, int slot = -1) { return false; }",
                "int &, int &, int &, int = -1) { return false; }"),),
    "render": (
        ("  bool collectKolobanovEyeProbe(int slot, int &amodal, int &visible, int &missing)\n  {\n",
         "public:\n  bool collectKolobanovEyeProbe(int slot, int &amodal, int &visible, int &missing)\n  {\n"),
        ("  void closeKeProbeQueue()\n  {\n",
         "private:\n  void closeKeProbeQueue()\n  {\n"),
    ),
}


# Preserve the original patch literals for migration; diagnose each early exit
# before accepting a collision fallback as a usable AVF measurement.
RUNTIME_DIAGNOSTICS = (
    ("""        width > 512 || height > 512 || !d3d::validatepersp(perspective))
      return false;""",
     """        width > 512 || height > 512 || !d3d::validatepersp(perspective))
    {
      debug("[KE] AVF_NATIVE_FAIL stage=precondition rtype=%d width=%d height=%d wk=%f hk=%f zn=%f zf=%f ox=%f oy=%f",
        rtype,width,height,perspective.wk,perspective.hk,perspective.zn,perspective.zf,perspective.ox,perspective.oy);
      return false;
    }"""),
    ("""      if (!d3d::setpersp(perspective, &projection))
        return false;
      d3d::settm(TM_WORLD, TMatrix::IDENT);""",
     """      if (!d3d::setpersp(perspective, &projection))
      {
        debug("[KE] AVF_NATIVE_FAIL stage=initial_projection");
        return false;
      }
      d3d::settm(TM_WORLD, TMatrix::IDENT);"""),
    ("""      if (!renderMatrixOk)
        return false;
      d3d::settm(TM_VIEW, curView.tm);
      if (!d3d::setpersp(perspective, &projection))
        return false;""",
     """      if (!renderMatrixOk)
      {
        debug("[KE] AVF_NATIVE_FAIL stage=before_render_matrix");
        return false;
      }
      d3d::settm(TM_VIEW, curView.tm);
      if (!d3d::setpersp(perspective, &projection))
      {
        debug("[KE] AVF_NATIVE_FAIL stage=restored_projection");
        return false;
      }"""),
    ("""      if (!sceneTex)
        return false;
      // Queue the staging copy""",
     """      if (!sceneTex)
      {
        debug("[KE] AVF_NATIVE_FAIL stage=scene_depth_missing");
        return false;
      }
      // Queue the staging copy"""),
    ("""      if (!modelTex)
        return false;
      if (slot >= 0)""",
     """      if (!modelTex)
      {
        debug("[KE] AVF_NATIVE_FAIL stage=model_depth_missing");
        return false;
      }
      if (slot >= 0)"""),
    ("""      if (!read(sceneTex, sceneDepth) || !read(modelTex, modelDepth))
        return false;""",
     """      if (!read(sceneTex, sceneDepth))
      {
        debug("[KE] AVF_NATIVE_FAIL stage=scene_readback");
        return false;
      }
      if (!read(modelTex, modelDepth))
      {
        debug("[KE] AVF_NATIVE_FAIL stage=model_readback");
        return false;
      }"""),
    ("""      // An undersampled or inconsistent probe is unknown, not heavy overlap.
      return amodal >= 16 && missing <= max(4, amodal / 20);""",
     """      // An undersampled or inconsistent probe is unknown, not heavy overlap.
      if (amodal < 16 || missing > max(4, amodal / 20))
        debug("[KE] AVF_NATIVE_FAIL stage=depth_counts amodal=%d visible=%d missing=%d",amodal,visible,missing);
      return amodal >= 16 && missing <= max(4, amodal / 20);"""),
    ("""    if (!dynScene || !model || model->getCurrentLodNo() != lod)
      return false;
    d3d::GpuAutoLock gpuLock;
    const bool ok = dynScene->probeKolobanovEyeOpaque""",
     """    if (!dynScene || !model || model->getCurrentLodNo() != lod)
    {
      debug("[KE] AVF_NATIVE_FAIL stage=service_precondition scene=%d model=%d lod=%d current=%d",
        int(dynScene!=nullptr),int(model!=nullptr),lod,model?model->getCurrentLodNo():-1);
      return false;
    }
    d3d::GpuAutoLock gpuLock;
    const bool ok = dynScene->probeKolobanovEyeOpaque"""),
)
BUILD_FIXES["render"] += RUNTIME_DIAGNOSTICS

# Dagor frameStateTM.inc.h: validatepersp only recognizes a symmetric
# projection (col2.x/y must be zero). calcproj and setpersp explicitly
# support ox/oy and setpersp caches the complete Driver3dPerspective.
# Preserve the old diagnostic block as an upgrade anchor.
PROJECTION_FIX = (
    RUNTIME_DIAGNOSTICS[0][1],
    """        width > 512 || height > 512)
    {
      debug("[KE] AVF_NATIVE_FAIL stage=precondition rtype=%d width=%d height=%d",
        rtype,width,height);
      return false;
    }
    // [KE] AVF_ASYMMETRIC_PROJECTION_V5
    // The crop has already been checked against the base clip-space matrix.
    // Do not use validatepersp: it rejects any nonzero lens shift.
    if (!std::isfinite(perspective.wk) || !std::isfinite(perspective.hk) ||
        !std::isfinite(perspective.zn) || !std::isfinite(perspective.zf) ||
        !std::isfinite(perspective.ox) || !std::isfinite(perspective.oy) ||
        perspective.wk <= 0.f || perspective.hk <= 0.f ||
        perspective.zn <= 0.f || perspective.zf <= perspective.zn)
    {
      debug("[KE] AVF_NATIVE_FAIL stage=projection_parameters");
      return false;
    }
    TMatrix4 checkedProjection;
    if (!d3d::calcproj(perspective, checkedProjection))
    {
      debug("[KE] AVF_NATIVE_FAIL stage=projection_calculation");
      return false;
    }
    for (int row=0; row<4; ++row)
      for (int col=0; col<4; ++col)
        if (!std::isfinite(checkedProjection[row][col]))
        {
          debug("[KE] AVF_NATIVE_FAIL stage=projection_nonfinite");
          return false;
        }
    if (checkedProjection[0][0] == 0.f || checkedProjection[1][1] == 0.f ||
        checkedProjection[2][2] == 0.f || checkedProjection[3][2] == 0.f)
    {
      debug("[KE] AVF_NATIVE_FAIL stage=projection_degenerate");
      return false;
    }""",
)
BUILD_FIXES["render"] += (PROJECTION_FIX,)


# Counter buffers are bound directly via d3d::set_rwbuffer; they never need
# a managed texture ID. UniqueBuf registers names, so four equal names alias
# the same registry entry and cause a fatal refcount assertion on close.
# BufPtr owns the actual Sbuffer without managed registration.
RESOURCE_FIXES = (
    ("    UniqueBuf counters;\n", "    BufPtr counters; // [KE] AVF_UNMANAGED_COUNTERS_V6\n"),
    ("""    if (!q.counters)
      q.counters = dag::buffers::create_ua_structured_readback(sizeof(uint32_t), 4, "ke_avf_counts");""",
     """    if (!q.counters)
    {
      static const char *const names[4] = {
        "ke_avf_counts_0", "ke_avf_counts_1", "ke_avf_counts_2", "ke_avf_counts_3"
      };
      q.counters = dag::buffers::create_ua_structured_readback(sizeof(uint32_t), 4, names[slot]);
    }"""),
    ("    auto *buf = q.counters.getBuf();", "    auto *buf = q.counters.get();"),
    ("lock_sbuffer<const uint32_t>(q.counters.getBuf(),", "lock_sbuffer<const uint32_t>(q.counters.get(),"),
)
BUILD_FIXES["render"] += RESOURCE_FIXES




def reverse_build_fixes(src, key):
    # An older checkout may lack later chained edits. Undo only present
    # successors, then validate the original GPU source anchors.
    for old, new in reversed(BUILD_FIXES[key]):
        count = src.count(new)
        if count > 1:
            raise ValueError(f"Ambiguous AVF GPU build fix: {key}")
        if count == 1:
            src = src.replace(new, old, 1)
    return src


def apply_build_fixes(src, key):
    src = reverse_build_fixes(src, key)
    for old, new in BUILD_FIXES[key]:
        if src.count(new) == 1:
            continue
        if src.count(old) != 1 or src.count(new):
            raise ValueError(f"AVF GPU build fix anchor missing: {key} {old[:80]!r}")
        src = src.replace(old, new, 1)
    return src


def normalize_gpu(src, key):
    from tools.ke_dagor_fixed_depth import normalize_fixed_renderer
    src = normalize_fixed_renderer(src,key)
    if MARKER not in src and ("collectKolobanovEyeProbe" not in src if key == "header" else True):
        return src
    src = reverse_build_fixes(src, key)
    for old, new in reversed(EDITS[key]):
        if src.count(new) != 1:
            raise ValueError(f"Partial AVF GPU queue {key}")
        src = src.replace(new, old, 1)
    return src

def install_avf_gpu(dagor: Path):
    paths = {"header": dagor/BASE/"include/de3_dynRenderService.h",
             "render": dagor/BASE/"services/dynRenderSrv/dynRender.cpp"}
    raw = {k: p.read_bytes() for k,p in paths.items()}
    src = {k: v.replace(b"\r\n",b"\n").decode("utf-8") for k,v in raw.items()}
    from tools.ke_dagor_fixed_depth import normalize_fixed_renderer, add_fixed_renderer
    original = dict(src)
    src = {k: normalize_fixed_renderer(v,k) for k,v in src.items()}
    fixed = {k: original[k] != src[k] for k in paths}
    if MARKER in src["render"]:
        for k in paths:
            normalize_gpu(src[k], k)
    else:
        for k, edits in EDITS.items():
            for old, new in edits:
                if old == new or src[k].count(old) != 1:
                    raise ValueError(f"AVF GPU queue anchor missing {k}: {old[:80]!r}")
                src[k] = src[k].replace(old,new,1)
    for k in paths:
        src[k] = apply_build_fixes(src[k], k)
    for k in paths:
        if fixed[k]:
            src[k] = add_fixed_renderer(src[k],k)
    changed = False
    for k,p in paths.items():
        updated = src[k].encode("utf-8").replace(b"\n",b"\r\n" if b"\r\n" in raw[k] else b"\n")
        if updated != raw[k]:
            p.write_bytes(updated)
            changed = True
    if changed:
        print("[KE] AVF_GPU_BUILD_FIX public collector, unnamed default slot", flush=True)
    return changed
