"""Native fixed-camera preselection of already validated nearby placements."""
from pathlib import Path

MARKER = "// [KE] FIXED_CAMERA_PLACEMENT_V1"
LOOP = "  for (int attempt = 0; attempt < candidateCount; ++attempt)\n  {\n"
SETUP = r'''  // [KE] FIXED_CAMERA_PLACEMENT_V1
  const int keFixedCount=keSmartPlacement?keSmartCandidates.getInt("fixed_count",0):0;
  if (keFixedCount<0 || keFixedCount>6 || keFixedCount>candidateCount ||
      (keFixedCount && (keFixedCount<2 || framesPerPosition!=1 || keSmartTarget<=0)))
    return fail("FIXED_CAMERA","Invalid local group");
  struct KeFixedPlacement { Point3 point, normal; float height; int index; };
  KeFixedPlacement keFixedPlaces[6];
  int keFixedFound=0,keFixedValid=0,keFixedPasses=0;
  float keFixedEstimate=-1.f;
'''
OK = '    console->addMessage(ILogWriter::NOTE, "[KE] PLACEMENT_OK attempt=%d x=%.3f z=%.3f range=%.3f water=unknown obstacles=unknown", attempt, candidate.x, candidate.z, hmax-hmin);\n'
PRESELECT = r'''  if (keFixedFound)
  {
    // The first valid local placement is the ordinary fallback.
    int best=0;
    if (keFixedFound>=2)
    {
      const Point3 proposed=keSmartCandidates.getPoint3("fixed_eye",Point3::ZERO);
      const Point2 anchor=keSmartCandidates.getPoint2("fixed_aim",Point2(0.f,0.f));
      float eyeGround=0.f,aimGround=0.f;
      Point3 normal;
      if (finite_point(proposed) && std::isfinite(anchor.x) && std::isfinite(anchor.y) &&
          proposed.y>=config.getReal("camera_clearance",.5f) &&
          scene.getHeightmapAtPoint(proposed.x,proposed.z,eyeGround,&normal) &&
          scene.getHeightmapAtPoint(anchor.x,anchor.y,aimGround,&normal) &&
          std::isfinite(eyeGround) && std::isfinite(aimGround))
      {
        const Point3 fixedEye(proposed.x,eyeGround+proposed.y,proposed.z);
        const Point3 fixedAim(anchor.x,aimGround+keFixedPlaces[0].point.y-bounds[0].y+
          (bounds[0].y+bounds[1].y)*.5f,anchor.y);
        if (lengthSq(Point3(0,1,0)%(fixedAim-fixedEye))>.01f)
        {
          const Point3 forward=normalize(fixedAim-fixedEye);
          const Point3 right=normalize(Point3(0,1,0)%forward);
          TMatrix fixedCamera=TMatrix::IDENT;
          fixedCamera.setcol(0,right);
          fixedCamera.setcol(1,forward%right);
          fixedCamera.setcol(2,forward);
          fixedCamera.setcol(3,fixedEye);
          const float wk=1.f/tanf(fov*DEG_TO_RAD*.5f);
          const Driver3dPerspective perspective(wk,wk*float(width)/height,.2f,20000.f,0.f,0.f);
          const int longSide=max(width,height);
          // Use a full, common projection. A per-pose ROI would invalidate depth reuse.
          const int pw=int(256.f*width/longSide+.5f),ph=int(256.f*height/longSide+.5f);
          plugin->setKolobanovEyeModel(nullptr);
          scene.update(fixedEye,0.f);
          perform_delayed_actions();
          const auto deadline=std::chrono::steady_clock::now()+std::chrono::milliseconds(100);
          auto ready=[&]() {
            return rendinst::isRIGenPrepareFinished() && (!scene.getSsm() || !scene.getSsm()->isLoading());
          };
          while (!ready() && std::chrono::steady_clock::now()<deadline)
          {
            cpujobs::release_done_jobs();
            perform_delayed_actions();
            sleep_msec(1);
          }
          cpujobs::release_done_jobs();
          perform_delayed_actions();
          if (ready() && get_managed_textures_streaming_pending_count()==0)
          {
            // An independent render instance avoids modifying the final model,
            // its skeleton, bounding box or chosen LOD during preselection.
            DynamicRenderableSceneInstance fixedModel(lods);
            GeomNodeTreeUniquePtr fixedTree(sourceTree?GeomNodeTree::make(*sourceTree):nullptr);
            auto prepare=[&](int i) -> bool {
              if (i<0 || i>=keFixedFound) return false;
              const auto &p=keFixedPlaces[i];
              TMatrix pose=TMatrix::IDENT;
              pose.setcol(0,placeCs,0,-placeSn);
              pose.setcol(2,placeSn,0,placeCs);
              pose.setcol(3,Point3(p.point.x,p.height+p.point.y-bounds[0].y,p.point.z));
              Point4 projected;
              if (!project_bounds(bounds,pose,fixedCamera,.2f,20000.f,wk,wk*float(width)/height,width,height,projected))
                return false;
              if (fixedTree)
              {
                fixedTree->setRootTmScalar(pose);
                fixedTree->invalidateWtm();
                fixedTree->calcWtm();
              }
              for (unsigned n=0;n<fixedModel.getNodeCount();++n)
              {
                const char *name=lods->getNames().node.getNameSlow(n);
                TMatrix transform=pose;
                if (fixedTree && name && strcmp(name,"@root")!=0)
                {
                  const auto node=fixedTree->findNodeIndex(name);
                  if (!node) return false;
                  fixedTree->partialCalcWtm(node);
                  fixedTree->getNodeWtmScalar(node,transform);
                }
                fixedModel.setNodeWtm(n,transform);
              }
              BBox3 worldBounds;
              include_transformed_bounds(worldBounds,bounds,pose);
              fixedModel.setBoundingBox(worldBounds);
              fixedModel.setDisableAutoChooseLod(false);
              fixedModel.beforeRender(fixedEye);
              const int lod=fixedModel.getCurrentLodNo();
              if (lod<0 || lod>=lods->lods.size() || !lods->lods[lod].scene)
                return false;
              fixedModel.setDisableAutoChooseLod(true);
              return true;
            };
            float fractions[6]={-1.f,-1.f,-1.f,-1.f,-1.f,-1.f};
            const bool measured=renderer->probeKolobanovEyeFixed(&fixedModel,fixedCamera,perspective,pw,ph,
              keFixedFound,prepare,fractions,keFixedPasses);
            if (measured && ready() && get_managed_textures_streaming_pending_count()==0)
            {
              const float centers[]={.97f,.775f,.475f,.2f};
              float bestScore=1e9f;
              for (int i=0;i<keFixedFound;++i)
              {
                if (!std::isfinite(fractions[i]) || fractions[i]<0.f || fractions[i]>1.f)
                  continue;
                ++keFixedValid;
                const float score=fabsf(fractions[i]-centers[keSmartTarget]);
                if (score<bestScore)
                {
                  bestScore=score; best=i; keFixedEstimate=fractions[i];
                }
              }
            }
          }
        }
      }
    }
    requested=keFixedPlaces[best].point;
    ground=keFixedPlaces[best].height;
    groundNormal=keFixedPlaces[best].normal;
    selectedCandidate=keFixedPlaces[best].index;
    console->addMessage(ILogWriter::NOTE,
      "[KE] FIXED_PRESELECT index=%d placements=%d valid=%d env_passes=%d selected=%d fraction=%.6f",
      sceneIndex,keFixedFound,keFixedValid,keFixedPasses,selectedCandidate,keFixedEstimate);
  }
'''
META = r'''  if (keFixedCount)
  {
    result.setInt("fixed_group_count",keFixedCount);
    result.setInt("fixed_group_valid_placements",keFixedFound);
    result.setInt("fixed_group_valid_probes",keFixedValid);
    result.setInt("fixed_group_environment_passes",keFixedPasses);
    result.setReal("fixed_group_estimate",keFixedEstimate);
  }
'''
EDITS=(
    (LOOP,SETUP+LOOP+'    if (keFixedFound && attempt>=keFixedCount) break;\n'),
    (OK+'    break;\n', OK+r'''    if (keFixedCount && attempt<keFixedCount)
    {
      keFixedPlaces[keFixedFound++]={candidate,normal,h,attempt};
      continue;
    }
    break;
'''),
    ('  if (!placementFound)\n',PRESELECT+'  if (!placementFound)\n'),
    ('  result.setInt("placement_candidate", selectedCandidate);\n',META+'  result.setInt("placement_candidate", selectedCandidate);\n'),
)

def normalize_fixed_capture(src):
    from tools.ke_dagor_fixed_final import normalize_fixed_final
    src = normalize_fixed_final(src)
    if MARKER not in src:
        return src
    for old,new in reversed(EDITS):
        if src.count(new)!=1:
            raise ValueError("Partial fixed camera placement patch")
        src=src.replace(new,old,1)
    return src


def install_fixed_capture(source: Path):
    raw=source.read_bytes()
    src=raw.replace(b"\r\n",b"\n").decode("utf-8")
    if MARKER in src:
        normalize_fixed_capture(src)
        return False
    for old,new in EDITS:
        if src.count(old)!=1:
            raise ValueError(f"Fixed camera placement anchor missing: {old[:90]!r}")
        src=src.replace(old,new,1)
    source.write_bytes(src.encode("utf-8").replace(b"\n",b"\r\n" if b"\r\n" in raw else b"\n"))
    return True
