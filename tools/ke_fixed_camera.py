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


def environment_visibility(scene,model):
    """CPU reference for environment-only reverse-Z depth, not combined depth."""
    if len(scene)!=len(model):
        raise ValueError("Depth arrays differ in size")
    amodal=visible=invalid=0
    for env,depth in zip(scene,model):
        if not math.isfinite(depth) or not 0<depth<=1:
            continue
        amodal+=1
        if not math.isfinite(env) or not 0<=env<=1:
            invalid+=1
            continue
        tolerance=max(1e-7,1e-4*max(depth,env))
        visible+=int(env<=depth+tolerance)
    return visible/amodal if amodal>=16 and not invalid else None


def read_fixed_metadata(blk,group,selected):
    import re
    def field(name,kind):
        matches=re.findall(r"^"+name+":"+kind+r"=([^\r\n]+)",blk,re.M)
        if len(matches)!=1:
            raise ValueError("Missing or duplicate fixed-camera metadata: "+name)
        return matches[0].strip()
    if group is None:
        if re.search(r"^fixed_group_count:",blk,re.M):
            raise ValueError("Unexpected fixed-camera group")
        return None
    count=int(field("fixed_group_count","i"))
    placed=int(field("fixed_group_valid_placements","i"))
    valid=int(field("fixed_group_valid_probes","i"))
    passes=int(field("fixed_group_environment_passes","i"))
    estimate=float(field("fixed_group_estimate","r"))
    if not (count==group["count"] and 0<=valid<=placed<=count<=MAX_POSES and
            passes in (0,1) and math.isfinite(estimate)):
        raise ValueError("Invalid fixed camera counters")
    if (placed>0) != (selected<count):
        raise ValueError("Selected placement is inconsistent with local group")
    if valid:
        if placed<2 or passes!=1 or not 0<=estimate<=1:
            raise ValueError("Invalid selected fixed-camera estimate")
    elif estimate!=-1:
        raise ValueError("Missing measurement must use -1")
    if passes and placed<2:
        raise ValueError("Depth pass without multiple placements")
    return {"method":"fixed_camera_depth_cpu_v1","planned":count,
            "valid_placements":placed,"valid_probes":valid,
            "environment_passes":passes,"estimate":estimate if valid else None,
            "selected_index":selected,"camera":group}


def read_fixed_final(blk, fixed):
    """Confirm actual eye and look-at against the preselection, not a plan."""
    import re
    def field(name,kind):
        values=re.findall(r"^"+name+":"+kind+r"=([^\r\n]+)",blk,re.M)
        if len(values)!=1:
            raise ValueError("Missing or duplicate fixed final field: "+name)
        return values[0].strip()
    def point(name):
        p=tuple(float(x) for x in field(name,"p3").split(","))
        if len(p)!=3 or not all(map(math.isfinite,p)):
            raise ValueError("Invalid point: "+name)
        return p
    flag=field("fixed_camera_selected","b")
    if flag not in ("yes","no","true","false","1","0"):
        raise ValueError("Invalid fixed camera selection flag")
    selected=flag in ("yes","true","1")
    attempt=int(field("camera_attempt","i"))
    measured=fixed["estimate"] is not None
    if selected!=(measured and attempt==0):
        raise ValueError("Inconsistent fixed camera selection")
    result={"selected":selected,"candidate_index":0 if measured else None}
    if measured:
        eye,look=point("fixed_group_eye"),point("fixed_group_look_at")
        planned=fixed["camera"]["eye_xz"]
        if max(abs(eye[0]-planned[0]),abs(eye[2]-planned[1]))>.1:
            raise ValueError("Native fixed camera differs from published eye")
        result.update(preselected_eye=list(eye),preselected_look_at=list(look))
        if selected:
            for name,expected in (("eye",eye),("look_at",look)):
                if max(abs(a-b) for a,b in zip(point(name),expected))>.01:
                    raise ValueError("Final camera differs from measured fixed view")
            tank=point("tank_world")
            dx,dz=eye[0]-tank[0],eye[2]-tank[2]
            result.update(actual_horizontal_distance=math.hypot(dx,dz),
                          actual_azimuth_deg=math.degrees(math.atan2(dx,dz)),
                          actual_eye=list(eye),actual_look_at=list(look))
    return result
