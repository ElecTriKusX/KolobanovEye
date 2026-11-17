"""Validate Dagor frame metadata and describe the approximate AABB annotation."""
import json
import math
import re
from pathlib import Path


def read_annotation(path):
    return parse_annotation(Path(path).read_text(encoding='utf-8'))


def parse_annotation(text):
    entries = {}
    for line in text.splitlines():
        match = re.fullmatch(r'([\w]+):([\w]+)=(.*)', line.strip())
        if match:
            entries[match.group(1)] = match.group(3).strip().strip('"')
    try:
        width, height = int(entries['width']), int(entries['height'])
        box = [float(n.strip()) for n in entries['bbox_amodal_aabb_xywh'].split(',')]
        if len(box) != 4 or not all(math.isfinite(x) for x in box):
            raise ValueError('invalid bbox values')
        x, y, w, h = box
        if width <= 0 or height <= 0 or x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width + 0.01 or y + h > height + 0.01:
            raise ValueError('bbox outside image')
        if entries['class'] != 'tank':
            raise ValueError('unexpected class')
    except (KeyError, ValueError) as exc:
        raise ValueError(f'Invalid frame annotation: {exc}') from exc
    method = ('projected_conservative_joint_envelope_depth_clipped_and_frame_clamped'
              if entries.get('pose_bounds_method') == 'conservative_joint_envelope'
              else 'projected_local_3d_aabb_depth_clipped_and_frame_clamped')
    return {'schema_version': 1, 'image': 'frame.png', 'width': width, 'height': height,
            'annotations': [{'class': 'tank', 'bbox_xywh': box,
                             'bbox_method': method,
                             'bbox_kind': 'approximate_amodal', 'visibility': None}],
            'source_metadata': 'frame.blk'}


def write_annotation(meta, output):
    data = read_annotation(meta)
    Path(output).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
