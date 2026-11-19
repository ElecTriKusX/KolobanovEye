# Native Windows fail-fast in daKernel / local OOZ, 19 November 2026

## Evidence and symbol match

The user supplied a Windows minidump \`daEditor3x-dev.exe.40732.dmp\` (~43 MiB)
and \`daKernel-dev.pdb\` (~18 MiB) after recurrent failures before the first
selected map. Neither binary is published to the repository.

The MSVC PDB **matches** the crashed \`daKernel-dev.dll\` exactly:
GUID \`6645a618-fd0f-b005-8b7b-a05985205bd9\`, age \`1\`.

Parsed from MINIDUMP_EXCEPTION_STREAM:

- Process: \`daEditor3x-dev.exe\`; exception thread ID \`41756\`.
- Exception code: \`0xC0000409\`; exception information parameter 0: \`0x2\`
  = \`FAST_FAIL_STACK_COOKIE_CHECK_FAILURE\`.
- Exception RIP \`daKernel-dev.dll + 0x183FD1\` resolves through the matching
  PDB to \`__report_gsfailure + 0x1D\`. \`daKernel+0x183C57\` resolves to
  \`__GSHandlerCheck + 0x13\`.
- Additional addresses from the crashed thread's saved stack (candidate
  frames, not a fully verified WinDbg unwind) resolve to:
  \`Kraken_DecodeMultiArray\`, \`Leviathan_ReadLzTable\`,
  \`Leviathan_DecodeQuantum\`, \`Kraken_DecodeStep\`,
  \`Kraken_Decompress\`, \`oodle_decompress\`,
  \`OodleLoadCB::decodeData\`, \`OodleLoadCB::read\`,
  and \`FastSeqReader::tryRead\`.
- The original \`kraken.obj\` in this PDB has no C13 source-line debugging
  records. Exact line of memory corruption therefore **cannot be proven** from
  the supplied binaries alone.

The failure is in the decoder/IO path, not evidence of a multi-editor launch,
map-switch or oversized batch queue. \`wait.frame\` removal did not resolve it.

## Decoder bounds correction after the November 19 retry

New archive `.logs(5).zip` records a real, non-crashing editor start and successful entry into `kolobanoveye.batch` for `levels/avg_abandoned_factory.bin`:

```text
[KE] MAP_BATCH_BEGIN maps=1
[KE] MAP_TASK_BEGIN index=1/1
[KE] MAP_BEGIN .../avg_abandoned_factory.bin
ooz: decode failed ... stream type=12, packed=3630624,
     expected=13009598, result=-1
[KE] FAIL MAP: Binary dump did not produce a terrain renderer
```

The previous `0xC0000409` **did not recur** in this trace. The decoding failure could have been triggered by the earlier count guard but that is unproven without a dedicated diagnostic marker or a second test run.

The pinned upstream `kraken.cpp` (near lines 1311–1355) uses:

```cpp
num_arrays_in_file &= 0x3f;       // Six-bit count: 0..63
uint8 *entropy_array_data[32];   // Too small for some representable counts
uint32 entropy_array_size[32];
```

Initially we rejected values 33–63, preventing the specific overflow but potentially rejecting valid encoded data. **Current build-copy fix:** expand both arrays to `[64]`, preserving the entire representable count range without allowing that stack overwrite. No upstream or pinned vendor source files change. The compilation copy is `build-logs/ooz-compiled/kraken.cpp`; `build-logs/ooz-local` remains the original staged snapshot. MSVC `/Z7` remains enabled to help decode any future crash.

The editor's async `ConBatch::startLog()` never creates `probe.dcmd.log`: it only resets console counters. `ke_single_editor.py` now reads `.logs/<DaEditor>/debug` and normalizes `CON: [KE]` messages. It excludes duplicate bare `debug("[KE] MAP_BEGIN")` records and reports native `logerr` details for failed maps. The previous `File missing: probe.dcmd.log` was a Python-observability error, not proof the editor had not started.

A separate Windows test flake `WinError 145` deleting `tempfile.TemporaryDirectory` is addressed with bounded retries in `tests/test_frame_stability.py`. This does not silence persistent cleanup errors.

**Still unverified:** full decoding of abandoned_factory with [64] arrays and two-level sequence on the actual Windows editor. The `360s` timeout belongs to a *different* run `run-20261119T005713Z-df57877a`; that run's native logs were not in this archive, so its particular cause remains unknown.

## Windows verification gate

\`\`\`powershell
cd D:\Projects\KolobanovEye
git pull --ff-only origin feature/avg-map-catalog-whitelist
python -m unittest discover -v
python .\kolobanoveye.py build --dagor D:\Projects\DagorEngine
python .\ke_gui.py
\`\`\`

Choose Normandy only, two RGB images. During build verify:

\`\`\`text
[KE] OOZ_KRAKEN_GUARD_OK capacity=64 encoded_max=63 patched=1
[KE] BUILD_OK ...
\`\`\`

If the new 64-entry arrays decode this stream successfully, proceed to validating
terrain, RGB and COCO. If decoding still fails, use the new native log details
to isolate the remaining unsupported or invalid decoder operation.
If \`0xC0000409\` persists, preserve the newly generated \`daKernel-dev.pdb\`
and WER dump and use the new \`/Z7\` line tables to locate the remaining
unsafe code; do not assume this single guard solves every decoder defect.

**Status:** Source-level bounds check implemented, Python CI tests pass.
Windows compilation and real DX11 startup **not yet confirmed**. Do not merge
to \`main\` until the Windows acceptance criteria succeed.
