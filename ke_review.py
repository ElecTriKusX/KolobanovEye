"""Create diagnostic bbox overlays from a sequence without changing RGB originals."""
import argparse
import hashlib
import json
import math
import re
import struct
import sys
import zlib
from pathlib import Path

from ke_pixel_debug import load_png


def chunk(kind, payload):
    return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)


def write_rgb_png(path, width, height, rgb):
    stride = width * 3
    rows = b''.join(b'\x00' + rgb[y * stride:(y + 1) * stride] for y in range(height))
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) +
           chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))
    with path.open('xb') as stream:
        stream.write(png)


def draw_box(width, height, pixels, bbox):
    if len(bbox) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in bbox):
        raise ValueError('invalid bbox values')
    x, y, w, h = bbox
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width + .01 or y + h > height + .01:
        raise ValueError('bbox outside image')
    x0, y0 = max(0, math.floor(x)), max(0, math.floor(y))
    x1, y1 = min(width - 1, math.ceil(x + w) - 1), min(height - 1, math.ceil(y + h) - 1)
    for n in range(3):
        left, right, top, bottom = x0 + n, x1 - n, y0 + n, y1 - n
        if left > right or top > bottom:
            break
        for px in range(left, right + 1):
            for py in (top, bottom):
                i = (py * width + px) * 3
                pixels[i:i + 3] = b'\x00\xff\x00'
        for py in range(top, bottom + 1):
            for px in (left, right):
                i = (py * width + px) * 3
                pixels[i:i + 3] = b'\x00\xff\x00'


def parse_vector(blk, key):
    match = re.search(r'^' + re.escape(key) + r':p3=([^\r\n]+)', blk, re.MULTILINE)
    if not match:
        raise ValueError(f'{key} missing from Dagor metadata')
    result = [float(v.strip()) for v in match.group(1).split(',')]
    if len(result) != 3 or not all(math.isfinite(v) for v in result):
        raise ValueError(f'invalid {key}')
    return result


def review(run, output):
    run = run.resolve()
    output = output.resolve()
    # Never overwrite source files, including when output aliases an input directory.
    if output == run or output.is_relative_to(run / 'images'):
        raise ValueError('Choose a separate new review directory, not the images directory')
    records = [json.loads(line) for line in (run / 'metadata.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    if not records:
        raise ValueError('metadata.jsonl contains no frames')
    output.mkdir(parents=True, exist_ok=False)
    frames = []
    seen = set()
    for item in records:
        idx = item['frame_index']
        if not isinstance(idx, int) or idx < 0 or idx in seen:
            raise ValueError('frame indices must be unique non-negative integers')
        seen.add(idx)
        image = (run / item['image']).resolve()
        if not image.is_relative_to(run) or not image.is_file():
            raise ValueError('image path must be an existing file inside the run directory')
        width, height, rgb = load_png(image)
        ann = item['annotation']
        if ann['class'] != 'tank' or ann['bbox_kind'] != 'approximate_amodal':
            raise ValueError('unexpected annotation type')
        draw_box(width, height, rgb, ann['bbox_xywh'])
        name = f'{idx:06d}.png'
        write_rgb_png(output / name, width, height, rgb)
        raw = item['dagor_frame_metadata_blk']
        frames.append({'index': idx, 'image': item['image'], 'overlay': name,
                       'source_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
                       'tank_world': parse_vector(raw, 'tank_world'), 'eye': parse_vector(raw, 'eye'),
                       'bbox_xywh': ann['bbox_xywh'], 'tank_yaw_deg': item.get('tank_yaw_deg')})
    report = {'frames': frames, 'note': 'Green rectangles are projected 3D AABB, not visible boxes. '
              'Visual inspection is required; source images are unchanged.'}
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'[KE] REVIEW_WRITTEN {len(frames)} overlays: {output}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        review(args.run, args.out)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f'[KE] ERROR {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
