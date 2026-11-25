"""Measure opening-like topology in the already-rendered game depth map.

An aperture witness is a continuous visible run of isolated model pixels
between two occluded pixels, with real environment depth edges at both ends.
It is not a semantic window/door detector.
"""
MARKER="// [KE] FIXED_APERTURE_V5"

def renderer_edits(key):
    from tools.ke_dagor_depth_proposals import renderer_successors
    out=[]
    for _,old in renderer_successors(key):
        if key=="header":
            new=old.replace("float *, int *, Point3 *","float *, int *, int *, Point3 *")
        elif "bool probeKolobanovEyeFixed(const TMatrix" in old:
            new=old.replace("int *edge_hits, Point3 *world_edges","int *edge_hits, int *aperture_hits, Point3 *world_edges")
            new=new.replace("!edge_hits || !world_edges","!edge_hits || !aperture_hits || !world_edges")
            new=new.replace("edge_hits[i]=0;","edge_hits[i]=0; aperture_hits[i]=0;")
            anchor="          edge_hits[pose]=hits;\n"
            extra=r'''          // [KE] FIXED_APERTURE_V5
          // Count at most one witnessed opening per row and column. A hole
          // in the isolated model is not an opening in the environment.
          auto opening=[&](int start,int stride,int count,unsigned char flag) {
            int run=0;
            bool started=false;
            for (int j=1;j<count;++j)
            {
              const int i=start+(j-1)*stride,k=i+stride;
              if (!validModel(modelDepth[i]) || !validModel(modelDepth[k]) ||
                  !validEnv(sceneDepth[i]) || !validEnv(sceneDepth[k]))
              { run=0; started=false; continue; }
              const bool v0=unobstructed(i),v1=unobstructed(k);
              if (!v0 && v1)
              { started=(edgeFlags[i]&flag)!=0; run=1; }
              else if (v0 && v1 && started)
                ++run;
              else if (v0 && !v1)
              {
                if (started && run>=2 && (edgeFlags[i]&flag)) return true;
                run=0; started=false;
              }
              else if (!v1)
              { run=0; started=false; }
            }
            return false;
          };
          int openings=0;
          for (int y=0;y<height;++y) openings+=int(opening(y*width,1,width,1));
          for (int x=0;x<width;++x) openings+=int(opening(x,width,height,2));
          aperture_hits[pose]=openings;
          debug("[KE] FIXED_APERTURE pose=%d scanlines=%d",pose,openings);
'''
            if new.count(anchor)!=1:
                raise ValueError("Missing aperture renderer anchor")
            new=new.replace(anchor,anchor+extra,1)
        else:
            new=old.replace("int *edges, Point3 *world_edges","int *edges, int *apertures, Point3 *world_edges")
            new=new.replace("},fractions,edges,world_edges","},fractions,edges,apertures,world_edges")
        if new==old:
            raise ValueError("Missing aperture signature anchor")
        out.append((old,new))
    return tuple(out)


CAPTURE_EDITS=(
    ("  int keWorldEdgeCount=0;\n",
     """  int keWorldEdgeCount=0;
  // [KE] FIXED_APERTURE_V5
  int keFixedApertureHits=0;
"""),
    ("            int edges[6]={};\n","            int edges[6]={},apertures[6]={};\n"),
    ("              keFixedFound,prepare,fractions,edges,keWorldEdges,keWorldEdgeCount,keFixedPasses);",
     "              keFixedFound,prepare,fractions,edges,apertures,keWorldEdges,keWorldEdgeCount,keFixedPasses);"),
    ("                  fminf(.015f,.001f*float(edges[i]));\n",
     """                  fminf(.015f,.001f*float(edges[i]))-
                  (strcmp(keSmartCandidates.getStr("guided_scene_family",""),"aperture")==0 ?
                    fminf(.025f,.002f*float(apertures[i])) : 0.f);
"""),
    ("                  keFixedEdgeHits=edges[i];\n",
     "                  keFixedEdgeHits=edges[i];\n                  keFixedApertureHits=apertures[i];\n"),
    ('    result.setInt("fixed_group_edge_hits",keFixedEdgeHits);\n',
     '    result.setInt("fixed_group_edge_hits",keFixedEdgeHits);\n    result.setInt("fixed_group_aperture_hits",keFixedApertureHits);\n'),
)
