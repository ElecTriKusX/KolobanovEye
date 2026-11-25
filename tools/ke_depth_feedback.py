"""Run-local feedback from real rendered depth boundaries.

Never grant a placement or quota credit from a depth proposal. Re-publish
the points through the normal native validation pipeline, at most once.
"""
import math
import re
from collections import defaultdict, deque


def read_world_edges(blk, measured):
    def field(name,kind):
        values=re.findall(r"^"+name+":"+kind+r"=([^\r\n]+)",blk,re.M)
        if len(values)!=1:
            raise ValueError("Missing or duplicate world edge field: "+name)
        return values[0]
    n=int(field("fixed_world_edge_count","i"))
    if not 0<=n<=6 or (not measured and n):
        raise ValueError("Invalid depth proposal count")
    out=[]
    for i in range(n):
        p=tuple(float(v) for v in field(f"fixed_world_edge_{i}","p3").split(","))
        if len(p)!=3 or not all(map(math.isfinite,p)):
            raise ValueError("Invalid world edge coordinate")
        out.append(p)
    if len(re.findall(r"^fixed_world_edge_\d+:p3=",blk,re.M))!=n:
        raise ValueError("Unexpected world edge coordinates")
    return out


def unproject_plane(px,py,width,height,wk,hk,z,eye,right,up,forward):
    """Independent reference for symmetric Dagor camera pixel coordinates."""
    if min(width,height,wk,hk,z)<=0:
        raise ValueError("Invalid projection")
    x=(2*px/width-1)*z/wk
    y=(1-2*py/height)*z/hk
    return tuple(eye[i]+x*right[i]+y*up[i]+z*forward[i] for i in range(3))


class DepthFeedback:
    def __init__(self,limit=32):
        self.limit=limit
        self.pending=defaultdict(deque)
        self.seen=set()
        self.offered=0
        self.used=0

    def offer(self,mid,target,group,edges,bounds,cfg):
        if target=="open" or group.get("origin")=="depth_feedback":
            return 0 # no recursive local feedback chain
        xmin,xmax,zmin,zmax=bounds
        margin=cfg.get("map_margin_percent",0.)/100
        ex,ez=group["eye_xz"]
        ax,az=group["aim_xz"]
        queued=0
        for x,_,z in edges:
            if math.hypot(x-ax,z-az)>14:
                continue
            u,v=(x-xmin)/(xmax-xmin),(z-zmin)/(zmax-zmin)
            d=math.hypot(x-ex,z-ez)
            if not (margin<=u<=1-margin and margin<=v<=1-margin and
                    cfg["distance_min"]<=d<=cfg["distance_max"]):
                continue
            key=(mid,round(x/2),round(z/2))
            if key in self.seen or len(self.pending[mid])>=self.limit:
                continue
            self.seen.add(key)
            self.pending[mid].append((target,x,z,dict(group)))
            self.offered+=1
            queued+=1
        return queued

    def take(self,mid,target,points,bounds,cfg):
        if target=="open" or len(points)<4:
            return points,None
        queue=self.pending[mid]
        xmin,xmax,zmin,zmax=bounds
        sx,sz=xmax-xmin,zmax-zmin
        margin=cfg.get("map_margin_percent",0.)/100
        for _ in range(len(queue)):
            requested,x,z,parent=queue.popleft()
            if requested!=target:
                queue.append((requested,x,z,parent))
                continue
            ex,ez=parent["eye_xz"]
            dist=math.hypot(x-ex,z-ez)
            if dist<=0:
                continue
            dx,dz=(ex-x)/dist,(ez-z)/dist
            local=[]
            for shift in (0.,-1.,1.,-2.5,2.5,-4.):
                xx,zz=x+shift*dz,z-shift*dx
                u,v=(xx-xmin)/sx,(zz-zmin)/sz
                d=math.hypot(xx-ex,zz-ez)
                if (margin<=u<=1-margin and margin<=v<=1-margin and
                        cfg["distance_min"]<=d<=cfg["distance_max"]):
                    local.append((u,v,"fixed_depth_edge"))
            local=local[:min(6,len(points)-1)]
            if len(local)<2:
                continue
            keys={p[:2] for p in local}
            combined=(local+[p for p in points if p[:2] not in keys])[:len(points)]
            if len(combined)!=len(points):
                continue
            self.used+=1
            # Keep the earlier camera origin and original look-at anchor. The
            # current frame still applies its own screen composition offset.
            group=dict(parent,count=len(local),origin="depth_feedback",
                       proposed_anchor_xz=[x,z])
            return combined,group
        return points,None
