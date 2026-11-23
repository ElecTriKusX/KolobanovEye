"""Experimental seeded sequence of independent real Dagor captures; not a batch renderer."""
import argparse
import json
import random
import subprocess
import sys
from pathlib import Path


def plan(seed, count):
    rng = random.Random(seed)
    return [round(rng.uniform(-180, 180), 4) for _ in range(count)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dagor', type=Path, required=True)
    parser.add_argument('--game', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--count', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.count <= 100:
        parser.error('--count must be between 1 and 100')
    game = args.game.resolve()
    out = args.out.resolve()
    if out == game or out.is_relative_to(game):
        parser.error('output must be outside the game directory')
    out.mkdir(parents=True, exist_ok=False)
    angles = plan(args.seed, args.count)
    (out / 'plan.json').write_text(json.dumps({'seed': args.seed, 'yaw_degrees': angles}, indent=2) + '\n', encoding='utf-8')
    script = Path(__file__).resolve().parent.parent / 'kolobanoveye.py'
    for i, angle in enumerate(angles):
        frame_out = out / f'frame_{i:04d}'
        cmd = [sys.executable, str(script), 'frame', '--dagor', str(args.dagor), '--game', str(game),
               '--out', str(frame_out), '--yaw', str(angle)]
        print(f'[KE] PILOT_FRAME index={i} yaw={angle}', flush=True)
        result = subprocess.run(cmd)
        if result.returncode:
            print(f'[KE] PILOT_FAILED index={i} exit={result.returncode}', file=sys.stderr)
            return result.returncode
        annotation = json.loads((frame_out / 'annotation.json').read_text(encoding='utf-8'))
        record = {'seed': args.seed, 'frame_index': i, 'yaw_deg': angle,
                  'image': f'{frame_out.name}/frame.png', 'metadata': f'{frame_out.name}/frame.blk',
                  'annotation': annotation['annotations'][0]}
        with (out / 'metadata.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    return 0

if __name__ == '__main__':
    sys.exit(main())
