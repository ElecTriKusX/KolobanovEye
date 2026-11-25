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
      q.counters = dag::create_ua_structured_readback(sizeof(uint32_t), 4, "ke_avf_counts");
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

def normalize_gpu(src, key):
    if MARKER not in src and ("collectKolobanovEyeProbe" not in src if key == "header" else True):
        return src
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
    if MARKER in src["render"]:
        for k in paths:
            normalize_gpu(src[k], k)
        return False
    for k, edits in EDITS.items():
        for old, new in edits:
            if old == new or src[k].count(old) != 1:
                raise ValueError(f"AVF GPU queue anchor missing {k}: {old[:80]!r}")
            src[k] = src[k].replace(old,new,1)
    for k,p in paths.items():
        p.write_bytes(src[k].encode("utf-8").replace(b"\n",b"\r\n" if b"\r\n" in raw[k] else b"\n"))
    return True
