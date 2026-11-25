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


## 2.8.b–c — native implementation (code written, Windows acceptance pending)

The pinned Dagor opaque deferred pass uses renderGeomOpaque(), while
AcesScene::beforeRender prepares clipmaps, impostors and scene visibility.
The new renderer probe keeps that preparation, reuses a small separate
DeferredRenderTarget, draws scene plus model and then model alone, compares
the D32 reverse-Z buffers, and skips the lighting resolve, cascaded-shadow
rendering, atmosphere, postfx and image writes. This is an **opaque G-buffer
probe**, not a pure depth-only shader pass or asynchronous GPU reduction.
Two small buffers are currently read synchronously on the CPU.

avf_probe_size = 0 (default, baseline) / 64 / 128 / 256, specifying the
long side of the probe; the aspect and output projection are preserved.
The probe is experimental; requires smart placement, online strict quotas,
search_occlusion and mask capture, without render_debug. GUI exposes the
same option. The live camera bank has up to 64 candidates, 1–3 rings
with stratified distance and requested height, a seed-dependent phase, and
far-apart angles first. The native loop covers broad directions, then
prioritizes untested candidates between valid angular neighbours bracketing
the target, with periodic exploration. It may stop when a valid estimate
lies within 0.04 of the desired bin center. It preserves original camera
indices and the existing terrain/projection checks.

The native probe can be invalid (streaming, absent LOD, undersampled
silhouette, missing depth). Only a valid probe outranks the old collision
estimate. If all probes are invalid, the existing collision-ranked camera
is explicitly labeled collision_fallback; its GPU fraction is unknown,
not 0. Full-size masks remain authoritative. This is NOT evidence of
acceleration or a correct alternative final annotation.

Metadata records attempts, valid probes, selected method, counts, fraction
and error against full-size masks; AVF_PROBE logs also contain duration and
actual candidate index. Summary separates valid-probe accuracy from fallback
frequency. The adaptive online quota scheduler can retain a useful map
for up to 16 attempts (strong preference ends earlier after six consecutive
no-gain candidates); existing capacity and budget constraints remain.
Journal cursor avoids repeated reads, and host timing remains enabled.

Known limitations: native compilation and real map coverage unverified;
scene preparation still has costs, small full-frame buffers can undersample
distant models, renderer-global state restoration requires Windows validation.
ROI cropping, asynchronous GPU reduction, speculative multi-frame feedback
and full map-lifetime reuse across task boundaries are not implemented.
The initial bank is finite, so adaptive ordering does not guarantee finding
all narrow occlusion regions. Current GUI option is deliberately off by default.
No new test run has been requested during this implementation stage.

Final acceptance: build once on Windows and compare matched configurations
with size=0, then AVF enabled. Review per-bin actual hits, invalid/fallback
rates, probe mean absolute error, p50/p95 probe latency, map loads, host
turnaround, total seconds per accepted image, exact quotas and final masks.
Do not merge until the real tests succeed.
