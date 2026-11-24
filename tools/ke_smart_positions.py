"""Deterministic candidate bank for synthetic game scenes; not a collision map."""
from array import array
from hashlib import sha256
import json
import math
from pathlib import Path
import random
import re

BINS = ('open', 'light', 'moderate', 'heavy')
STEP = 8.
CLEARANCE = 5.5
RANGE = 24.
ELIGIBLE = ('building', 'rock', 'vegetation', 'small', 'unknown')
COLORS = ((87,170,106),(231,193,65),(231,135,53),(201,83,112))


def build_pool(bounds, objects):
    xmin,xmax,zmin,zmax = bounds
    if not all(map(math.isfinite,bounds)) or not xmin<xmax or not zmin<zmax:
        raise ValueError('Invalid candidate bank bounds')
    nx,nz = math.ceil((xmax-xmin)/STEP),math.ceil((zmax-zmin)/STEP)
    if nx*nz > 1048576:
        raise ValueError('Candidate bank exceeds grid budget')
    blocked=bytearray(nx*nz)
    nearest=array('f',[RANGE*RANGE])*(nx*nz)
    def span(lo,hi,origin,n):
        return max(0,math.ceil((lo-origin)/STEP-.5)),min(n-1,math.floor((hi-origin)/STEP-.5))
    for o in objects:
        if o.get('duplicate_of') is not None:
            continue
        lo,hi=o['bbox_min'],o['bbox_max']
        # Conservative horizontal broad phase using RAW collidable AABBs.
        # Low details are left to the native height/volume check.
        if o.get('has_collision_resource') and hi[1]-lo[1]>=.9:
            a,b=span(lo[0]-CLEARANCE,hi[0]+CLEARANCE,xmin,nx)
            c,d=span(lo[2]-CLEARANCE,hi[2]+CLEARANCE,zmin,nz)
            if a<=b:
                fill=b'\x01'*(b-a+1)
                for j in range(c,d+1):blocked[j*nx+a:j*nx+b+1]=fill
        if o.get('analysis_layer') not in ELIGIBLE:
            continue
        # Capped scoring bounds, separate from raw conservative broad phase.
        cap=32. if o['analysis_layer'] in ('building','rock') else 16.
        cx,cz=(lo[0]+hi[0])/2,(lo[2]+hi[2])/2
        lx,hx=max(lo[0],cx-cap/2),min(hi[0],cx+cap/2)
        lz,hz=max(lo[2],cz-cap/2),min(hi[2],cz+cap/2)
        a,b=span(lx-RANGE,hx+RANGE,xmin,nx);c,d=span(lz-RANGE,hz+RANGE,zmin,nz)
        dx2=[max(lx-(xmin+(i+.5)*STEP),0.,xmin+(i+.5)*STEP-hx)**2 for i in range(a,b+1)]
        for j in range(c,d+1):
            z=zmin+(j+.5)*STEP;dz2=max(lz-z,0.,z-hz)**2
            for i,v in enumerate(dx2,a):
                k=j*nx+i;score=v+dz2
                if score<nearest[k]:nearest[k]=score
    pools={key:[] for key in BINS}
    edge=0;blocked_count=0
    for j in range(nz):
        z=zmin+(j+.5)*STEP
        for i in range(nx):
            x=xmin+(i+.5)*STEP;k=j*nx+i
            if not xmin+CLEARANCE<=x<=xmax-CLEARANCE or not zmin+CLEARANCE<=z<=zmax-CLEARANCE:
                edge+=1;continue
            if blocked[k]:blocked_count+=1;continue
            d=nearest[k]
            key='open' if d>=24**2 else 'light' if d>=14**2 else 'moderate' if d>=9**2 else 'heavy'
            pools[key].append([x,z])
    return {'version':1,'bounds':list(bounds),'step_m':STEP,'clearance_proxy_m':CLEARANCE,
            'method':'raw_collidable_aabb_xz_exclusion_and_capped_nearest_obstacle_distance',
            'status':'UNVALIDATED_TERRAIN_AND_COLLISION','grid_points':nx*nz,'edge_excluded':edge,
            'aabb_excluded':blocked_count,'counts':{k:len(v) for k,v in pools.items()},'pools':pools}


def digest(path):
    h=sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def load_pool(root, map_id):
    folder=Path(root)/Path(map_id).stem
    path=folder/'placement-candidates.json'
    bank=json.loads(path.read_text(encoding='utf-8'))
    if bank.get('version')!=1 or bank.get('map_id')!=map_id or bank.get('source_sha256')!=digest(folder/'objects-native.tsv'):
        raise ValueError(f'Stale or incompatible candidate bank: {path}')
    return bank


class CandidateSampler:
    def __init__(self, bank, seed, margin=0.):
        self.bounds=tuple(bank['bounds']);xmin,xmax,zmin,zmax=self.bounds
        if len(self.bounds)!=4 or not all(map(math.isfinite,self.bounds)) or not xmin<xmax or not zmin<zmax:
            raise ValueError('Invalid placement cache bounds')
        self.rng=random.Random(seed);self.pools={};self.cursors=dict.fromkeys(BINS,0)
        self.margin=margin/100
        if not 0<=self.margin<.5 or set(bank['pools'])!=set(BINS):raise ValueError('Invalid cache/margin')
        seen=set()
        for key in BINS:
            pts=[]
            for x,z in bank['pools'][key]:
                u,v=(x-xmin)/(xmax-xmin),(z-zmin)/(zmax-zmin)
                if not (math.isfinite(u) and math.isfinite(v) and 0<=u<=1 and 0<=v<=1) or (x,z) in seen:
                    raise ValueError('Invalid or duplicate candidate bank position')
                seen.add((x,z))
                if self.margin<=u<=1-self.margin and self.margin<=v<=1-self.margin:pts.append((u,v,key))
            self.rng.shuffle(pts);self.pools[key]=pts
    def choose(self,target,count):
        if target not in BINS or not 1<=count<=64:raise ValueError('Invalid placement request')
        out=[];seen=set()
        def take(key):
            pool=self.pools[key]
            for _ in range(min(len(pool),count+1)):
                cursor=self.cursors[key]
                if cursor>=len(pool):self.rng.shuffle(pool);cursor=0
                point=pool[cursor];self.cursors[key]=cursor+1
                if point[:2] not in seen:
                    seen.add(point[:2]);out.append(point);return True
            return False
        # 75% nearby target, then other bands (including open), then explicit
        # uniform exploration if the requested pool is sparse or empty.
        order=sorted(BINS,key=lambda k:abs(BINS.index(k)-BINS.index(target)))
        for _ in range(max(1,math.ceil(count*.75))):
            if not any(take(k) for k in order):break
        explore=list(BINS);self.rng.shuffle(explore)
        while len(out)<count and any(take(k) for k in explore):
            explore=explore[1:]+explore[:1]
        while len(out)<count:
            u,v=self.rng.uniform(self.margin,1-self.margin),self.rng.uniform(self.margin,1-self.margin)
            if (u,v) not in seen:seen.add((u,v));out.append((u,v,'uniform_fallback'))
        return out


def frame_block(index, target, points, offset):
    rows=[f'target:i={BINS.index(target)}']
    for i,(u,v,kind) in enumerate(points):
        rows.extend((f'candidate_{index}_{i}:p3={u:.9f},{offset:.9f},{v:.9f}',f'source_{i}:t="{kind}"'))
    return '\n'.join(rows)+'\n'


def verify_frame(blk, target, points, bounds):
    def value(name,kind):
        match=re.search(rf'^{name}:{kind}=([^\r\n]+)',blk,re.M)
        if not match:raise ValueError(f'Missing smart placement metadata: {name}')
        return match.group(1).strip().strip('"')
    attempt=int(value('placement_candidate','i'))
    if not 0<=attempt<len(points) or int(value('smart_placement_target','i'))!=BINS.index(target):
        raise ValueError('Smart placement target or attempt mismatch')
    u,v,source=points[attempt]
    if value('smart_placement_source','t')!=source or value('smart_placement_check','t')!='ri_collision_bounds_static_rays_v1':
        raise ValueError('Smart placement source/check mismatch')
    x,y,z=map(float,value('tank_world','p3').split(','))
    xmin,xmax,zmin,zmax=bounds
    if not all(map(math.isfinite,(x,y,z))) or abs(x-(xmin+u*(xmax-xmin)))>.1 or abs(z-(zmin+v*(zmax-zmin)))>.1:
        raise ValueError('Native placement differs from published candidate')
    return {'requested_bin':target,'source':source,'attempt':attempt,'position_xz':[x,z],
            'check':'ri_collision_bounds_static_rays_v1'}
