# DX11 impostor shadow atlas assertion — 19 November 2026

## Evidence

Successful Windows runs: 1 map × 6 frames and 2 maps × 10 frames in ONE DaEditor process; both produced dataset/raw successfully.

20-map run run-20261119T170525Z-d7dece13 failed on map 4/20 (avg_alaska_town.bin). The uploaded .logs(6).zip is from this run only. Its fatalerr gives a complete native C++ stack.

Maps 1–3 successfully completed 10 frames each: avg_abandoned_factory, avg_abandoned_town, avg_africa_desert. First terrain/camera placement on avg_alaska_town succeeded, but frame 31 was never completed.

Fatal error in validateUpdateSubRegion.h:22:

    Invalid update_sub_region dst: subres=7 -> mip=0 slice=1 >= tex.slices=1, tree_fagus_hurtgen_pl_l_a_s

Call chain: BcCompressor::copyToMip (bcCompressor.cpp:365) -> ImpostorTextureManager::update_shadow (impostorTextureMgr.cpp:722–728) -> updateImpostorsPreshadow -> AcesScene::beforeRender (strmLevel.cpp:823) -> KolobanovEye renderInTex.

The tree shadow texture has 7 mip levels, 1 array slice, but palette_id=1 is used to form destination subresource 7. The DX11 validator correctly rejects this out-of-range update. Do not modify the validator or redirect index 7 to index 0.

Native debug also reports discrepancies between exported map rotation palette size (1) and asset palette size (3) on several vegetation resources. This supports an asset/palette compatibility problem, though the precise origin of the specific tree's 1-slice atlas is not proven.

60-map / 310-frame run also exited with code 1, but this archive does not contain that run's fatal stack. Do not assert it has the same root cause without its logs.

## Proposed containment in the pinned Dagor source

New idempotent migration guard_impostor_shadow_atlas_slices() in kolobanoveye.py patches prog/gameLibs/rendInst/impostorTextureMgr.cpp at the unique anchor immediately after arrayTex->getinfo(texInfo).

Before rendering a preshadow or compressing it, check palette_id < 0 || palette_id >= texInfo.a. If out of bounds, emit [KE] IMPOSTOR_SHADOW_SLICE_SKIPPED with asset/palette/slice counts, release the acquired managed texture, and return true ONLY when KolobanovEye probe mode is active. Ordinary Dagor returns false; no out-of-bounds write occurs.

This skips an auxiliary baked tree-impostor shadow for an incompatible rotation palette, NOT the terrain, tank or normal rendering. It is a quality compromise; tree shadows may differ. Successful output with warnings must still undergo visual review.

After a completed run, raw/metadata/maps.json records per-map render_quality_flags, impostor_shadow_skipped_updates and impostor_shadow_assets. raw/manifest.json aggregates skipped counts and maps needing review. Generator prints RENDER_QUALITY_WARNING.

editor_failure() now reads fatalerr and the last MAP_TASK_BEGIN instead of only returning daEditor exit code 1.

## Windows acceptance

1. Update feature/avg-map-catalog-whitelist and run Python tests.
2. Build: python .\kolobanoveye.py build --dagor D:\Projects\DagorEngine; require BUILD_OK and IMPOSTOR_SHADOW_SLICE_GUARD enabled.
3. In the GUI select exactly four previously tested maps, including avg_alaska_town, with 8 total frames (2/map).
4. Confirm SINGLE_EDITOR_OK frames=8 maps=4 instances=1 and inspect output images, COCO and quality flags.
5. Repeat 20 maps and only then 60. Preserve native logs for any new failure.

Status: 101 Python CI tests pass at commit 60297c44. Windows native compile, DX11 behavior, and output visual quality are NOT YET CONFIRMED. Do not merge to main.

Security: do not restore or run the previously Kaspersky-flagged optional ke-ooz-replay.exe. It remains excluded from standard build.
