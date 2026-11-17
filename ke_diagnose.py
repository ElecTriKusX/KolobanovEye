"""Collect and analyze a KolobanovEye frame run without touching game assets.

This is a log/image analyzer, not a graphics API debugger: global shader values
are not proof of bindings at an individual draw call.
"""
import argparse
import hashlib
import json
import platform
import re
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

VERSION = 1
LOG_LIMIT = 32 * 1024 * 1024


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def image_stats(path):
    """Analyze non-interlaced 8-bit PNG, without external dependencies."""
    data = path.read_bytes()
    if len(data) > 100 * 1024 * 1024 or data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('unsupported/oversized PNG')
    pos, parts, header = 8, [], None
    while pos + 12 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], 'big')
        end = pos + 12 + length
        if end > len(data):
            raise ValueError('truncated PNG chunk')
        kind, payload = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        if zlib.crc32(kind + payload) & 0xffffffff != int.from_bytes(data[pos + 8 + length:end], 'big'):
            raise ValueError('bad PNG CRC')
        if kind == b'IHDR':
            header = struct.unpack('>IIBBBBB', payload)
        elif kind == b'IDAT':
            parts.append(payload)
        elif kind == b'IEND':
            break
        pos = end
    if not header:
        raise ValueError('PNG has no IHDR')
    w, h, depth, color, compression, filtering, interlace = header
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
    if not channels or depth != 8 or compression or filtering or interlace or not (0 < w <= 8192 and 0 < h <= 8192):
        raise ValueError('PNG format not supported for pixel statistics')
    stride = w * channels
    expected = (stride + 1) * h
    if expected > 150 * 1024 * 1024:
        raise ValueError('PNG uncompressed size exceeds limit')
    decoder = zlib.decompressobj()
    raw = decoder.decompress(b''.join(parts), expected + 1)
    if len(raw) != expected or not decoder.eof or decoder.unused_data:
        raise ValueError('invalid PNG image data')
    prev = bytearray(stride)
    black = near = 0
    for y in range(h):
        off = y * (stride + 1)
        mode = raw[off]
        row = bytearray(raw[off + 1:off + 1 + stride])
        if mode > 4:
            raise ValueError('invalid PNG filter')
        for i in range(stride):
            a = row[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if mode == 1:
                row[i] = (row[i] + a) & 255
            elif mode == 2:
                row[i] = (row[i] + b) & 255
            elif mode == 3:
                row[i] = (row[i] + ((a + b) // 2)) & 255
            elif mode == 4:
                p = a + b - c
                pa, pb, pc = abs(p-a), abs(p-b), abs(p-c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                row[i] = (row[i] + pred) & 255
        for x in range(0, stride, channels):
            rgb = row[x:x + (1 if color in (0, 4) else 3)]
            m = max(rgb)
            black += m == 0
            near += m <= 5
        prev = row
    return {'width': w, 'height': h, 'black_pixels': black, 'near_black_pixels': near,
            'pixels': w * h, 'note': 'Whole image; includes sky and objects, not a terrain mask.'}


def find_lines(text, key):
    return [line.strip() for line in text.splitlines() if key in line]


def latest_debug(run):
    candidates = list((run / '.logs').rglob('debug')) if (run / '.logs').is_dir() else []
    return max(candidates, key=lambda p: p.stat().st_mtime_ns) if candidates else None


def analyze(run, dagor=None, game=None):
    checks = []
    def add(name, status, detail, evidence=None):
        checks.append({'name': name, 'status': status, 'detail': detail, 'evidence': evidence or []})
    debug = latest_debug(run)
    text = ''
    if debug:
        with debug.open('rb') as f:
            data = f.read(LOG_LIMIT + 1)
        text = data[:LOG_LIMIT].decode('utf-8', 'replace')
        add('engine_log', 'warning' if len(data) > LOG_LIMIT else 'ok',
            'Latest engine debug log; truncated for analysis.' if len(data) > LOG_LIMIT else 'Latest engine debug log found.', [str(debug.relative_to(run))])
    else:
        add('engine_log', 'unknown', 'No engine debug log found.')
    batch = run / 'probe.dcmd.log'
    batch_text = batch.read_text(encoding='utf-8', errors='replace') if batch.is_file() and batch.stat().st_size <= LOG_LIMIT else ''
    for marker in ('MAP_OK', 'MODEL_OK', 'CAMERA_OK', 'FRAME_OK'):
        lines = find_lines(batch_text, '[KE] ' + marker) or find_lines(text, '[KE] ' + marker)
        add(marker.lower(), 'ok' if lines else 'unknown', 'Marker found.' if lines else 'Marker missing; inspect host and batch logs.', lines[:2])
    frame = run / 'frame.png'
    add('frame_file', 'ok' if frame.is_file() else 'unknown', 'Frame file exists; image validity is checked separately.' if frame.is_file() else 'No completed frame found.')
    for stage in ('render_buffer', 'cache'):
        lines = find_lines(text, 'CLIPMAP_TILE ' + stage)
        match = re.search(r'status=(\w+).*?nonblack=(-?\d+) pixels=(\d+)', lines[-1]) if lines else None
        status = ('ok' if match and match.group(1) == 'ok' and int(match.group(2)) > 0 else 'warning') if lines else 'unknown'
        add('clipmap_tile_' + stage, status, 'One sampled tile only; nonblack does not prove correctness.' if lines else 'No tile readback.', lines[-1:])
    for source in ('gpu', 'cpu_upload'):
        lines = find_lines(text, 'INDIRECTION_CHECK source=' + source)
        m = re.search(r'entries=(\d+) mismatches=(\d+)(?: zeros=(\d+))?', lines[-1]) if lines else None
        status = ('ok' if int(m.group(2)) == 0 else 'error') if m else 'unknown'
        add('indirection_' + source, status, 'Expected-versus-readback immediately after ' + source + '.' if m else 'No comparable readback.', lines[-1:])
    lines = find_lines(text, 'INDIRECTION_CPU_UPLOAD')
    add('cpu_upload', ('ok' if 'success=1' in lines[-1] else 'error') if lines else 'unknown',
        'CPU upload is an opt-in diagnostic override, not a validated production fix.', lines[-1:])
    lines = find_lines(text, 'TERRAIN_BIND indirection_readback')
    add('indirection_before_draw', ('ok' if 'zeros=0' in lines[-1] else 'warning') if lines else 'unknown',
        'Counts zeros and first value only; not a full comparison at draw time.', lines[-1:])
    for name in ('indirection_tex', 'cache_tex0', 'cache_tex1', 'cache_tex2', 'last_clip_tex'):
        lines = find_lines(text, 'TERRAIN_BIND var=' + name + ' ')
        add('global_' + name, ('ok' if 'bound=1' in lines[-1] else 'warning') if lines else 'unknown',
            'Global variable only; actual GPU shader slot binding is not verified.', lines[-1:])
    bind_lines = find_lines(text, 'TERRAIN_BIND')
    add('terrain_shader_globals', 'ok' if bind_lines else 'unknown',
        'Logged globals and pre-draw readback; this does not capture GPU shader registers.', bind_lines[:40])
    warmup = find_lines(text, 'WARMUP')
    add('warmup', ('ok' if 'settled=1' in warmup[-1] else 'warning') if warmup else 'unknown',
        'Warmup status as reported by the engine.', warmup[-1:])
    abi_lines = find_lines(text, '[KE] CLIPMAP_ABI ')
    add('clipmap_storage', 'ok' if abi_lines else 'unknown', 'Clipmap table format selected by the engine.', abi_lines[-2:])
    native_draws = find_lines(text, '[KE] DX11_DRAW ')
    native_scopes = find_lines(text, '[KE] DX11_SCOPE ')
    add('dx11_draw_state', 'ok' if native_draws else ('warning' if native_scopes else 'unknown'),
        'Native DX11 state for selected draws. Full binding and reflection lines are in the engine log.',
        [*native_scopes[:12], *native_draws[:12]])
    pass_lines = find_lines(text, 'PASS_CAPTURE')
    add('resolve_passes', 'ok' if pass_lines and all('status=ok' in l for l in pass_lines) else ('warning' if pass_lines else 'unknown'),
        'Raw previews around resolve; channel meaning and color-space conversion are not verified.', pass_lines[-12:])
    lines = find_lines(text, 'TERRAIN_DRAW')
    add('terrain_draw', ('ok' if 'renderer=1' in lines[-1] else 'warning') if lines else 'unknown',
        'Requested terrain draw; not proof of correct pixel output.', lines[-1:])
    for category, pattern in [('shader_errors', r'shader variable .*?(?:not found|not present)'),
                              ('decompression_errors', r'(?i)(?:oodle|ooz|decompress).*(?:fail|error|fatal)')]:
        lines = [line.strip() for line in text.splitlines() if re.search(pattern, line, re.I)]
        add(category, 'warning' if lines else ('ok' if debug else 'unknown'),
            'Reported log messages are leads, not established causes.' if lines else 'No matching messages in analyzed log.', lines[:30])
    files = {}
    debug_images = {'static-albedo.png', 'static-depth-mask.png', 'terrain-last-clip.png', 'resolve-input-mrt0.png',
                    'resolve-input-mrt1.png', 'resolve-input-mrt2.png', 'resolve-input-depth.png', 'resolve-output.png'}
    paths = [run / 'frame.png', *sorted(p for p in (run / 'render-debug').rglob('*.png') if p.name in debug_images)]
    for p in paths:
        rel = p.relative_to(run).as_posix()
        if not p.is_file():
            continue
        item = {'bytes': p.stat().st_size, 'sha256': sha256(p)}
        try:
            item['statistics'] = image_stats(p)
        except (ValueError, OSError, zlib.error) as exc:
            item['statistics_error'] = str(exc)
        files[rel] = item
    info = {'python': sys.version.split()[0], 'platform': platform.platform(), 'machine': platform.machine()}
    if dagor:
        try:
            r = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=dagor, capture_output=True, text=True, timeout=10)
            info['dagor_commit'] = r.stdout.strip() if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            info['dagor_commit'] = None
        editor = dagor / 'tools/dagor_cdk/windows-x86_64/daEditor3x-dev.exe'
        if editor.is_file():
            info['editor_sha256'] = sha256(editor)
    if game:
        shader = game / 'WarThunderCDK/tools.ps50.shdump.bin'
        if shader.is_file():
            info['cdk_shader_sha256'] = sha256(shader)
    return {'schema_version': VERSION, 'run': str(run), 'environment': info, 'checks': checks, 'images': files,
            'limitations': ['Global shader variables are not GPU register bindings or proof of shader execution.',
                            'One tile sample and one immediate CPU upload do not validate all resource contents.',
                            'No automatic conclusion about Oodle/ooz, shader ABI compatibility, or root cause.']}


def write_outputs(run, report):
    dest = run / 'diagnostics'
    dest.mkdir(exist_ok=True)
    (dest / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# KolobanovEye diagnostic report', '', f"Run: `{report['run']}`", '', '| Check | Status | Details |', '|---|---|---|']
    for item in report['checks']:
        lines.append(f"| {item['name']} | {item['status']} | {item['detail']} |")
    lines += ['', '## Environment and image fingerprints', '', '```json',
              json.dumps({'environment': report['environment'], 'images': report['images']}, ensure_ascii=False, indent=2), '```',
              '', '## Evidence', '']
    for item in report['checks']:
        if item['evidence']:
            lines += [f"### {item['name']}", '', '```text', *item['evidence'], '```', '']
    lines += ['## Limitations', ''] + ['- ' + x for x in report['limitations']]
    (dest / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    archive = dest / 'diagnostics.zip'
    allowed = {'frame.png', 'frame.blk', 'capture.blk', 'inventory.json', 'host-stdout.log', 'grp.txt',
               'probe.dcmd', 'probe.dcmd.log', 'probe.dcmd.err', 'diagnostic-capture.log', 'diagnostic-ooz.log', 'terrain-last-clip.png', 'static-albedo.png',
               'static-depth-mask.png', 'resolve-input-mrt0.png', 'resolve-input-mrt1.png', 'resolve-input-mrt2.png', 'resolve-input-depth.png', 'resolve-output.png', 'debug', 'logerr', 'logwarn', 'last_debug'}
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.write(dest / 'report.json', 'diagnostics/report.json')
        z.write(dest / 'report.md', 'diagnostics/report.md')
        for folder in (run, run / 'render-debug', run / '.logs'):
            if not folder.is_dir():
                continue
            for p in (folder.rglob('*') if folder != run else folder.iterdir()):
                if p.is_file() and not p.is_symlink() and p.name in allowed and p.stat().st_size <= 100 * 1024 * 1024:
                    z.write(p, p.relative_to(run).as_posix())
    return dest, archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True, help='Existing frame output directory, including failed runs')
    parser.add_argument('--renderdoc-cli', type=Path, help='renderdoccmd.exe; requires --capture --keep-open')
    parser.add_argument('--dx11-trace', action='store_true', help='Capture native DX11 draw state; requires --capture')
    parser.add_argument('--keep-open', action='store_true', help='Keep editor open until manually closed (requires --capture)')
    parser.add_argument('--capture', action='store_true', help='Run a new frame first; --run must not exist')
    parser.add_argument('--model', default='pzkpfw_VI_ausf_h1_tiger')
    parser.add_argument('--skeleton', default='pzkpfw_VI_ausf_h1_tiger_skeleton')
    parser.add_argument('--tank-xz', nargs=2, type=float, default=(0, 0), metavar=('X', 'Z'))
    parser.add_argument('--clipmap-feedback', choices=('software', 'hardware'), default='software')
    parser.add_argument('--clipmap-abi', choices=('legacy-color', 'native-int'), default='legacy-color')
    parser.add_argument('--indirection', choices=('cpu', 'gpu'), default='gpu')
    parser.add_argument('--dagor', type=Path, help='Dagor checkout for version and executable fingerprint')
    parser.add_argument('--game', type=Path, help='Game directory for CDK shader dump fingerprint; read-only')
    parser.add_argument('--ooz-exe', type=Path, help='Optionally test the real Normandy lightmap with ooz; requires --game')
    args = parser.parse_args()
    run = args.run.resolve()
    if args.game and (run == args.game.resolve() or run.is_relative_to(args.game.resolve())):
        parser.error('Run and diagnostics must be outside the game directory')
    if args.renderdoc_cli and not (args.capture and args.keep_open):
        parser.error('--renderdoc-cli requires --capture --keep-open')
    if args.dx11_trace and not args.capture:
        parser.error('--dx11-trace requires --capture')
    if args.keep_open and not args.capture:
        parser.error('--keep-open requires --capture')
    capture_status = None
    if args.capture:
        if not args.dagor or not args.game:
            parser.error('--capture requires --dagor and --game')
        if run.exists():
            parser.error('--capture requires a new --run directory')
        cmd = [sys.executable, str(Path(__file__).with_name('kolobanoveye.py')), 'frame', '--dagor', str(args.dagor),
               '--game', str(args.game), '--out', str(run), '--model', args.model, '--skeleton', args.skeleton,
               '--tank-xz', str(args.tank_xz[0]), str(args.tank_xz[1]), '--clipmap-feedback', args.clipmap_feedback,
               '--clipmap-abi', args.clipmap_abi, '--indirection', args.indirection, '--render-debug']
        if args.keep_open:
            cmd.append('--keep-open')
        if args.dx11_trace:
            cmd.append('--dx11-trace')
        if args.renderdoc_cli:
            cmd.extend(['--renderdoc-cli', str(args.renderdoc_cli)])
        print('[KE] Starting diagnostic frame...', flush=True)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        capture_lines = []
        for line in proc.stdout:
            print(line, end='', flush=True)
            capture_lines.append(line)
        capture_status = proc.wait()
        if run.is_dir():
            (run / 'diagnostic-capture.log').write_text(''.join(capture_lines), encoding='utf-8')
        else:
            print('[KE] No run directory was created; see the error above.', file=sys.stderr)
            return capture_status or 1
    if not run.is_dir():
        parser.error('Run directory does not exist: ' + str(run))
    if args.ooz_exe and not args.game:
        parser.error('--ooz-exe requires --game')
    probe = None
    if args.ooz_exe:
        probe_cmd = [sys.executable, str(Path(__file__).with_name('kolobanoveye.py')), 'probe-ooz',
                     '--game', str(args.game), '--ooz-exe', str(args.ooz_exe)]
        try:
            result = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding='utf-8', errors='replace', timeout=120)
            probe = {'status': 'ok' if result.returncode == 0 and 'OOZ_LIGHTMAP_OK' in result.stdout else 'error',
                     'exit_code': result.returncode, 'output': result.stdout}
        except (OSError, subprocess.SubprocessError) as exc:
            probe = {'status': 'error', 'exit_code': None, 'output': str(exc)}
        (run / 'diagnostic-ooz.log').write_text(probe['output'], encoding='utf-8')
    report = analyze(run, args.dagor, args.game)
    if probe:
        report['checks'].append({'name': 'ooz_lightmap_probe', 'status': probe['status'],
            'detail': 'One real lightmap decoded; this is not a reference decoder comparison.',
            'evidence': probe['output'].splitlines()[-8:]})
    report['capture_exit_code'] = capture_status
    dest, archive = write_outputs(run, report)
    for c in report['checks']:
        print(f"[KE] {c['status'].upper():7} {c['name']}: {c['detail']}")
    print(f'[KE] REPORT {dest / "report.md"}')
    print(f'[KE] BUNDLE {archive}')
    for capture in sorted(run.glob('gpu-capture*.rdc')):
        print(f'[KE] GPU_CAPTURE {capture} (not included in diagnostics.zip)')
    print('[KE] Bundle contains local paths, logs and screenshots; review before sharing.')
    return capture_status if capture_status is not None else 0


if __name__ == '__main__':
    raise SystemExit(main())
