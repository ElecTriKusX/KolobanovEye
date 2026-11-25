"""Persistent hints for offline game-scene composition; never quota credit.

SQLite transactions avoid partial writes and concurrent lost counter updates.
Only audited full-mask observations are learned. Replays use the ordinary
placement checks and full render, with a bounded fraction of new proposals.
"""
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from collections import Counter

VERSION=1
BINS=("light","moderate","heavy")
CELL=32
LIMIT=512
SCOPE_KEYS=("width","height","fov","tank_yaw","ground_offset","minimum_ground_y",
    "max_height_range","camera_clearance","camera_height_min","camera_height_max",
    "distance_min","distance_max","map_margin_percent","elevation_min","elevation_max",
    "clipmap_feedback","turret_yaw_min","turret_yaw_max","gun_pitch_min","gun_pitch_max")


def scope_key(cfg,mid,bank,model,skeleton,map_digest,asset_digest,objects_digest):
    payload={"version":VERSION,"map":mid,"bank":bank["source_sha256"],
             "objects":objects_digest,"map_sha256":map_digest,"asset_sha256":asset_digest,
             "model":model,"skeleton":skeleton,"settings":{k:cfg.get(k) for k in SCOPE_KEYS}}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,allow_nan=False).encode()).hexdigest()


def sector(x,z):
    return math.floor(x/CELL),math.floor(z/CELL)


def _pair(value):
    if not isinstance(value,(list,tuple)) or len(value)!=2:
        raise ValueError("Invalid cached coordinate")
    p=tuple(float(x) for x in value)
    if not all(map(math.isfinite,p)):
        raise ValueError("Nonfinite cached coordinate")
    return p


def validate_hint(row):
    target=row["target"]
    if target not in BINS:
        raise ValueError("Invalid cached visibility bin")
    x,z=_pair(row["position"])
    eye=_pair(row["eye"])
    aim=_pair(row["aim"])
    height=float(row["height"])
    if not math.isfinite(height) or height<0:
        raise ValueError("Invalid cached height")
    return {"target":target,"position":[x,z],"eye":list(eye),"aim":list(aim),"height":height}


def hint_from_frame(record,fixed,target):
    """Learn only a confirmed final fixed-camera pose with a matching mask."""
    if not fixed or target not in BINS or fixed.get("estimate") is None:
        return None
    final=fixed.get("final_view",{})
    if not final.get("selected") or record["visibility_measurement"]["visibility_bin"]!=target:
        return None
    group=fixed["camera"]
    if group.get("origin")=="scene_cache" or fixed["selected_index"]>=group["count"]:
        return None
    eye=final["actual_eye"]
    return validate_hint({"target":target,"position":record["smart_placement"]["position_xz"],
                          "eye":[eye[0],eye[2]],"aim":group["aim_xz"],"height":group["height"]})


def propose(hint,points,bounds,cfg):
    """Revalidate bounds and distances before publishing new local points."""
    hint=validate_hint(hint)
    if len(points)<4:
        return points,None
    x,z=hint["position"]
    ex,ez=hint["eye"]
    ax,az=hint["aim"]
    height=hint["height"]
    xmin,xmax,zmin,zmax=bounds
    sx,sz=xmax-xmin,zmax-zmin
    if not (sx>0 and sz>0 and xmin<=ex<=xmax and zmin<=ez<=zmax and
            xmin<=ax<=xmax and zmin<=az<=zmax and
            max(cfg["camera_clearance"],cfg["camera_height_min"])<=height<=cfg["camera_height_max"]):
        return points,None
    dist=math.hypot(ex-x,ez-z)
    if not cfg["distance_min"]<=dist<=cfg["distance_max"] or dist<=0:
        return points,None
    dx,dz=(ex-x)/dist,(ez-z)/dist
    margin=cfg.get("map_margin_percent",0.)/100
    local=[]
    # Do not replay the exact old tank position. A new yaw and screen
    # composition come from the normal generator plan.
    for shift in (-1.5,1.5,-3.,3.,-5.,5.):
        xx,zz=x+shift*dz,z-shift*dx
        u,v=(xx-xmin)/sx,(zz-zmin)/sz
        dd=math.hypot(xx-ex,zz-ez)
        if (margin<=u<=1-margin and margin<=v<=1-margin and
                cfg["distance_min"]<=dd<=cfg["distance_max"]):
            local.append((u,v,"scene_cache_local"))
    local=local[:min(6,len(points)-1)]
    if len(local)<2:
        return points,None
    used={p[:2] for p in local}
    combined=(local+[p for p in points if p[:2] not in used])[:len(points)]
    if len(combined)!=len(points):
        return points,None
    return combined,{"count":len(local),"eye_xz":[ex,ez],"aim_xz":[ax,az],
                     "height":height,"origin":"scene_cache"}


class SceneCache:
    def __init__(self,path,scopes):
        self.path=Path(path)
        self.scopes=scopes
        self.pending=[]
        self.used_sectors=set()
        self.entries={}
        self.stats={"loaded":0,"replays":0,"hits":0,"misses":0,"technical_rejections":0,
                    "new_hints":0,"committed":False}
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS hints_v1(
                scope TEXT NOT NULL, target TEXT NOT NULL, sx INTEGER NOT NULL, sz INTEGER NOT NULL,
                hint TEXT NOT NULL, hits INTEGER NOT NULL DEFAULT 0, misses INTEGER NOT NULL DEFAULT 0,
                technical INTEGER NOT NULL DEFAULT 0, uses INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(scope,target,sx,sz))""")
            for mid,scope in scopes.items():
                rows=db.execute("""SELECT target,sx,sz,hint,hits,misses,uses FROM hints_v1
                    WHERE scope=? ORDER BY (hits+1.0)/(hits+misses+2) DESC,uses ASC,target,sx,sz LIMIT ?""",
                    (scope,LIMIT)).fetchall()
                entries=[]
                for target,sx,sz,raw,hits,misses,uses in rows:
                    try:
                        hint=validate_hint(json.loads(raw))
                        if hint["target"]!=target or sector(*hint["position"])!=(sx,sz):
                            continue
                    except (ValueError,TypeError,KeyError,OverflowError):
                        continue
                    entries.append((target,sx,sz,hint))
                self.entries[mid]=entries
                self.stats["loaded"]+=len(entries)

    def connect(self):
        # Context manager below closes explicitly; commit is transactional.
        from contextlib import contextmanager
        @contextmanager
        def connection():
            db=sqlite3.connect(self.path,timeout=10)
            try:
                with db:
                    yield db
            finally:
                db.close()
        return connection()

    def take(self,mid,target,points,bounds,cfg):
        if target not in BINS:
            return points,None,None
        for bin_,sx,sz,hint in self.entries.get(mid,()):
            cell=(mid,sx,sz)
            if bin_!=target or cell in self.used_sectors:
                continue
            bank,group=propose(hint,points,bounds,cfg)
            if group is None:
                continue
            self.used_sectors.add(cell)
            self.stats["replays"]+=1
            return bank,group,(mid,target,sx,sz)
        return points,None,None

    def observe(self,key,record=None,fixed=None):
        if key is None:
            return
        if record is None:
            outcome="technical"
            self.stats["technical_rejections"]+=1
        elif (fixed and fixed["final_view"]["selected"] and
              fixed["selected_index"]<fixed["camera"]["count"] and
              record["visibility_measurement"]["visibility_bin"]==key[1]):
            outcome="hits"
            self.stats["hits"]+=1
        else:
            outcome="misses"
            self.stats["misses"]+=1
        self.pending.append(("replay",key,outcome))

    def learn(self,mid,record,fixed,target):
        hint=hint_from_frame(record,fixed,target)
        if hint is None:
            return
        sx,sz=sector(*hint["position"])
        self.pending.append(("learn",(mid,target,sx,sz),hint))
        self.stats["new_hints"]+=1

    def commit(self):
        if self.stats["committed"]:
            raise ValueError("Cache observations already committed")
        with self.connect() as db:
            for action,(mid,target,sx,sz),value in self.pending:
                key=(self.scopes[mid],target,sx,sz)
                if action=="learn":
                    db.execute("""INSERT INTO hints_v1(scope,target,sx,sz,hint,hits)
                        VALUES(?,?,?,?,?,1) ON CONFLICT(scope,target,sx,sz)
                        DO UPDATE SET hits=hits+1""",(*key,json.dumps(value,allow_nan=False)))
                else:
                    column=value
                    if column not in ("hits","misses","technical"):
                        raise ValueError("Invalid cache outcome")
                    db.execute(f"UPDATE hints_v1 SET {column}={column}+1,uses=uses+1 WHERE scope=? AND target=? AND sx=? AND sz=?",key)
            for scope in set(self.scopes.values()):
                db.execute("""DELETE FROM hints_v1 WHERE scope=? AND rowid NOT IN
                    (SELECT rowid FROM hints_v1 WHERE scope=?
                     ORDER BY (hits+1.0)/(hits+misses+2) DESC,uses ASC,target,sx,sz LIMIT ?)""",
                    (scope,scope,LIMIT))
        self.stats["committed"]=True
        self.pending.clear()
