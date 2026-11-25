// Offline screenshot visibility reduction. D32 reverse-Z; same rules as CPU.
Texture2D<float> scene_depth : register(t0);
Texture2D<float> model_depth : register(t1);
RWStructuredBuffer<uint> counts : register(u0);

groupshared uint aa[64];
groupshared uint vv[64];
groupshared uint mm[64];

bool depth_valid(float d)
{
    return isfinite(d) && d > 0.0 && d <= 1.0;
}

[numthreads(8, 8, 1)]
void main(uint3 pos : SV_DispatchThreadID, uint lane : SV_GroupIndex)
{
    uint w, h;
    model_depth.GetDimensions(w, h);
    uint a = 0, v = 0, m = 0;
    if (pos.x < w && pos.y < h)
    {
        float model = model_depth.Load(int3(pos.xy, 0));
        float scene = scene_depth.Load(int3(pos.xy, 0));
        if (depth_valid(model))
        {
            a = 1;
            bool valid = depth_valid(scene);
            float tolerance = max(1e-7, 1e-4 * max(model, scene));
            v = valid && abs(model - scene) <= tolerance ? 1 : 0;
            m = !valid || scene + tolerance < model ? 1 : 0;
        }
    }
    aa[lane] = a; vv[lane] = v; mm[lane] = m;
    GroupMemoryBarrierWithGroupSync();
    [unroll]
    for (uint stride = 32; stride > 0; stride >>= 1)
    {
        if (lane < stride)
        {
            aa[lane] += aa[lane + stride];
            vv[lane] += vv[lane + stride];
            mm[lane] += mm[lane + stride];
        }
        GroupMemoryBarrierWithGroupSync();
    }
    if (lane == 0)
    {
        InterlockedAdd(counts[0], aa[0]);
        InterlockedAdd(counts[1], vv[0]);
        InterlockedAdd(counts[2], mm[0]);
        InterlockedAdd(counts[3], 1); // completion witness: one write per group
    }
}
