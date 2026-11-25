# 2.8 — Adaptive Visibility Field (experimental)
 
Base: feature/smart-placement. This iteration preserves the existing opaque
depth masks and strict global/map quotas. Nothing in the experimental search
is used to grant quota credit.
 
## 2.8.a: reference search and online timing
 
- tools/ke_avf.py: bounded, deterministic angular rings, adaptive interval
  subdivision with cyclic seam handling, batch evaluator contract and explicit
  invalid probe results. This is a CPU orchestration reference with a synthetic
  callback, not a native Dagor GPU probe. The live camera selector is unchanged.
- tests/test_avf.py: synthetic coverage of refinement, budget, invalid
  samples, boundary semantics and deterministic results.
- online-timing.jsonl: per-candidate host monotonic timing for waiting for
  native acknowledgement, rejection checks, validation + quota accounting,
  bookkeeping, scheduling and publishing the next decision. The wait includes
  journal reading and polling; on the last frame of a task the publish time is
  only the local decision stage, not next-task construction. No change to
  acceptance or the native protocol.
 
## Windows experiment
 
Run the same online + strict + smart-placement configuration, seed,
resolution and maps as the previous run. Use the normal existing entrypoint.
Collect online-timing.jsonl, online-progress.jsonl, manifest.json,
visibility-quotas.json (if present), and native debug. Do not publish game
assets, captured frames or local absolute paths. Compare median/p95 host
turnaround, validation, task reloads, native render time, selected/candidate
ratio and total seconds per selected frame. Do not claim a speedup from timing
instrumentation alone.
 
## 2.8.b: pending native implementation
 
Inspect existing isolated-depth implementation and identify a supported
depth-only scene pass using the same camera, model pose, projection and
opaque/alpha-tested geometry. Prototype low resolution / cropped projection
(64/128/256), compare to full-resolution masks on identical views and
record probe latency and error. Do not assume a small render target removes
draw-call/streaming costs. Preserve invalid/unavailable states. Evaluate GPU
count/readback without blocking per camera. Only after validation connect
the adaptive search to the actual native camera loop, preserving camera
constraints and truthful camera metadata. If unsupported, report the exact
renderer constraint rather than falling back silently to the old proxy.
 
Then evaluate map residency and bounded queue scheduling separately. Existing
quotas remain authoritative; avoid repeated map reloads without sacrificing
feasible exact quota completion. Windows build and game asset validation
are not available in the chat environment.
