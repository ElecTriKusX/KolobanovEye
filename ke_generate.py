"""Run a reproducible local sequence from JSON configuration."""
import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from kolobanoveye import plan_camera_orbits

DEFAULTS = {
    'dagor': r'D:\Projects\DagorEngine', 'game': r'D:\Games\WarThunder',
    'output': r'D:\Projects\ke-generate-01', 'count': 6, 'seed': 42,
    'width': 1280, 'height': 720, 'fov': 55.0, 'tank_x': 0.0, 'tank_z': 0.0,
    'tank_yaw': 20.0, 'ground_offset': 0.05, 'camera_clearance': 0.5,
    'distance_min': 18.0, 'distance_max': 35.0, 'elevation_min': 10.0, 'elevation_max': 25.0,
    'clipmap_feedback': 'software', 'timeout': 360,
}
INTS = {'count', 'seed', 'width', 'height', 'timeout'}
FLOATS = {'fov', 'tank_x', 'tank_z', 'tank_yaw', 'ground_offset', 'camera_clearance',
          'distance_min', 'distance_max', 'elevation_min', 'elevation_max'}


def validate_config(raw):
    if not isinstance(raw, dict):
        raise ValueError('Настройки должны быть JSON-объектом')
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise ValueError('Неизвестные параметры: ' + ', '.join(sorted(unknown)))
    c = {**DEFAULTS, **raw}
    for key in ('dagor', 'game', 'output'):
        if not isinstance(c[key], str) or not c[key].strip():
            raise ValueError(f'{key}: укажите путь')
        c[key] = c[key].strip()
    for key in INTS:
        if isinstance(c[key], bool) or not isinstance(c[key], int):
            raise ValueError(f'{key}: требуется целое число')
    for key in FLOATS:
        if isinstance(c[key], bool) or not isinstance(c[key], (int, float)) or not math.isfinite(c[key]):
            raise ValueError(f'{key}: требуется конечное число')
    if not 2 <= c['count'] <= 1000 or not 64 <= c['width'] <= 4096 or not 64 <= c['height'] <= 4096:
        raise ValueError('Количество кадров: 2–1000; стороны изображения: 64–4096')
    if not 15 <= c['fov'] <= 100 or not 0 <= c['camera_clearance'] <= 100 or c['timeout'] <= 0:
        raise ValueError('FOV: 15–100; высота камеры: 0–100; таймаут положительный')
    if any(abs(c[k]) > 100000 for k in ('tank_x', 'tank_z', 'tank_yaw', 'ground_offset')):
        raise ValueError('Координаты и поворот должны быть в пределах ±100000')
    if c['clipmap_feedback'] not in ('software', 'hardware'):
        raise ValueError('clipmap_feedback: software или hardware')
    plan_camera_orbits(c['seed'], 0, (c['distance_min'], c['distance_max']), (c['elevation_min'], c['elevation_max']))
    return c


def command(c):
    c = validate_config(c)
    return [sys.executable, '-u', str(Path(__file__).resolve().with_name('kolobanoveye.py')), 'frame',
            '--dagor', c['dagor'], '--game', c['game'], '--out', c['output'],
            '--count', str(c['count']), '--seed', str(c['seed']), '--width', str(c['width']),
            '--height', str(c['height']), '--fov', str(c['fov']), '--tank-xz', str(c['tank_x']), str(c['tank_z']),
            '--yaw', str(c['tank_yaw']), '--ground-offset', str(c['ground_offset']),
            '--camera-clearance', str(c['camera_clearance']), '--camera-distance-range', str(c['distance_min']), str(c['distance_max']),
            '--camera-elevation-range', str(c['elevation_min']), str(c['elevation_max']),
            '--clipmap-feedback', c['clipmap_feedback'], '--timeout', str(c['timeout'])]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    try:
        cfg = validate_config(json.loads(args.config.read_text(encoding='utf-8')))
        if Path(cfg['output']).exists():
            raise ValueError('Выходная папка уже существует; укажите новую')
        result = subprocess.call(command(cfg), cwd=Path(__file__).resolve().parent)
        output = Path(cfg['output'])
        if output.is_dir():
            with (output / 'generation-config.json').open('x', encoding='utf-8') as stream:
                json.dump(cfg, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
        return result
    except (OSError, ValueError) as exc:
        print(f'[KE] ERROR {exc}', file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
