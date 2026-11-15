"""Compare saved render passes by screen coordinates. No GPU shader capture is performed."""
import argparse
import csv
import json
import struct
import zlib
from pathlib import Path


def load_png(path):
    data = path.read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n' or len(data) > 100 * 1024 * 1024:
        raise ValueError('Invalid or oversized PNG')
    pos, chunks, hdr = 8, [], None
    while pos + 12 <= len(data):
        n = int.from_bytes(data[pos:pos+4], 'big')
        end = pos + 12 + n
        if end > len(data):
            raise ValueError('Truncated PNG')
        typ, payload = data[pos+4:pos+8], data[pos+8:pos+8+n]
        if zlib.crc32(typ + payload) & 0xffffffff != int.from_bytes(data[pos+8+n:end], 'big'):
            raise ValueError('Invalid PNG checksum')
        if typ == b'IHDR': hdr = struct.unpack('>IIBBBBB', payload)
        if typ == b'IDAT': chunks.append(payload)
        if typ == b'IEND': break
        pos = end
    if not hdr: raise ValueError('Missing IHDR')
    w,h,depth,color,compression,filtering,interlace = hdr
    bpp = {0:1, 2:3, 4:2, 6:4}.get(color)
    if not bpp or depth != 8 or compression or filtering or interlace or not (0 < w <= 4096 and 0 < h <= 4096):
        raise ValueError('Only non-interlaced 8-bit RGB/RGBA/grayscale PNG up to 4096 supported')
    stride = w*bpp
    size = (stride+1)*h
    dec = zlib.decompressobj()
    raw = dec.decompress(b''.join(chunks), size+1)
    if len(raw) != size or not dec.eof: raise ValueError('Invalid PNG image stream')
    rgb = bytearray(w*h*3)
    prev = bytearray(stride)
    for y in range(h):
        off = y*(stride+1); mode = raw[off]; row = bytearray(raw[off+1:off+1+stride])
        if mode > 4: raise ValueError('Invalid PNG filter')
        for i in range(stride):
            a = row[i-bpp] if i >= bpp else 0; b = prev[i]; c = prev[i-bpp] if i >= bpp else 0
            if mode == 1: v = a
            elif mode == 2: v = b
            elif mode == 3: v = (a+b)//2
            elif mode == 4:
                p = a+b-c; pa,pb,pc = abs(p-a),abs(p-b),abs(p-c)
                v = a if pa <= pb and pa <= pc else b if pb <= pc else c
            else: v = 0
            row[i] = (row[i]+v)&255
        for x in range(w):
            v = row[x*bpp:x*bpp+(1 if color in (0,4) else 3)]
            rgb[(y*w+x)*3:(y*w+x+1)*3] = bytes(v)*3 if len(v)==1 else v
        prev = row
    return w,h,rgb


def compare(albedo, frame, cols=16, rows=9):
    w,h,a = albedo; fw,fh,b = frame
    if (w,h)!=(fw,fh): raise ValueError('Image dimensions differ; direct pixel comparison invalid')
    cells = []
    for gy in range(rows):
        for gx in range(cols):
            x0,x1 = gx*w//cols,(gx+1)*w//cols
            y0,y1 = gy*h//rows,(gy+1)*h//rows
            asum=[0,0,0]; bsum=[0,0,0]; color_to_dark=black_a=0; n=(x1-x0)*(y1-y0)
            for y in range(y0,y1):
                for x in range(x0,x1):
                    i=(y*w+x)*3; av=a[i:i+3]; bv=b[i:i+3]
                    for k in range(3): asum[k]+=av[k]; bsum[k]+=bv[k]
                    black_a += max(av)<=5
                    color_to_dark += max(av)>=30 and max(bv)<=10
            cells.append({'col':gx,'row':gy,'bounds':[x0,y0,x1,y1],
                'albedo_mean':[round(v/n,2) for v in asum], 'frame_mean':[round(v/n,2) for v in bsum],
                'albedo_near_black_pct':round(100*black_a/n,2),
                'albedo_color_to_frame_dark_pct':round(100*color_to_dark/n,2)})
    return cells


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',required=True,type=Path)
    p.add_argument('--resolve', action='store_true', help='Compare G-buffer MRT0 immediately before resolve with its output')
    p.add_argument('--point',nargs=2,type=int,action='append',metavar=('X','Y'),default=[])
    args=p.parse_args(); run=args.run.resolve()
    if not run.is_dir(): p.error('Run directory does not exist')
    paths={'albedo':run/'render-debug/static-albedo.png','frame':run/'frame.png',
           'depth_mask':run/'render-debug/static-depth-mask.png',
           'normal':run/'render-debug/static-normal.png','lighting':run/'render-debug/static-lighting.png'}
    if args.resolve:
        paths['albedo'] = run/'render-debug/resolve-input-mrt0.png'
        paths['frame'] = run/'render-debug/resolve-output.png'
        paths.pop('normal')
        paths['mrt1_packed'] = run/'render-debug/resolve-input-mrt1.png'
    images={}; missing=[]
    for name,path in paths.items():
        if path.exists(): images[name]=load_png(path)
        else: missing.append(name)
    if 'frame' not in images or 'albedo' not in images: p.error('Need frame.png and render-debug/static-albedo.png')
    w,h,_=images['frame']; cells=compare(images['albedo'],images['frame']); points=[]
    for x,y in args.point:
        if not (0<=x<w and 0<=y<h): p.error(f'Point {x},{y} outside {w}x{h}')
        values={}
        for name,(iw,ih,rgb) in images.items():
            if (iw,ih)==(w,h): values[name]=list(rgb[(y*w+x)*3:(y*w+x+1)*3])
        points.append({'x':x,'y':y,'rgb':values})
    result={'resolution':[w,h], 'mode':'resolve' if args.resolve else 'static_vs_final', 'missing_passes':missing,'points':points,'grid':cells,
        'limitations':['Albedo and frame are separate captures: dynamic objects, overlays, temporal effects and timing may differ.',
          'PNG RGB values may use different color spaces and exposure; comparison is descriptive, not a lighting calculation.',
          'No object/terrain segmentation; cells can include sky, trees, tank and editor overlays.',
          'Depth mask encoding and meaning must be checked separately; it is not a calibrated depth map.',
          'MRT1 is a raw packed buffer, not decoded normals; resolve output is lit color, not an isolated lighting term.',
          'Actual GPU shader registers and individual shader execution are not captured.']}
    out=run/'diagnostics'; out.mkdir(exist_ok=True)
    (out/'pixel-report.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    with (out/'pixel-grid.csv').open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=cells[0].keys()); writer.writeheader();writer.writerows(cells)
    ranked=sorted(cells,key=lambda c:c['albedo_color_to_frame_dark_pct'],reverse=True)
    print('[KE] Missing passes:', ', '.join(missing) or 'none')
    print('[KE] Top areas with albedo RGB >=30 and final RGB <=10 (may include objects / timing differences):')
    hits=[c for c in ranked if c['albedo_color_to_frame_dark_pct'] > 0]
    if not hits: print('[KE] No pixels passed the strict threshold; this does not rule out darkening.')
    for c in hits[:8]: print('[KE]',c['bounds'],str(c['albedo_color_to_frame_dark_pct'])+'%', 'albedo',c['albedo_mean'],'frame',c['frame_mean'])
    print('[KE] Reports:',out/'pixel-report.json',out/'pixel-grid.csv')
    print('[KE] Pixel correspondence and color spaces are not verified. Inspect report limitations.')

if __name__=='__main__': main()
