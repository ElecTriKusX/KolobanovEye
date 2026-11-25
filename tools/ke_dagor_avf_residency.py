"""Reuse only the consecutive, successfully loaded map within a native batch."""
from pathlib import Path

MARKER = "// [KE] AVF_MAP_RESIDENCY_V1"
OLD_LOAD = '''  if (!plugin->loadKolobanovEyeLevel(level))
    return fail("MAP", "Binary dump did not produce a terrain renderer");
'''
NEW_LOAD = r'''  const bool keResidentEnabled = keResidentBatch && config.getInt("avf_probe_size", 0)>0 &&
    config.getBool("avf_reuse_map", true);
  const bool keMapReused = keResidentEnabled && keResidentBatch->level == level &&
    keResidentBatch->plugin == plugin && scene.hasKolobanovEyeTerrain();
  if (!keMapReused)
  {
    if (keResidentBatch)
      keResidentBatch->level.clear();
    if (!plugin->loadKolobanovEyeLevel(level))
      return fail("MAP", "Binary dump did not produce a terrain renderer");
    if (keResidentEnabled)
    {
      keResidentBatch->level = level;
      keResidentBatch->plugin = plugin;
    }
  }
  console->addMessage(ILogWriter::NOTE, "[KE] MAP_RESIDENCY action=%s", keMapReused ? "reuse" : "load");
'''
METHOD = "bool DagorEdAppWindow::runKolobanovEyeFrame(const char *config_path)"
HELPER = r'''// [KE] AVF_MAP_RESIDENCY_V1
namespace
{
struct KeResidentBatch
{
  std::string level;
  BinSceneViewPlugin *plugin = nullptr;
};
KeResidentBatch *keResidentBatch = nullptr;

struct KeResidentScope
{
  KeResidentBatch state;
  KeResidentBatch *previous = keResidentBatch;
  KeResidentScope() { keResidentBatch = &state; }
  ~KeResidentScope() { keResidentBatch = previous; }
};
}

'''
BATCH = "bool DagorEdAppWindow::runKolobanovEyeBatch(const char *config_path)\n{\n"
EDITS = (
    ("#include <cstring>\n", "#include <cstring>\n#include <string>\n"),
    (METHOD, HELPER + METHOD),
    (OLD_LOAD, NEW_LOAD),
    (BATCH, BATCH + "  KeResidentScope keResidentScope;\n"),
)

def install_avf_residency(source: Path):
    raw = source.read_bytes()
    src = raw.replace(b"\r\n", b"\n").decode("utf-8")
    if MARKER in src:
        if not all(src.count(new) == 1 for _, new in EDITS):
            raise ValueError("Partial AVF map residency")
        return False
    for old, new in EDITS:
        if src.count(old) != 1:
            raise ValueError(f"AVF map residency anchor missing: {old[:80]!r}")
        src = src.replace(old, new, 1)
    source.write_bytes(src.encode("utf-8").replace(b"\n", b"\r\n" if b"\r\n" in raw else b"\n"))
    return True
