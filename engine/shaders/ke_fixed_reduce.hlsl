// Fixed-camera environment-only depth reduction for offline game imagery.
// Camera projection is symmetric reverse-Z, near=.2 and far=20000.
Texture2D<float> environment_depth : register(t0);
Texture2D<float> model_depth : register(t1);
RWStructuredBuffer<uint> counts : register(u0);
// 0 amodal, 1 visible, 2 invalid environment, 3 edges, 4 aperture lines,
// 5 sum x, 6 sum y, 7 max x+1, 8 max y+1, 9 max w-x, 10 max h-y,
// 11 completed groups; 12+ one float depth sum per group.
groupshared float metric[64];

bool env_valid(float d) { return isfinite(d) && d >= 0.0 && d <= 1.0; }
bool model_valid(float d) { return isfinite(d) && d > 0.0 && d <= 1.0; }
float linear_depth(float d) { return 4000.0 / (0.2 + d * 19999.8); }
float env_at(uint2 p) { return environment_depth.Load(int3(p,0)); }
float model_at(uint2 p) { return model_depth.Load(int3(p,0)); }
bool visible(float e,float m) { return e <= m + max(1e-7,1e-4*max(e,m)); }
bool edge(float a,float b)
{
    float za=linear_depth(a),zb=linear_depth(b);
    return env_valid(a) && env_valid(b) && abs(za-zb)>max(0.6,0.06*min(za,zb));
}
bool crossing(uint2 p,uint2 q)
{
    float a=env_at(p),b=env_at(q),m=model_at(p),n=model_at(q);
    return model_valid(m) && model_valid(n) && edge(a,b) && visible(a,m)!=visible(b,n);
}
bool opening(uint2 start,uint2 step,uint count)
{
    bool started=false;
    uint run=0;
    for (uint j=1;j<count;++j)
    {
        uint2 p=start+(j-1)*step,q=p+step;
        float a=env_at(p),b=env_at(q),m=model_at(p),n=model_at(q);
        if (!env_valid(a) || !env_valid(b) || !model_valid(m) || !model_valid(n))
        { started=false; run=0; continue; }
        bool v0=visible(a,m),v1=visible(b,n);
        if (!v0 && v1) { started=edge(a,b); run=1; }
        else if (v0 && v1 && started) ++run;
        else if (v0 && !v1)
        {
            if (started && run>=2 && edge(a,b)) return true;
            started=false; run=0;
        }
        else if (!v1) { started=false; run=0; }
    }
    return false;
}

[numthreads(8,8,1)]
void main(uint3 pos:SV_DispatchThreadID,uint3 group:SV_GroupID,uint lane:SV_GroupIndex)
{
    uint w,h;
    model_depth.GetDimensions(w,h);
    float d=0;
    if (pos.x<w && pos.y<h)
    {
        uint2 p=pos.xy;
        float m=model_at(p),e=env_at(p);
        if (model_valid(m))
        {
            InterlockedAdd(counts[0],1);
            if (!env_valid(e)) InterlockedAdd(counts[2],1);
            else if (visible(e,m)) InterlockedAdd(counts[1],1);
            uint hits=0;
            if (p.x+1<w && crossing(p,p+uint2(1,0))) ++hits;
            if (p.y+1<h && crossing(p,p+uint2(0,1))) ++hits;
            if (hits) InterlockedAdd(counts[3],hits);
            InterlockedAdd(counts[5],p.x);
            InterlockedAdd(counts[6],p.y);
            InterlockedMax(counts[7],p.x+1);
            InterlockedMax(counts[8],p.y+1);
            InterlockedMax(counts[9],w-p.x);
            InterlockedMax(counts[10],h-p.y);
            d=linear_depth(m);
        }
        if (p.x==0 && opening(p,uint2(1,0),w)) InterlockedAdd(counts[4],1);
        if (p.y==0 && opening(p,uint2(0,1),h)) InterlockedAdd(counts[4],1);
    }
    metric[lane]=d;
    GroupMemoryBarrierWithGroupSync();
    [unroll]
    for (uint stride=32;stride>0;stride>>=1)
    {
        if (lane<stride) metric[lane]+=metric[lane+stride];
        GroupMemoryBarrierWithGroupSync();
    }
    if (lane==0)
    {
        counts[12+group.y*((w+7)/8)+group.x]=asuint(metric[0]);
        InterlockedAdd(counts[11],1);
    }
}
