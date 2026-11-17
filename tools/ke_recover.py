"""Recover verified image/metadata pairs from an interrupted sequence, without resuming Dagor."""
import argparse
import json
import re
import sys
from pathlib import Path
from tools.ke_annotation import parse_annotation
from tools.ke_frame_journal import JOURNAL, read_journal
from tools.ke_pixel_debug import load_png
from kolobanoveye import select_camera_attempt, verify_png


def recover(run):
    run = run.resolve()
    if (run / 'metadata.jsonl').exists():
        raise ValueError('Серия уже завершена: metadata.jsonl существует')
    plan = json.loads((run / 'plan.json').read_text(encoding='utf-8'))
    if plan.get('schema_version') != 3 or not isinstance(plan.get('cameras'), list):
        raise ValueError('Поддерживается только план schema_version=3')
    cams = plan['cameras']
    per = plan['frames_per_position']
    if isinstance(per, bool) or not isinstance(per, int) or per < 1 or not cams:
        raise ValueError('Неверный план композиций')
    target = run / 'metadata.recovered.jsonl'
    summary_path = run / 'recovery-report.json'
    if target.exists() or summary_path.exists():
        raise ValueError('Отчёт восстановления уже существует; файлы не перезаписаны')
    journal = run / JOURNAL
    journal_records = read_journal(journal, allow_truncated=True) if journal.is_file() else None
    records, failures = [], []
    for i, candidates in enumerate(cams):
        image = run / 'images' / f'{i:06d}.png'
        meta = run / f'frame-{i:06d}.pending.blk'
        has_meta = i in journal_records if journal_records is not None else meta.is_file()
        if not image.is_file() and not has_meta:
            continue
        try:
            if not image.is_file() or not has_meta:
                raise ValueError('Отсутствует PNG или соответствующий BLK')
            raw = journal_records[i] if journal_records is not None else meta.read_text(encoding='utf-8')
            ann = parse_annotation(raw)
            verify_png(image, ann['width'], ann['height'])
            w, h, _ = load_png(image)
            if (w, h) != (ann['width'], ann['height']):
                raise ValueError('Размер изображения не соответствует метаданным')
            attempt, camera = select_camera_attempt(raw, candidates)
            m = re.search(r'^composition_index:i=(\d+)\s*$', raw, re.MULTILINE)
            if not m or int(m.group(1)) != i // per:
                raise ValueError('Некорректный номер композиции')
            records.append({'seed': plan['seed'], 'frame_index': i, 'composition_index': int(m.group(1)),
                'tank_yaw_deg': plan['tank_yaw_deg'], 'camera_azimuth_deg': camera['azimuth_deg'],
                'camera_horizontal_distance': camera['horizontal_distance'],
                'planned_camera_elevation_deg': camera['elevation_deg'] if camera.get('height_above_ground') is None else None,
                'planned_camera_offset': camera['offset'], 'planned_height_above_ground': camera.get('height_above_ground'), 'camera_attempt': attempt, 'image': f'images/{i:06d}.png',
                'annotation': ann['annotations'][0], 'dagor_frame_metadata_blk': raw})
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            failures.append({'frame_index': i, 'reason': str(exc)})
    if not records:
        raise ValueError('Нет пар PNG и BLK, прошедших проверку; исходные файлы сохранены')
    result = {'planned_frames': len(cams), 'recovered_frames': len(records),
              'missing_indices': sorted(set(range(len(cams))) - {r['frame_index'] for r in records}),
              'invalid': failures, 'note': 'Данные восстановлены из уже сохранённых кадров; повторный рендер и продолжение не выполнялись.'}
    # Exclusive creation prevents overwriting existing work. Never rename or delete originals.
    with target.open('x', encoding='utf-8') as f:
        for record in records: f.write(json.dumps(record, ensure_ascii=False) + '\n')
    with summary_path.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2); f.write('\n')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--run', required=True, type=Path); a = p.parse_args()
    try: print(json.dumps(recover(a.run), ensure_ascii=False, indent=2)); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f'[KE] ERROR {exc}', file=sys.stderr); return 1

if __name__ == '__main__': sys.exit(main())
