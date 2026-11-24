"""Build, inspect and run the KolobanovEye 2.0 single-frame probe."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import random
import shutil
import struct
import subprocess
import sys
import tempfile

from tools.ke_annotation import parse_annotation, write_annotation
from tools.ke_frame_journal import JOURNAL, read_journal
from tools.ke_maps import MAP_ID, parse_tank_bounds_log
from tools.vendor_ooz import VENDOR as OOZ_VENDOR, verify_vendor as verify_vendor_ooz, install as install_vendor_ooz
from tools.ke_ooz_hardening import guard_kraken_multi_array, trace_kraken_failures

REV = "9639942de5a8e5b01d91977f60ed82cda22d5f41"
HERE = Path(__file__).resolve().parent
RENDER_DEBUG_PATCH = HERE / "engine/patches/dagor-render-debug.patch"
DIRECT_DEPTH_PATCH = HERE / "engine/patches/dagor-direct-debug-depth.patch"
INDIRECTION_PATCH = HERE / "engine/patches/dagor-indirection-check.patch"
INDIRECTION_FIX_PATCH = HERE / "engine/patches/dagor-indirection-readback-fix.patch"
INDIRECTION_CPU_PATCH = HERE / "engine/patches/dagor-indirection-cpu-test.patch"
RESOLVE_PASS_PATCH = HERE / "engine/patches/dagor-resolve-pass-capture.patch"
RESOLVE_FLOAT_PATCH = HERE / "engine/patches/dagor-resolve-float-capture.patch"
DX11_TRACE_PATCH = HERE / "engine/patches/dagor-dx11-terrain-trace.patch"
CLIPMAP_LEGACY_PATCH = HERE / "engine/patches/dagor-clipmap-legacy-color.patch"
SEQUENCE_PATCH = HERE / "engine/patches/dagor-sequence.patch"
ORBIT_PATCH = HERE / "engine/patches/dagor-orbit-camera.patch"
CLEAN_CAPTURE_PATCH = HERE / "engine/patches/dagor-clean-capture.patch"
CAMERA_TERRAIN_PATCH = HERE / "engine/patches/dagor-camera-terrain.patch"
PLACEMENT_PATCH = HERE / "engine/patches/dagor-placement-search.patch"
CAMERA_RETRY_PATCH = HERE / "engine/patches/dagor-camera-retry.patch"
WATER_PROBE_PATCH = HERE / "engine/patches/dagor-water-probe.patch"
MULTI_COMPOSITION_PATCH = HERE / "engine/patches/dagor-multi-composition.patch"
CAMERA_HEIGHT_PATCH = HERE / "engine/patches/dagor-camera-ground-height.patch"
MAP_PLACEMENT_PATCH = HERE / "engine/patches/dagor-map-placement.patch"
WATER_FILTER_PATCH = HERE / "engine/patches/dagor-water-ground-filter.patch"
LAST_CLIP_BOUNDS_PATCH = HERE / "engine/patches/dagor-last-clip-bounds.patch"
REMOVE_CLIPMAP_EXPORT_PATCH = HERE / "engine/patches/dagor-remove-clipmap-export.patch"
LAST_CLIP_OUTPUT_PATCH = HERE / "engine/patches/dagor-last-clip-output.patch"
FRAME_JOURNAL_PATCH = HERE / "engine/patches/dagor-frame-journal.patch"
SCENE_QUALITY_PATCH = HERE / "engine/patches/dagor-scene-quality.patch"
OFFSCREEN_STREAMING_PATCH = HERE / "engine/patches/dagor-offscreen-streaming.patch"
TEXTURE_PUMP_PATCH = HERE / "engine/patches/dagor-offscreen-texture-pump.patch"
OFFSCREEN_FRAME_CLOCK_PATCH = HERE / "engine/patches/dagor-offscreen-frame-clock.patch"
OFFSCREEN_SERIAL_STREAMING_PATCH = HERE / "engine/patches/dagor-offscreen-serial-streaming.patch"
OFFSCREEN_STREAMING_WAIT_PATCH = HERE / "engine/patches/dagor-offscreen-streaming-wait.patch"
OFFSCREEN_RESOLVE_CAMERA_PATCH = HERE / "engine/patches/dagor-offscreen-resolve-camera.patch"
HAZE_CONVERGENCE_PATCH = HERE / "engine/patches/dagor-haze-convergence-warmup.patch"
HAZE_STABILITY_PATCH = HERE / "engine/patches/dagor-haze-frame-stability.patch"
TANK_ARTICULATION_PATCH = HERE / "engine/patches/dagor-tank-articulation.patch"
TANK_MAP_PATCH = HERE / "engine/patches/dagor-tank-map-bounds.patch"
TANK_MAP_RENDER_PATCH = HERE / "engine/patches/dagor-tank-map-render.patch"
CLIPMAP_TILE_PATCH = HERE / "engine/patches/dagor-clipmap-tile-readback.patch"
PATCHES = (
    HERE / "engine/patches/dagor-phase0.patch",
    HERE / "engine/patches/dagor-editor-unused-parameters.patch",
    HERE / "engine/patches/dagor-clipmap-render-mask.patch",
    HERE / "engine/patches/dagor-screenshot-lighting.patch",
    HERE / "engine/patches/dagor-terrain-draw.patch",
    RENDER_DEBUG_PATCH,
    DIRECT_DEPTH_PATCH,
    HERE / "engine/patches/dagor-clipmap-software-feedback.patch",
    CLIPMAP_TILE_PATCH,
    HERE / "engine/patches/dagor-deferred-terrain-view-rays.patch",
    INDIRECTION_PATCH,
    INDIRECTION_FIX_PATCH,
    INDIRECTION_CPU_PATCH,
    HERE / "engine/patches/dagor-terrain-bind-diagnostics.patch",
    RESOLVE_PASS_PATCH,
    RESOLVE_FLOAT_PATCH,
    DX11_TRACE_PATCH,
    CLIPMAP_LEGACY_PATCH,
    SEQUENCE_PATCH,
    ORBIT_PATCH,
    CLEAN_CAPTURE_PATCH,
    CAMERA_TERRAIN_PATCH,
    PLACEMENT_PATCH,
    CAMERA_RETRY_PATCH,
    WATER_PROBE_PATCH,
    MULTI_COMPOSITION_PATCH,
    CAMERA_HEIGHT_PATCH,
    MAP_PLACEMENT_PATCH,
    WATER_FILTER_PATCH,
    LAST_CLIP_BOUNDS_PATCH,
    LAST_CLIP_OUTPUT_PATCH,
    FRAME_JOURNAL_PATCH,
    SCENE_QUALITY_PATCH,
    OFFSCREEN_STREAMING_PATCH,
    TEXTURE_PUMP_PATCH,
    OFFSCREEN_FRAME_CLOCK_PATCH,
    OFFSCREEN_SERIAL_STREAMING_PATCH,
    OFFSCREEN_STREAMING_WAIT_PATCH,
    OFFSCREEN_RESOLVE_CAMERA_PATCH,
)
OOZ_REV = "05038060aa68f9187ae9923b2388ca8db40e58d1"
OOZ_PATCH = HERE / "engine/patches/dagor-ooz.patch"
OOZ_LIMIT = 256 * 1024 * 1024
OOZ_STREAM_TYPES = {5: "LZNA", 6: "Kraken", 10: "Mermaid/Selkie", 11: "Bitknit", 12: "Leviathan"}
BIN = Path("tools/dagor_cdk/windows-x86_64")
LEVEL = Path("levels/avg_normandy.bin")
GRP = Path("content/base/res/tanks/germ_pzkpfw_vi_ausf_h1_tiger.grp")
DXP = Path("content.hq/hq_tex/res/hq_tex_tanks/germ_pzkpfw_vi_ausf_h1_tiger.dxp.bin")
DYNMODEL = 0xB4B7D9C4
SKELETON = 0x56F81B6D
RES_LINE = re.compile(r'^\s*\d+:\s+"([^"]+)".*?cls=0x([0-9a-f]+)\b', re.I)
REF_LINE = re.compile(r"^\s*\[\s*\d+\]\s+(\S+)\s*$")
IDENTIFIER = re.compile(r"[A-Za-z0-9_@.+$-]+")


def read_exact(stream, count):
    data = stream.read(count)
    if len(data) != count:
        raise ValueError(f"Truncated data at byte {stream.tell()}")
    return data


def level_info(path):
    size = path.stat().st_size
    with path.open("rb") as stream:
        signature, textures = struct.unpack("<8sI", read_exact(stream, 12))
        if signature != b"DBLD3x64":
            raise ValueError(f"Expected DBLD/3x64, got {signature!r}")
        blocks = []
        while stream.tell() < size:
            offset = stream.tell()
            (field,) = struct.unpack("<I", read_exact(stream, 4))
            length = field & 0x3FFFFFFF
            end = offset + 4 + length
            if length < 4 or end > size:
                raise ValueError(f"Invalid block length {length} at byte {offset}")
            tag = read_exact(stream, 4).decode("ascii", "backslashreplace").strip("\0")
            blocks.append({"tag": tag, "offset": offset, "bytes": length, "flags": field >> 30})
            if tag == "END":
                return {"texture_count_header": textures, "blocks": blocks}
            stream.seek(end)
    raise ValueError("Missing END block")


def pack_info(path):
    with path.open("rb") as stream:
        signature, version = struct.unpack("<4sI", read_exact(stream, 8))
    if path.suffix == ".grp":
        if signature not in (b"GRP2", b"GRP3"):
            raise ValueError(f"Unsupported resource pack {signature!r}")
        return {"signature": signature.decode()}
    if signature != b"DxP2" or version not in (2, 3):
        raise ValueError(f"Unsupported texture pack {signature!r}, version {version}")
    return {"signature": signature.decode(), "version": version}


def shader_info(path):
    with path.open("rb") as stream:
        signature = read_exact(stream, 12)
    if signature != b"VSPSdump11.3":
        raise ValueError(f"Expected VSPSdump/11.3, got {signature!r}")
    return {"signature": "VSPSdump", "version": "11.3"}


def inspect(game, cdk, level=LEVEL):
    required = {
        game / level: level_info,
        game / GRP: pack_info,
        cdk / "application.blk": None,
        cdk / "tools.ps50.shdump.bin": shader_info,
        game / "aces.vromfs.bin": None,
        game / "content/base/res/grp_hdr.vromfs.bin": None,
    }
    optional = {
        game / DXP: pack_info,
        cdk / "tools.fexp.ps50.shdump.bin": shader_info,
        game / "content/base/res/riDesc.bin": None,
        game / "content/base/res/dynModelDesc.bin": None,
    }
    files, errors = {}, {}
    for path, reader in {**required, **optional}.items():
        try:
            if path in optional and not path.is_file():
                files[str(path)] = {"present": False}
                continue
            require_file(path)
            files[str(path)] = {"bytes": path.stat().st_size, **(reader(path) if reader else {})}
        except (OSError, ValueError) as exc:
            errors[str(path)] = str(exc)
    return {"dagor_commit": REV, "files": files, "errors": errors}


def parse_resources(text):
    resources = {}
    current = None
    for line in text.splitlines():
        if match := RES_LINE.match(line):
            name, class_id = match.groups()
            current = (int(class_id, 16), [])
            resources[name] = current
        elif current is not None and (match := REF_LINE.match(line)):
            current[1].append(match[1])
    return resources


def choose_resources(resources, stem, model, skeleton):
    models = sorted(name for name, (kind, _) in resources.items() if kind == DYNMODEL)
    skeletons = sorted(name for name, (kind, _) in resources.items() if kind == SKELETON)
    if model is None:
        stem = stem.lower()
        preferred = {stem, stem + "_dynmodel", stem + "_model"}
        options = [name for name in models if name.lower() in preferred]
        if not options and stem.startswith("germ_"):
            # The Tiger H1 GRP has a nation prefix that its DynModel omits.
            options = [name for name in models if name.lower() == stem.removeprefix("germ_")]
        options = options or models
        if len(options) != 1:
            raise ValueError(f"Set --model. DynModel candidates (see grp.txt): {options}")
        model = options[0]
    if model not in models:
        raise ValueError(f"{model!r} is not a DynModel listed by dumpGrp; see grp.txt.")
    if skeleton is None:
        deps = sorted(set(resources[model][1]).intersection(skeletons))
        matching = [name for name in skeletons if name.lower() == model.lower() + "_skeleton"]
        options = deps or matching or skeletons
        if len(options) > 1:
            raise ValueError(f"Set --skeleton. GeomNodeTree candidates (see grp.txt): {options}")
        skeleton = options[0] if options else ""
    if not IDENTIFIER.fullmatch(model) or (skeleton and not IDENTIFIER.fullmatch(skeleton)):
        raise ValueError("Specify plain Dagor resource names")
    return model, skeleton


def require_file(path):
    if not path.is_file():
        raise ValueError(f"File missing: {path}")
    return path


def dagor_path(path):
    value = path.resolve().as_posix()
    if not value.isascii() or any(char.isspace() or char == '"' for char in value) or len(value) >= 240:
        raise ValueError(f"Use short ASCII paths without spaces or quotes: {path}")
    return value


def numbers(values):
    if not all(math.isfinite(value) and abs(value) <= 100_000 for value in values):
        raise ValueError("Use finite coordinates and angles within ±100000")
    return ", ".join(f"{value:.9g}" for value in values)


def run_logged(command, log, cwd, timeout=None, env=None):
    print(f"[KE] {log.name}  {subprocess.list2cmdline(list(map(str, command)))}", flush=True)
    with log.open("wb") as stream:
        run = subprocess.run(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout, env=env)
    if run.returncode:
        raise ValueError(f"{Path(command[0]).name} exited with {run.returncode}. Logs: {log.parent}")


def editor_failure(out, detail):
    logs = sorted((out / ".logs").glob("daEditor3x-dev.exe-*/fatalerr"))
    for log in reversed(logs):
        with log.open(encoding="utf-8", errors="replace") as stream:
            if any("OodleLoadCB::open not implemented" in line for line in stream):
                return (
                    f"Dagor was built with the Oodle stub. The War Thunder build requires Oodle "
                    f"to decode its compressed textures. See {log} and README.md."
                )
    if "3221226505" in detail or "0XC0000409" in detail.upper():
        debug_logs = sorted((out / ".logs").glob("daEditor3x-dev.exe-*/debug"),
                            key=lambda p: p.stat().st_mtime)
        if debug_logs:
            latest = debug_logs[-1]
            tail = latest.read_bytes()[-80000:].decode("utf-8", errors="replace")
            begin = "[KE] MAP_BEGIN" in tail or "[KE] MAP_TASK_BEGIN" in tail
            stage = "during map processing" if begin else "before the first map task"
            # A native fast-fail often leaves an incomplete final record like "97.85 [D]".
            # Report the last *meaningful* record instead of that empty timestamp.
            meaningful = [line.strip() for line in tail.splitlines()
                          if line.strip() and not re.fullmatch(r"\d+(?:\.\d+)? \[\w\]", line.strip())]
            last_line = meaningful[-1] if meaningful else "unknown"
            command_lines = [line for line in meaningful if "CON: exec command" in line]
            last_command = command_lines[-1] if command_lines else "not executed"
            return (f"Windows native crash 0xC0000409 ({stage}), not a Python exception. "
                    f"Last useful debug line: {last_line!r}. Last command: {last_command!r}. "
                    f"See {latest}; check Windows Application Error event 1000 for the failing module.")
    # Non-fastfail DaEditor assertions also produce native exit 1, e.g.
    # DX11 invalid atlas slice. Report the actual native message and map index.
    fatal_logs = sorted((out / ".logs").glob("daEditor3x-dev.exe-*/fatalerr"),
                        key=lambda p: p.stat().st_mtime_ns)
    for fatal in reversed(fatal_logs):
        raw = fatal.read_text(encoding="utf-8", errors="replace")
        access_violation = "C0000005" in raw.upper()
        if not access_violation and "FATAL ERROR:" not in raw and "assert failed" not in raw:
            continue
        dst = re.search(r"Invalid update_sub_region dst:[^\r\n]*", raw)
        if access_violation:
            reason = re.search(r"Exception details:[^\r\n]*", raw)
            description = reason.group(0) if reason else "Windows access violation 0xC0000005"
            if "TraverseRayCell" in raw and "traceRayRendInstsNormalized" in raw:
                description += "; RendInst collision ray traversal (occlusion proxy)"
        elif "unitedVdata<rendInst> OOM" in raw:
            description = ("RendInst united vertex/index buffer pool exhausted "
                           "(unitedVdata<rendInst> OOM; not necessarily total GPU VRAM)")
            context = re.search(r"---fatal context---\s*([^\r\n]+)", raw)
            if context:
                description += f"; resource context: {context.group(1).strip()}"
        elif dst:
            description = dst.group(0)
        else:
            meaningful = [line.strip() for line in raw.splitlines()
                          if line.strip() and not line.lstrip().startswith("Call stack")]
            description = next((line for line in meaningful if "assert failed" in line),
                               meaningful[0] if meaningful else "native assertion")
        debug = fatal.parent / "debug"
        stage = ""
        if debug.is_file():
            # An OOM can dump thousands of resource records AFTER the last map
            # marker, so a fixed tail may miss the actual failing map.
            last_task = None
            completed_frames = 0
            with debug.open(encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    match = re.search(r"CON: \[KE\] MAP_TASK_BEGIN index=\d+/\d+[^\r\n]*", line)
                    if match:
                        last_task = match.group(0)
                    if "CON: [KE] FRAME_OK " in line:
                        completed_frames += 1
            if last_task:
                stage = f" Last map task: {last_task}. Completed RGB frames: {completed_frames}."
        kind = "access violation" if access_violation else "fatal assertion"
        return f"DaEditor native {kind}: {description}.{stage} Full stack: {fatal}"
    return f"{detail} See engine logs in {out / '.logs'}."


def verify_png(path, width, height):
    with path.open("rb") as stream:
        header = read_exact(stream, 33)
        if header[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
            raise ValueError("Invalid PNG header")
        if struct.unpack(">IIBB", header[16:26]) != (width, height, 8, 2):
            raise ValueError("Expected the requested dimensions and an 8-bit RGB PNG")
        stream.seek(-12, os.SEEK_END)
        if stream.read(12) != b"\x00\x00\x00\x00IEND\xaeB\x60\x82":
            raise ValueError("Truncated PNG")


def read_normandy_lightmap(path):
    """Find the embedded DDSx and return its metadata and raw Oodle stream."""
    name = b"avg_normandy_lightmap"
    blocks = (item for item in level_info(path)["blocks"] if item["tag"] == "TEX")
    with path.open("rb") as stream:
        for block in blocks:
            end = block["offset"] + 4 + block["bytes"]
            if block["bytes"] < 8:
                raise ValueError("Truncated TEX block")
            stream.seek(block["offset"] + 8)
            (length,) = struct.unpack("<i", read_exact(stream, 4))
            aligned_length = (length + 3) & ~3
            if length < 0 or aligned_length > end - stream.tell():
                raise ValueError("Invalid TEX name length")
            encoded_name = read_exact(stream, aligned_length)[:length].rstrip(b"\0")
            # Dagor's TEX name can end with a managed-texture marker and sampling metadata.
            base_name = encoded_name.rsplit(b"?", 1)[0].removesuffix(b"*")
            if base_name != name:
                continue
            if end - stream.tell() < 36:
                raise ValueError("Truncated embedded DDSx block")
            (nested,) = struct.unpack("<I", read_exact(stream, 4))
            nested_size = nested & 0x3FFFFFFF
            if not 32 <= nested_size <= end - stream.tell():
                raise ValueError("Invalid embedded DDSx block length")
            label, fmt, flags, width, height, levels, _, depth, bpp, _, mip, unpacked, packed = struct.unpack(
                "<4s4sIHHBBHHBBII", read_exact(stream, 32)
            )
            if label != b"DDSx" or not (width and height and levels) or levels > 32:
                raise ValueError("Invalid embedded DDSx header")
            if flags & 0xE0000000 != 0x60000000:
                raise ValueError(f"Expected DDSx Oodle compression, got flags=0x{flags:08x}")
            if not 2 <= packed <= min(nested_size - 32, OOZ_LIMIT) or not 1 <= unpacked <= OOZ_LIMIT:
                raise ValueError(f"Invalid DDSx sizes (packed={packed}, decoded={unpacked})")

            # Plain PC 2D textures store one surface per level (including reversed mip order).
            surface_bytes = None
            if not depth and not flags & (0x800 | 0x1000 | 0x2000 | 0x4000 | 0x20000 | 0x100000 | 0x200000):
                shift = mip & 0xF
                sizes = []
                for level in range(levels):
                    w, h = max(width >> level, 1), max(height >> level, 1)
                    sizes.append((max(w >> 2, 1) * max(h >> 2, 1)) << shift if shift else w * h * bpp // 8)
                surface_bytes = sum(sizes)
                if surface_bytes != unpacked:
                    raise ValueError(f"DDSx mip sizes total {surface_bytes}, header declares {unpacked}")
            offset = stream.tell()
            payload = read_exact(stream, packed)
            meta = {
                "texture": name.decode(),
                "encoded_texture": encoded_name.decode("ascii", errors="backslashreplace"),
                "texture_format_hex": fmt.hex(),
                "width": width,
                "height": height,
                "mip_levels": levels,
                "mip_bytes": surface_bytes,
                "payload_offset": offset,
                "packed_bytes": packed,
                "expected_bytes": unpacked,
                "stream_header_hex": payload[:2].hex(),
                "first_stream_type": OOZ_STREAM_TYPES.get(payload[1] & 0x7F, "unsupported"),
            }
            return meta, payload
    raise ValueError("Embedded avg_normandy_lightmap DDSx not found")


def inspect_large_oodle_quanta(data, decoded_size):
    """Inspect framing for Kraken, Mermaid/Selkie and Leviathan without decoding."""
    stride = 0x40000
    offset = 0
    restarts = []
    for output_offset in range(0, decoded_size, stride):
        block_offset = offset
        size = min(stride, decoded_size - output_offset)
        if offset + 2 > len(data):
            return {"error": "Truncated block header", "packed_offset": block_offset}
        mode, codec = data[offset:offset + 2]
        if mode & 0x3F != 0x0C or codec & 0x7F not in (6, 10, 12):
            return {"error": "Expected a large-quantum Oodle header", "packed_offset": block_offset}
        if mode & 0x80:
            restarts.append(block_offset)
        offset += 2
        if not mode & 0x40:
            if offset + 3 > len(data):
                return {"error": "Truncated quantum header", "packed_offset": block_offset}
            header = int.from_bytes(data[offset:offset + 3], "big")
            offset += 3
            packed_size = (header & 0x3FFFF) + 1
            if packed_size == stride:
                if header >> 18 != 1:
                    return {"error": "Unsupported special quantum", "packed_offset": block_offset}
                size = 1
            elif packed_size > size:
                return {"error": "Compressed quantum exceeds its output size", "packed_offset": block_offset}
            else:
                size = packed_size + (3 if codec & 0x80 else 0)
        offset += size
        if offset > len(data):
            return {"error": "Truncated quantum payload", "packed_offset": block_offset}
    return {"count": (decoded_size + stride - 1) // stride, "packed_bytes_left": len(data) - offset,
            "restart_offsets": restarts}


def probe_ooz(game, executable, unoodle=False):
    executable = require_file(executable.resolve())
    meta, data = read_normandy_lightmap(require_file(game / LEVEL))
    if data[1] & 0x7F in (6, 10, 12):
        layout = inspect_large_oodle_quanta(data, meta["expected_bytes"])
        meta["large_quanta"] = layout
        remaining = layout.get("packed_bytes_left", 0)
        if remaining:
            stream_size = len(data) - remaining
            layout["tail_prefix_hex"] = data[stream_size:stream_size + 32].hex()
            meta["decoder_input_bytes"] = stream_size
            data = data[:stream_size]
    print(json.dumps(meta, ensure_ascii=False), flush=True)
    if data[0] & 0x3F != 0x0C or meta["first_stream_type"] == "unsupported":
        raise ValueError("Unsupported Oodle stream header")
    if data[1] & 0x80:
        raise ValueError("These CLI decoders do not implement Oodle quantum checksums")
    decoder = "unoodle" if unoodle else "ooz"
    with tempfile.TemporaryDirectory(prefix="ke-ooz-probe-") as temporary:
        work = Path(temporary)
        packed = work / "lightmap.ooz"
        unpacked = work / "lightmap.raw"
        if unoodle:
            packed.write_bytes(data)
            command = [executable, packed, "--length", str(meta["expected_bytes"]), "--output", unpacked, "--verbose"]
        else:
            packed.write_bytes(struct.pack("<Q", meta["expected_bytes"]) + data)
            command = [executable, "-d", packed, unpacked]
        run = subprocess.run(command, cwd=work, capture_output=True, timeout=60)
        detail = (run.stdout + run.stderr).decode(errors="replace").strip()
        if run.returncode:
            raise ValueError(f"{decoder} exited with {run.returncode} while decoding the lightmap: {detail}")
        if unoodle and f'Extracted {meta["expected_bytes"]} bytes' not in detail.splitlines():
            raise ValueError(f"unoodle did not report the expected decoded byte count: {detail}")
        size = unpacked.stat().st_size
        if size != meta["expected_bytes"]:
            raise ValueError(f"{decoder} produced {size} bytes, expected {meta['expected_bytes']}")
        with unpacked.open("rb") as decoded:
            digest = hashlib.sha256()
            for chunk in iter(lambda: decoded.read(1024 * 1024), b""):
                digest.update(chunk)
    print(f"[KE] OOZ_LIGHTMAP_OK {size} bytes sha256={digest.hexdigest()} decoder={decoder}")


def stage_ooz_sources(ooz, work):
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ooz, text=True).strip()
    if actual != OOZ_REV:
        raise ValueError(f"Expected powzix/ooz {OOZ_REV}, got {actual}")
    for filename in ("stdafx.h", "targetver.h", "lzna.cpp", "bitknit.cpp"):
        content = subprocess.check_output(["git", "show", f"HEAD:{filename}"], cwd=ooz)
        if filename == "lzna.cpp":
            content = content.replace(b"lut->short_length[0][i] = 0x2000;", b"lut->short_length[i / 4][i % 4] = 0x2000;")
        (work / filename).write_bytes(content)
    source = subprocess.check_output(["git", "show", "HEAD:kraken.cpp"], cwd=ooz)
    source, marker, _ = source.partition(b"// The decompressor will write outside of the target buffer.")
    if not marker:
        raise ValueError("Could not identify the upstream ooz CLI boundary")
    source = source.replace(b"  memset(dec, 0, sizeof(KrakenDecoder));",
                            b"  if (!dec) return NULL;\n  memset(dec, 0, sizeof(KrakenDecoder));", 1)
    source = source.replace(b"  KrakenDecoder *dec = Kraken_Create();",
                            b"  KrakenDecoder *dec = Kraken_Create();\n  if (!dec) return -1;", 1)
    source = source.replace(b"src = Kraken_ParseHeader(&dec->hdr, src);\n    if (!src)",
                            b"src = Kraken_ParseHeader(&dec->hdr, src);\n    if (!src || dec->hdr.use_checksums)", 1)
    # Oodle's compressed length is the available input span. The declared output length
    # ends the decode, even when the containing DDSx block has unused bytes.
    source = source.replace(
        b"    if (dec->src_used == 0)\n      goto FAIL;",
        b"    if (dec->src_used <= 0 || size_t(dec->src_used) > src_len ||\n"
        b"        dec->dst_used <= 0 || size_t(dec->dst_used) > dst_len)\n      goto FAIL;",
        1,
    )
    source = source.replace(b"  if (src_len != 0)\n    goto FAIL;\n", b"", 1)
    (work / "kraken.cpp").write_bytes(source)


def prepare_ooz(ooz, logs):
    cl, lib = shutil.which("cl.exe"), shutil.which("lib.exe")
    if not cl or not lib or os.environ.get("VSCMD_ARG_TGT_ARCH") != "x64":
        raise ValueError("Run build --ooz from Developer PowerShell configured for x64")
    # Keep build-logs/ooz-local as an immutable pinned staging source. Only
    # ooz-compiled contains the hardened, compiler-ready derived snapshot.
    work = logs / "ooz-compiled"
    dagor_path(work)
    work.mkdir(exist_ok=True)
    if ooz is not None:
        stage_ooz_sources(ooz, work)
        # The selected commit must produce precisely the pinned decode-only sources.
        # Keep the verified snapshot inside the project for all later builds.
        install_vendor_ooz(work)
    else:
        verify_vendor_ooz()
        for filename in ("stdafx.h", "targetver.h", "kraken.cpp", "lzna.cpp", "bitknit.cpp"):
            shutil.copyfile(OOZ_VENDOR / filename, work / filename)
    # Hardening applies ONLY to work/kraken.cpp, never to the SHA-256-pinned vendor snapshot.
    hardened = guard_kraken_multi_array(work / "kraken.cpp")
    print(f"[KE] OOZ_KRAKEN_GUARD_OK capacity=64 encoded_max=63 patched={int(hardened)}", flush=True)
    traced = trace_kraken_failures(work / "kraken.cpp")
    print(f"[KE] OOZ_DECODER_TRACE_OK patched={int(traced)}", flush=True)
    # /Z7 embeds symbols and source line tables into the .obj so the daKernel PDB
    # can identify exact decoder lines on any remaining Windows native crash.
    run_logged([cl, "/nologo", "/c", "/O2", "/MT", "/Zl", "/Z7", "kraken.cpp", "lzna.cpp", "bitknit.cpp"],
               logs / "ooz-compile.log", work)
    library = work / "ke-ooz.lib"
    run_logged([lib, "/nologo", "/machine:x64", "/out:ke-ooz.lib", "kraken.obj", "lzna.obj", "bitknit.obj"],
               logs / "ooz-lib.log", work)
    # A security product classified the optional ke-ooz-replay.exe as high-risk
    # heuristic malware (VHO:Trojan.Win32.Agent.gen). Until independently
    # reviewed, do NOT build or execute that optional diagnostic binary.
    # The essential ke-ooz.lib is still compiled for the actual DaEditor.
    return require_file(library)


def apply_warmup_patch(source: Path, patch: Path, *, label: str):
    """Apply unique hunk contexts without relying on Dagor line numbers."""
    diff = patch.read_bytes().replace(b"\r\n", b"\n")
    hunks = re.findall(rb"(?ms)^@@ [^\n]*\n(.*?)(?=^@@ |\Z)", diff)
    if len(hunks) != 3:
        raise ValueError("Invalid offscreen warm-up patch: expected 3 hunks")
    raw = source.read_bytes()
    crlf = b"\r\n" in raw
    data = raw.replace(b"\r\n", b"\n")
    changed = 0
    for index, hunk in enumerate(hunks, 1):
        lines = hunk.splitlines(keepends=True)
        if any(not line or line[:1] not in (b" ", b"-", b"+") for line in lines):
            raise ValueError(f"Malformed offscreen warm-up patch hunk {index}")
        old = b"".join(line[1:] for line in lines if line.startswith((b" ", b"-")))
        new = b"".join(line[1:] for line in lines if line.startswith((b" ", b"+")))
        before, after = data.count(old), data.count(new)
        if before == 1 and after == 0:
            data = data.replace(old, new, 1)
            changed += 1
        elif before == 0 and after == 1:
            pass  # already upgraded
        else:
            raise ValueError(f"Ambiguous offscreen warm-up patch hunk {index}: old={before} new={after}; "
                             "do not reset Dagor, inspect the offscreen capture source")
    if changed:
        source.write_bytes(data.replace(b"\n", b"\r\n") if crlf else data)
        print(f"[KE] {label} applied={changed}/3 source={source}", flush=True)
    else:
        print(f"[KE] {label} already_applied=1", flush=True)


def apply_haze_capture_pipeline(source: Path, convergence_patch: Path, stability_patch: Path):
    """Apply successive warm-up patches, including when the later patch supersedes a prior hunk.

    The stability patch replaces the sceneReady line installed by the convergence patch.
    Therefore reverse/apply checks of the older patch alone are NOT an idempotence test
    after the successor has been installed. Validate the complete successor instead.
    """
    existing = source.read_bytes()
    if b'[KE] FRAME_STABILITY_FINAL ' in existing:
        required = (b'wait_frame_stability', b'[KE] HAZE_CONVERGENCE ',
                    b'const bool lightingReady =', b'keStableChecks >= 3',
                    b'[KE] FRAME_STABILITY frame=')
        missing = [marker.decode('ascii', errors='replace') for marker in required if marker not in existing]
        if missing:
            raise ValueError(f"Incomplete already-applied adaptive patch in {source}: {missing}")
        print("[KE] HAZE_CONVERGENCE_PATCH superseded_by=HAZE_STABILITY_PATCH", flush=True)
    else:
        apply_warmup_patch(source, convergence_patch, label="HAZE_CONVERGENCE_PATCH")
    # Streaming grace inserts state inside stability hunk 1 and changes loop
    # deadlines. Validate the complete known successor on an in-memory copy;
    # never roll back or rewrite the user's Dagor checkout.
    from tools.ke_streaming_grace import MARKER, EDITS
    data = source.read_bytes().replace(b"\r\n", b"\n")
    if MARKER.encode() in data:
        for old, new in reversed(EDITS):
            old, new = old.encode(), new.encode()
            if data.count(new) != 1:
                raise ValueError(f"Partial streaming grace installation in {source}")
            data = data.replace(new, old, 1)
        diff = stability_patch.read_bytes().replace(b"\r\n", b"\n")
        hunks = re.findall(rb"(?ms)^@@ [^\n]*\n(.*?)(?=^@@ |\Z)", diff)
        if len(hunks) != 3:
            raise ValueError("Invalid offscreen warm-up patch: expected 3 hunks")
        for index, hunk in enumerate(hunks, 1):
            lines = hunk.splitlines(keepends=True)
            if any(not line or line[:1] not in (b" ", b"-", b"+") for line in lines):
                raise ValueError(f"Malformed offscreen warm-up patch hunk {index}")
            old = b"".join(line[1:] for line in lines if line.startswith((b" ", b"-")))
            new = b"".join(line[1:] for line in lines if line.startswith((b" ", b"+")))
            if data.count(old) != 0 or data.count(new) != 1:
                raise ValueError(f"Incomplete stability predecessor under streaming grace: hunk {index}; "
                                 "do not reset Dagor, inspect the offscreen capture source")
        print("[KE] HAZE_STABILITY_PATCH superseded_by=STREAMING_GRACE_V1", flush=True)
        return
    apply_warmup_patch(source, stability_patch, label="HAZE_STABILITY_PATCH")


def is_upgraded_offscreen_capture(source: Path) -> bool:
    """Recognize an already-upgraded capture loop; never skip predecessors blindly."""
    if not source.is_file():
        return False
    contents = source.read_bytes()
    if b'[KE] HAZE_CONVERGENCE ' not in contents:
        return False
    required = (
        b'const auto textureWorkerBusy = [&]()',
        b'const auto texturesIdle = [&]()',
        b'frame = captureFrame(nullptr);',
        b'const bool previousSkipRenderObjects = skipRenderObjects;',
        b'metadataJournal',
        b'[KE] STREAMING_PUMP ',
        b'const int minWarmupRenders = config.getInt("min_warmup_renders", 8);',
    )
    missing = [marker.decode('ascii', errors='replace') for marker in required if marker not in contents]
    if missing:
        raise ValueError(f"Partial offscreen-capture upgrade in {source}: missing {missing}; "
                         "do not reset Dagor; inspect the existing file")
    return True


def has_last_clip_bounds(source: Path) -> bool:
    """Recognize already installed last-clip metadata, even after successor patches.

    git apply --reverse --check fails after the tank-map successor modifies the
    surrounding metadata block. Check feature markers, never line-number context.
    A partially inserted block is an error, not a reason to reapply an old patch.
    """
    contents = source.read_bytes()
    markers = (
        b'const auto cellOrigin = scene.lmeshMgr->getCellOrigin();',
        b'const float lastClipX0 =',
        b'const float lastClipZ1 =',
        b'return fail("LAST_CLIP_BOUNDS", "Invalid fixed clip bounds");',
        b'result.setPoint3("last_clip_min",',
        b'result.setPoint3("last_clip_max",',
    )
    found = [marker in contents for marker in markers]
    if any(found) and not all(found):
        missing = [marker.decode("ascii") for marker, exists in zip(markers, found) if not exists]
        raise ValueError(f"Partial last-clip bounds integration in {source}: missing {missing}")
    return all(found)


def ooz_patch_installed(dagor: Path) -> bool:
    """Recognize the pinned OOZ patch even after a later native migration.

    The first build patches oodleIoOoz.cpp and then injects failed-stream
    capture into its error branch. That mutation invalidates git apply
    --reverse --check for the original multi-file patch, despite the Oodle
    integration being present. Never re-apply it or reset the Dagor checkout.
    Refuse incomplete combinations rather than treating them as installed.
    """
    io = dagor / "prog/engine/ioSys"
    jam = io / "jamfile"
    cpp = io / "oodleIoOoz.cpp"
    if not jam.is_file():
        raise ValueError(f"Missing Dagor IO Jamfile: {jam}")
    j = jam.read_bytes()
    installed_jam = (
        b"local ooz_enabled =" in j and
        b"Sources += oodleIoOoz.cpp oodleIoFatal.cpp" in j and
        b"Building daKernel with the decode-only ooz backend" in j
    )
    candidates = (b"local ooz_enabled =", b"UseOoz", b"OozLib",
                  b"oodleIoOoz.cpp oodleIoFatal.cpp")
    partial_jam = any(x in j for x in candidates)
    if cpp.is_file():
        data = cpp.read_bytes()
        required = (b"Kraken_Decompress(", b"void OodleLoadCB::open(",
                    b"inline int OodleLoadCB::decodeData(", b"size_t oodle_decompress(",
                    b"bool OodleLoadCB::ceaseReading()")
        if not installed_jam or not all(x in data for x in required):
            raise ValueError("Partial or unexpected Dagor OOZ integration; "
                             f"inspect {jam} and {cpp}, do not reset sources")
        return True
    if partial_jam:
        raise ValueError(f"Dagor OOZ Jamfile is modified but decoder source is absent: {cpp}")
    return False


def apply_dagor_patches(dagor, *, with_ooz=False):
    apply = ["git", "apply", "--whitespace=error-all"]
    patches = (*PATCHES, OOZ_PATCH) if with_ooz else PATCHES
    ooz_already_applied = ooz_patch_installed(dagor) if with_ooz else False
    if ooz_already_applied:
        print("[KE] OOZ_PATCH already_applied=1 (including post-patch diagnostics)", flush=True)
    direct_depth_diff = DIRECT_DEPTH_PATCH.read_bytes().replace(b"\r\n", b"\n")
    direct_depth_applied = subprocess.run([*apply, "--reverse", "--check"], input=direct_depth_diff, cwd=dagor,
                                          capture_output=True).returncode == 0
    indirection_fixed = subprocess.run([*apply, "--reverse", "--check"],
        input=INDIRECTION_FIX_PATCH.read_bytes().replace(b"\r\n", b"\n"), cwd=dagor,
        capture_output=True).returncode == 0
    indirection_cpu_applied = subprocess.run([*apply, "--reverse", "--check"],
        input=INDIRECTION_CPU_PATCH.read_bytes().replace(b"\r\n", b"\n"), cwd=dagor,
        capture_output=True).returncode == 0
    resolve_pass_applied = subprocess.run([*apply, "--reverse", "--check"],
        input=RESOLVE_PASS_PATCH.read_bytes().replace(b"\r\n", b"\n"), cwd=dagor,
        capture_output=True).returncode == 0
    resolve_float_applied = subprocess.run([*apply, "--reverse", "--check"],
        input=RESOLVE_FLOAT_PATCH.read_bytes().replace(b"\r\n", b"\n"), cwd=dagor,
        capture_output=True).returncode == 0
    legacy_clip_applied = subprocess.run([*apply, "--reverse", "--check"],
        input=CLIPMAP_LEGACY_PATCH.read_bytes().replace(b"\r\n", b"\n"), cwd=dagor,
        capture_output=True).returncode == 0
    sequence_applied = subprocess.run([*apply, "--reverse", "--check"],
        input=SEQUENCE_PATCH.read_bytes().replace(b"\r\n", b"\n"), cwd=dagor, capture_output=True).returncode == 0
    terrain_patch_applied = subprocess.run([*apply, "--reverse", "--check"],
        input=CAMERA_TERRAIN_PATCH.read_bytes().replace(b"\r\n", b"\n"), cwd=dagor, capture_output=True).returncode == 0
    placement_patch_applied = subprocess.run([*apply, "--reverse", "--check"],
        input=PLACEMENT_PATCH.read_bytes().replace(b"\r\n", b"\n"), cwd=dagor, capture_output=True).returncode == 0
    camera_retry_applied = subprocess.run([*apply, "--reverse", "--check"],
        input=CAMERA_RETRY_PATCH.read_bytes().replace(b"\r\n", b"\n"), cwd=dagor, capture_output=True).returncode == 0
    water_probe_applied = subprocess.run([*apply, "--reverse", "--check"],
        input=WATER_PROBE_PATCH.read_bytes().replace(b"\r\n", b"\n"), cwd=dagor, capture_output=True).returncode == 0
    multi_applied = subprocess.run([*apply, "--reverse", "--check"],
        input=MULTI_COMPOSITION_PATCH.read_bytes().replace(b"\r\n", b"\n"), cwd=dagor, capture_output=True).returncode == 0
    camera_height_applied = subprocess.run([*apply, "--reverse", "--check"],
        input=CAMERA_HEIGHT_PATCH.read_bytes().replace(b"\r\n", b"\n"), cwd=dagor, capture_output=True).returncode == 0
    map_placement_applied = subprocess.run([*apply, "--reverse", "--check"],
        input=MAP_PLACEMENT_PATCH.read_bytes().replace(b"\r\n", b"\n"), cwd=dagor, capture_output=True).returncode == 0
    # Remove the former experimental tile writer when upgrading an existing Dagor tree.
    removal = REMOVE_CLIPMAP_EXPORT_PATCH.read_bytes().replace(b"\r\n", b"\n")
    if subprocess.run([*apply, "--check"], input=removal, cwd=dagor, capture_output=True).returncode == 0:
        print("[KE] Removing the experimental clipmap tile exporter", flush=True)
        subprocess.run(apply, input=removal, cwd=dagor, check=True)
    water_filter_applied = subprocess.run([*apply, "--reverse", "--check"], input=WATER_FILTER_PATCH.read_bytes().replace(b"\r\n", b"\n"), cwd=dagor, capture_output=True).returncode == 0
    capture_cpp = dagor / "prog/tools/sceneTools/daEditorX/kolobanovEye.cpp"
    last_clip_bounds_applied = has_last_clip_bounds(capture_cpp) or subprocess.run(
        [*apply, "--reverse", "--check"], input=LAST_CLIP_BOUNDS_PATCH.read_bytes().replace(b"\r\n", b"\n"),
        cwd=dagor, capture_output=True).returncode == 0
    scene_quality_applied = subprocess.run([*apply, "--reverse", "--check"],
        input=SCENE_QUALITY_PATCH.read_bytes().replace(b"\r\n", b"\n"), cwd=dagor, capture_output=True).returncode == 0
    offscreen_streaming_applied = subprocess.run([*apply, "--reverse", "--check"],
        input=OFFSCREEN_STREAMING_PATCH.read_bytes().replace(b"\r\n", b"\n"), cwd=dagor, capture_output=True).returncode == 0
    upgraded_capture = is_upgraded_offscreen_capture(capture_cpp)
    streaming_wait_applied = upgraded_capture or subprocess.run([*apply, "--reverse", "--check"],
        input=OFFSCREEN_STREAMING_WAIT_PATCH.read_bytes().replace(b"\r\n", b"\n"), cwd=dagor, capture_output=True).returncode == 0
    serial_streaming_applied = streaming_wait_applied or subprocess.run([*apply, "--reverse", "--check"],
        input=OFFSCREEN_SERIAL_STREAMING_PATCH.read_bytes().replace(b"\r\n", b"\n"), cwd=dagor, capture_output=True).returncode == 0
    offscreen_frame_clock_applied = serial_streaming_applied or subprocess.run([*apply, "--reverse", "--check"],
        input=OFFSCREEN_FRAME_CLOCK_PATCH.read_bytes().replace(b"\r\n", b"\n"), cwd=dagor, capture_output=True).returncode == 0
    texture_pump_applied = offscreen_frame_clock_applied or subprocess.run([*apply, "--reverse", "--check"],
        input=TEXTURE_PUMP_PATCH.read_bytes().replace(b"\r\n", b"\n"), cwd=dagor, capture_output=True).returncode == 0
    from tools.ke_patch_predecessors import predecessor_additions_present
    for patch in patches:
        if patch == OFFSCREEN_RESOLVE_CAMERA_PATCH and predecessor_additions_present(dagor, patch):
            print("[KE] OFFSCREEN_RESOLVE_CAMERA_PATCH already present (including visibility successor)", flush=True)
            continue
        if patch == OOZ_PATCH and ooz_already_applied:
            continue
        if last_clip_bounds_applied and patch == LAST_CLIP_BOUNDS_PATCH:
            continue
        if upgraded_capture and patch == OFFSCREEN_STREAMING_WAIT_PATCH:
            continue
        if streaming_wait_applied and patch == OFFSCREEN_SERIAL_STREAMING_PATCH:
            continue
        if serial_streaming_applied and patch == OFFSCREEN_FRAME_CLOCK_PATCH:
            continue
        if (texture_pump_applied and patch == OFFSCREEN_STREAMING_PATCH) or (offscreen_frame_clock_applied and patch == TEXTURE_PUMP_PATCH):
            continue
        if (offscreen_streaming_applied or texture_pump_applied) and patch == SCENE_QUALITY_PATCH:
            continue
        if (scene_quality_applied or offscreen_streaming_applied or texture_pump_applied) and patch in (CLEAN_CAPTURE_PATCH, FRAME_JOURNAL_PATCH):
            continue
        if last_clip_bounds_applied and (patch.name == "dagor-phase0.patch" or patch in (SEQUENCE_PATCH, ORBIT_PATCH, CAMERA_TERRAIN_PATCH, PLACEMENT_PATCH, CAMERA_RETRY_PATCH, WATER_PROBE_PATCH, MULTI_COMPOSITION_PATCH, CAMERA_HEIGHT_PATCH, MAP_PLACEMENT_PATCH, WATER_FILTER_PATCH)):
            continue
        if water_filter_applied and (patch.name == "dagor-phase0.patch" or patch in (SEQUENCE_PATCH, ORBIT_PATCH, CAMERA_TERRAIN_PATCH, PLACEMENT_PATCH, CAMERA_RETRY_PATCH, WATER_PROBE_PATCH, MULTI_COMPOSITION_PATCH, CAMERA_HEIGHT_PATCH, MAP_PLACEMENT_PATCH)):
            continue
        if map_placement_applied and (patch.name == "dagor-phase0.patch" or patch in (SEQUENCE_PATCH, ORBIT_PATCH, CAMERA_TERRAIN_PATCH, PLACEMENT_PATCH, CAMERA_RETRY_PATCH, WATER_PROBE_PATCH, MULTI_COMPOSITION_PATCH, CAMERA_HEIGHT_PATCH)):
            continue
        if camera_height_applied and (patch.name == "dagor-phase0.patch" or patch in (SEQUENCE_PATCH, ORBIT_PATCH, CAMERA_TERRAIN_PATCH, PLACEMENT_PATCH, CAMERA_RETRY_PATCH, WATER_PROBE_PATCH, MULTI_COMPOSITION_PATCH)):
            continue
        if multi_applied and (patch.name == "dagor-phase0.patch" or patch in (SEQUENCE_PATCH, ORBIT_PATCH, CAMERA_TERRAIN_PATCH, PLACEMENT_PATCH, CAMERA_RETRY_PATCH, WATER_PROBE_PATCH)):
            continue
        if water_probe_applied and (patch.name == "dagor-phase0.patch" or patch in (SEQUENCE_PATCH, ORBIT_PATCH, CAMERA_TERRAIN_PATCH, PLACEMENT_PATCH, CAMERA_RETRY_PATCH)):
            continue
        if camera_retry_applied and (patch.name == "dagor-phase0.patch" or patch in (SEQUENCE_PATCH, ORBIT_PATCH, CAMERA_TERRAIN_PATCH, PLACEMENT_PATCH)):
            continue
        if placement_patch_applied and (patch.name == "dagor-phase0.patch" or patch in (SEQUENCE_PATCH, ORBIT_PATCH, CAMERA_TERRAIN_PATCH)):
            continue
        if terrain_patch_applied and (patch.name == "dagor-phase0.patch" or patch in (SEQUENCE_PATCH, ORBIT_PATCH)):
            continue
        if sequence_applied and patch.name == "dagor-phase0.patch":
            continue
        if legacy_clip_applied and patch in (INDIRECTION_PATCH, INDIRECTION_FIX_PATCH, INDIRECTION_CPU_PATCH, DX11_TRACE_PATCH,
                                             CLIPMAP_LEGACY_PATCH):
            continue
        if resolve_float_applied and patch in (RENDER_DEBUG_PATCH, DIRECT_DEPTH_PATCH, RESOLVE_PASS_PATCH, RESOLVE_FLOAT_PATCH):
            continue
        if resolve_pass_applied and patch in (RENDER_DEBUG_PATCH, DIRECT_DEPTH_PATCH, RESOLVE_PASS_PATCH):
            continue
        if indirection_cpu_applied and patch in (INDIRECTION_PATCH, INDIRECTION_FIX_PATCH, INDIRECTION_CPU_PATCH):
            continue
        if indirection_fixed and patch in (INDIRECTION_PATCH, INDIRECTION_FIX_PATCH):
            continue
        # The follow-up edits lines inserted by the original render diagnostics patch.
        if direct_depth_applied and patch in (RENDER_DEBUG_PATCH, DIRECT_DEPTH_PATCH):
            continue
        # Git for Windows may have checked out the patch as CRLF. Keep strict
        # whitespace validation and pass the original diff with LF endings.
        diff = patch.read_bytes().replace(b"\r\n", b"\n")
        if subprocess.run([*apply, "--reverse", "--check"], input=diff, cwd=dagor, capture_output=True).returncode:
            print(f"[KE] Applying {patch.name}", flush=True)
            subprocess.run([*apply, "--check"], input=diff, cwd=dagor, check=True)
            subprocess.run(apply, input=diff, cwd=dagor, check=True)

    apply_haze_capture_pipeline(capture_cpp, HAZE_CONVERGENCE_PATCH, HAZE_STABILITY_PATCH)
    from tools.ke_streaming_grace import install_streaming_grace
    install_streaming_grace(capture_cpp)
    diff = TANK_ARTICULATION_PATCH.read_bytes().replace(b"\r\n", b"\n")
    if (not predecessor_additions_present(dagor, TANK_ARTICULATION_PATCH) and
            subprocess.run([*apply, "--reverse", "--check"], input=diff, cwd=dagor, capture_output=True).returncode):
        print(f"[KE] Applying {TANK_ARTICULATION_PATCH.name}", flush=True)
        subprocess.run([*apply, "--check"], input=diff, cwd=dagor, check=True)
        subprocess.run(apply, input=diff, cwd=dagor, check=True)
    ensure_level_settings_accessor(dagor / "prog/tools/sceneTools/daEditorX/binSceneView/strmlevel.h")
    diff = TANK_MAP_PATCH.read_bytes().replace(b"\r\n", b"\n")
    if (not predecessor_additions_present(dagor, TANK_MAP_PATCH) and
            subprocess.run([*apply, "--reverse", "--check"], input=diff, cwd=dagor, capture_output=True).returncode):
        print(f"[KE] Applying {TANK_MAP_PATCH.name}", flush=True)
        subprocess.run([*apply, "--check"], input=diff, cwd=dagor, check=True)
        subprocess.run(apply, input=diff, cwd=dagor, check=True)
    map_cpp = dagor / "prog/tools/sceneTools/daEditorX/binSceneView/strmLevel.cpp"
    upgrade_roi_unique_tex(map_cpp)
    if b"ke-tank-map-output-dir" not in map_cpp.read_bytes():
        diff = TANK_MAP_RENDER_PATCH.read_bytes().replace(b"\r\n", b"\n")
        if subprocess.run([*apply, "--reverse", "--check"], input=diff, cwd=dagor, capture_output=True).returncode:
            print(f"[KE] Applying {TANK_MAP_RENDER_PATCH.name}", flush=True)
            subprocess.run([*apply, "--check"], input=diff, cwd=dagor, check=True)
            subprocess.run(apply, input=diff, cwd=dagor, check=True)
    enable_batch_tank_map_output(map_cpp)
    reset_level_settings_on_reload(map_cpp)
    preserve_landmesh_renderer_release_on_reload(map_cpp)
    from tools.ke_map_resource_gc import install_map_resource_gc
    install_map_resource_gc(map_cpp)
    enable_single_editor_map_batch(dagor)
    from tools.ke_dagor_composition import install_screen_composition
    install_screen_composition(capture_cpp)
    from tools.ke_dagor_occlusion import install_occlusion_search
    install_occlusion_search(capture_cpp)
    from tools.ke_dagor_visibility import install_visibility_capture
    install_visibility_capture(dagor)
    from tools.ke_dagor_online import install_online_feedback
    install_online_feedback(capture_cpp)
    from tools.ke_ri_trace_props import install_ri_trace_props
    install_ri_trace_props(dagor / "prog/gameLibs/rendInst/rendInstGenCollision.cpp")
    guard_impostor_shadow_atlas_slices(dagor)
    if with_ooz:
        enable_failed_ooz_stream_capture(dagor)



def guard_impostor_shadow_atlas_slices(dagor: Path) -> bool:
    """Prevent invalid palette slice writes in the pinned Dagor DX11 renderer.

    Real 20-map batch failed at map 4 avg_alaska_town.bin, targeting subres=7
    (slice 1) on the one-slice tree_fagus_hurtgen_pl_l_a_s shadow atlas.
    The error originates in ImpostorTextureManager::update_shadow(), not
    BcCompressor or validate_update_sub_region_params(). Never weaken driver
    validation or remap to a different atlas slice.

    For KolobanovEye only, skip that *preshadow update* and record a quality
    warning; normal Dagor callers receive false. Base scene/mesh rendering
    still occurs, but the affected palette-specific preshadow is unavailable.
    """
    source = dagor / "prog/gameLibs/rendInst/impostorTextureMgr.cpp"
    raw = source.read_bytes()
    crlf = b"\r\n" in raw
    data = raw.replace(b"\r\n", b"\n")
    anchor = (b"  TextureInfo texInfo;\n"
              b"  arrayTex->getinfo(texInfo);\n"
              b"  BaseTexture *targetTex = arrayTex;\n")
    marker = b"  // [KE] IMPOSTOR_SHADOW_SLICE_GUARD_V1\n"
    patch = (
        b"  // [KE] IMPOSTOR_SHADOW_SLICE_GUARD_V1\n"
        b"  // Some WT maps use a rotation palette with more entries than the\n"
        b"  // baked shadow-atlas resource has array slices. Never write beyond it.\n"
        b"  if (palette_id < 0 || palette_id >= texInfo.a)\n"
        b"  {\n"
        b"    const bool keCapture = dgs_get_argv(\"kolobanoveye-probe\") != nullptr;\n"
        b"    logwarn(\"[KE] IMPOSTOR_SHADOW_SLICE_SKIPPED asset=%s palette_id=%d atlas_slices=%d mips=%d layer=%d ri=%d ke_capture=%d\",\n"
        b"      arrayTex->getTexName(), palette_id, texInfo.a, texInfo.mipLevels, layer_id, ri_id, int(keCapture));\n"
        b"    release_managed_tex(res->getImpostorTextures().shadowAtlas);\n"
        b"    return keCapture; // Only the dataset probe accepts reduced preshadow quality.\n"
        b"  }\n"
    )
    if data.count(marker) == 1:
        if data.count(patch) != 1:
            raise ValueError(f"Partial KolobanovEye impostor slice guard in {source}")
        return False
    if data.count(anchor) != 1 or marker in data:
        raise ValueError(f"Cannot locate unique original impostor slice update in {source}")
    data = data.replace(anchor, anchor.replace(b"  BaseTexture *targetTex = arrayTex;\n", b"") +
                        patch + b"  BaseTexture *targetTex = arrayTex;\n", 1)
    source.write_bytes(data.replace(b"\n", b"\r\n") if crlf else data)
    print(f"[KE] IMPOSTOR_SHADOW_SLICE_GUARD enabled source={source}", flush=True)
    return True


def enable_failed_ooz_stream_capture(dagor: Path) -> bool:
    """Save only the first rejected Oodle block for isolated decoder A/B tests.

    Enabled only if Python passes KE_OOZ_FAILURE_DIR to the editor process.
    This avoids collecting proprietary game data in the repository or logs.
    Does not alter the original 5 pinned OOZ source files.
    """
    source = dagor / "prog/engine/ioSys/oodleIoOoz.cpp"
    raw = source.read_bytes()
    crlf = b"\r\n" in raw
    data = raw.replace(b"\r\n", b"\n")
    signature = b"if (result != static_cast<int>(unpacked_size))\n  {\n"
    mark = b"    // [KE] OOZ_FAILED_STREAM_CAPTURE_V1\n"
    if data.count(mark) == 1:
        if b"KE_OOZ_FAILURE_DIR" not in data or b"failed-ooz.bin" not in data:
            raise ValueError(f"Partial rejected Oodle stream capture in {source}")
        return False
    if data.count(signature) != 1 or mark in data:
        raise ValueError(f"Cannot find exact OOZ decoder failure branch in {source}")
    # Protect against unbounded dumps; on this level the rejected packed block
    # has a length of 3,630,624 bytes, comfortably below the 16 MiB cap.
    code = r'''    // [KE] OOZ_FAILED_STREAM_CAPTURE_V1
    const char *keDumpDir = std::getenv("KE_OOZ_FAILURE_DIR");
    if (keDumpDir && *keDumpDir && packed_size <= 16u * 1024u * 1024u)
    {
      static std::atomic_flag keCaptured = ATOMIC_FLAG_INIT;
      if (!keCaptured.test_and_set())
      {
        char keDumpPath[2048];
        char keMetaPath[2048];
        const int keN = std::snprintf(keDumpPath, sizeof(keDumpPath), "%s/failed-ooz.bin", keDumpDir);
        const int keM = std::snprintf(keMetaPath, sizeof(keMetaPath), "%s/failed-ooz.txt", keDumpDir);
        if (keN > 0 && keN < int(sizeof(keDumpPath)) && keM > 0 && keM < int(sizeof(keMetaPath)))
        {
          if (FILE *keFile = std::fopen(keDumpPath, "wb"))
          {
            const size_t keWritten = std::fwrite(input, 1, packed_size, keFile);
            std::fclose(keFile);
            if (keWritten == packed_size)
            {
              if (FILE *keMeta = std::fopen(keMetaPath, "wb"))
              {
                std::fprintf(keMeta, "packed=%zu\nunpacked=%zu\ncodec=%d\nsource=%s\n",
                  packed_size, unpacked_size, codec, name ? name : "<unknown>");
                std::fclose(keMeta);
              }
              logerr("ooz: rejected compressed block saved for local replay: %s (packed=%u, expected=%u)",
                keDumpPath, static_cast<unsigned>(packed_size), static_cast<unsigned>(unpacked_size));
            }
          }
        }
      }
    }
'''.encode("ascii")
    data = data.replace(signature, signature + code, 1)
    include_anchor = b"#include <cstdlib>\n"
    if data.count(include_anchor) != 1:
        raise ValueError(f"Cannot safely include atomic/stdio in {source}")
    data = data.replace(include_anchor, include_anchor + b"#include <atomic>\n#include <cstdio>\n", 1)
    source.write_bytes(data.replace(b"\n", b"\r\n") if crlf else data)
    print("[KE] Enabled first-failed-Oodle-block capture for native decoder diagnosis", flush=True)
    return True


def upgrade_roi_unique_tex(source):
    """Upgrade the already-installed ROI renderer from ResFactory to UniqueTex.

    This is an in-place migration for Dagor checkouts where the OLD patch was
    already applied before the new patch was committed. It changes one exact
    declaration only and refuses ambiguous/partial renderer changes.
    """
    data = source.read_bytes()
    before = b"auto target = dag::create_tex(nullptr, size, size,"
    after = b"UniqueTex target = dag::create_tex(nullptr, size, size,"
    old_count, new_count = data.count(before), data.count(after)
    if old_count == 0 and new_count == 0:
        return False  # fresh checkout; git apply installs the corrected patch
    if b"render_last_clip_region_to_tex" not in data or old_count + new_count != 1:
        raise ValueError("Ambiguous tank-map render target in strmLevel.cpp")
    if new_count:
        return False
    source.write_bytes(data.replace(before, after, 1))
    print("[KE] Upgraded existing tank map render target to UniqueTex", flush=True)
    return True


def enable_single_editor_map_batch(dagor: Path) -> bool:
    """Install a single native batch command into an already-patched DaEditor.

    A 60-map session previously inserted 121 strings into Dagor's ConBatch queue.
    The native command keeps the queue constant-size: just wait, one C++ job,
    and exit. The crash may have another cause; this is a robustness change,
    not proof that queue size caused Windows STATUS_STACK_BUFFER_OVERRUN.
    The migration is safe to repeat and preserves each file's line endings.
    """
    base = dagor / "prog/tools/sceneTools/daEditorX"
    header = base / "de_appwnd.h"
    commands = base / "de_appwndConCmd.cpp"
    source = base / "kolobanovEye.cpp"

    updates = []
    raw = header.read_bytes()
    eol = b"\r\n" if b"\r\n" in raw else b"\n"
    normalized = raw.replace(b"\r\n", b"\n")
    old = b"  bool runKolobanovEyeFrame(const char *config_path);"
    addition = old + b"\n  bool runKolobanovEyeBatch(const char *config_path);"
    if normalized.count(addition) == 1:
        pass
    elif normalized.count(old) == 1 and b"runKolobanovEyeBatch" not in normalized:
        updates.append((header, normalized.replace(old, addition, 1), eol))
    else:
        raise ValueError(f"Cannot safely add native batch method to {header}")

    raw = commands.read_bytes()
    eol = b"\r\n" if b"\r\n" in raw else b"\n"
    normalized = raw.replace(b"\r\n", b"\n")
    old = (b'  CONSOLE_CHECK_NAME_EX("kolobanoveye", "frame", 2, 2,'
           b' "Render one compatibility probe frame.", "<config.blk>")\n'
           b'  {\n'
           b'    CONSOLE_COMMAND_BATCH_MODE_RESULT(get_app().runKolobanovEyeFrame(params[0]));\n'
           b'  }\n')
    addition = (b'\n'
                b'  CONSOLE_CHECK_NAME_EX("kolobanoveye", "batch", 2, 2,\n'
                b'    "Generate all selected maps in this DaEditor process.", "<batch.blk>")\n'
                b'  {\n'
                b'    CONSOLE_COMMAND_BATCH_MODE_RESULT(get_app().runKolobanovEyeBatch(params[0]));\n'
                b'  }\n')
    if normalized.count(addition) == 1:
        pass
    elif normalized.count(old) == 1 and b'runKolobanovEyeBatch' not in normalized:
        updates.append((commands, normalized.replace(old, old + addition, 1), eol))
    else:
        raise ValueError(f"Cannot safely register native batch command in {commands}")

    raw = source.read_bytes()
    eol = b"\r\n" if b"\r\n" in raw else b"\n"
    normalized = raw.replace(b"\r\n", b"\n")
    code = b'''// One command per dataset, all maps stay inside the same DaEditor instance.
bool DagorEdAppWindow::runKolobanovEyeBatch(const char *config_path)
{
  DataBlock config;
  if (!config.load(config_path))
  {
    console->addMessage(ILogWriter::ERROR, "[KE] FAIL BATCH_CONFIG: Cannot read map batch");
    return false;
  }
  const int mapCount = config.getInt("map_count", 0);
  if (mapCount < 1 || mapCount > 256)
  {
    console->addMessage(ILogWriter::ERROR, "[KE] FAIL BATCH_CONFIG: Expected 1..256 maps");
    return false;
  }
  console->addMessage(ILogWriter::NOTE, "[KE] MAP_BATCH_BEGIN maps=%d", mapCount);
  for (int index = 0; index < mapCount; ++index)
  {
    char name[32];
    snprintf(name, sizeof(name), "map_%d", index);
    const char *capture = config.getStr(name, "");
    if (!*capture || !dd_file_exist(capture))
    {
      console->addMessage(ILogWriter::ERROR, "[KE] FAIL BATCH_CONFIG: Missing capture for map %d", index + 1);
      return false;
    }
    console->addMessage(ILogWriter::NOTE, "[KE] MAP_TASK_BEGIN index=%d/%d capture=%s", index + 1, mapCount, capture);
    if (!runKolobanovEyeFrame(capture))
    {
      console->addMessage(ILogWriter::ERROR, "[KE] FAIL MAP_TASK index=%d/%d", index + 1, mapCount);
      return false;
    }
    console->addMessage(ILogWriter::NOTE, "[KE] MAP_TASK_OK index=%d/%d", index + 1, mapCount);
  }
  console->addMessage(ILogWriter::NOTE, "[KE] MAP_BATCH_OK maps=%d", mapCount);
  return true;
}
'''
    marker = b"bool DagorEdAppWindow::runKolobanovEyeBatch(const char *config_path)"
    if normalized.count(marker) == 1:
        if b"[KE] MAP_BATCH_OK" not in normalized:
            raise ValueError(f"Partial native batch implementation in {source}")
    elif normalized.count(marker) == 0 and b"bool DagorEdAppWindow::runKolobanovEyeFrame(" in normalized:
        updates.append((source, normalized.rstrip() + b"\n\n" + code, eol))
    else:
        raise ValueError(f"Cannot safely add native batch implementation to {source}")

    for path, data, eol in updates:
        path.write_bytes(data.replace(b"\n", b"\r\n") if eol == b"\r\n" else data)
    if updates:
        print(f"[KE] Installed native single-editor map batch in {len(updates)} Dagor files", flush=True)
    return bool(updates)


def preserve_landmesh_renderer_release_on_reload(source: Path) -> bool:
    """Do not orphan the previous map's LandMeshRenderer on scene reload.

    Upstream loadLevel assigns lmeshRenderer = NULL before clear(), even though
    clear() calls del_it(lmeshRenderer). For a single long-lived editor process,
    that discards the pointer before it can be deleted. Let clear() own teardown.
    """
    contents = source.read_bytes()
    old = b"  lmeshRenderer = NULL;\n  hasWater = false;"
    new = b"  // clear() releases the previous map's lmeshRenderer during a batch reload.\n  hasWater = false;"
    crlf = b"\r\n" in contents
    normalized = contents.replace(b"\r\n", b"\n")
    if normalized.count(new) == 1 and normalized.count(old) == 0:
        return False
    if normalized.count(old) != 1 or normalized.count(new):
        raise ValueError("Ambiguous landmesh renderer reset in AcesScene::loadLevel")
    normalized = normalized.replace(old, new, 1)
    source.write_bytes(normalized.replace(b"\n", b"\r\n") if crlf else normalized)
    print("[KE] Preserving landmesh renderer cleanup across maps", flush=True)
    return True


def reset_level_settings_on_reload(source: Path) -> bool:
    """Avoid carrying the previous map's tankMapCoord0/1 into a later level.

    AcesScene::loadLevel() invokes clear() and reloads levelSettingsBlk, but
    clear() does not clear this DataBlock. An invalid/missing next level BLK must
    not inherit valid tank bounds from the previous map.
    """
    data = source.read_bytes()
    newer = b"  levelFileName = bindump;\n  clear();\n  levelSettingsBlk.clearData();"
    older = b"  levelFileName = bindump;\n  clear();"
    crlf = b"\r\n" in data
    normalized = data.replace(b"\r\n", b"\n")
    if normalized.count(newer) == 1:
        return False
    if normalized.count(older) != 1:
        raise ValueError("Cannot identify unique AcesScene level reload in strmLevel.cpp")
    updated = normalized.replace(older, newer, 1)
    source.write_bytes(updated.replace(b"\n", b"\r\n") if crlf else updated)
    print("[KE] AcesScene level-settings isolation enabled", flush=True)
    return True


def enable_batch_tank_map_output(source: Path) -> bool:
    """Let one editor process save an independent overview for each loaded map.

    The Dagor CLI argv is process-global; a single PNG destination would only
    work for the first level. The new CLI flag names an output DIRECTORY and
    AcesScene derives the output filename from its currently loaded BIN.
    Safe on previously patched Windows checkouts (CRLF preserved).
    """
    data = source.read_bytes()
    if b"ke-tank-map-output-dir" in data:
        if data.count(b"ke-tank-map-output-dir") != 1 or b"keMapOutput.str()" not in data:
            raise ValueError("Partial per-level tank-map output change")
        return False
    old = b'if (const char *output = dgs_get_argv("ke-tank-map-output"); output && *output && !dd_file_exist(output))'
    old_save = b'const bool saved = pixels && save_png32(output, '
    if data.count(old) != 1 or data.count(old_save) != 1:
        raise ValueError("Cannot locate unique ROI output destination in strmLevel.cpp")
    eol = b"\r\n" if b"\r\n" in data else b"\n"
    lines = [
        b'const char *keMapOutputDir = dgs_get_argv("ke-tank-map-output-dir");',
        b'  const char *keMapOutputFile = dgs_get_argv("ke-tank-map-output");',
        b'  const String keMapOutput(keMapOutputDir && *keMapOutputDir',
        b'    ? String(0, "%s/%s-tank.png", keMapOutputDir, dd_get_fname(levelFileName))',
        b'    : String(keMapOutputFile ? keMapOutputFile : ""));',
        b'  if (*keMapOutput.str() && !dd_file_exist(keMapOutput.str()))',
    ]
    updated = data.replace(old, eol.join(lines), 1)
    updated = updated.replace(old_save, b'const bool saved = pixels && save_png32(keMapOutput.str(), ', 1)
    source.write_bytes(updated)
    print("[KE] Enabled per-map overviews for single-editor batch", flush=True)
    return True


def ensure_level_settings_accessor(header):
    """Add the small public accessor without depending on surrounding Dagor patch hunks.

    Older patched Dagor checkouts can have a different strmlevel.h context near
    isInited(); only the stable, unique lmeshMgr declaration is needed here.
    Preserve original CRLF/LF and make repeated builds idempotent.
    """
    data = header.read_bytes()
    getter = b"getKolobanovEyeLevelSettings"
    if getter in data:
        if data.count(getter) != 1:
            raise ValueError("Multiple tank-map accessors in strmlevel.h")
        return False
    marker = b"  LandMeshManager *lmeshMgr;"
    if data.count(marker) != 1 or b"  DataBlock levelSettingsBlk;" not in data:
        raise ValueError("Cannot find unique AcesScene level-settings insertion point in strmlevel.h")
    eol = b"\r\n" if b"\r\n" in data else b"\n"
    insertion = b"  const DataBlock &getKolobanovEyeLevelSettings() const { return levelSettingsBlk; }" + eol + eol
    header.write_bytes(data.replace(marker, insertion + marker, 1))
    print(f"[KE] Inserted tank-map accessor in {header}", flush=True)
    return True


def build(dagor, ooz=None):
    if os.name != "nt":
        raise ValueError("Build requires Windows x64")
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=dagor, text=True).strip()
    if actual != REV:
        raise ValueError(f"Expected Dagor {REV}, got {actual}")
    require_file(dagor / "prog/platform.jam")
    require_file(dagor / "tools/dagor_cdk/commonData/guiShaders.ps40.shdump.bin")
    jam = shutil.which("jam")
    if not jam:
        raise ValueError("jam.exe missing from PATH. Run Dagor make_devtools.py first.")
    # Freeze the decoder once, reusing an already compiled checked source snapshot.
    # The source files are local/gitignored due to missing clear upstream license.
    if ooz is None and not OOZ_VENDOR.is_dir():
        cached = HERE / "build-logs" / "ooz-local"
        if cached.is_dir():
            install_vendor_ooz(cached)
    use_ooz = ooz is not None or OOZ_VENDOR.is_dir()
    if use_ooz and ooz is None:
        verify_vendor_ooz()
    apply_dagor_patches(dagor, with_ooz=use_ooz)

    logs = HERE / "build-logs"
    logs.mkdir(exist_ok=True)
    args = [jam, "-sRoot=../..", "-sPlatformArch=x86_64", "-sConfig=dev"]
    if use_ooz:
        require_file(dagor / BIN / "dumpGrp-dev.exe")
        library = prepare_ooz(ooz, logs)
        args += ["-sUseOoz=yes", f"-sOozLib={dagor_path(library)}"]
    editor_log = logs / ("editor-ooz.log" if use_ooz else "editor.log")
    run_logged([*args, "-f", "sceneTools/daEditorX/jamfile-editor"], editor_log, dagor / "prog/tools")
    if use_ooz:
        if b"Building daKernel with the decode-only ooz backend" not in editor_log.read_bytes():
            raise ValueError(f"Jam did not select the ooz backend. See {editor_log}")
        require_file(dagor / BIN / "daKernel-dev.dll")
    else:
        run_logged([*args, "-f", "sceneTools/dumpGrp/jamfile"], logs / "dumpGrp.log", dagor / "prog/tools")
    require_file(dagor / BIN / "dumpGrp-dev.exe")
    print(f"[KE] BUILD_OK {require_file(dagor / BIN / 'daEditor3x-dev.exe')}")


def camera_orbit_offset(azimuth_deg, horizontal_distance=20.0, height=5.0):
    """Offset relative to the actual world-space model center (Y is up)."""
    a = math.radians(azimuth_deg)
    return (round(horizontal_distance * math.sin(a), 6), height, round(horizontal_distance * math.cos(a), 6))


def plan_camera_orbits(seed, count, distance_range, elevation_range):
    """Sample horizontal distance and elevation relative to the model center."""
    dmin, dmax = distance_range
    emin, emax = elevation_range
    if not all(math.isfinite(v) for v in (dmin, dmax, emin, emax)):
        raise ValueError("Camera ranges must be finite")
    if not (2 <= dmin <= dmax <= 10000):
        raise ValueError("Horizontal camera distance must satisfy 2 <= min <= max <= 10000")
    if not (0 <= emin <= emax <= 80):
        raise ValueError("Camera elevation must satisfy 0 <= min <= max <= 80 degrees")
    rng = random.Random(seed)
    result = []
    for _ in range(count):
        azimuth = round(rng.uniform(-180, 180), 4)
        distance = round(rng.uniform(dmin, dmax), 4)
        elevation = round(rng.uniform(emin, emax), 4)
        height = round(distance * math.tan(math.radians(elevation)), 6)
        result.append({"azimuth_deg": azimuth, "horizontal_distance": distance,
                       "elevation_deg": elevation,
                       "offset": camera_orbit_offset(azimuth, distance, height)})
    return result


def plan_placements(seed, center, radius, attempts):
    if not math.isfinite(radius) or not 0 <= radius <= 10000 or not 1 <= attempts <= 64:
        raise ValueError("Placement radius must be 0–10000; attempts must be 1–64")
    if radius == 0:
        return [(center[0], center[1])]
    rng = random.Random(f"placement:{seed}")
    return [(round(center[0] + radius * math.sqrt(u) * math.cos(a), 5),
             round(center[1] + radius * math.sqrt(u) * math.sin(a), 5))
            for u, a in ((rng.random(), rng.uniform(-math.pi, math.pi)) for _ in range(attempts))]


def plan_compositions(seed, center, radius, attempts, count, frames_per_position):
    if not 1 <= frames_per_position <= 1000 or not 1 <= count <= 1000:
        raise ValueError("Frames and frames per position must be 1–1000")
    scene_count = (count + frames_per_position - 1) // frames_per_position
    return [plan_placements(seed if i == 0 else f"{seed}:scene:{i}", center, radius, attempts)
            for i in range(scene_count)]


def plan_map_compositions(seed, margin_percent, attempts, count, frames_per_position):
    """Sample reproducible normalized X/Z coordinates inside the landscape bounds."""
    if not math.isfinite(margin_percent) or not 0 <= margin_percent < 50:
        raise ValueError("Map edge margin must be between 0 and 50 percent")
    if not 1 <= attempts <= 64 or not 1 <= count <= 1000 or not 1 <= frames_per_position <= 1000:
        raise ValueError("Invalid placement count or attempts")
    rng = random.Random(f"map-placement:{seed}")
    margin = margin_percent / 100
    return [[(round(rng.uniform(margin, 1-margin), 6), round(rng.uniform(margin, 1-margin), 6))
             for _ in range(attempts)] for _ in range((count + frames_per_position - 1) // frames_per_position)]


def select_camera_attempt(raw_metadata, candidates):
    match = re.search(r"^camera_attempt:i=(\d+)\s*$", raw_metadata, re.MULTILINE)
    if not match or int(match.group(1)) >= len(candidates):
        raise ValueError("Missing or invalid camera_attempt in Dagor metadata; rebuild the editor")
    index = int(match.group(1))
    return index, candidates[index]


def plan_articulation(seed, count, turret_range, pitch_range):
    # Separate RNGs keep camera, placement and either joint independent.
    for label, (low, high), (minimum, maximum) in (
            ("turret yaw", turret_range, (-180, 180)), ("gun pitch", pitch_range, (-8, 15))):
        if not all(math.isfinite(x) for x in (low, high)) or not minimum <= low <= high <= maximum:
            raise ValueError(f"Invalid {label} range: expected {minimum} <= min <= max <= {maximum}")
    if tuple(turret_range) == tuple(pitch_range) == (0, 0):
        return []
    if seed is None:
        if turret_range[0] != turret_range[1] or pitch_range[0] != pitch_range[1]:
            raise ValueError("Random turret or gun pose requires --seed")
        return [(turret_range[0], pitch_range[0])] * count
    turret_rng = random.Random(f"turret-yaw:{seed}")
    pitch_rng = random.Random(f"gun-pitch:{seed}")
    return [(round(turret_rng.uniform(*turret_range), 5), round(pitch_rng.uniform(*pitch_range), 5))
            for _ in range(count)]


def read_articulation(metadata, expected):
    angles = []
    for key, angle in zip(("turret_yaw_deg", "gun_pitch_deg"), expected):
        match = re.search(rf"^{key}:r=([^\r\n]+)", metadata, re.MULTILINE)
        if not match or not math.isclose(float(match.group(1)), angle, rel_tol=1e-5, abs_tol=1e-4):
            raise ValueError(f"Missing or mismatched {key} in Dagor metadata; rebuild the editor")
        angles.append(float(match.group(1)))
    return dict(zip(("turret_yaw_deg", "gun_pitch_deg"), angles))


def frame(args, game, cdk):
    map_id = args.level
    if not isinstance(map_id, str) or MAP_ID.fullmatch(map_id) is None:
        raise ValueError("--level must be levels/avg_<name>.bin")
    level = Path(map_id)
    level_file = game / level
    if os.name != "nt":
        raise ValueError("DX11 capture requires Windows x64")
    if not (64 <= args.width <= 4096 and 64 <= args.height <= 4096 and 15 <= args.fov <= 100):
        raise ValueError("Frame sides must be 64–4096 pixels and horizontal FOV 15–100 degrees")
    if not 1 <= args.count <= 1000 or (args.count > 1 and args.seed is None):
        raise ValueError("Sequence count must be 1–1000 and requires --seed for multiple frames")
    if args.count > 1:
        plan_camera_orbits(args.seed, 0, args.camera_distance_range, args.camera_elevation_range)
    if args.camera_height_range:
        low, high = args.camera_height_range
        if not all(math.isfinite(x) for x in (low, high)) or not 0 <= low <= high <= 10000:
            raise ValueError("Camera height above ground range must be between 0 and 10000")
    if not 256 <= args.tank_map_resolution <= 4096 or args.tank_map_resolution & (args.tank_map_resolution - 1):
        raise ValueError("--tank-map-resolution must be a power of two from 256 to 4096")
    if not 8 <= args.min_warmup_renders <= 64:
        raise ValueError("--min-warmup-renders must be between 8 and 64")
    if not 1 <= args.frames_per_position <= 1000:
        raise ValueError("Frames per position must be 1–1000")
    if not 1 <= args.camera_attempts <= 64:
        raise ValueError("Camera attempts must be 1–64")
    if args.count > 1 and args.eye:
        raise ValueError("--eye is for a single frame; sequences choose camera offsets around the tank")
    if not math.isfinite(args.camera_clearance) or not 0 <= args.camera_clearance <= 100:
        raise ValueError("Camera clearance must be finite and between 0 and 100")
    if not math.isfinite(args.minimum_ground_y) or abs(args.minimum_ground_y) > 100000:
        raise ValueError("Minimum ground height must be finite and within ±100000")
    if not math.isfinite(args.max_height_range) or not 0 <= args.max_height_range <= 100:
        raise ValueError("Maximum terrain height range must be 0–100")
    if args.placement_mode in ("whole_map", "tank_map"):
        if args.seed is None:
            raise ValueError("Map search requires --seed")
        candidate_groups = plan_map_compositions(args.seed, args.map_margin_percent, args.placement_attempts, args.count, args.frames_per_position)
    else:
        candidate_groups = plan_compositions(args.seed, args.tank_xz, args.placement_radius,
            args.placement_attempts, args.count, args.frames_per_position)
    if args.placement_radius > 0 and args.seed is None:
        raise ValueError("Placement search requires --seed")
    if args.timeout <= 0:
        raise ValueError("Timeout must be positive")
    poses = plan_articulation(args.seed, args.count, args.turret_yaw_range, args.gun_pitch_range)
    out = args.out.resolve()
    if out.is_relative_to(game) or out.is_relative_to(cdk) or args.dagor.is_relative_to(game) or args.dagor.is_relative_to(cdk):
        raise ValueError("Keep Dagor and the output directory outside War Thunder")
    for path in (out, game, cdk, args.dagor, out / "probe.level.local.blk"):
        dagor_path(path)
    out.mkdir(parents=True, exist_ok=False)

    report = inspect(game, cdk, level)
    (out / "inventory.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if report["errors"]:
        raise ValueError(f"Input check failed. See {out / 'inventory.json'}")
    dump = require_file(args.dagor / BIN / "dumpGrp-dev.exe")
    editor = require_file(args.dagor / BIN / "daEditor3x-dev.exe")
    grp_log = out / "grp.txt"
    run_logged([dump, game / GRP, "-dumpR"], grp_log, out, 60)
    resources = parse_resources(grp_log.read_text(encoding="utf-8", errors="replace"))
    model, skeleton = choose_resources(resources, GRP.stem, args.model, args.skeleton)
    if poses and (model.casefold() != "pzkpfw_vi_ausf_h1_tiger" or skeleton.casefold() != model.casefold() + "_skeleton"):
        raise ValueError("Turret and gun articulation currently require Tiger H1 and its matching skeleton")
    print(f"[KE] DynModel={model}, GeomNodeTree={skeleton or '(unresolved)'}", flush=True)

    project = out / "probe.level.blk"
    project.write_text("lastUniqueId:i=1\n", encoding="ascii")
    (out / "probe.level.local.blk").write_text(
        'viewport0 { currentProjection:t="Perspective"; orthogonalProjection:b=no; wireframe:b=no }\n'
        'local_plugin_data { bin_scene_view { visible:b=no } }\n', encoding="ascii")
    env = out / "environment"
    env.mkdir()
    (env / "environment.plugin.blk").write_text('enviName:t="day"\nweatherType:t="clear"\n', encoding="ascii")

    png, meta = out / "frame.pending.png", out / "frame.pending.blk"
    config = out / "capture.blk"
    entries = [
        f'level:t="{dagor_path(level_file)}"',
        f'map_id:t="{map_id}"',
        f'model:t="{model}"',
        f'expected_grp:t="{GRP.name}"',
        f'skeleton:t="{skeleton}"',
        f'output:t="{dagor_path(png)}"',
        f'metadata:t="{dagor_path(meta)}"',
        f'width:i={args.width}',
        f'height:i={args.height}',
        f'fov:r={numbers([args.fov])}',
        f'camera_clearance:r={numbers([args.camera_clearance])}',
        f'max_height_range:r={numbers([args.max_height_range])}',
        f'minimum_ground_y:r={numbers([args.minimum_ground_y])}',
        f'candidate_count:i={len(candidate_groups[0])}',
        f'placement_whole_map:b={"yes" if args.placement_mode == "whole_map" else "no"}',
        f'placement_tank_map:b={"yes" if args.placement_mode == "tank_map" else "no"}',
        f'frames_per_position:i={args.frames_per_position}',
        f'min_warmup_renders:i={args.min_warmup_renders}',
        f'wait_sky_convergence:b={"yes" if args.wait_sky_convergence else "no"}',
        f'wait_frame_stability:b={"yes" if args.wait_frame_stability else "no"}',
        f'yaw:r={numbers([args.yaw])}',
        f'tank:p3={numbers((args.tank_xz[0], args.ground_offset, args.tank_xz[1]))}',
    ]
    if poses:
        entries.append("articulate_tank:b=yes")
        entries.extend(f"articulation_{i}:p2={numbers(angles)}" for i, angles in enumerate(poses))
    for scene_index, candidates in enumerate(candidate_groups):
        for i, (x, z) in enumerate(candidates):
            entries.append(f'candidate_{scene_index}_{i}:p3={numbers((x, args.ground_offset, z))}')
    if args.seed is not None:
        images = out / "images"
        images.mkdir()
        flat_orbits = plan_camera_orbits(args.seed, args.count * args.camera_attempts, args.camera_distance_range, args.camera_elevation_range)
        if args.camera_height_range:
            hrng = random.Random(f"camera-ground-height:{args.seed}")
            for camera in flat_orbits:
                camera["height_above_ground"] = round(hrng.uniform(*args.camera_height_range), 5)
        orbit_plan = [flat_orbits[i * args.camera_attempts:(i + 1) * args.camera_attempts] for i in range(args.count)]
        (out / "plan.json").write_text(json.dumps({"schema_version": 3, "seed": args.seed, "map_id": map_id,
            "tank_yaw_deg": args.yaw, "placement_mode": args.placement_mode, "map_margin_percent": args.map_margin_percent if args.placement_mode in ("whole_map", "tank_map") else None, "placement_candidate_coordinates": "normalized_tank_map" if args.placement_mode == "tank_map" else "normalized_landscape_bbox" if args.placement_mode == "whole_map" else "world_xz", "placement_candidates_xz": candidate_groups, "frames_per_position": args.frames_per_position, "max_height_range": args.max_height_range,
            "camera_distance_range": args.camera_distance_range, "camera_elevation_range": args.camera_elevation_range,
            "camera_attempts": args.camera_attempts, "camera_height_range": args.camera_height_range, "cameras": orbit_plan,
            **({"tank_articulation_deg": poses} if poses else {})}, indent=2) + "\n", encoding="utf-8")
        journal = out / JOURNAL
        entries.extend((f"frame_count:i={args.count}", f"camera_attempts:i={args.camera_attempts}",
                        f'metadata_journal:t="{dagor_path(journal)}"'))
        for i, camera_candidates in enumerate(orbit_plan):
            for j, camera in enumerate(camera_candidates):
                entries.append(f"camera_offset_{i}_{j}:p3={numbers(camera['offset'])}")
                if "height_above_ground" in camera:
                    entries.append(f"camera_height_{i}_{j}:r={numbers([camera['height_above_ground']])}")
            entries.append(f'output_{i}:t="{dagor_path(images / f"{i:06d}.png")}"')
    if args.eye:
        entries.append(f'eye:p3={numbers(args.eye)}')
    render_debug = args.render_debug or args.indirection == "cpu" or args.dx11_trace
    if render_debug:
        debug_dir = out / "render-debug"
        for i in range(args.count):
            frame_dir = debug_dir / f"{i:06d}"
            frame_dir.mkdir(parents=True)
            entries.append(f'render_debug_{i}:t="{dagor_path(frame_dir)}"')
    config.write_text("\n".join(entries) + "\n", encoding="ascii")
    batch = out / "probe.dcmd"
    batch.write_text(f"wait.frame 8\nkolobanoveye.frame {dagor_path(config)}\n" + ("" if args.keep_open else "exit no\n"), encoding="ascii")

    # Use a named workspace to avoid duplicate matches for the same application.blk.
    local = editor.parent.parent / ".local"
    local.mkdir(exist_ok=True)
    workspace = local / "workspace.blk"
    if not workspace.is_file():
        workspace.write_text("// Workspaces\n", encoding="ascii")
    workspace_name = "KolobanovEye-" + hashlib.sha256(dagor_path(cdk).lower().encode()).hexdigest()[:12]
    if workspace_name.encode() not in workspace.read_bytes():
        with workspace.open("ab") as stream:
            stream.write(
                f'\nworkspace {{\n name:t="{workspace_name}"\n application_dir:t="{dagor_path(cdk)}/"\n'
                f' application_path:t="{dagor_path(cdk / "application.blk")}"\n}}\n'.encode("ascii"))
    command = [editor, "-quiet", "-drv:dx11", "-no_src_assets", "-no_dabuild", "-async_batch", "-kolobanoveye-probe",
               f"-rootdir:{dagor_path(out)}", f"-addpath:{dagor_path(game)}", f"-addpath:{dagor_path(cdk)}",
               "-enable:Scene view", "-disable:Landscape", r"-exclude_dll_re:heightmapLand-aces-dev\.dll$",
               f"-ws:{workspace_name}", project, batch]
    tank_map_pending = out / "tank-map.pending.png" if args.capture_tank_map else None
    if tank_map_pending is not None:
        (out / "maps").mkdir(exist_ok=True)
        command.insert(-2, f"-ke-tank-map-output:{dagor_path(tank_map_pending)}")
        command.insert(-2, f"-ke-tank-map-size:{args.tank_map_resolution}")
    map_capture = None
    if args.capture_last_clip:
        map_image = out / "maps" / f"{level.stem}.png"
        stamp = level_file.stat()
        cache_key = hashlib.sha256(
            f"{level_file.resolve()}|{stamp.st_size}|{stamp.st_mtime_ns}".encode("utf-8")
        ).hexdigest()[:16]
        map_cache = (out.parent / ".ke-map-cache" / f"{level.stem}-{cache_key}.png").resolve()
        if map_cache.is_relative_to(game) or map_cache.is_relative_to(cdk):
            raise ValueError("Keep the maps cache outside War Thunder")
        map_image.parent.mkdir()
        if not map_cache.is_file():
            map_cache.parent.mkdir(exist_ok=True)
            map_capture = out / "terrain-last-clip.pending.png"
            command.insert(-2, f"-ke-last-clip-output:{dagor_path(map_capture)}")
    if args.keep_open:
        command.remove("-quiet")
        print("[KE] Editor remains open. Close its window to finalize the frame and report.", flush=True)
    if args.clipmap_abi == "legacy-color":
        command.insert(-2, "-ke-clipmap-legacy-color")
    if args.clipmap_feedback == "software":
        command.insert(-2, "-ke-clipmap-software")
    if args.indirection == "cpu":
        command.insert(-2, "-ke-indirection-cpu")
    if args.dx11_trace:
        command.insert(-2, "-ke-dx11-trace")
    if render_debug:
        command.insert(-2, f"-ke-render-debug:{dagor_path(debug_dir)}")
    if args.renderdoc_cli:
        if not args.keep_open:
            raise ValueError("--renderdoc-cli requires --keep-open")
        cli = require_file(args.renderdoc_cli.resolve())
        command = [cli, "capture", "--wait-for-exit", "--capture-file", str(out / "gpu-capture"), *command]
        print("[KE] RenderDoc active: focus the editor and press its configured capture hotkey; close the editor afterward.", flush=True)
    try:
        run_logged(command, out / "host-stdout.log", out, None if args.keep_open else args.timeout)
    except (ValueError, subprocess.TimeoutExpired) as exc:
        raise ValueError(editor_failure(out, str(exc))) from exc
    if map_capture is not None:
        if map_capture.is_file() and map_capture.stat().st_size >= 45:
            with map_capture.open("rb") as image:
                signature = image.read(8)
                image.seek(-12, os.SEEK_END)
                trailer = image.read(12)
            if signature != b"\x89PNG\r\n\x1a\n" or trailer != b"\x00\x00\x00\x00IEND\xaeB`\x82":
                print(f"[KE] Map capture incomplete: {map_capture}", flush=True)
                map_capture = None
        else:
            print("[KE] Map capture missing; check Dagor build and RENDER_DEBUG terrain_last_clip in the engine log.", flush=True)
            map_capture = None
        if map_capture is not None:
            try:
                # On Windows rename publishes the completed PNG without overwriting a concurrent capture.
                map_capture.rename(map_cache)
            except FileExistsError:
                map_capture.unlink()
    if args.capture_last_clip and map_cache.is_file():
        try:
            os.link(map_cache, map_image)
        except OSError:
            shutil.copyfile(map_cache, map_image)
        print(f"[KE] Map available at {map_image}", flush=True)
    error_log = out / "probe.dcmd.err"
    if error_log.is_file():
        lines = error_log.read_text(encoding="utf-8", errors="replace").splitlines()
        failure = next((line.strip() for line in reversed(lines) if "[KE] FAIL " in line), "Dagor reported errors.")
        streaming = next((line.strip() for line in reversed(lines) if "[KE] STREAMING_PUMP " in line), "")
        if "FAIL STREAMING:" in failure:
            serial = next((line.strip() for line in reversed(lines) if "[KE] STREAMING_SERIAL " in line), "")
            failure = "\n".join(line for line in (failure, serial, streaming) if line)
        raise ValueError(editor_failure(out, f"{failure}\nSee {error_log}."))
    log_path = out / "probe.dcmd.log"
    if args.keep_open and not log_path.is_file():
        debug_logs = sorted((out / ".logs").glob("daEditor3x-dev.exe-*/debug"), key=lambda p: p.stat().st_mtime)
        log_path = debug_logs[-1] if debug_logs else log_path
    log = require_file(log_path).read_text(encoding="utf-8", errors="replace")
    if args.wait_frame_stability and len(re.findall(
            r"\[KE\] FRAME_STABILITY_FINAL enabled=1 stable=1\b", log)) != args.count:
        raise ValueError("Missing frame stability confirmation; check the capture log and rebuild Dagor")
    if render_debug and log.count("[KE] RENDER_DEBUG_FRAME ") != args.count:
        raise ValueError("Missing per-frame render diagnostics; rebuild Dagor and check the capture log")
    if "[KE] WATER_GROUND_FILTER " not in log:
        raise ValueError("Water ground filter missing: rebuild Dagor with current code")
    if args.placement_mode in ("whole_map", "tank_map") and "[KE] MAP_BOUNDS " not in log:
        raise ValueError("Whole-map mode requires the updated Dagor build; rebuild the editor")
    bounds_report = parse_tank_bounds_log(log, map_id)
    (out / "tank-map-bounds.json").write_text(
        json.dumps(bounds_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[KE] TANK_MAP {bounds_report['status']}: {out / 'tank-map-bounds.json'}", flush=True)
    if tank_map_pending is not None:
        if bounds_report['status'] != "available":
            raise ValueError("Cannot render tank overview without valid tankMapCoord0/1")
        if not tank_map_pending.is_file():
            raise ValueError("Dagor did not save the tank-square overview: check TANK_MAP_RENDER in .logs/debug")
        verify_png(tank_map_pending, args.tank_map_resolution, args.tank_map_resolution)
        overview = out / "maps" / f"{level.stem}-tank.png"
        tank_map_pending.replace(overview)
        (out / "maps" / f"{level.stem}-tank.json").write_text(
            json.dumps({**bounds_report, "image": overview.name,
                        "resolution": args.tank_map_resolution,
                        "orientation": "verify_in_editor",
                        "render_method": "Dagor.render_last_clip_region_to_tex"},
                       indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[KE] TANK_MAP_IMAGE {overview}", flush=True)
    if args.placement_mode == "tank_map" and bounds_report["status"] != "available":
        raise ValueError("Tank placement requires valid tankMapCoord0/1")
    for marker in ("MAP_OK", "MODEL_OK", "PLACEMENT_OK", "WATER_PROXY", "CAMERA_TERRAIN", "SUPPORT_HEIGHTS", "CAMERA_OK", "FRAME_OK"):
        if f"[KE] {marker}" not in log:
            raise ValueError(f"{marker} missing from probe.dcmd.log")
    if args.seed is not None:
        if f"[KE] SEQUENCE_OK frames={args.count}" not in log or log.count("[KE] FRAME_OK ") != args.count or log.count("[KE] ORBIT_FRAME ") != args.count:
            raise ValueError("Sequence incomplete or old editor build: check SEQUENCE_OK and FRAME_OK in logs")
        if not journal.is_file():
            raise ValueError("Frame journal missing; rebuild Dagor with current code")
        metadata = read_journal(journal)
        if set(metadata) != set(range(args.count)):
            raise ValueError("Frame journal does not contain all frames")
        records = []
        for i, camera_candidates in enumerate(orbit_plan):
            image_path = images / f"{i:06d}.png"
            verify_png(image_path, args.width, args.height)
            raw_metadata = metadata[i]
            annotation = parse_annotation(raw_metadata)
            camera_attempt, camera = select_camera_attempt(raw_metadata, camera_candidates)
            composition_match = re.search(r"^composition_index:i=(\d+)\s*$", raw_metadata, re.MULTILINE)
            expected_composition = i // args.frames_per_position
            if not composition_match or int(composition_match.group(1)) != expected_composition:
                raise ValueError("Missing or invalid composition_index in Dagor metadata; rebuild the editor")
            records.append({"map_id": map_id, "seed": args.seed, "frame_index": i, "composition_index": expected_composition, "tank_yaw_deg": args.yaw,
                "camera_azimuth_deg": camera["azimuth_deg"], "camera_horizontal_distance": camera["horizontal_distance"],
                "planned_camera_elevation_deg": camera["elevation_deg"] if args.camera_height_range is None else None,
                "planned_camera_offset": camera["offset"], "planned_height_above_ground": camera.get("height_above_ground"), "camera_attempt": camera_attempt,
                "image": f"images/{i:06d}.png", "annotation": annotation["annotations"][0],
                "dagor_frame_metadata_blk": raw_metadata, **(read_articulation(raw_metadata, poses[i]) if poses else {})})
        manifest_pending = out / "metadata.jsonl.tmp"
        manifest_pending.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in records), encoding="utf-8")
        manifest_pending.replace(out / "metadata.jsonl")
        journal.unlink()
        print(f"[KE] SEQUENCE_WRITTEN {len(records)} frames in {out}")
        return
    verify_png(png, args.width, args.height)
    if poses:
        read_articulation(require_file(meta).read_text(encoding="utf-8"), poses[0])
    write_annotation(require_file(meta), out / "annotation.json")
    require_file(meta).rename(out / "frame.blk")
    final = png.rename(out / "frame.png")
    print(f"[KE] FRAME_WRITTEN {final}  SHA256={hashlib.sha256(final.read_bytes()).hexdigest()}")
    if render_debug:
        print(f"[KE] Render diagnostics: {debug_dir / '000000'}")
        for name in ("static-albedo.png", "static-depth-mask.png"):
            if not (debug_dir / "000000" / name).is_file():
                print(f"[KE] Diagnostic image missing: {name}. See RENDER_DEBUG in .logs.")
    print("[KE] Visual verification is still required.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build_cmd, inspect_cmd, frame_cmd = (commands.add_parser(name) for name in ("build", "inspect", "frame"))
    ooz_cmd = commands.add_parser("probe-ooz", help="Decode Normandy's embedded lightmap with a local decoder")
    ooz_cmd.add_argument("--game", type=Path, default=Path(r"D:\Games\WarThunder"))
    decoders = ooz_cmd.add_mutually_exclusive_group(required=True)
    decoders.add_argument("--ooz-exe", type=Path, help="powzix/ooz executable")
    decoders.add_argument("--unoodle-exe", type=Path, help="oozextract unoodle executable")
    build_cmd.add_argument("--ooz", type=lambda path: Path(path).resolve(), help="Local powzix/ooz checkout for decode-only daKernel")
    for command in (build_cmd, frame_cmd):
        command.add_argument("--dagor", type=lambda path: Path(path).resolve(), required=True)
    for command in (inspect_cmd, frame_cmd):
        command.add_argument("--game", type=Path, default=Path(r"D:\Games\WarThunder"))
        command.add_argument("--cdk", type=Path, help="Default is <game>/WarThunderCDK")
    frame_cmd.add_argument("--out", type=Path, required=True, help="A new directory outside the game")
    for map_parser in (inspect_cmd, frame_cmd):
        map_parser.add_argument("--level", type=str, default=LEVEL.as_posix(),
                                help="Installed ground map ID: levels/avg_*.bin")
    frame_cmd.add_argument("--model")
    frame_cmd.add_argument("--skeleton")
    frame_cmd.add_argument("--tank-xz", nargs=2, type=float, default=(0, 0))
    frame_cmd.add_argument("--ground-offset", type=float, default=0.05)
    frame_cmd.add_argument("--eye", nargs=3, type=float)
    frame_cmd.add_argument("--yaw", type=float, default=20)
    frame_cmd.add_argument("--count", type=int, default=1, help="Experimental single-process sequence count")
    frame_cmd.add_argument("--seed", type=int, help="Seed for the sequence camera angles and distances")
    frame_cmd.add_argument("--fov", type=float, default=55)
    frame_cmd.add_argument("--frames-per-position", type=int, default=6, help="Number of camera views per selected tank position")
    frame_cmd.add_argument("--camera-height-range", nargs=2, type=float, metavar=("MIN", "MAX"), help="Actual camera height above local heightmap for sequences")
    frame_cmd.add_argument("--camera-attempts", type=int, default=8, help="Maximum camera candidates per frame")
    frame_cmd.add_argument("--placement-mode", choices=("around_point", "whole_map", "tank_map"), default="around_point")
    frame_cmd.add_argument("--map-margin-percent", type=float, default=15.0, help="Margin on each landscape edge in percent, 0 <= value < 50")
    frame_cmd.add_argument("--placement-radius", type=float, default=0, help="Search radius around tank X/Z; 0 keeps fixed position")
    frame_cmd.add_argument("--placement-attempts", type=int, default=24, help="Up to 64 heightmap candidates")
    frame_cmd.add_argument("--minimum-ground-y", type=float, default=0.5, help="Reject terrain below this world Y (Normandy sea-level proxy)")
    frame_cmd.add_argument("--max-height-range", type=float, default=0.5, help="Allowed heightmap range across AABB corners")
    frame_cmd.add_argument("--camera-clearance", type=float, default=0.5, help="Minimum height above heightmap; not obstacle checking")
    frame_cmd.add_argument("--camera-distance-range", nargs=2, type=float, default=(18, 35), metavar=("MIN", "MAX"),
                           help="Horizontal distance from tank center for sequences (default: 18 35)")
    frame_cmd.add_argument("--camera-elevation-range", nargs=2, type=float, default=(10, 25), metavar=("MIN", "MAX"),
                           help="Elevation in degrees above tank center for sequences (default: 10 25)")
    frame_cmd.add_argument("--width", type=int, default=1280)
    frame_cmd.add_argument("--height", type=int, default=720)
    frame_cmd.add_argument("--renderdoc-cli", type=Path, help="renderdoccmd.exe; manual GPU capture requires --keep-open")
    frame_cmd.add_argument("--capture-tank-map", action="store_true", help="Render the selected tank square into a separate PNG")
    frame_cmd.add_argument("--tank-map-resolution", type=int, default=1024)
    frame_cmd.add_argument("--capture-last-clip", action="store_true", help="Save the overview in the run maps directory and reuse cached pixels")
    frame_cmd.add_argument("--keep-open", action="store_true", help="Keep editor open until window is closed; no process timeout")
    frame_cmd.add_argument("--timeout", type=int, default=360)
    frame_cmd.add_argument("--dx11-trace", action="store_true", help="Log native DX11 draw state for terrain and clipmap updates")
    frame_cmd.add_argument("--turret-yaw-range", type=float, nargs=2, default=(0, 0), metavar=("MIN", "MAX"),
                           help="Tiger H1 turret yaw relative to hull, degrees")
    frame_cmd.add_argument("--gun-pitch-range", type=float, nargs=2, default=(0, 0), metavar=("MIN", "MAX"),
                           help="Tiger H1 gun pitch relative to neutral pose, positive raises muzzle")
    frame_cmd.add_argument("--min-warmup-renders", type=int, default=8,
                           help="Minimum offscreen renders before checking frame readiness (8–64)")
    frame_cmd.add_argument("--wait-sky-convergence", action="store_true",
                           help="Require sky lighting convergence even without adaptive RGB sampling")
    frame_cmd.add_argument("--wait-frame-stability", action=argparse.BooleanOptionalAction, default=True,
                           help="Wait for stable RGB samples before saving (enabled by default)")
    frame_cmd.add_argument("--render-debug", action="store_true", help="Export terrain and static G-buffer diagnostics")
    frame_cmd.add_argument("--clipmap-abi", choices=("legacy-color", "native-int"), default="legacy-color",
                           help="Clipmap table encoding required by the loaded CDK shaders")
    frame_cmd.add_argument("--clipmap-feedback", choices=("hardware", "software"), default="hardware",
                           help="Compute terrain tile requests on the GPU or CPU")
    frame_cmd.add_argument("--indirection", choices=("gpu", "cpu"), default="gpu",
                           help="Diagnostic CPU table upload after GPU check; cpu enables render-debug")
    args = parser.parse_args()
    try:
        if args.command == "build":
            build(args.dagor, args.ooz)
        elif args.command == "probe-ooz":
            probe_ooz(args.game.resolve(), args.unoodle_exe or args.ooz_exe, unoodle=args.unoodle_exe is not None)
        else:
            game = args.game.resolve()
            cdk = args.cdk.resolve() if args.cdk else (game / "WarThunderCDK").resolve()
            if args.command == "inspect":
                if MAP_ID.fullmatch(args.level) is None:
                    raise ValueError("--level must be levels/avg_<name>.bin")
                report = inspect(game, cdk, Path(args.level))
                print(json.dumps(report, indent=2, ensure_ascii=False))
                return int(bool(report["errors"]))
            frame(args, game, cdk)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"[KE] ERROR {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
