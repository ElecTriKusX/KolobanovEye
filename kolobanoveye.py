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

REV = "9639942de5a8e5b01d91977f60ed82cda22d5f41"
HERE = Path(__file__).resolve().parent
PATCHES = (
    HERE / "patches/dagor-phase0.patch",
    HERE / "patches/dagor-editor-unused-parameters.patch",
)
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


def build(dagor):
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
    for patch in PATCHES:
        if subprocess.run([*apply, "--reverse", "--check", patch], cwd=dagor, capture_output=True).returncode:
            subprocess.run([*apply, "--check", patch], cwd=dagor, check=True)
            subprocess.run([*apply, patch], cwd=dagor, check=True)

    logs = HERE / "build-logs"
    logs.mkdir(exist_ok=True)
    for target, jamfile in (("editor", "sceneTools/daEditorX/jamfile-editor"), ("dumpGrp", "sceneTools/dumpGrp/jamfile")):
        args = [jam, "-sRoot=../..", "-sPlatformArch=x86_64", "-sConfig=dev", "-f", jamfile]
        run_logged(args, logs / f"{target}.log", dagor / "prog/tools")
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
            build(args.dagor)
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
