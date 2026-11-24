"""Bounds-safe geometry-only trace for missing game-side RendInst properties.

DaEditor can load a BIN with collision resources but without the gameplay
RendinstProperties array. The pinned traversal dereferences the array before
using the collision mesh. For tools builds, keep the geometry trace when no
material/destruction filter is requested; do not invent a gameplay material,
damage state or canopy. Other strategies cannot infer missing properties.
"""
from pathlib import Path

MARKER = "// [KE] RI_TRACE_PROPS_V1"
HELPER = r'''#if _TARGET_PC_TOOLS_BUILD
// [KE] RI_TRACE_PROPS_V1
template <typename Strategy>
static const rendinst::props::RendinstProperties *keTraceProps(const RendInstGenData &rgl, int pool, const Strategy &strategy)
{
  if (unsigned(pool) < rgl.rtData->riProperties.size())
    return &rgl.rtData->riProperties[pool];

  // Only the geometry-only TraceRayStrat used by the editor proxy may
  // proceed without the game-side properties. Keep real collision meshes.
  if constexpr (std::is_same_v<Strategy, TraceRayStrat>)
  {
    if (strategy.rayMatId == PHYSMAT_INVALID && (strategy.traceFlags & TraceFlag::Destructible))
    {
      static const rendinst::props::RendinstProperties neutral = [] {
        rendinst::props::RendinstProperties p{};
        p.matId = PHYSMAT_INVALID;
        return p;
      }();
      static const bool reported = [] {
        debug("[KE] RI_TRACE_PROPS_FALLBACK missing gameplay properties; geometry-only tool trace");
        return true;
      }();
      (void)reported;
      return &neutral;
    }
  }
  return nullptr;
}
#endif

'''
CELL_OLD = '''        const rendinst::props::RendinstProperties &riProp = rgl.rtData->riProperties[p];
        if (strategy.shouldIgnoreRendinst(isPosInst, riProp.immortal, riProp.damageable, riProp.matId))
          continue;
'''
CELL_NEW = '''#if _TARGET_PC_TOOLS_BUILD
        const auto *keProps = keTraceProps(rgl, p, strategy);
        if (!keProps)
          continue;
        const auto &riProp = *keProps;
#else
        const rendinst::props::RendinstProperties &riProp = rgl.rtData->riProperties[p];
#endif
        if (strategy.shouldIgnoreRendinst(isPosInst, riProp.immortal, riProp.damageable, riProp.matId))
          continue;
'''
EXTRA_OLD = '''          const rendinst::props::RendinstProperties &riProp = rgl->rtData->riProperties[poolRef];
          ignoreRiType = strategy.shouldIgnoreRendinst(/*isPos*/ false, riProp.immortal, riProp.damageable, riProp.matId);
'''
EXTRA_NEW = '''#if _TARGET_PC_TOOLS_BUILD
          const auto *keProps = keTraceProps(*rgl, poolRef, strategy);
          ignoreRiType = !keProps ||
            strategy.shouldIgnoreRendinst(/*isPos*/ false, keProps->immortal, keProps->damageable, keProps->matId);
#else
          const rendinst::props::RendinstProperties &riProp = rgl->rtData->riProperties[poolRef];
          ignoreRiType = strategy.shouldIgnoreRendinst(/*isPos*/ false, riProp.immortal, riProp.damageable, riProp.matId);
#endif
'''
CACHED_OLD = '''      const rendinst::props::RendinstProperties &riProp = rgl->rtData->riProperties[pool->riPoolRef];
      if (strategy.shouldIgnoreRendinst(/*isPos*/ false, riProp.immortal, riProp.damageable, riProp.matId))
        return false;
'''
CACHED_NEW = '''#if _TARGET_PC_TOOLS_BUILD
      const auto *keProps = keTraceProps(*rgl, pool->riPoolRef, strategy);
      if (!keProps ||
          strategy.shouldIgnoreRendinst(/*isPos*/ false, keProps->immortal, keProps->damageable, keProps->matId))
        return false;
#else
      const rendinst::props::RendinstProperties &riProp = rgl->rtData->riProperties[pool->riPoolRef];
      if (strategy.shouldIgnoreRendinst(/*isPos*/ false, riProp.immortal, riProp.damageable, riProp.matId))
        return false;
#endif
'''
ANCHOR = "// Closest-hit trace that also records the hit RendInstDesc per ray into a caller span.\n"
EDITS = (
    ("#include <rendInst/traceUtils.h>\n", "#include <rendInst/traceUtils.h>\n#include <type_traits>\n"),
    (ANCHOR, HELPER + ANCHOR),
    (CELL_OLD, CELL_NEW),
    (EXTRA_OLD, EXTRA_NEW),
    (CACHED_OLD, CACHED_NEW),
)


def install_ri_trace_props(source: Path) -> bool:
    raw = source.read_bytes()
    text = raw.replace(b"\r\n", b"\n").decode("utf-8")
    if MARKER in text:
        if text.count(MARKER) != 1 or not all(text.count(new) == 1 for _, new in EDITS):
            raise ValueError(f"Partial RendInst trace-properties repair in {source}; do not reset Dagor")
        return False
    for old, new in EDITS:
        if text.count(old) != 1:
            raise ValueError(f"RendInst trace-properties anchor missing or ambiguous: {old[:100]!r}")
        text = text.replace(old, new, 1)
    source.write_bytes(text.encode("utf-8").replace(b"\n", b"\r\n" if b"\r\n" in raw else b"\n"))
    print("[KE] Installed tools-only missing RendInst trace-properties guard", flush=True)
    return True
