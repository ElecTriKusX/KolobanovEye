"""Opt-in live validation of the neutral synthetic model envelope.

Cached points are only proposals. RI collision-resource AABBs are a
conservative broad phase; static geometry is checked with finite rays.
Not an exact mesh/mesh collision test or proof of collision-free placement.
"""
from pathlib import Path

MARKER = "// [KE] SMART_PLACEMENT_V1"
INCLUDE = "namespace rendinst { void keForeachGeometryInBox(const BBox3 &, ForeachCB &); }\n"
SETUP_ANCHOR = "  const float placeCs = cosf(placeYaw), placeSn = sinf(placeYaw);\n"
SETUP = r'''  // [KE] SMART_PLACEMENT_V1
  const bool keSmartPlacement = config.getBool("smart_placement", false);
  DataBlock keSmartCandidates;
  int keSmartTarget = -1;
  if (keSmartPlacement)
  {
    const char *dir = config.getStr("online_feedback_dir", "");
    const Point2 expectedMin = config.getPoint2("smart_bounds_min", Point2(0.f,0.f));
    const Point2 expectedMax = config.getPoint2("smart_bounds_max", Point2(0.f,0.f));
    char path[4096];
    if (!sampleTankMap || framesPerPosition != 1 || config.getBool("articulate_tank", false) ||
        !*dir || !std::isfinite(expectedMin.x) || !std::isfinite(expectedMin.y) ||
        !std::isfinite(expectedMax.x) || !std::isfinite(expectedMax.y) ||
        fabsf(expectedMin.x-mapBounds[0].x) > .05f || fabsf(expectedMin.y-mapBounds[0].z) > .05f ||
        fabsf(expectedMax.x-mapBounds[1].x) > .05f || fabsf(expectedMax.y-mapBounds[1].z) > .05f)
      return fail("SMART_PLACEMENT", "Cache bounds or neutral online placement settings mismatch");
    if (!keOnlineDecision(dir, sceneIndex, keSmartTarget) || keSmartTarget < 0 ||
        !keOnlinePath(path, sizeof(path), dir, "smart_", sceneIndex, ".blk") ||
        !keOnlineWaitFile(path) || !keSmartCandidates.load(path) ||
        keSmartCandidates.getInt("target", -1) != keSmartTarget)
      return fail("SMART_PLACEMENT", "Missing or inconsistent candidate bank command");
  }
'''
CANDIDATE_ANCHOR = "    Point3 candidate = config.getPoint3(key, requested);\n"
CANDIDATE = r'''    if (keSmartPlacement)
    {
      if (!keSmartCandidates.paramExists(key))
        return fail("SMART_PLACEMENT", "Missing proposed candidate");
      candidate = keSmartCandidates.getPoint3(key, requested);
    }
'''
CHECK_ANCHOR = "    requested = candidate;\n"
CHECK = r'''    if (keSmartPlacement)
    {
      // Nine footprint points, actual neutral model dimensions and yaw.
      bool supportOk = true;
      for (int iz = 0; iz < 3; ++iz)
        for (int ix = 0; ix < 3; ++ix)
        {
          const float x = bounds[0].x+(bounds[1].x-bounds[0].x)*float(ix)*.5f;
          const float z = bounds[0].z+(bounds[1].z-bounds[0].z)*float(iz)*.5f;
          const float wx = candidate.x+placeCs*x+placeSn*z, wz = candidate.z-placeSn*x+placeCs*z;
          float height = 0.f;
          Point3 supportNormal;
          if (wx < mapBounds[0].x || wx > mapBounds[1].x || wz < mapBounds[0].z || wz > mapBounds[1].z ||
              !scene.getHeightmapAtPoint(wx,wz,height,&supportNormal) || !std::isfinite(height) || height < minimumGroundY)
            supportOk = false;
          else
          {
            hmin = fminf(hmin,height);
            hmax = fmaxf(hmax,height);
          }
        }
      if (!supportOk || hmax-hmin > maxHeightRange)
      {
        console->addMessage(ILogWriter::NOTE,"[KE] SMART_PLACEMENT_REJECT attempt=%d reason=footprint",attempt);
        continue;
      }
      const Point3 poi(candidate.x,h+5.f,candidate.z);
      scene.update(poi,1.f/60.f);
      perform_delayed_actions();
      rendinst::scheduleRIGenPrepare(make_span_const(&poi,1));
      const auto deadline = std::chrono::steady_clock::now()+std::chrono::seconds(30);
      while (!rendinst::isRIGenPrepareFinished() || (scene.getSsm() && scene.getSsm()->isLoading()))
      {
        if (std::chrono::steady_clock::now() >= deadline)
          return fail("SMART_PLACEMENT","Local RendInst preparation timed out");
        cpujobs::release_done_jobs();
        perform_delayed_actions();
        sleep_msec(5);
      }
      cpujobs::release_done_jobs();
      perform_delayed_actions();
      TMatrix pose = TMatrix::IDENT;
      pose.setcol(0,placeCs,0,-placeSn);
      pose.setcol(2,placeSn,0,placeCs);
      pose.setcol(3,Point3(candidate.x,h+candidate.y-bounds[0].y,candidate.z));
      BBox3 envelope = bounds;
      // Ground-contact tolerance only: bottom 10 cm of neutral envelope.
      envelope[0].y = fminf(envelope[1].y-.01f,envelope[0].y+.1f);
      BBox3 volume;
      include_transformed_bounds(volume,envelope,pose);
      struct VolumeCollector : rendinst::ForeachCB
      {
        BBox3 query;
        bool blocked = false;
        void check(const CollisionResource *res,const TMatrix &tm)
        {
          if (!res || blocked) return;
          if (!finite_point(res->boundingBox[0]) || !finite_point(res->boundingBox[1]))
          {
            blocked = true;
            return;
          }
          BBox3 box;
          include_transformed_bounds(box,res->boundingBox,tm);
          if (box[0].x <= query[1].x && box[1].x >= query[0].x &&
              box[0].y <= query[1].y && box[1].y >= query[0].y &&
              box[0].z <= query[1].z && box[1].z >= query[0].z)
            blocked = true;
        }
        void executeForTm(RendInstGenData *,const rendinst::RendInstDesc &d,const TMatrix &tm) override
        { check(rendinst::getRIGenCollInfo(d),tm); }
        void executeForPos(RendInstGenData *,const rendinst::RendInstDesc &d,const TMatrix &tm) override
        { check(rendinst::getRIGenCollInfo(d),tm); }
      } volumeCollector;
      volumeCollector.query = volume;
      rendinst::keForeachGeometryInBox(volume,volumeCollector);
      Tab<rendinst::riex_handle_t> handles;
      for (int pool=0,pools=rendinst::getRiGenExtraResCount();pool<pools && !volumeCollector.blocked;++pool)
        if (rendinst::isRiGenExtraResIdValid(pool))
        {
          rendinst::getRiGenExtraInstances(handles,pool,v_ldu_bbox3(volume));
          for (const auto handle:handles)
          {
            CollisionResource *collision = nullptr;
            rendinst::getRIExtraCollInfo(handle,&collision,nullptr);
            volumeCollector.check(collision,rendinst::getRIGenMatrix(rendinst::RendInstDesc(handle)));
            if (volumeCollector.blocked) break;
          }
        }
      if (volumeCollector.blocked)
      {
        console->addMessage(ILogWriter::NOTE,"[KE] SMART_PLACEMENT_REJECT attempt=%d reason=ri_collision_bounds",attempt);
        continue;
      }
      // Finite 3-axis ray lattice through the oriented neutral envelope.
      // Tests static scene and live RI in both directions, not exact mesh intersection.
      bool rayBlocked = false;
      for (int axis=0;axis<3 && !rayBlocked;++axis)
        for (int i=0;i<3 && !rayBlocked;++i)
          for (int j=0;j<3 && !rayBlocked;++j)
          {
            Point3 a=envelope[0],b=envelope[0];
            const int u=(axis+1)%3,v=(axis+2)%3;
            a[u]=b[u]=envelope[0][u]+(envelope[1][u]-envelope[0][u])*float(i)*.5f;
            a[v]=b[v]=envelope[0][v]+(envelope[1][v]-envelope[0][v])*float(j)*.5f;
            b[axis]=envelope[1][axis];
            a=pose*a;b=pose*b;
            const float distance=length(b-a);
            if (distance<=.01f) continue;
            const Point3 direction=(b-a)/distance;
            for (int reverse=0;reverse<2 && !rayBlocked;++reverse)
            {
              const Point3 origin=reverse?b:a,dir=reverse?direction*-1.f:direction;
              float t=distance;
              if (scene.hasStaticScene())
                rayBlocked=scene.tracerayNormalizedStaticScene(origin,dir,t,nullptr,nullptr);
              t=distance;
              Point3 norm(0.f,0.f,0.f);
              if (!rayBlocked)
                rayBlocked=rendinst::traceRayRendInstsNormalized(origin,dir,t,norm,false,true,nullptr,true);
            }
          }
      if (rayBlocked)
      {
        console->addMessage(ILogWriter::NOTE,"[KE] SMART_PLACEMENT_REJECT attempt=%d reason=volume_rays",attempt);
        continue;
      }
      console->addMessage(ILogWriter::NOTE,"[KE] SMART_PLACEMENT_OK target=%d attempt=%d x=%.3f z=%.3f static=%d",
        keSmartTarget,attempt,candidate.x,candidate.z,int(scene.hasStaticScene()));
    }
'''
META_ANCHOR = '  result.setInt("placement_candidate", selectedCandidate);\n'
META = r'''  if (keSmartPlacement)
  {
    char key[32];
    snprintf(key,sizeof(key),"source_%d",selectedCandidate);
    result.setInt("smart_placement_target",keSmartTarget);
    result.setStr("smart_placement_source",keSmartCandidates.getStr(key,"missing"));
    result.setStr("smart_placement_check","ri_collision_bounds_static_rays_v1");
    result.setBool("smart_static_collision_available",scene.hasStaticScene());
  }
'''
EDITS=((INCLUDE,INCLUDE+"#include <gameRes/dag_collisionResource.h>\n"),(SETUP_ANCHOR,SETUP_ANCHOR+SETUP),
       (CANDIDATE_ANCHOR,CANDIDATE_ANCHOR+CANDIDATE),(CHECK_ANCHOR,CHECK+CHECK_ANCHOR),
       (META_ANCHOR,META_ANCHOR+META))


def upgrade_capture(src):
    if MARKER in src:
        if not all(src.count(new)==1 for _,new in EDITS):
            raise ValueError("Partial smart placement patch")
        return src
    for old,new in EDITS:
        if src.count(old)!=1:
            raise ValueError(f"Smart placement patch anchor missing/ambiguous: {old[:90]}")
        src=src.replace(old,new,1)
    return src


def install_smart_placement(source: Path):
    raw=source.read_bytes()
    src=raw.replace(b"\r\n",b"\n").decode("utf-8")
    changed=upgrade_capture(src)
    if src==changed:return False
    source.write_bytes(changed.encode("utf-8").replace(b"\n",b"\r\n" if b"\r\n" in raw else b"\n"))
    print("[KE] Installed experimental smart placement and live volume checks",flush=True)
    return True
