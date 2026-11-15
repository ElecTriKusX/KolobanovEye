"""Build, inspect and run the KolobanovEye 2.0 single-frame probe."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile

REV = "9639942de5a8e5b01d91977f60ed82cda22d5f41"
HERE = Path(__file__).resolve().parent
PATCHES = (
    HERE / "patches/dagor-phase0.patch",
    HERE / "patches/dagor-editor-unused-parameters.patch",
)
OOZ_REV = "05038060aa68f9187ae9923b2388ca8db40e58d1"
OOZ_PATCH = HERE / "patches/dagor-ooz.patch"
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


def inspect(game, cdk):
    required = {
        game / LEVEL: level_info,
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


def run_logged(command, log, cwd, timeout=None):
    print(f"[KE] {log.name}  {subprocess.list2cmdline(list(map(str, command)))}", flush=True)
    with log.open("wb") as stream:
        run = subprocess.run(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
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
        meta["large_quanta"] = inspect_large_oodle_quanta(data, meta["expected_bytes"])
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
        if meta.get("large_quanta", {}).get("packed_bytes_left", 0):
            raise ValueError("Oodle stream contains bytes after the last declared quantum")
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
    (work / "kraken.cpp").write_bytes(source)


def prepare_ooz(ooz, logs):
    cl, lib = shutil.which("cl.exe"), shutil.which("lib.exe")
    if not cl or not lib or os.environ.get("VSCMD_ARG_TGT_ARCH") != "x64":
        raise ValueError("Run build --ooz from Developer PowerShell configured for x64")
    work = logs / "ooz-local"
    dagor_path(work)
    work.mkdir(exist_ok=True)
    stage_ooz_sources(ooz, work)
    run_logged([cl, "/nologo", "/c", "/O2", "/MT", "/Zl", "kraken.cpp", "lzna.cpp", "bitknit.cpp"],
               logs / "ooz-compile.log", work)
    library = work / "ke-ooz.lib"
    run_logged([lib, "/nologo", "/machine:x64", "/out:ke-ooz.lib", "kraken.obj", "lzna.obj", "bitknit.obj"],
               logs / "ooz-lib.log", work)
    return require_file(library)


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
    apply = ["git", "apply", "--whitespace=error-all"]
    patches = (*PATCHES, OOZ_PATCH) if ooz else PATCHES
    for patch in patches:
        if subprocess.run([*apply, "--reverse", "--check", patch], cwd=dagor, capture_output=True).returncode:
            subprocess.run([*apply, "--check", patch], cwd=dagor, check=True)
            subprocess.run([*apply, patch], cwd=dagor, check=True)

    logs = HERE / "build-logs"
    logs.mkdir(exist_ok=True)
    args = [jam, "-sRoot=../..", "-sPlatformArch=x86_64", "-sConfig=dev"]
    if ooz:
        require_file(dagor / BIN / "dumpGrp-dev.exe")
        library = prepare_ooz(ooz, logs)
        args += ["-sUseOoz=yes", f"-sOozLib={dagor_path(library)}"]
    editor_log = logs / ("editor-ooz.log" if ooz else "editor.log")
    run_logged([*args, "-f", "sceneTools/daEditorX/jamfile-editor"], editor_log, dagor / "prog/tools")
    if ooz:
        if b"Building daKernel with the decode-only ooz backend" not in editor_log.read_bytes():
            raise ValueError(f"Jam did not select the ooz backend. See {editor_log}")
        require_file(dagor / BIN / "daKernel-dev.dll")
    else:
        run_logged([*args, "-f", "sceneTools/dumpGrp/jamfile"], logs / "dumpGrp.log", dagor / "prog/tools")
    require_file(dagor / BIN / "dumpGrp-dev.exe")
    print(f"[KE] BUILD_OK {require_file(dagor / BIN / 'daEditor3x-dev.exe')}")


def frame(args, game, cdk):
    if os.name != "nt":
        raise ValueError("DX11 capture requires Windows x64")
    if not (64 <= args.width <= 4096 and 64 <= args.height <= 4096 and 15 <= args.fov <= 100):
        raise ValueError("Frame sides must be 64–4096 pixels and horizontal FOV 15–100 degrees")
    if args.timeout <= 0:
        raise ValueError("Timeout must be positive")
    out = args.out.resolve()
    if out.is_relative_to(game) or out.is_relative_to(cdk) or args.dagor.is_relative_to(game) or args.dagor.is_relative_to(cdk):
        raise ValueError("Keep Dagor and the output directory outside War Thunder")
    for path in (out, game, cdk, args.dagor, out / "probe.level.local.blk"):
        dagor_path(path)
    out.mkdir(parents=True, exist_ok=False)

    report = inspect(game, cdk)
    (out / "inventory.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if report["errors"]:
        raise ValueError(f"Input check failed. See {out / 'inventory.json'}")
    dump = require_file(args.dagor / BIN / "dumpGrp-dev.exe")
    editor = require_file(args.dagor / BIN / "daEditor3x-dev.exe")
    grp_log = out / "grp.txt"
    run_logged([dump, game / GRP, "-dumpR"], grp_log, out, 60)
    resources = parse_resources(grp_log.read_text(encoding="utf-8", errors="replace"))
    model, skeleton = choose_resources(resources, GRP.stem, args.model, args.skeleton)
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
        f'level:t="{dagor_path(game / LEVEL)}"',
        f'model:t="{model}"',
        f'expected_grp:t="{GRP.name}"',
        f'skeleton:t="{skeleton}"',
        f'output:t="{dagor_path(png)}"',
        f'metadata:t="{dagor_path(meta)}"',
        f'width:i={args.width}',
        f'height:i={args.height}',
        f'fov:r={numbers([args.fov])}',
        f'yaw:r={numbers([args.yaw])}',
        f'tank:p3={numbers((args.tank_xz[0], args.ground_offset, args.tank_xz[1]))}',
    ]
    if args.eye:
        entries.append(f'eye:p3={numbers(args.eye)}')
    config.write_text("\n".join(entries) + "\n", encoding="ascii")
    batch = out / "probe.dcmd"
    batch.write_text(f"wait.frame 8\nkolobanoveye.frame {dagor_path(config)}\nexit no\n", encoding="ascii")

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
               "-enable:Scene view", "-disable:Landscape", f"-ws:{workspace_name}", project, batch]
    try:
        run_logged(command, out / "host-stdout.log", out, args.timeout)
    except (ValueError, subprocess.TimeoutExpired) as exc:
        raise ValueError(editor_failure(out, str(exc))) from exc
    error_log = out / "probe.dcmd.err"
    if error_log.is_file():
        raise ValueError(editor_failure(out, f"Dagor reported errors. See {error_log}."))
    log = require_file(out / "probe.dcmd.log").read_text(encoding="utf-8", errors="replace")
    for marker in ("MAP_OK", "MODEL_OK", "CAMERA_OK", "FRAME_OK"):
        if f"[KE] {marker}" not in log:
            raise ValueError(f"{marker} missing from probe.dcmd.log")
    verify_png(png, args.width, args.height)
    require_file(meta).rename(out / "frame.blk")
    final = png.rename(out / "frame.png")
    print(f"[KE] FRAME_WRITTEN {final}  SHA256={hashlib.sha256(final.read_bytes()).hexdigest()}")
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
    frame_cmd.add_argument("--model")
    frame_cmd.add_argument("--skeleton")
    frame_cmd.add_argument("--tank-xz", nargs=2, type=float, default=(0, 0))
    frame_cmd.add_argument("--ground-offset", type=float, default=0.05)
    frame_cmd.add_argument("--eye", nargs=3, type=float)
    frame_cmd.add_argument("--yaw", type=float, default=20)
    frame_cmd.add_argument("--fov", type=float, default=55)
    frame_cmd.add_argument("--width", type=int, default=1280)
    frame_cmd.add_argument("--height", type=int, default=720)
    frame_cmd.add_argument("--timeout", type=int, default=360)
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
                report = inspect(game, cdk)
                print(json.dumps(report, indent=2, ensure_ascii=False))
                return int(bool(report["errors"]))
            frame(args, game, cdk)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"[KE] ERROR {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
