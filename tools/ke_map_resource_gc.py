"""Release unused game resources between KolobanovEye map loads.

Use the native refcount-aware factory API, not a global reset or relaxed
vertex-buffer limits. Apply after the existing level-settings isolation patch.
"""
from pathlib import Path

MARKER = b"// [KE] MAP_RESOURCE_GC_V1"
INCLUDE = b"#include <shaders/dag_shaderResUnitedData.h>"
RESOURCE_API = b"#include <gameRes/dag_gameResources.h>"
ANCHOR = b"  levelFileName = bindump;\n  clear();\n  levelSettingsBlk.clearData();"
GC = b"""
  // [KE] MAP_RESOURCE_GC_V1
  // The editor keeps a process-wide RendInst resource cache. Unloading a BIN
  // drops scene references but does not reclaim all unused GameRes factories.
  // Sweep only unreferenced resources before the next map preloads its meshes.
  if (dgs_get_argv("kolobanoveye-probe"))
  {
    const int before = unitedvdata::riUnitedVdata.getResCount();
    free_unused_game_resources();
    const int after = unitedvdata::riUnitedVdata.getResCount();
    debug("[KE] MAP_RESOURCE_GC map=%s ri_before=%d ri_after=%d released=%d",
      bindump, before, after, before - after);
  }
"""


def install_map_resource_gc(source: Path) -> bool:
    raw = source.read_bytes()
    normalized = raw.replace(b"\r\n", b"\n")
    if MARKER in normalized:
        if (normalized.count(MARKER) != 1 or normalized.count(ANCHOR + GC) != 1
                or INCLUDE not in normalized or RESOURCE_API not in normalized):
            raise ValueError(f"Partial map resource GC in {source}")
        return False
    if normalized.count(ANCHOR) != 1:
        raise ValueError(f"Cannot locate unique post-clear level reload in {source}")
    include_anchor = b"#include <gameRes/dag_gameResSystem.h>"
    if normalized.count(include_anchor) != 1:
        raise ValueError(f"Cannot locate GameRes includes in {source}")
    additions = [item for item in (RESOURCE_API, INCLUDE) if item not in normalized]
    if additions:
        normalized = normalized.replace(include_anchor, include_anchor + b"\n" + b"\n".join(additions), 1)
    updated = normalized.replace(ANCHOR, ANCHOR + GC, 1)
    source.write_bytes(updated.replace(b"\n", b"\r\n") if b"\r\n" in raw else updated)
    print("[KE] Installed refcount-safe map resource cleanup before preload", flush=True)
    return True
