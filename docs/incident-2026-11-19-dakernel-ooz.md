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

## Confirmed source-level defect and defensive fix

Pinned upstream: \`powzix/ooz\` commit
\`05038060aa68f9187ae9923b2388ca8db40e58d1\`,
used as a decode-only locally staged snapshot.

In \`Kraken_DecodeMultiArray\` (\`kraken.cpp\` near upstream lines 1311–1355):

\`\`\`cpp
int num_arrays_in_file = *src++;
num_arrays_in_file &= 0x3f; // Possible values: 0..63
uint8 *entropy_array_data[32];
uint32 entropy_array_size[32];
for (int i = 0; i < num_arrays_in_file; i++) {
    // Writes to entropy_array_data[i] and entropy_array_size[i].
}
\`\`\`

There is no check ensuring \`num_arrays_in_file <= 32\` before the loop,
so malformed input with 33–63 declared entropy arrays can overwrite local
stack variables. **This is a real unchecked array bound, consistent with
the stack-cookie failure, but its activation in the specific War Thunder
stream has not been proven.**

\`tools/ke_ooz_hardening.py\` adds immediately after the \`&= 0x3f\` step:

\`\`\`cpp
// KolobanovEye: reject counts above the fixed local arrays[32].
if (num_arrays_in_file > 32)
  return -1;
\`\`\`

Build pipeline \`kolobanoveye.py prepare_ooz()\` now:

1. Verifies the SHA-256 pinned \`vendor/ooz\` snapshot.
2. Copies the source to separate \`build-logs/ooz-compiled\`.
3. Applies only this guarded, idempotent bounds check to the compilation copy.
4. Compiles with MSVC \`/Z7\` to preserve source line information in future
   \`daKernel-dev.pdb\` dumps.
5. Relinks the OOZ static library and DaEditor/daKernel via Dagor Jam.

Neither \`vendor/ooz/kraken.cpp\` nor the original
\`build-logs/ooz-local/kraken.cpp\` is altered; all existing hashes stay valid.

Tests in \`tests/test_ooz_hardening.py\` cover insertion before the writes,
idempotence, both newline styles and refusal to modify unknown/partial sources.

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
[KE] OOZ_KRAKEN_GUARD_OK capacity=32 patched=1
[KE] BUILD_OK ...
\`\`\`

If the guard rejects the compressed stream, the engine must report a decoding
failure without a stack-cookie fast-fail. This may still interrupt generation.
If \`0xC0000409\` persists, preserve the newly generated \`daKernel-dev.pdb\`
and WER dump and use the new \`/Z7\` line tables to locate the remaining
unsafe code; do not assume this single guard solves every decoder defect.

**Status:** Source-level bounds check implemented, Python CI tests pass.
Windows compilation and real DX11 startup **not yet confirmed**. Do not merge
to \`main\` until the Windows acceptance criteria succeed.
