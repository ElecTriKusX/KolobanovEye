"""Successor to fixed-depth V2: bounded world-coordinate edge proposals.

The projected points are hypotheses on a model-depth plane, never validated
placements. The Python scheduler can publish them on a later frame through
the ordinary native placement checks.
"""
from pathlib import Path

MARKER = "// [KE] FIXED_WORLD_EDGES_V4"

def renderer_successors(key):
    from tools.ke_dagor_fixed_depth import HEADER_V2, SERVICE_V2, edge_scene_method
    if key=="header":
        return ((HEADER_V2,HEADER_V2.replace("float *, int *, int &)","float *, int *, Point3 *, int &, int &)")),)
    old=edge_scene_method()
    new=old.replace("int *edge_hits, int &environment_passes)",
        "int *edge_hits, Point3 *world_edges, int &world_count, int &environment_passes)")
    new=new.replace("    environment_passes = 0;","    environment_passes = 0;\n    world_count=0;")
    new=new.replace("!fractions || !edge_hits ||","!fractions || !edge_hits || !world_edges ||")
    anchor='          edge_hits[pose]=hits;\n'
    extra=r'''          // [KE] FIXED_WORLD_EDGES_V4
          // Locate the model silhouette center and average metric depth.
          // Select a nearby edge separating foreground and farther geometry,
          // even if the current model is completely visible.
          float sumX=0.f,sumY=0.f,sumZ=0.f;
          int minX=width,minY=height,maxX=-1,maxY=-1;
          for (int y=0;y<height;++y)
            for (int x=0;x<width;++x)
              if (validModel(modelDepth[y*width+x]))
              {
                sumX+=x+.5f; sumY+=y+.5f;
                sumZ+=linearDepth(modelDepth[y*width+x]);
                minX=min(minX,x); minY=min(minY,y);
                maxX=max(maxX,x); maxY=max(maxY,y);
              }
          const float cx=sumX/amodal,cy=sumY/amodal,z=sumZ/amodal;
          const float radius=max(4.f,1.5f*float(max(maxX-minX+1,maxY-minY+1)));
          float best=radius*radius;
          Point3 proposal(0.f,0.f,0.f);
          bool found=false;
          auto consider=[&](int i,int j,float px,float py) {
            const float a=linearDepth(sceneDepth[i]),b=linearDepth(sceneDepth[j]);
            if (min(a,b)>z-.5f || max(a,b)<z+.5f) return;
            const float dx=px-cx,dy=py-cy,score=dx*dx+dy*dy;
            if (score>=best) return;
            // Symmetric perspective; camera columns are the world basis.
            const Point3 local((2.f*px/width-1.f)*z/perspective.wk,
              (1.f-2.f*py/height)*z/perspective.hk,z);
            const Point3 center((2.f*cx/width-1.f)*z/perspective.wk,
              (1.f-2.f*cy/height)*z/perspective.hk,z);
            const Point3 p=camera*local,c=camera*center;
            const float d2=(p.x-c.x)*(p.x-c.x)+(p.z-c.z)*(p.z-c.z);
            if (!std::isfinite(p.x)||!std::isfinite(p.y)||!std::isfinite(p.z)||
                d2<.25f || d2>64.f) return;
            best=score; proposal=p; found=true;
          };
          for (int y=0;y<height;++y)
            for (int x=0;x<width;++x)
            {
              const int i=y*width+x;
              if (edgeFlags[i]&1) consider(i,i+1,x+1.f,y+.5f);
              if (edgeFlags[i]&2) consider(i,i+width,x+.5f,y+1.f);
            }
          if (found && world_count<6)
          {
            bool duplicate=false;
            for (int k=0;k<world_count;++k)
            {
              const float dx=world_edges[k].x-proposal.x,dz=world_edges[k].z-proposal.z;
              duplicate|=dx*dx+dz*dz<1.f;
            }
            if (!duplicate) world_edges[world_count++]=proposal;
          }
'''
    if new.count(anchor)!=1:
        raise ValueError("World edge projection anchor missing")
    new=new.replace(anchor,anchor+extra,1)
    service=SERVICE_V2.replace("int *edges, int &passes)",
        "int *edges, Point3 *world_edges, int &world_count, int &passes)").replace(
        "},fractions,edges,passes);","},fractions,edges,world_edges,world_count,passes);")
    return ((old,new),(SERVICE_V2,service))


CAPTURE_EDITS=(
    ("  int keFixedEdgeHits=0;\n",
     """  int keFixedEdgeHits=0;
  // [KE] FIXED_WORLD_EDGES_V4
  Point3 keWorldEdges[6];
  int keWorldEdgeCount=0;
"""),
    ("              keFixedFound,prepare,fractions,edges,keFixedPasses);",
     "              keFixedFound,prepare,fractions,edges,keWorldEdges,keWorldEdgeCount,keFixedPasses);"),
    ('    result.setInt("fixed_group_edge_hits",keFixedEdgeHits);\n',
     '''    result.setInt("fixed_group_edge_hits",keFixedEdgeHits);
    const int publishedEdges=keFixedEstimate>=0.f ? keWorldEdgeCount : 0;
    result.setInt("fixed_world_edge_count",publishedEdges);
    for (int i=0;i<publishedEdges;++i)
    {
      char key[48];
      snprintf(key,sizeof(key),"fixed_world_edge_%d",i);
      result.setPoint3(key,keWorldEdges[i]);
    }
'''),
)


def reverse_successors(src,edits):
    for old,new in reversed(edits):
        if src.count(new)==1:
            src=src.replace(new,old,1)
        elif src.count(old)!=1:
            raise ValueError("Incomplete world edge successor")
    return src


def apply_successors(src,edits):
    src=reverse_successors(src,edits)
    for old,new in edits:
        if src.count(old)!=1:
            raise ValueError("Missing world edge successor anchor")
        src=src.replace(old,new,1)
    return src
