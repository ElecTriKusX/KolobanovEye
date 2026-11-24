"""Install bounded, file-based feedback into the existing native batch command.

Only a fresh per-run directory is used. Python publishes immutable commands
by atomic rename. A frame is acknowledged only after its journal and files
are closed. No new renderer API, GPU synchronization or mask logic is added.
"""
from pathlib import Path

MARKER = "// [KE] ONLINE_FEEDBACK_V1"
OLD_TARGET = "  const int keOcclusionTarget = config.getInt(keOcclusionKey, 0);"
NEW_TARGET = "  const int keOcclusionTarget = *keOnlineDir ? keOnlineTarget : config.getInt(keOcclusionKey, 0);"
HELPERS = r'''// [KE] ONLINE_FEEDBACK_V1
namespace
{
bool keOnlinePath(char *out, size_t size, const char *dir, const char *prefix, int index, const char *suffix)
{
  const int n = snprintf(out, size, "%s/%s%d%s", dir, prefix, index, suffix);
  return n > 0 && size_t(n) < size;
}

bool keOnlineWaitFile(const char *path)
{
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(300);
  while (std::chrono::steady_clock::now() < deadline)
  {
    if (FILE *file = fopen(path, "rb"))
    {
      fclose(file);
      return true;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
  }
  return false;
}

bool keOnlineDecision(const char *dir, int index, int &decision)
{
  char path[4096];
  if (!keOnlinePath(path, sizeof(path), dir, "decision_", index, ".txt") || !keOnlineWaitFile(path))
    return false;
  FILE *file = fopen(path, "rb");
  if (!file)
    return false;
  char extra;
  const bool valid = fscanf(file, "%d %c", &decision, &extra) == 1 && decision >= -1 && decision <= 3;
  fclose(file);
  return valid;
}

bool keOnlineDone(const char *dir, const char *prefix, int index)
{
  char path[4096];
  if (!keOnlinePath(path, sizeof(path), dir, prefix, index, ".txt"))
    return false;
  char temporary[4096];
  if (!keOnlinePath(temporary, sizeof(temporary), dir, prefix, index, ".tmp"))
    return false;
  FILE *file = fopen(temporary, "wb");
  if (!file)
    return false;
  const bool written = fputs("ok\n", file) >= 0;
  const bool closed = fclose(file) == 0;
  return written && closed && rename(temporary, path) == 0;
}
}

'''
BEFORE_FRAME = r'''  const char *keOnlineDir = config.getStr("online_feedback_dir", "");
  int keOnlineTarget = 0;
  if (*keOnlineDir && (!keOnlineDecision(keOnlineDir, frameIndex, keOnlineTarget) || keOnlineTarget < 0))
    return fail("ONLINE_FEEDBACK", "Missing or invalid frame decision");
'''
AFTER_FRAME = r'''  if (*keOnlineDir && !keOnlineDone(keOnlineDir, "ready_", frameIndex))
    return fail("ONLINE_FEEDBACK", "Could not acknowledge written frame");
  if (*keOnlineDir && frameIndex + 1 < frameCount)
  {
    int nextDecision = 0;
    if (!keOnlineDecision(keOnlineDir, frameIndex + 1, nextDecision))
      return fail("ONLINE_FEEDBACK", "Timed out or invalid next decision");
    if (nextDecision < 0)
    {
      console->addMessage(ILogWriter::NOTE, "[KE] ONLINE_SEQUENCE_STOP frames=%d", frameIndex + 1);
      console->addMessage(ILogWriter::NOTE, "[KE] SEQUENCE_OK frames=%d", frameIndex + 1);
      return true;
    }
  }
'''
BATCH = r'''  const char *onlineDir = config.getStr("online_dir", "");
  if (*onlineDir)
  {
    console->addMessage(ILogWriter::NOTE, "[KE] ONLINE_BATCH_BEGIN");
    for (int index = 1; index <= 5000000; ++index)
    {
      char path[4096];
      DataBlock task;
      if (!keOnlinePath(path, sizeof(path), onlineDir, "task_", index, ".blk") ||
          !keOnlineWaitFile(path) || !task.load(path))
      {
        console->addMessage(ILogWriter::ERROR, "[KE] FAIL ONLINE_FEEDBACK: Invalid or missing task %d", index);
        return false;
      }
      if (task.getBool("stop", false))
      {
        console->addMessage(ILogWriter::NOTE, "[KE] MAP_BATCH_OK maps=%d", index - 1);
        return true;
      }
      const char *capture = task.getStr("capture", "");
      if (!*capture || !dd_file_exist(capture))
      {
        console->addMessage(ILogWriter::ERROR, "[KE] FAIL ONLINE_FEEDBACK: Missing capture %d", index);
        return false;
      }
      console->addMessage(ILogWriter::NOTE, "[KE] MAP_TASK_BEGIN index=%d/0 capture=%s", index, capture);
      if (!runKolobanovEyeFrame(capture))
      {
        console->addMessage(ILogWriter::ERROR, "[KE] FAIL MAP_TASK index=%d/0", index);
        return false;
      }
      console->addMessage(ILogWriter::NOTE, "[KE] MAP_TASK_OK index=%d/0", index);
      if (!keOnlineDone(onlineDir, "done_", index))
      {
        console->addMessage(ILogWriter::ERROR, "[KE] FAIL ONLINE_FEEDBACK: Could not acknowledge task %d", index);
        return false;
      }
    }
    console->addMessage(ILogWriter::ERROR, "[KE] FAIL ONLINE_FEEDBACK: Task limit exceeded");
    return false;
  }
'''
FRAME_OK = '  console->addMessage(ILogWriter::NOTE, "[KE] FRAME_OK %dx%d %s", width, height, frameOutput);\n'
FRAME_METHOD = "bool DagorEdAppWindow::runKolobanovEyeFrame(const char *config_path)"
EDITS = (
    ("#include <chrono>\n", "#include <chrono>\n#include <thread>\n"),
    (FRAME_METHOD, HELPERS + FRAME_METHOD),
    ("  // [KE] OCCLUSION_SEARCH_V1\n", BEFORE_FRAME + "  // [KE] OCCLUSION_SEARCH_V1\n"),
    (OLD_TARGET, NEW_TARGET),
    (FRAME_OK, FRAME_OK + AFTER_FRAME),
    ('  const int mapCount = config.getInt("map_count", 0);\n',
     BATCH + '  const int mapCount = config.getInt("map_count", 0);\n'),
)


def install_online_feedback(source: Path) -> bool:
    raw = source.read_bytes()
    text = raw.replace(b"\r\n", b"\n").decode("utf-8")
    if MARKER in text:
        if not all(text.count(new) == 1 for _, new in EDITS):
            raise ValueError(f"Partial online feedback integration in {source}")
        return False
    for old, new in EDITS:
        if text.count(old) != 1:
            raise ValueError(f"Online feedback anchor missing or ambiguous: {old[:100]!r}")
        text = text.replace(old, new, 1)
    source.write_bytes(text.encode("utf-8").replace(b"\n", b"\r\n" if b"\r\n" in raw else b"\n"))
    print("[KE] Installed single-editor online feedback protocol", flush=True)
    return True
