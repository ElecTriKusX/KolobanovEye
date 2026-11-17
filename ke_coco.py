"""Export approximate 3D-AABB annotations to COCO using references to existing PNGs."""
import argparse
import hashlib
import json
import math
import os
import random
import re
import sys
from pathlib import Path
from ke_validate import validate


def field(blk, name, typ):
    match = re.search(r'^' + re.escape(name) + ':' + typ + r'=([^\r\n]+)', blk, re.MULTILINE)
    if not match:
        raise ValueError(f'В метаданных нет поля {name}')
    return match.group(1).strip().strip('"')


def scene_metadata(blk):
    """Read actual world-space values saved by Dagor, never planned offsets."""
    def optional(name, kind, vector=False):
        try:
            value = field(blk, name, kind)
        except ValueError:
            return None
        values = [float(x.strip()) for x in value.split(',')] if vector else [float(value)]
        if (vector and len(values) != 3) or not all(math.isfinite(v) for v in values):
            raise ValueError(f'Некорректное значение {name}')
        return values if vector else values[0]
    return {'tank_world_xyz': optional('tank_world', 'p3', True),
            'tank_yaw_deg': optional('yaw_deg', 'r'),
            'turret_yaw_deg': optional('turret_yaw_deg', 'r'),
            'gun_pitch_deg': optional('gun_pitch_deg', 'r'),
            'camera_eye_world_xyz': optional('eye', 'p3', True),
            'camera_look_at_world_xyz': optional('look_at', 'p3', True),
            'camera_horizontal_fov_deg': optional('fov_x_deg', 'r'),
            'coordinate_system': 'Dagor world XYZ; Y is vertical'}


def group_for(blk, tile_size):
    """Group by map and a fixed X/Z cell, intentionally independent of seed and yaw."""
    level = field(blk, 'level', 't').replace('\\', '/').split('/')[-1].casefold()
    if not level: raise ValueError('Не задана карта')
    point = [float(x.strip()) for x in field(blk, 'tank_world', 'p3').split(',')]
    if len(point) != 3 or not all(math.isfinite(x) for x in point):
        raise ValueError('Неверные координаты танка')
    tile = [math.floor(point[0] / tile_size), math.floor(point[2] / tile_size)]
    return f'{level}:{tile_size:g}:{tile[0]}:{tile[1]}'


def split_groups(groups, seed):
    names = sorted(set(groups))
    if len(names) < 3:
        raise ValueError('Для train/val/test нужны не менее трёх различных групп участков карты')
    random.Random(seed).shuffle(names)
    n = len(names)
    nval = max(1, int(n * 0.1))
    ntest = max(1, int(n * 0.1))
    ntrain = n - nval - ntest
    return {name: ('train' if i < ntrain else 'val' if i < ntrain + nval else 'test') for i, name in enumerate(names)}


def export(runs, out, tile_size=256, split=False, seed=42):
    if not math.isfinite(tile_size) or not 1 <= tile_size <= 100000:
        raise ValueError('Размер участка должен быть от 1 до 100000')
    runs = [p.resolve() for p in runs]
    out = out.resolve()
    if len(set(runs)) != len(runs): raise ValueError('Папки серий повторяются')
    if out.exists(): raise ValueError('Выходная папка уже существует')
    if any(out == run or out.is_relative_to(run / 'images') for run in runs):
        raise ValueError('Выходная папка не может быть серией или папкой изображений')
    data = []
    for run in runs:
        validate(run)
        for line in (run / 'metadata.jsonl').read_text(encoding='utf-8').splitlines():
            record = json.loads(line)
            blk = record['dagor_frame_metadata_blk']
            img = (run / record['image']).resolve()
            try: relative = os.path.relpath(img, out).replace('\\', '/')
            except ValueError as exc: raise ValueError('Экспорт и исходные кадры должны быть на одном диске') from exc
            map_id = record.get('map_id') or (field(blk, 'level', 't').replace('\\', '/').split('/')[-1])
            data.append({'relative': relative, 'map_id': map_id, 'group': group_for(blk, tile_size),
                         'width': int(field(blk, 'width', 'i')), 'height': int(field(blk, 'height', 'i')),
                         'annotation': record['annotation'], 'scene': scene_metadata(blk)})
    assignments = split_groups([r['group'] for r in data], seed) if split else {r['group']: 'all' for r in data}
    subsets = ('train', 'val', 'test') if split else ('all',)
    output = {}
    for subset in subsets:
        result = {'info': {'description': 'Approximate projected 3D AABB; visibility not measured',
                           'group_method': 'map_xz_tile', 'tile_size': tile_size, 'seed': seed},
                  'images': [], 'annotations': [], 'categories': [{'id': 1, 'name': 'tank'}]}
        for rec in data:
            if assignments[rec['group']] != subset: continue
            i = len(result['images']) + 1
            x, y, w, h = rec['annotation']['bbox_xywh']
            result['images'].append({'id': i, 'file_name': rec['relative'], 'width': rec['width'], 'height': rec['height'],
                                     'scene_group': rec['group'], 'map_id': rec['map_id'], 'ke_scene': rec['scene']})
            result['annotations'].append({'id': i, 'image_id': i, 'category_id': 1, 'bbox': [x, y, w, h],
                                          'area': w*h, 'iscrowd': 0,
                                          'bbox_method': rec['annotation']['bbox_method'], 'bbox_kind': 'approximate_amodal'})
        output[subset] = result
    out.mkdir(parents=True, exist_ok=False)
    for subset, result in output.items():
        (out / f'{subset}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    manifest = {'groups': assignments, 'images_per_split': {k: len(v['images']) for k,v in output.items()},
                'warning': 'Cell grouping cannot prevent leakage between nearby positions across cell boundaries. '
                           'Images are referenced, not copied. Boxes are approximate, visibility is not checked.'}
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs', nargs='+', required=True, type=Path)
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--split', action='store_true')
    p.add_argument('--tile-size', type=float, default=256)
    p.add_argument('--seed', type=int, default=42)
    a = p.parse_args()
    try:
        print(json.dumps(export(a.runs, a.out, a.tile_size, a.split, a.seed), ensure_ascii=False, indent=2)); return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f'[KE] ERROR {exc}', file=sys.stderr); return 1

if __name__ == '__main__': sys.exit(main())
