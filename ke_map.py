"""Create a local map with tank positions. Image bounds and orientation must be calibrated."""
import argparse
import base64
import json
import math
import re
from pathlib import Path


def positions(run):
    result = []
    for line in (run / 'metadata.jsonl').read_text(encoding='utf-8').splitlines():
        item = json.loads(line)
        match = re.search(r'^tank_world:p3=([^\r\n]+)', item['dagor_frame_metadata_blk'], re.MULTILINE)
        if not match:
            raise ValueError('В метаданных отсутствует tank_world')
        xyz = [float(v.strip()) for v in match.group(1).split(',')]
        if len(xyz) != 3 or not all(math.isfinite(v) for v in xyz):
            raise ValueError('Неверные координаты танка')
        result.append({'frame': item['frame_index'], 'x': xyz[0], 'z': xyz[2]})
    if not result: raise ValueError('Нет кадров')
    return result


def read_landscape_bounds(run, *, fixed_clip=False):
    """Read world-space bounds recorded by Dagor, not image calibration."""
    found = []
    for line in (run / 'metadata.jsonl').read_text(encoding='utf-8').splitlines():
        item = json.loads(line)
        blk = item['dagor_frame_metadata_blk']
        matches = [re.search(r'^' + key + r':p3=([^\r\n]+)', blk, re.MULTILINE) for key in (('last_clip_min', 'last_clip_max') if fixed_clip else ('map_bounds_min', 'map_bounds_max'))]
        if not all(matches):
            continue
        values = [[float(v.strip()) for v in m.group(1).split(',')] for m in matches]
        if any(len(v) != 3 or not all(math.isfinite(x) for x in v) for v in values):
            raise ValueError('Некорректные границы ландшафта в метаданных')
        bounds = (values[0][0], values[1][0], values[0][2], values[1][2])
        if bounds[0] >= bounds[1] or bounds[2] >= bounds[3]:
            raise ValueError('Пустые границы ландшафта')
        found.append(bounds)
    if not found:
        raise ValueError('В серии нет нужных границ. Для last clip пересоберите Dagor и создайте новую серию.')
    if any(b != found[0] for b in found):
        raise ValueError('В серии обнаружены разные границы ландшафта')
    return found[0]


def create(run, image, out, bounds=None, flip_x=False, flip_z=False, fixed_clip=False):
    if bounds is None: bounds = read_landscape_bounds(run, fixed_clip=fixed_clip)
    x0, x1, z0, z1 = bounds
    if not all(math.isfinite(x) for x in bounds) or x0 >= x1 or z0 >= z1:
        raise ValueError('Границы изображения некорректны')
    if image.suffix.lower() not in ('.png', '.jpg', '.jpeg'):
        raise ValueError('Используйте PNG или JPEG')
    points = positions(run)
    for p in points:
        p['u'] = (p['x'] - x0) / (x1 - x0)
        p['v'] = (p['z'] - z0) / (z1 - z0)
        if flip_x: p['u'] = 1 - p['u']
        if flip_z: p['v'] = 1 - p['v']
    mime = 'image/png' if image.suffix.lower() == '.png' else 'image/jpeg'
    encoded = base64.b64encode(image.read_bytes()).decode('ascii')
    data = json.dumps(points, ensure_ascii=True)
    document = '''<!doctype html><meta charset="utf-8"><title>Позиции танка</title>
<style>body{font:16px sans-serif;background:#222;color:white} .map{position:relative;display:inline-block;max-width:95vw}.map img{display:block;width:100%;height:auto}.dot{position:absolute;width:12px;height:12px;transform:translate(-50%,-50%);border:2px solid white;border-radius:50%;background:red;box-sizing:border-box}</style>
<h2>Позиции танка</h2><p>Для обзорной текстуры использованы её границы из Dagor; для другого изображения нужна проверка привязки. Ориентация точек ещё требует визуальной проверки.</p><div class="map"><img src="data:MIME;base64,IMAGE" id="map"></div><p id="info"></p>
<script>const points=POINTS;const root=document.querySelector('.map');let outside=0;for(const p of points){if(p.u<0||p.u>1||p.v<0||p.v>1){outside++;continue;}const d=document.createElement('div');d.className='dot';d.style.left=(100*p.u)+'%';d.style.top=(100*p.v)+'%';d.title=`Кадр ${p.frame}: X=${p.x}, Z=${p.z}`;root.append(d)}document.querySelector('#info').textContent=`Кадров: ${points.length}; за границей изображения: ${outside}. Наведите на точку для координат.`;</script>'''.replace('MIME', mime).replace('IMAGE', encoded).replace('POINTS', data)
    with out.open('x', encoding='utf-8') as f: f.write(document)
    return len(points)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True); p.add_argument('--image', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True); p.add_argument('--bounds', type=float, nargs=4, metavar=('XMIN','XMAX','ZMIN','ZMAX'))
    p.add_argument('--fixed-clip', action='store_true', help='Use Dagor fixed clip world bounds')
    p.add_argument('--flip-x', action='store_true'); p.add_argument('--flip-z', action='store_true')
    a=p.parse_args()
    try: print(f'[KE] Точек: {create(a.run,a.image,a.out,a.bounds,a.flip_x,a.flip_z,a.fixed_clip)}; файл: {a.out}')
    except (ValueError, OSError, KeyError) as exc: p.exit(1, f'[KE] ERROR {exc}\n')
if __name__ == '__main__': main()
