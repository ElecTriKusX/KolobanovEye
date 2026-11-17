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
    'tank_yaw': 20.0, 'ground_offset': 0.05, 'minimum_ground_y': 0.5, 'camera_clearance': 0.5,
    'placement_mode': 'whole_map', 'map_margin_percent': 15.0, 'placement_radius': 0.0, 'placement_attempts': 24, 'frames_per_position': 6, 'max_height_range': 0.5, 'camera_attempts': 8,
    'camera_height_min': 4.0, 'camera_height_max': 16.0, 'distance_min': 18.0, 'distance_max': 35.0, 'elevation_min': 10.0, 'elevation_max': 25.0,
    'min_warmup_renders': 8, 'wait_sky_convergence': False, 'wait_frame_stability': True,
    'keep_open': False, 'render_debug': False, 'scan_buildings': False, 'capture_last_clip': False, 'clipmap_feedback': 'software', 'timeout': 360,
}
INTS = {'count', 'seed', 'width', 'height', 'timeout', 'placement_attempts', 'camera_attempts', 'frames_per_position', 'min_warmup_renders'}
FLOATS = {'fov', 'tank_x', 'tank_z', 'tank_yaw', 'ground_offset', 'camera_clearance',
          'minimum_ground_y', 'distance_min', 'distance_max', 'elevation_min', 'elevation_max', 'placement_radius', 'max_height_range', 'camera_height_min', 'camera_height_max', 'map_margin_percent'}


def validate_config(raw):
    if not isinstance(raw, dict):
        raise ValueError('Настройки должны быть JSON-объектом')
    raw = {k: v for k, v in raw.items() if k != 'collect_clipmap_tiles'}
    # Accept saved configurations from ordinary runs of the experimental branch.
    # Replay and resolve isolation require that branch when explicitly enabled.
    for key in ('resolve_no_ssao', 'resolve_no_ssr', 'resolve_no_sun',
                'resolve_no_ambient', 'resolve_no_envi_reflections', 'source_indices'):
        if key in raw:
            inactive = None if key == 'source_indices' else False
            if raw.pop(key) is not inactive:
                raise ValueError(f'{key}: доступно в ветке fix/resolve-white-haze')
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
    if not 8 <= c['min_warmup_renders'] <= 64:
        raise ValueError('min_warmup_renders: требуется целое число 8–64')
    if not 1 <= c['frames_per_position'] <= 1000:
        raise ValueError('Кадров на позицию: 1–1000')
    if not 2 <= c['count'] <= 1000 or not 64 <= c['width'] <= 4096 or not 64 <= c['height'] <= 4096:
        raise ValueError('Количество кадров: 2–1000; стороны изображения: 64–4096')
    if not 15 <= c['fov'] <= 100 or not 0 <= c['camera_clearance'] <= 100 or c['timeout'] <= 0:
        raise ValueError('FOV: 15–100; высота камеры: 0–100; таймаут положительный')
    if any(abs(c[k]) > 100000 for k in ('tank_x', 'tank_z', 'tank_yaw', 'ground_offset', 'minimum_ground_y')):
        raise ValueError('Координаты и поворот должны быть в пределах ±100000')
    if c['placement_mode'] not in ('whole_map', 'around_point') or not 0 <= c['map_margin_percent'] < 50:
        raise ValueError('Режим размещения или отступ от краёв карты некорректен (0–49.99%)')
    if not 0 <= c['placement_radius'] <= 10000 or not 1 <= c['placement_attempts'] <= 64 or not 1 <= c['camera_attempts'] <= 64 or not 0 <= c['max_height_range'] <= 100:
        raise ValueError('Радиус: 0–10000; попытки: 1–64; перепад: 0–100')
    if not 0 <= c['camera_height_min'] <= c['camera_height_max'] <= 10000:
        raise ValueError('Высота камеры: 0–10000, от не больше до')
    if c['camera_height_max'] < c['camera_clearance']:
        raise ValueError('Максимальная высота камеры меньше минимального зазора над землёй')
    if not all(isinstance(c[k], bool) for k in ('keep_open', 'render_debug', 'capture_last_clip', 'scan_buildings')):
        raise ValueError('Параметры keep_open, render_debug, capture_last_clip и scan_buildings должны быть true или false')
    for key in ('wait_frame_stability', 'wait_sky_convergence'):
        if not isinstance(c[key], bool):
            raise ValueError(f'{key}: требуется true или false')
    if c['clipmap_feedback'] not in ('software', 'hardware'):
        raise ValueError('clipmap_feedback: software или hardware')
    plan_camera_orbits(c['seed'], 0, (c['distance_min'], c['distance_max']), (c['elevation_min'], c['elevation_max']))
    return c


def command(c):
    c = validate_config(c)
    args = ([sys.executable, '-u', str(Path(__file__).resolve().with_name('kolobanoveye.py')), 'frame',
            '--dagor', c['dagor'], '--game', c['game'], '--out', c['output'],
            '--count', str(c['count']), '--seed', str(c['seed']), '--width', str(c['width']),
            '--height', str(c['height']), '--fov', str(c['fov']), '--tank-xz', str(c['tank_x']), str(c['tank_z']),
            '--yaw', str(c['tank_yaw']), '--ground-offset', str(c['ground_offset']),
            '--placement-mode', c['placement_mode'], '--map-margin-percent', str(c['map_margin_percent']), '--frames-per-position', str(c['frames_per_position']), '--camera-attempts', str(c['camera_attempts']), '--placement-radius', str(c['placement_radius']), '--placement-attempts', str(c['placement_attempts']),
            '--max-height-range', str(c['max_height_range']), '--minimum-ground-y', str(c['minimum_ground_y']), '--camera-clearance', str(c['camera_clearance']), '--camera-height-range', str(c['camera_height_min']), str(c['camera_height_max']), '--camera-distance-range', str(c['distance_min']), str(c['distance_max']),
            '--camera-elevation-range', str(c['elevation_min']), str(c['elevation_max']),
            '--clipmap-feedback', c['clipmap_feedback'], '--timeout', str(c['timeout'])] + (['--keep-open'] if c['keep_open'] else []) + (['--capture-last-clip'] if c['capture_last_clip'] else []) + (['--render-debug'] if c['render_debug'] else []) + (['--scan-buildings'] if c['scan_buildings'] else []))

    args.extend(('--min-warmup-renders', str(c['min_warmup_renders']),
                 '--wait-frame-stability' if c['wait_frame_stability'] else '--no-wait-frame-stability'))
    if c['wait_sky_convergence']:
        args.append('--wait-sky-convergence')
    return args


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
