"""Optional scene-family sampling for offline game screenshot diversity.

Families are proposal provenance, not semantic ground truth. The rendered
mask remains the only visibility authority.
"""
import json
import math
from pathlib import Path
from collections import Counter
from tools.ke_guided_sampling import OccluderIndex, angle, ranked_edges, prioritize_points, candidate_fields
from tools.ke_fixed_camera import fixed_camera_group

FAMILIES=("building","vegetation","aperture","terrain","explore")
APERTURE_HINTS=("ruin","destroy","hangar","garage","warehouse","arch","entrance","window","gate","door")


class ApertureIndex(OccluderIndex):
    def edges(self,x,z,maximum):
        # Aim toward the interior span of likely openings, not only AABB
        # tangents. This proposes a view; it does NOT assert an opening.
        for a,b,c,d,h in self.nearby(x,z,maximum):
            cx,cz=(a+b)/2-x,(c+d)/2-z
            corners=[(u-x,v-z) for u in (a,b) for v in (c,d)]
            far=max(math.hypot(u,v) for u,v in corners)+2
            if far<=maximum:
                yield angle(cx,cz),far,h
        yield from super().edges(x,z,maximum)


class SceneIndexes:
    def __init__(self,objects):
        buckets={k:[] for k in ("building","vegetation","aperture")}
        for obj in objects:
            layer=obj.get("analysis_layer")
            if layer in ("building","rock","small"):
                buckets["building"].append(obj)
            if layer=="vegetation":
                buckets["vegetation"].append(obj)
            if layer in ("building","small") and any(t in obj.get("resource","").casefold() for t in APERTURE_HINTS):
                buckets["aperture"].append(obj)
        self.indices={k:(ApertureIndex if k=="aperture" else OccluderIndex)(v)
                      for k,v in buckets.items()}

    @classmethod
    def load(cls,root,mid):
        with (Path(root)/Path(mid).stem/"objects.jsonl").open(encoding="utf-8") as stream:
            return cls(json.loads(line) for line in stream if line.strip())


def family_for(target,attempt):
    if target=="open":
        return "explore"
    # A separate, one-based non-open counter per map is used by the caller.
    # Missing geometry falls back, not a failed or fabricated family.
    return FAMILIES[(attempt-1)%len(FAMILIES)]


def profile(cfg,family):
    result=dict(cfg)
    lo=max(cfg["camera_height_min"],cfg.get("camera_clearance",0.))
    hi=cfg["camera_height_max"]
    factors={"building":.55,"vegetation":.25,"aperture":.45,"terrain":.2}
    if family in factors and lo<=hi:
        result["camera_height_min"]=lo
        result["camera_height_max"]=lo+(hi-lo)*factors[family]
    return result


def prepare_family(indexes,general,bounds,points,target,cfg,seed,family):
    """Return actual proposal family, points, optional group and bank profile."""
    if family=="explore":
        return family,points,None,general,cfg
    if family=="terrain":
        # The camera bank is broad and low; live terrain scoring is native.
        return family,points,None,general,profile(cfg,family)
    index=indexes.indices[family]
    if not index.cells:
        return "fallback",prioritize_points(general,bounds,points,target,cfg),None,general,cfg
    local_cfg=profile(cfg,family)
    ordered=prioritize_points(index,bounds,points,target,local_cfg)
    ordered,group=fixed_camera_group(index,bounds,ordered,target,local_cfg,seed)
    if group is None:
        return "fallback",prioritize_points(general,bounds,points,target,cfg),None,general,cfg
    group["family"]=family
    return family,ordered,group,index,local_cfg


def family_fields(base,index,bounds,points,target,cfg,seed,family):
    if family=="explore":
        return [base for _ in points]
    if family=="terrain":
        # Keep angular coverage; lower two rings without altering their
        # horizontal distance or planned angular coordinates.
        lo,hi=cfg["camera_height_min"],cfg["camera_height_max"]
        rings=sorted({c["avf_ring"] for c in base})
        low={r:round(lo+(hi-lo)*(j+.5)/len(rings),5) for j,r in enumerate(rings)}
        bank=[dict(c,height_above_ground=low[c["avf_ring"]]) for c in base]
        return [bank for _ in points]
    return candidate_fields(base,index,bounds,points,target,cfg,seed)


def count_family(stats, family, target, measured=None, accepted=False):
    row=stats.setdefault(family,{"attempts":0,"validated":0,"target_matches":0,"quota_gain_attempts":0,"actual_bins":{}})
    if measured is None:
        row["attempts"]+=1
    else:
        row["validated"]+=1
        row["target_matches"]+=int(target==measured)
        row["quota_gain_attempts"]+=int(accepted)
        bins=row["actual_bins"]
        bins[measured]=bins.get(measured,0)+1
