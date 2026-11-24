"""Install an opt-in inventory of loaded game-scene RendInst objects.

No changes to gameplay or normal dataset capture. The geometry-only walk is
explicitly selected by the new diagnostic, because CDK secondary layers may
lack gameplay properties. The old collision paths retain their exact behavior.
"""
from pathlib import Path

BASE = Path("prog/tools/sceneTools/daEditorX")
MARKER = "// [KE] OBJECT_SCAN_V1"
WALK_MARKER = "// [KE] OBJECT_WALK_V1"
WALK_START = "static bool forEachRendinstInBounding("
WALK_END = "static CheckBoxRIResultFlags testObjToRendInstIntersectionNoCache("
SIGNATURE = "  bool unlock_before_cb) DAG_TS_NO_THREAD_SAFETY_ANALYSIS"
SIGNATURE_NEW = "  bool unlock_before_cb, bool keGeometryOnly = false) DAG_TS_NO_THREAD_SAFETY_ANALYSIS"
EMPTY = "if (!rgl->rtData->riProperties.empty()) // we cannot run on empty properties. In most cases it's just a second layer"
EMPTY_NEW = "if (keGeometryOnly || !rgl->rtData->riProperties.empty()) // geometry-only diagnostic does not require gameplay props"
PROPS = """                const rendinst::props::RendinstProperties &riProp = rgl->rtData->riProperties[p];
                isPosInst = (riPosInstData[p / (sizeof(riPosInstBit) * CHAR_BIT)] & riPosInstBit) != 0;
                if (should_ignore(isPosInst, riProp.immortal, riProp.damageable, riProp.matId))
                  goto nextPool;"""
PROPS_NEW = """                // [KE] OBJECT_WALK_V1
                const rendinst::props::RendinstProperties *riProp =
                  (!keGeometryOnly || unsigned(p) < rgl->rtData->riProperties.size()) ? &rgl->rtData->riProperties[p] : nullptr;
                isPosInst = (riPosInstData[p / (sizeof(riPosInstBit) * CHAR_BIT)] & riPosInstBit) != 0;
                if (!keGeometryOnly && should_ignore(isPosInst, riProp->immortal, riProp->damageable, riProp->matId))
                  goto nextPool;"""
WALK_EDITS = (
    (SIGNATURE, SIGNATURE_NEW), (EMPTY, EMPTY_NEW), (PROPS, PROPS_NEW),
    ("vFullBBox = collRes ? collRes->vFullBBox : rgl->rtData->riCollResBb[p];",
     "vFullBBox = keGeometryOnly ? rgl->rtData->riResBb[p] : (collRes ? collRes->vFullBBox : rgl->rtData->riCollResBb[p]);"),
    ("checkCanopy = (ri_types & GatherRiTypeFlag::RiGenCanopy) && riProp.canopyOpacity > 0.f;",
     "checkCanopy = !keGeometryOnly && (ri_types & GatherRiTypeFlag::RiGenCanopy) && riProp->canopyOpacity > 0.f;"),
    ("getRIGenCanopyBBox(riProp, rgl->rtData->riRes[p]->bbox, canopyBox);",
     "getRIGenCanopyBBox(*riProp, rgl->rtData->riRes[p]->bbox, canopyBox);"),
    ("if (collRes)\n                    {\n                      if (!objectBounding.testSphereIntersectionEst",
     "if (collRes && !keGeometryOnly)\n                    {\n                      if (!objectBounding.testSphereIntersectionEst"),
)
LIMIT_OLD = "G_ASSERTF(testedNum <= max_number_of_rendinst_collision_callbacks,"
LIMIT_NEW = "G_ASSERTF(keGeometryOnly || testedNum <= max_number_of_rendinst_collision_callbacks,"
WRAPPER_ANCHOR = "void foreachRIGenInBox(const BBox3 &box, GatherRiTypeFlags ri_types, ForeachCB &cb)"
WRAPPER = r'''// [KE] OBJECT_WALK_ENTRY_V1
void keForeachGeometryInBox(const BBox3 &box, ForeachCB &cb)
{
  ForeachRIGenStrat strat(cb);
  const auto ignore = [](bool, bool, bool, PhysMat::MatID) { return false; };
  const auto callback = [&strat](RendInstGenData *rgl, rendinst::RendInstDesc &desc, const mat44f &tm,
                          const BBox3 &bbox, const CollisionResource *res, bool pos) {
    if (pos)
      strat.executeForPos(rgl, desc, tm, bbox, res);
    else
      strat.executeForTm(rgl, desc, tm, bbox, res);
    return false;
  };
  FOR_EACH_RG_LAYER_DO (rgl)
    forEachRendinstInBounding(_layer, box, box, GatherRiTypeFlag::RiGenOnly, nullptr, ignore, callback, false, true);
}

'''
INCLUDES = r'''#include <rendInst/rendInstCollision.h>
#include <rendInst/rendInstGen.h>
#include <rendInst/rendInstAccess.h>
#include <rendInst/rendInstExtra.h>
#include <rendInst/rendInstExtraAccess.h>
namespace rendinst { void keForeachGeometryInBox(const BBox3 &, ForeachCB &); }
'''
# Keep the existing includes and bounds marker for previous patch installers.
INCLUDE_ANCHOR = "#include <thread>\n"
CAPTURE_ANCHOR = '  float ground = 0.f;\n  Point3 groundNormal;'
CAPTURE = r'''  // [KE] OBJECT_SCAN_V1
  if (const char *inventory = config.getStr("object_inventory", ""); *inventory)
  {
    if (!validTankBounds)
      return fail("OBJECT_SCAN", "Tank field bounds unavailable");
    const float side = fminf(tankMax.x - tankMin.x, tankMax.y - tankMin.y);
    const float xmin = (tankMin.x + tankMax.x - side) * 0.5f;
    const float zmin = (tankMin.y + tankMax.y - side) * 0.5f;
    const float xmax = xmin + side, zmax = zmin + side;
    const int tileCount = int(ceilf(side / 128.f));
    if (tileCount < 1 || tileCount > 128)
      return fail("OBJECT_SCAN", "Unsupported tank field size");
    FILE *file = fopen(inventory, "wb");
    if (!file)
      return fail("OBJECT_SCAN", "Cannot open object inventory");
    struct InventoryFile { FILE *f; ~InventoryFile() { if (f) fclose(f); } } guard{file};
    fprintf(file, "KEOBJECTS\t1\t%.9g\t%.9g\t%.9g\t%.9g\t%d\n", xmin, xmax, zmin, zmax, tileCount);
    struct Collector : rendinst::ForeachCB
    {
      FILE *file = nullptr;
      float x0 = 0, x1 = 0, z0 = 0, z1 = 0;
      bool lastX = false, lastZ = false;
      int count = 0, invalid = 0;
      bool failed = false;
      void emit(const char *kind, const rendinst::RendInstDesc &desc, const TMatrix &tm, bool collision)
      {
        const Point3 p = tm.getcol(3);
        if (!finite_point(p))
        {
          ++invalid;
          return;
        }
        // Ownership by root: no duplicate records at tile seams or after streaming.
        if (p.x < x0 || (lastX ? p.x > x1 : p.x >= x1) ||
            p.z < z0 || (lastZ ? p.z > z1 : p.z >= z1))
          return;
        const BBox3 local = rendinst::getRIGenFullBBox(desc);
        if (!finite_point(local[0]) || !finite_point(local[1]) ||
            local[0].x > local[1].x || local[0].y > local[1].y || local[0].z > local[1].z)
        {
          ++invalid;
          return;
        }
        Point3 lo, hi;
        for (int i = 0; i < 8; ++i)
        {
          const Point3 q = tm * Point3(local[(i & 1) != 0].x, local[(i & 2) != 0].y, local[(i & 4) != 0].z);
          if (!finite_point(q)) { ++invalid; return; }
          if (!i) lo = hi = q;
          else
          {
            lo.x = fminf(lo.x, q.x); lo.y = fminf(lo.y, q.y); lo.z = fminf(lo.z, q.z);
            hi.x = fmaxf(hi.x, q.x); hi.y = fmaxf(hi.y, q.y); hi.z = fmaxf(hi.z, q.z);
          }
        }
        if (count >= 1000000) { failed = true; return; }
        const char *name = rendinst::getRIGenResName(desc);
        if (fprintf(file, "O\t%s\t%d\t%d\t%.9g\t%.9g\t%.9g\t%.9g\t%.9g\t%.9g\t%.9g\t%.9g\t%.9g\t%d\t",
            kind, desc.layer, desc.pool, p.x, p.y, p.z, lo.x, lo.y, lo.z, hi.x, hi.y, hi.z, int(collision)) < 0)
          failed = true;
        // Hex encoding preserves resource names without TSV delimiter/locale issues.
        for (const unsigned char *c = reinterpret_cast<const unsigned char *>(name ? name : ""); *c; ++c)
          if (fprintf(file, "%02x", unsigned(*c)) < 0) failed = true;
        if (fputc('\n', file) == EOF) failed = true;
        ++count;
      }
      void executeForTm(RendInstGenData *, const rendinst::RendInstDesc &d, const TMatrix &tm) override
      { emit("ri_tm", d, tm, rendinst::getRIGenCollInfo(d) != nullptr); }
      void executeForPos(RendInstGenData *, const rendinst::RendInstDesc &d, const TMatrix &tm) override
      { emit("ri_pos", d, tm, rendinst::getRIGenCollInfo(d) != nullptr); }
    } collector;
    collector.file = file;
    console->addMessage(ILogWriter::NOTE, "[KE] OBJECT_SCAN_BEGIN tiles=%d side=%.3f", tileCount * tileCount, side);
    const BBox3 land = scene.lmeshMgr->getBBox();
    if (!finite_point(land[0]) || !finite_point(land[1]))
      return fail("OBJECT_SCAN", "Invalid landscape bounds");
    int tilesDone = 0;
    for (int tz = 0; tz < tileCount; ++tz)
      for (int tx = 0; tx < tileCount; ++tx)
      {
        collector.x0 = xmin + side * float(tx) / tileCount;
        collector.x1 = tx + 1 == tileCount ? xmax : xmin + side * float(tx + 1) / tileCount;
        collector.z0 = zmin + side * float(tz) / tileCount;
        collector.z1 = tz + 1 == tileCount ? zmax : zmin + side * float(tz + 1) / tileCount;
        collector.lastX = tx + 1 == tileCount;
        collector.lastZ = tz + 1 == tileCount;
        const float cx = (collector.x0 + collector.x1) * 0.5f, cz = (collector.z0 + collector.z1) * 0.5f;
        float h = 0.f;
        Point3 normal;
        if (!scene.getHeightmapAtPoint(cx, cz, h, &normal) || !std::isfinite(h)) h = 0.f;
        const Point3 poi(cx, h + 5.f, cz);
        scene.update(poi, 1.f / 60.f);
        perform_delayed_actions();
        rendinst::scheduleRIGenPrepare(make_span_const(&poi, 1));
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(30);
        while (!rendinst::isRIGenPrepareFinished() || (scene.getSsm() && scene.getSsm()->isLoading()))
        {
          if (std::chrono::steady_clock::now() >= deadline)
            return fail("OBJECT_SCAN", "RendInst tile preparation timed out");
          cpujobs::release_done_jobs();
          perform_delayed_actions();
          sleep_msec(5);
        }
        cpujobs::release_done_jobs();
        perform_delayed_actions();
        const int before = collector.count;
        const BBox3 query(Point3(collector.x0, land[0].y - 1000.f, collector.z0),
                          Point3(collector.x1, land[1].y + 1000.f, collector.z1));
        rendinst::keForeachGeometryInBox(query, collector);
        // Extra pool query includes valid non-collidable instances too. Only
        // query the current loaded snapshot, not a camera visibility list.
        Tab<rendinst::riex_handle_t> handles;
        for (int pool = 0, pools = rendinst::getRiGenExtraResCount(); pool < pools; ++pool)
          if (rendinst::isRiGenExtraResIdValid(pool))
          {
            rendinst::getRiGenExtraInstances(handles, pool, v_ldu_bbox3(query));
            for (const auto handle : handles)
            {
              const rendinst::RendInstDesc desc(handle);
              CollisionResource *collision = nullptr;
              rendinst::getRIExtraCollInfo(handle, &collision, nullptr);
              collector.emit("ri_extra", desc, rendinst::getRIGenMatrix(desc), collision != nullptr);
            }
          }
        if (collector.failed || fprintf(file, "T\t%d\t%d\t%d\t%d\n", tx, tz, collector.count - before,
              rendinst::getRIGenLoadedInstancesCount()) < 0 || fflush(file) != 0)
          return fail("OBJECT_SCAN", "Object inventory exceeded limit or write failed");
        ++tilesDone;
        if (tilesDone % 16 == 0 || tilesDone == tileCount * tileCount)
          console->addMessage(ILogWriter::NOTE, "[KE] OBJECT_SCAN_PROGRESS tiles=%d/%d objects=%d",
            tilesDone, tileCount * tileCount, collector.count);
      }
    if (fprintf(file, "END\t%d\t%d\t%d\n", tilesDone, collector.count, collector.invalid) < 0 || fflush(file) != 0)
      return fail("OBJECT_SCAN", "Cannot finish inventory");
    const int closed = fclose(file);
    guard.f = nullptr;
    if (closed != 0) return fail("OBJECT_SCAN", "Cannot close inventory");
    // Existing terrain overview only; no tank, no RGB frame, no mask capture.
    scene.prepareFixedClip(1024);
    console->addMessage(ILogWriter::NOTE, "[KE] OBJECT_SCAN_OK tiles=%d objects=%d invalid=%d",
      tilesDone, collector.count, collector.invalid);
    return true;
  }
'''


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"Object scan patch anchor missing or ambiguous: {old[:90]!r}")
    return text.replace(old, new, 1)


def upgrade_walk(src):
    start = src.index(WALK_START)
    end = src.index(WALK_END, start)
    body = src[start:end]
    if WALK_MARKER in body:
        if (not all(body.count(new) == 1 for _, new in WALK_EDITS)
                or body.count(LIMIT_NEW) != 2 or src.count(WRAPPER) != 1):
            raise ValueError("Partial geometry walk installation")
        return src
    for old, new in WALK_EDITS:
        body = once(body, old, new)
    if body.count(LIMIT_OLD) != 2:
        raise ValueError("Unexpected geometry walk callback limits")
    body = body.replace(LIMIT_OLD, LIMIT_NEW)
    src = src[:start] + body + src[end:]
    return once(src, WRAPPER_ANCHOR, WRAPPER + WRAPPER_ANCHOR)


def upgrade_capture(src):
    if MARKER in src:
        if src.count(CAPTURE) != 1 or src.count(INCLUDES) != 1:
            raise ValueError("Partial object scan capture installation")
        return src
    src = once(src, INCLUDE_ANCHOR, INCLUDE_ANCHOR + INCLUDES)
    return once(src, CAPTURE_ANCHOR, CAPTURE + CAPTURE_ANCHOR)


def install_object_scan(dagor: Path):
    paths = [dagor / "prog/gameLibs/rendInst/rendInstGenCollision.cpp", dagor / BASE / "kolobanovEye.cpp"]
    raw = [p.read_bytes() for p in paths]
    original = [v.replace(b"\r\n", b"\n").decode("utf-8") for v in raw]
    changed = [upgrade_walk(original[0]), upgrade_capture(original[1])]
    for path, old, new, data in zip(paths, original, changed, raw):
        if old != new:
            path.write_bytes(new.encode("utf-8").replace(b"\n", b"\r\n" if b"\r\n" in data else b"\n"))
    print("[KE] Object scan diagnostic installed", flush=True)
