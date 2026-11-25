"""Bounded camera-first proposal groups for offline game-scene composition."""
import math
import random
from tools.ke_guided_sampling import ranked_edges

MAX_POSES = 6


def fixed_camera_group(index,bounds,points,target,cfg,seed):
    """Choose one camera and a lateral sweep behind an approximate object edge.

    The first local points form the group. Remaining points are the ordinary
    placement fallback. All positions still require native terrain/collision
    validation. The group's camera is a preselection view, not final RGB pose.
    """
    if target=="open" or len(points)<4:
        return points,None
    xmin,xmax,zmin,zmax=bounds
    sx,sz=xmax-xmin,zmax-zmin
    margin=cfg.get("map_margin_percent",0.)/100
    rng=random.Random(seed)
    for u,v,_ in points[:min(8,len(points))]:
        x,z=xmin+u*sx,zmin+v*sz
        edges=ranked_edges(index,x,z,cfg)
        if not edges:
            continue
        bearing,minimum,_=rng.choice(edges[:4])
        dist=rng.uniform(max(cfg["distance_min"],minimum),cfg["distance_max"])
        rad=math.radians(bearing)
        dx,dz=math.sin(rad),math.cos(rad)
        eye_x,eye_z=x+dist*dx,z+dist*dz
        if not (xmin<=eye_x<=xmax and zmin<=eye_z<=zmax):
            continue
        h0=max(cfg["camera_clearance"],cfg["camera_height_min"])
        h1=cfg["camera_height_max"]
        if h0>h1:
            continue
        h=rng.uniform(h0,h0+.25*(h1-h0))
        local=[]
        # Move across the approximate silhouette edge, not through the object.
        # The live native checks are required even for these small displacements.
        offsets=(0.,-2.,2.,-4.,4.,-6.)
        for shift in offsets:
            xx,zz=x+shift*dz,z-shift*dx
            uu,vv=(xx-xmin)/sx,(zz-zmin)/sz
            dd=math.hypot(xx-eye_x,zz-eye_z)
            if (margin<=uu<=1-margin and margin<=vv<=1-margin and
                    cfg["distance_min"]<=dd<=cfg["distance_max"]):
                local.append((uu,vv,"fixed_camera_local"))
        cap=min(MAX_POSES,len(points)-1)
        local=local[:cap]
        if len(local)<2:
            continue
        used={p[:2] for p in local}
        fallback=[p for p in points if p[:2] not in used]
        ordered=(local+fallback)[:len(points)]
        if len(ordered)!=len(points):
            continue
        return ordered,{"count":len(local),"eye_xz":[eye_x,eye_z],
                        "height":h,"aim_xz":[x,z]}
    return points,None


def fixed_block(group):
    if group is None:
        return ""
    x,z=group["eye_xz"]
    ax,az=group["aim_xz"]
    return (f'fixed_count:i={group["count"]}\n'
            f'fixed_eye:p3={x:.7f},{group["height"]:.7f},{z:.7f}\n'
            f'fixed_aim:p2={ax:.7f},{az:.7f}\n')
