"""Offline semantic hints and bounded spatial groups for game-scene inventory."""
from collections import Counter, defaultdict
import math
import re

LAYERS = ('building', 'rock', 'vegetation', 'small', 'unknown')
COLORS = dict(zip(LAYERS, ((232,149,57),(169,143,117),(63,173,105),(87,159,229),(183,126,207))))
SECTOR_CELLS = 8
SUB = 4


def classify(o):
    n = o.get('resource','').casefold()
    x,h,z = (b-a for a,b in zip(o['bbox_min'],o['bbox_max']))
    if 'icefloe' in n:
        return 'water_surface'
    if 'clsn' in n or 'collision_only' in n:
        return 'collision_only'
    if re.search(r'rails?_sleeper|pavement_border|ground_plant|(?:^|_)decal[s]?(?:_|$)|(?:^|_)grass(?:_|$)', n):
        return 'detail'
    if re.search(r'ropeway_rope|(?:^|_)(?:wire|cable|rope)(?:_|$)', n) and max(x,z)>32:
        return 'linear'
    if h < .9:
        return 'detail'
    if re.search(r'tree|bush|liveoak|forest|shrub|vegetation', n):
        return 'vegetation'
    if re.search(r'stone|rock|cliff|boulder', n) and not re.search(r'fence|wall|pavement|column',n):
        return 'rock' if h>=1.5 and min(x,z)>=1.5 else 'small'
    if re.search(r'house|building|hangar|factory|barn|barrack|warehouse|church|hut|garage|silo|chimney|power_plant|chemical_tank|oil_tank|water_tower|^af_cc_|^af_ruin_wall|^af_loading_platform|^af_conveyor',n) and not re.search(r'aerial|window|frame|ladder|porch|pipe|column|grid|entrance',n) and h>=2 and max(x,z)>=3:
        return 'building'
    if re.search(r'fence|barbedwire|wall|column|lamp|post|pipe|debris|barrel|barricade|hedgehog|hay|window|ladder|aerial|frame|porch|entrance',n):
        return 'small'
    return 'unknown' if h>=2 and min(x,z)>=1.5 else 'small'


def build_index(bounds, objects, cell=16.):
    if not math.isfinite(cell) or cell<=0:
        raise ValueError('Invalid cell size')
    xmin,xmax,zmin,zmax=bounds
    nx,nz=math.ceil((xmax-xmin)/cell),math.ceil((zmax-zmin)/cell)
    if nx*nz>1048576:
        raise ValueError('Spatial grid exceeds limit')
    def idx(x,z,step,limitx,limitz):
        return max(0,min(limitx-1,int((x-xmin)//step))),max(0,min(limitz-1,int((z-zmin)//step)))
    centers,footprints=defaultdict(list),defaultdict(list)
    roots=defaultdict(list)
    samples=defaultdict(dict)
    seen={}
    counts=Counter()
    refs=0
    step=cell/SUB
    for o in objects:
        oid=o['id'];lo,hi=o['bbox_min'],o['bbox_max'];p=o['position']
        layer=classify(o);o['analysis_layer']=layer
        key=(o.get('source'),o.get('layer'),o.get('pool'),o.get('resource'),tuple(p),tuple(lo),tuple(hi),o.get('has_collision_resource'))
        duplicate=seen.get(key)
        o['duplicate_of']=duplicate
        if duplicate is None:
            seen[key]=oid
        counts[layer]+=1
        root=idx(p[0],p[2],cell,nx,nz)
        centers[root].append(oid)
        if hi[0]>=xmin and lo[0]<=xmax and hi[2]>=zmin and lo[2]<=zmax:
            x0,z0=idx(lo[0],lo[2],cell,nx,nz);x1,z1=idx(hi[0],hi[2],cell,nx,nz)
            refs+=(x1-x0+1)*(z1-z0+1)
            if refs>10000000:
                raise ValueError('Raw AABB index exceeds reference budget')
            for z in range(z0,z1+1):
                for x in range(x0,x1+1):footprints[x,z].append(oid)
        if layer not in LAYERS or duplicate is not None:
            continue
        roots[layer,*root].append(oid)
        # Capped render AABB proxy; never a physical occupancy mask. Max per
        # subcell prevents overlapping building parts from inflating density.
        cap=32. if layer in ('building','rock') else 16.
        cx,cz=(lo[0]+hi[0])/2,(lo[2]+hi[2])/2
        a,b=max(xmin,lo[0],cx-cap/2),min(xmax,hi[0],cx+cap/2)
        c,d=max(zmin,lo[2],cz-cap/2),min(zmax,hi[2],cz+cap/2)
        if a>=b or c>=d:continue
        x0,z0=idx(a,c,step,nx*SUB,nz*SUB);x1,z1=idx(b,d,step,nx*SUB,nz*SUB)
        for z in range(z0,z1+1):
            az=zmin+z*step; bz=min(zmax,az+step)
            for x in range(x0,x1+1):
                ax=xmin+x*step;bx=min(xmax,ax+step)
                area=max(0,min(b,bx)-max(a,ax))*max(0,min(d,bz)-max(c,az))
                if area:
                    k=(layer,x//SUB,z//SUB); sub=(x%SUB,z%SUB)
                    samples[k][sub]=max(samples[k].get(sub,0),area)
    scores=defaultdict(dict)
    for (layer,x,z),vals in samples.items():
        area=min(cell,xmax-xmin-x*cell)*min(cell,zmax-zmin-z*cell)
        scores[x,z][layer]=round(min(1.,sum(vals.values())/area),4)
    # Four-connected root cells by semantic layer, bounded to 128m sectors.
    # This is a local sampling group, not a claim about city/forest boundaries.
    remaining=set(roots);groups=[];membership=defaultdict(dict)
    for seed in sorted(remaining):
        if seed not in remaining:continue
        layer,sx,sz=seed;sector=(sx//SECTOR_CELLS,sz//SECTOR_CELLS)
        remaining.remove(seed);stack=[seed];members=[];ids=[]
        while stack:
            key=stack.pop();_,x,z=key;members.append((x,z));ids.extend(roots[key])
            for dx,dz in ((-1,0),(1,0),(0,-1),(0,1)):
                k=(layer,x+dx,z+dz)
                if k in remaining and ((x+dx)//SECTOR_CELLS,(z+dz)//SECTOR_CELLS)==sector:
                    remaining.remove(k);stack.append(k)
        gid=len(groups)+1
        for p in members:membership[p][layer]=gid
        groups.append({'id':gid,'layer':layer,'cells':len(members),'object_ids':sorted(ids),
                       'cell_bounds':[min(x for x,z in members),max(x for x,z in members),min(z for x,z in members),max(z for x,z in members)]})
    cells=[]
    for x,z in sorted(set(centers)|set(footprints)|set(scores),key=lambda p:(p[1],p[0])):
        sc=scores[x,z];gs=membership[x,z]
        cells.append({'x':x,'z':z,'center_ids':centers.get((x,z),[]),'bbox_ids':footprints.get((x,z),[]),
                      'layer_scores':sc,'density':max(sc.values(),default=0.),'groups_by_layer':gs})
    return {'version':2,'cell_size_m':cell,'nx':nx,'nz':nz,'bounds':bounds,'cells':cells,'groups':groups,
            'layer_counts':dict(counts),'duplicate_records':len(objects)-len(seen),
            'eligible_unique':sum(len(g['object_ids']) for g in groups),'sector_size_m':cell*SECTOR_CELLS}
