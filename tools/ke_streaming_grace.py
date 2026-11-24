"""Bounded texture-streaming grace period for the native warm-up loop.

Preserve all readiness checks and the original 10/20 s stability window.
Only extend while the texture queue/worker is still active, up to 120 s.
The previous haze patch's added blocks remain intact for rebuild detection.
"""
from pathlib import Path

MARKER = "// [KE] STREAMING_GRACE_V1"
DEADLINE = "  const auto deadline = warmupStart + std::chrono::seconds(waitFrameStability ? 20 : 10);\n"
STATE = """  // [KE] STREAMING_GRACE_V1
  const auto keHardDeadline = warmupStart + std::chrono::seconds(120);
  auto keEffectiveDeadline = deadline;
  auto keNextProgress = warmupStart + std::chrono::seconds(5);
  const auto keRefreshStreamingDeadline = [&]() {
    const auto now = Clock::now();
    if (!texturesIdle())
    {
      const auto next = now + std::chrono::seconds(waitFrameStability ? 20 : 10);
      keEffectiveDeadline = next < keHardDeadline ? next : keHardDeadline;
    }
    if (now >= keNextProgress)
    {
      console->addMessage(ILogWriter::NOTE,
        "[KE] STREAMING_PROGRESS index=%d elapsed_ms=%d pending=%d worker_busy=%d limit_ms=%d",
        frameIndex, int(std::chrono::duration_cast<std::chrono::milliseconds>(now - warmupStart).count()),
        get_managed_textures_streaming_pending_count(), int(textureWorkerBusy()),
        int(std::chrono::duration_cast<std::chrono::milliseconds>(keEffectiveDeadline - warmupStart).count()));
      keNextProgress = now + std::chrono::seconds(5);
    }
  };
"""
EDITS = (
    (DEADLINE, DEADLINE + STATE),
    ("  while (Clock::now() < deadline)\n", "  while (Clock::now() < keEffectiveDeadline)\n"),
    ("    pumpStreaming();\n\n    mapLoading", "    pumpStreaming();\n    keRefreshStreamingDeadline();\n\n    mapLoading"),
    ("    while (sceneReady && !texturesIdle() && Clock::now() < deadline)\n",
     "    while (sceneReady && !texturesIdle() && Clock::now() < keEffectiveDeadline)\n"),
    ("      pumpStreaming();\n    }\n    if (!sceneReady)", "      pumpStreaming();\n      keRefreshStreamingDeadline();\n    }\n    if (!sceneReady)"),
    ('  if (!ready)\n    return fail("STREAMING", "Scene, textures or sky did not settle within 10 seconds of warm-up");',
     '''  if (!ready)
  {
    char reason[256];
    snprintf(reason, sizeof(reason),
      "Warm-up timed out after %d ms (pending=%d worker_busy=%d map_loading=%d sky_ready=%d stable_checks=%d; hard cap 120 s)",
      elapsedMs, pendingAfter, int(textureWorkerBusy()), int(mapLoading), int(skyReady), keStableChecks);
    return fail("STREAMING", reason);
  }'''),
)


def install_streaming_grace(source: Path) -> bool:
    raw = source.read_bytes()
    text = raw.replace(b"\r\n", b"\n").decode("utf-8")
    if MARKER in text:
        if not all(text.count(new) == 1 for _, new in EDITS):
            raise ValueError(f"Partial streaming grace installation in {source}")
        return False
    for old, new in EDITS:
        if text.count(old) != 1:
            raise ValueError(f"Cannot identify streaming grace anchor in {source}: {old[:100]!r}")
        text = text.replace(old, new, 1)
    source.write_bytes(text.encode("utf-8").replace(b"\n", b"\r\n" if b"\r\n" in raw else b"\n"))
    print("[KE] Installed bounded texture-streaming grace (120 s hard cap)", flush=True)
    return True
