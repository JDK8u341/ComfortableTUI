#pragma once
#include "comfortable/compositor.hpp"
#include <array>
namespace comfortable::detail {
inline void compositor_clip_params(std::array<float,60> &values,const std::vector<RoundedClip> &clips,bool replace=false){
    values[11]=static_cast<float>(clips.size())*(replace?-1.f:1.f);
    for(std::size_t i=0;i<clips.size();i++){
        const auto &s=clips[i];values[12+i*4]=s.box.x;values[13+i*4]=s.box.y;
        values[14+i*4]=s.box.width;values[15+i*4]=s.box.height;values[48+i]=s.radius;
    }
}
inline const char *compositor_shader = R"(
cbuffer Params : register(b0) { float4 target; float4 uvRect; float4 options; float4 clipRects[9]; float4 radii[3]; };
struct V { float4 pos:SV_POSITION; float2 uv:TEXCOORD; };
V vs(uint id:SV_VertexID) {
    float2 p=float2((id<<1)&2,id&2);
    V o;float2 q=target.xy+p*target.zw;
    o.pos=float4(q.x*2-1,1-q.y*2,0,1);o.uv=uvRect.xy+p*uvRect.zw;return o;
}
Texture2D image:register(t0); SamplerState sampler0:register(s0);
float4 premul(float4 c){c.rgb*=c.a;return c;}
float4 sampleImage(V v) {
 if(any(v.uv<0)||any(v.uv>1))discard;
 if(options.x>0.5 && options.y>0.5) {
  uint w,h;image.GetDimensions(w,h);float2 at=v.uv*float2(w,h)-.5;int2 a=int2(floor(at));float2 f=frac(at);
  int2 edge=int2(w-1,h-1);
  float4 c00=premul(image.Load(int3(clamp(a,int2(0,0),edge),0)));
  float4 c01=premul(image.Load(int3(clamp(a+int2(1,0),int2(0,0),edge),0)));
  float4 c10=premul(image.Load(int3(clamp(a+int2(0,1),int2(0,0),edge),0)));
  float4 c11=premul(image.Load(int3(clamp(a+int2(1,1),int2(0,0),edge),0)));
  return lerp(lerp(c00,c01,f.x),lerp(c10,c11,f.x),f.y);
 }
 float4 c=image.Sample(sampler0,v.uv);return options.x>0.5?premul(c):c;
}
float4 ps(V v):SV_TARGET {
 float alpha=1;
 [loop]for(int i=0;i<(int)abs(options.w);i++){
  float4 rect=clipRects[i];float r=min(radii[i/4][i%4],min(rect.z,rect.w)*.5);
  float2 q=abs(v.pos.xy-rect.xy-rect.zw*.5)-(rect.zw*.5-r);
  float d=length(max(q,0))+min(max(q.x,q.y),0)-r;
  if(options.w<0){if(d>0)discard;}else alpha*=saturate(.5-d);
 }
 return sampleImage(v)*options.z*alpha;
}
)";
}
