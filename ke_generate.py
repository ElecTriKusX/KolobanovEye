"""Run a reproducible local sequence from JSON configuration."""
import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from kolobanoveye import plan_articulation, plan_camera_orbits
from tools.ke_maps import MAP_ID
from tools.ke_composition import DEFAULT_WEIGHTS, validate_weights

DEFAULTS = {
    'dagor': r'D:\Projects\DagorEngine', 'game': r'D:\Games\WarThunder',
    'output': r'D:\Projects\ke-generate-01', 'count': 6, 'seed': 42,
    'map_id': 'levels/avg_normandy.bin', 'map_ids': [], 'capture_tank_map': True, 'tank_map_resolution': 1024,
    'width': 1280, 'height': 720, 'fov': 55.0, 'tank_x': 0.0, 'tank_z': 0.0,
    'tank_yaw': 20.0, 'ground_offset': 0.05, 'minimum_ground_y': 0.5, 'camera_clearance': 0.5,
    'placement_mode': 'tank_map', 'map_margin_percent': 0.0, 'placement_radius': 0.0, 'placement_attempts': 24, 'frames_per_position': 6, 'max_height_range': 0.5, 'camera_attempts': 8,
    'camera_height_min': 4.0, 'camera_height_max': 16.0, 'distance_min': 18.0, 'distance_max': 35.0, 'elevation_min': 10.0, 'elevation_max': 25.0,
    'turret_yaw_min': 0.0, 'turret_yaw_max': 0.0, 'gun_pitch_min': 0.0, 'gun_pitch_max': 0.0,
    'min_warmup_renders': 8, 'wait_sky_convergence': False, 'wait_frame_stability': True,
    'screen_position_weights': DEFAULT_WEIGHTS.copy(),
    'keep_open': False, 'render_debug': False, 'capture_last_clip': False, 'clipmap_feedback': 'software', 'timeout': 360,
}
INTS = {'count', 'seed', 'width', 'height', 'timeout', 'placement_attempts', 'camera_attempts', 'frames_per_position', 'min_warmup_renders', 'tank_map_resolution'}
FLOATS = {'turret_yaw_min', 'turret_yaw_max', 'gun_pitch_min', 'gun_pitch_max', 'fov', 'tank_x', 'tank_z', 'tank_yaw', 'ground_offset', 'camera_clearance',
          'minimum_ground_y', 'distance_min', 'distance_max', 'elevation_min', 'elevation_max', 'placement_radius', 'max_height_range', 'camera_height_min', 'camera_height_max', 'map_margin_percent'}


def validate_config(raw):
    if not isinstance(raw, dict):
        raise ValueError('Настройки должны быть JSON-объектом')
    raw = {k: v for k, v in raw.items() if k not in ('collect_clipmap_tiles', 'scan_buildings')}
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
    c['screen_position_weights'] = validate_weights(c['screen_position_weights'])
    if not isinstance(c['map_ids'], list) or len(c['map_ids']) != len(set(str(x).casefold() for x in c['map_ids'])):
        raise ValueError('map_ids: требуется список уникальных карт')
    if any(not isinstance(x, str) or MAP_ID.fullmatch(x) is None for x in c['map_ids']):
        raise ValueError('map_ids: неправильный идентификатор карты')
    if not isinstance(c['capture_tank_map'], bool):
        raise ValueError('capture_tank_map: требуется true или false')
    if not 256 <= c['tank_map_resolution'] <= 4096 or c['tank_map_resolution'] & (c['tank_map_resolution'] - 1):
        raise ValueError('tank_map_resolution: степень двойки от 256 до 4096')
    if not isinstance(c['map_id'], str) or MAP_ID.fullmatch(c['map_id']) is None:
        raise ValueError('map_id: требуется levels/avg_имя.bin')
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
    if not 1 <= c['count'] <= 1000 or not 64 <= c['width'] <= 4096 or not 64 <= c['height'] <= 4096:
        raise ValueError('Количество кадров: 1–1000; стороны изображения: 64–4096')
    if not 15 <= c['fov'] <= 100 or not 0 <= c['camera_clearance'] <= 100 or c['timeout'] <= 0:
        raise ValueError('FOV: 15–100; высота камеры: 0–100; таймаут положительный')
    if any(abs(c[k]) > 100000 for k in ('tank_x', 'tank_z', 'tank_yaw', 'ground_offset', 'minimum_ground_y')):
        raise ValueError('Координаты и поворот должны быть в пределах ±100000')
    if c['placement_mode'] not in ('around_point', 'tank_map') or not 0 <= c['map_margin_percent'] < 50:
        raise ValueError('Режим размещения: только Танковое поле или Вокруг точки (0–49.99% отступ)')
    if not 0 <= c['placement_radius'] <= 10000 or not 1 <= c['placement_attempts'] <= 64 or not 1 <= c['camera_attempts'] <= 64 or not 0 <= c['max_height_range'] <= 100:
        raise ValueError('Радиус: 0–10000; попытки: 1–64; перепад: 0–100')
    if not 0 <= c['camera_height_min'] <= c['camera_height_max'] <= 10000:
        raise ValueError('Высота камеры: 0–10000, от не больше до')
    if c['camera_height_max'] < c['camera_clearance']:
        raise ValueError('Максимальная высота камеры меньше минимального зазора над землёй')
    if not all(isinstance(c[k], bool) for k in ('keep_open', 'render_debug', 'capture_last_clip')):
        raise ValueError('Параметры keep_open, render_debug и архивные настройки должны быть true или false')
    if c['capture_last_clip']:
        raise ValueError('Устаревшая диагностика last_clip удалена из генератора')
    for key in ('wait_frame_stability', 'wait_sky_convergence'):
        if not isinstance(c[key], bool):
            raise ValueError(f'{key}: требуется true или false')
    if c['clipmap_feedback'] not in ('software', 'hardware'):
        raise ValueError('clipmap_feedback: software или hardware')
    plan_articulation(c['seed'], 0, (c['turret_yaw_min'], c['turret_yaw_max']), (c['gun_pitch_min'], c['gun_pitch_max']))
    plan_camera_orbits(c['seed'], 0, (c['distance_min'], c['distance_max']), (c['elevation_min'], c['elevation_max']))
    return c


def command(c):
    c = validate_config(c)
    args = ([sys.executable, '-u', str(Path(__file__).resolve().with_name('kolobanoveye.py')), 'frame',
            '--dagor', c['dagor'], '--game', c['game'], '--level', c['map_id'], '--out', c['output'], '--tank-map-resolution', str(c['tank_map_resolution']),
            '--count', str(c['count']), '--seed', str(c['seed']), '--width', str(c['width']),
            '--height', str(c['height']), '--fov', str(c['fov']), '--tank-xz', str(c['tank_x']), str(c['tank_z']),
            '--yaw', str(c['tank_yaw']), '--ground-offset', str(c['ground_offset']),
            '--placement-mode', c['placement_mode'], '--map-margin-percent', str(c['map_margin_percent']), '--frames-per-position', str(c['frames_per_position']), '--camera-attempts', str(c['camera_attempts']), '--placement-radius', str(c['placement_radius']), '--placement-attempts', str(c['placement_attempts']),
            '--max-height-range', str(c['max_height_range']), '--minimum-ground-y', str(c['minimum_ground_y']), '--camera-clearance', str(c['camera_clearance']), '--camera-height-range', str(c['camera_height_min']), str(c['camera_height_max']), '--camera-distance-range', str(c['distance_min']), str(c['distance_max']),
            '--camera-elevation-range', str(c['elevation_min']), str(c['elevation_max']),
            '--clipmap-feedback', c['clipmap_feedback'], '--timeout', str(c['timeout'])] + (['--keep-open'] if c['keep_open'] else []) + (['--capture-last-clip'] if c['capture_last_clip'] else []) + (['--render-debug'] if c['render_debug'] else []) + (['--capture-tank-map'] if c['capture_tank_map'] else []))

    args.extend(('--turret-yaw-range', str(c['turret_yaw_min']), str(c['turret_yaw_max']),
                 '--gun-pitch-range', str(c['gun_pitch_min']), str(c['gun_pitch_max'])))
    args.extend(('--min-warmup-renders', str(c['min_warmup_renders']),
                 '--wait-frame-stability' if c['wait_frame_stability'] else '--no-wait-frame-stability'))
    if c['wait_sky_convergence']:
        args.append('--wait-sky-convergence')
    return args


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--replace-raw", action="store_true",
                        help="Подтвердить замену dataset/raw после успешной генерации")
    args = parser.parse_args()
    try:
        cfg = validate_config(json.loads(args.config.read_text(encoding="utf-8")))
        from ke_dataset import run_dataset
        run_dataset(cfg, replace_raw=args.replace_raw)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(f"[KE] ERROR {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
