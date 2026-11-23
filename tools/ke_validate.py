"""Validate sequence files and annotations; no inference about object visibility."""
import argparse
import json
import math
import sys
from pathlib import Path
from kolobanoveye import verify_png
from tools.ke_png_fast import validate_rgb_png


def validate(run):
    run = run.resolve()
    lines = (run / 'metadata.jsonl').read_text(encoding='utf-8').splitlines()
    if not lines: raise ValueError('metadata.jsonl пуст')
    plan = json.loads((run / 'plan.json').read_text(encoding='utf-8'))
    images, indices = set(), set()
    for line in lines:
        item = json.loads(line)
        index = item['frame_index']
        if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index in indices:
            raise ValueError('Повторяющийся или неверный номер кадра')
        indices.add(index)
        path = (run / item['image']).resolve()
        if not path.is_relative_to(run) or path in images or not path.is_file():
            raise ValueError('Отсутствует, повторяется или выходит за каталог путь изображения')
        images.add(path)
        ann = item['annotation']
        box = ann['bbox_xywh']
        if ann['class'] != 'tank' or ann['bbox_kind'] != 'approximate_amodal':
            raise ValueError('Неверный класс или тип bbox')
        if len(box) != 4 or any(not isinstance(x, (int,float)) or isinstance(x,bool) or not math.isfinite(x) for x in box):
            raise ValueError('Некорректные координаты bbox')
        # Metadata from Dagor records the actual frame dimensions.
        import re
        blk = item['dagor_frame_metadata_blk']
        def get_int(name):
            m = re.search(r'^' + name + r':i=(\d+)\s*$', blk, re.MULTILINE)
            if not m: raise ValueError('В BLK отсутствует ' + name)
            return int(m.group(1))
        width, height = get_int('width'), get_int('height')
        verify_png(path, width, height)
        decoded_width, decoded_height = validate_rgb_png(path, width, height)
        if (decoded_width, decoded_height) != (width, height):
            raise ValueError("Размер изображения не соответствует метаданным")
        x, y, w, h = box
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > width+.01 or y+h > height+.01:
            raise ValueError('BBox за пределами изображения')
    planned = len(plan.get('cameras', []))
    if planned != len(lines) or indices != set(range(planned)):
        raise ValueError(f'Несоответствие плана и метаданных: план={planned}, записей={len(lines)}')
    return {'frames': len(lines), 'class': 'tank', 'bbox_kind': 'approximate_amodal',
            'note': 'Проверена структура; корректность видимости и качество bbox не оценивались.'}


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--run', required=True, type=Path); a = p.parse_args()
    try: print(json.dumps(validate(a.run), ensure_ascii=False, indent=2)); return 0
    except (OSError, ValueError, KeyError, TypeError) as exc: print(f'[KE] ERROR {exc}', file=sys.stderr); return 1

if __name__ == '__main__': sys.exit(main())
