"""Summarize heightmap checks; unknown water/obstacles remain explicitly unknown."""
import argparse
import json
import math
import re
import sys
from pathlib import Path


def number(blk, key, typ):
    m = re.search(r'^' + re.escape(key) + ':' + typ + r'=([^\r\n]+)', blk, re.MULTILINE)
    if not m: return None
    try: value = float(m.group(1))
    except ValueError: return None
    return value if math.isfinite(value) else None


def analyze(records, threshold=0.5):
    if not math.isfinite(threshold) or not 0 <= threshold <= 100:
        raise ValueError('Порог перепада должен быть от 0 до 100')
    if not records: raise ValueError('Нет записей для анализа')
    frames = []
    for item in records:
        blk = item['dagor_frame_metadata_blk']
        count = number(blk, 'support_height_samples', 'i')
        lo = number(blk, 'support_height_min', 'r')
        hi = number(blk, 'support_height_max', 'r')
        clearance = number(blk, 'camera_clearance', 'r')
        minimum_ground_y = number(blk, 'minimum_ground_y', 'r')
        water_plane = number(blk, 'water_plane_y', 'r')
        above_water = number(blk, 'terrain_above_water_plane', 'r')
        known = count == 4 and lo is not None and hi is not None and hi >= lo
        delta = round(hi-lo, 6) if known else None
        frames.append({'index': item['frame_index'], 'heightmap_samples': count,
                       'corner_height_range': delta, 'max_allowed_range': threshold,
                       'heightmap_range_result': ('within_threshold' if delta <= threshold else 'above_threshold') if known else 'unknown',
                       'camera_clearance': clearance, 'camera_heightmap_result': 'measured' if clearance is not None else 'unknown',
                       'water_check': 'unknown', 'minimum_ground_y_filter': minimum_ground_y, 'water_plane_y': water_plane, 'terrain_above_water_plane': above_water,
                       'water_proxy_method': 'scene_water_plane_unverified', 'obstacle_check': 'unknown', 'track_contact': 'unknown'})
    return {'method': 'heightmap_at_four_projected_local_aabb_bottom_corners', 'frames': frames,
            'note': 'Диагностика перепада высот по углам габарита, не проверка воды, наклона, контакта гусениц или препятствий; '
                    'Уровень водной плоскости диагностический, наличие воды в точке не доказано; отбор и повторное размещение здесь не выполняются.'}


def audit(run, threshold=0.5):
    lines = (run / 'metadata.jsonl').read_text(encoding='utf-8').splitlines()
    return analyze([json.loads(x) for x in lines if x.strip()], threshold)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', required=True, type=Path)
    p.add_argument('--max-height-range', type=float, default=0.5)
    p.add_argument('--write', action='store_true', help='Сохранить terrain-report.json без перезаписи')
    a = p.parse_args()
    try:
        result = audit(a.run, a.max_height_range)
        if a.write:
            with (a.run / 'terrain-report.json').open('x', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2); f.write('\n')
        summary = {k: sum(f['heightmap_range_result'] == k for f in result['frames'])
                   for k in ('within_threshold', 'above_threshold', 'unknown')}
        print(json.dumps({'frame_count': len(result['frames']), 'heightmap_range': summary,
                          'unverified': ['water', 'obstacles', 'track_contact'], 'threshold': a.max_height_range},
                         ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f'[KE] ERROR {exc}', file=sys.stderr); return 1

if __name__ == '__main__': sys.exit(main())
