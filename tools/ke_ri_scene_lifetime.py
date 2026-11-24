"""Keep AcesScene terrain alive until pending RendInst generation is drained."""
from pathlib import Path

MARKER = "// [KE] RI_SCENE_LIFETIME_V1"
DESTRUCTOR_OLD = """AcesScene::~AcesScene()
{
  self = NULL;
  clear();
"""
DESTRUCTOR_NEW = """AcesScene::~AcesScene()
{
  // The RI height callback still needs self until clear() drains its jobs.
  clear();
  self = NULL;
"""
CLEAR_OLD = """void AcesScene::clear()
{
"""
CLEAR_NEW = CLEAR_OLD + """  // [KE] RI_SCENE_LIFETIME_V1
  // The main thread owns scene updates/scheduling. Do not pump new frame
  // updates while draining the existing RI jobs. Their height callbacks
  // read lmeshMgr and self, so wait BEFORE deleting either.
  if (!rendinst::isRIGenPrepareFinished())
  {
    debug("[KE] RI_SCENE_DRAIN_BEGIN");
    while (!rendinst::isRIGenPrepareFinished())
      sleep_msec(10);
    debug("[KE] RI_SCENE_DRAIN_OK");
  }
"""


def upgrade_scene(text):
    if MARKER in text:
        if text.count(CLEAR_NEW) != 1 or text.count(DESTRUCTOR_NEW) != 1:
            raise ValueError("Partial RI scene lifetime patch")
        return text
    for old, new in ((DESTRUCTOR_OLD, DESTRUCTOR_NEW), (CLEAR_OLD, CLEAR_NEW)):
        if text.count(old) != 1:
            raise ValueError("RI scene lifetime patch anchor missing or ambiguous")
        text = text.replace(old, new, 1)
    return text


def install_scene_lifetime(source: Path):
    raw = source.read_bytes()
    text = raw.replace(b"\r\n", b"\n").decode("utf-8")
    changed = upgrade_scene(text)
    if changed == text:
        return False
    source.write_bytes(changed.encode("utf-8").replace(b"\n", b"\r\n" if b"\r\n" in raw else b"\n"))
    print("[KE] Installed RI scene lifetime drain", flush=True)
    return True
