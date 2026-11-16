"""Assemble captured terrain clipmap tiles in world space; uncovered areas stay black."""
import argparse
import csv
import json
import math
import sys
from pathlib import Path
from ke_map import read_landscape_bounds, create
from ke_review import write_rgb_png


def read_ppm(path, expected):
    with path.open('rb') as f:
        if f.readline().strip() != b'P6': raise ValueError(f'Неверный PPM: {path.name}')
        dims = f.readline().split()
        if len(dims) != 2 or tuple(map(int, dims)) != (expected, expected) or f.readline().strip() != b'255':
            raise ValueError(f'Неверные размеры PPM: {path.name}')
        data = f.read(expected * expected * 3 + 1)
        if len(data) != expected * expected * 3: raise ValueError(f'Неверный размер данных: {path.name}')
        return data


def assemble(run, out, width=1024):
    if not 64 <= width <= 2048: raise ValueError('Размер карты должен быть 64–2048')
    run = run.resolve(); out = out.resolve()
    x0, x1, z0, z1 = read_landscape_bounds(run)
    height = max(1, min(2048, round(width * (z1-z0) / (x1-x0))))
    directory = run / 'ke-clipmap-tiles'
    with (directory / 'tiles.csv').open(encoding='ascii', newline='') as stream:
        rows = list(csv.reader(stream))
    if not rows: raise ValueError('Нет сохранённых тайлов')
    tiles=[]
    for row in rows:
        if len(row)!=8: raise ValueError('Неверная запись тайла')
        name=row[0]; path=(directory/name).resolve()
        if path.parent != directory.resolve() or path.suffix != '.ppm': raise ValueError('Недопустимый путь тайла')
        ax,az,bx,bz=map(float,row[1:5]); mip,size,border=map(int,row[5:])
        if not all(math.isfinite(v) for v in (ax,az,bx,bz)) or ax >= bx or az >= bz or not 0 <= mip <= 64 or not 1 <= size <= 4096 or not 0 <= border < size//2:
            raise ValueError('Неверные координаты или параметры тайла')
        # Cut the rendered border to avoid sampling adjacent tiles.
        dx=(bx-ax)*border/size; dz=(bz-az)*border/size
        tiles.append((mip,path,ax+dx,az+dz,bx-dx,bz-dz,size,border,ax,az,bx,bz))
    pixels=bytearray(width*height*3); coverage=bytearray(width*height)
    # Coarse first; finest mip overwrites coarse. Last tile wins on equal mip.
    for mip,path,ax,az,bx,bz,size,border,rx,rz,sx,sz in sorted(tiles, key=lambda t:-t[0]):
        left=max(0, math.floor((ax-x0)/(x1-x0)*width)); right=min(width, math.ceil((bx-x0)/(x1-x0)*width))
        top=max(0, math.floor((az-z0)/(z1-z0)*height)); bottom=min(height, math.ceil((bz-z0)/(z1-z0)*height))
        if left>=right or top>=bottom: continue
        data=read_ppm(path,size)
        for py in range(top,bottom):
            wz=z0+(py+.5)*(z1-z0)/height
            if wz < az or wz >= bz: continue
            ty=min(size-1,max(0,int((wz-rz)/(sz-rz)*size)))
            for px in range(left,right):
                wx=x0+(px+.5)*(x1-x0)/width
                if wx < ax or wx >= bx: continue
                tx=min(size-1,max(0,int((wx-rx)/(sx-rx)*size)))
                src=(ty*size+tx)*3; dst=(py*width+px)*3
                pixels[dst:dst+3]=data[src:src+3]; coverage[py*width+px]=1
    covered=sum(coverage)
    if not covered: raise ValueError('Тайлы не пересекают границы ландшафта')
    out.mkdir(parents=True,exist_ok=False)
    image=out/'terrain.png';write_rgb_png(image,width,height,pixels)
    create(run,image,out/'positions.html', (x0,x1,z0,z1))
    report={'tiles':len(tiles),'coverage_percent':round(100*covered/(width*height),3),'bounds_xz':[x0,x1,z0,z1], 'dimensions':[width,height], 'orientation':'x_right_z_down_unverified','note':'Uncovered pixels are black; tile orientation, channel order and full coverage require Windows verification.'}
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--width',type=int,default=1024);a=p.parse_args()
    try: print(json.dumps(assemble(a.run,a.out,a.width),ensure_ascii=False,indent=2))
    except (OSError,ValueError,KeyError) as exc: print(f'[KE] ERROR {exc}',file=sys.stderr);return 1
    return 0
if __name__ == '__main__': sys.exit(main())
