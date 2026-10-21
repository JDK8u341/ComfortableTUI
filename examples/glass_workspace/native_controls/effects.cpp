// Trusted example extension: a color-negative shader, no Python per frame.
#include "comfortable/glass_effect.hpp"
#include <pybind11/pybind11.h>
#include <wrl/client.h>
#include <d3dcompiler.h>
#include <atomic>
#include <cstring>
#include <memory>

using namespace comfortable;
using Microsoft::WRL::ComPtr;
namespace {
std::atomic<unsigned> draws{},created{},released{},wrong_thread{};
struct Resources {
    DWORD owner=GetCurrentThreadId();
    ComPtr<ID3D11VertexShader> vs;
    ComPtr<ID3D11PixelShader> ps;
    ComPtr<ID3D11SamplerState> sampler;
    ComPtr<ID3D11Buffer> buffer;
    ComPtr<ID3D11RasterizerState> raster;
};
const char *source=R"(
cbuffer P:register(b0){float4 amount;};
struct V{float4 pos:SV_POSITION;float2 uv:TEXCOORD;};
V vs(uint id:SV_VertexID){float2 p=float2((id<<1)&2,id&2);V o;o.pos=float4(p.x*2-1,1-p.y*2,0,1);o.uv=p;return o;}
Texture2D image:register(t0);SamplerState linearClamp:register(s0);
float4 ps(V v):SV_TARGET{
 float4 c=image.SampleLevel(linearClamp,v.uv,0);
 c.rgb=lerp(c.rgb,c.aaa-c.rgb,amount.x);return c;
})";
void *create(ID3D11Device *d) {
    // Callbacks report failure through nullptr/false; do not let exceptions cross.
    auto r=std::make_unique<Resources>();ComPtr<ID3DBlob> code,error;
    if(FAILED(D3DCompile(source,std::strlen(source),nullptr,nullptr,nullptr,"vs","vs_4_0",0,0,&code,&error)))return nullptr;
    if(FAILED(d->CreateVertexShader(code->GetBufferPointer(),code->GetBufferSize(),nullptr,&r->vs)))return nullptr;
    code.Reset();
    if(FAILED(D3DCompile(source,std::strlen(source),nullptr,nullptr,nullptr,"ps","ps_4_0",0,0,&code,&error)))return nullptr;
    if(FAILED(d->CreatePixelShader(code->GetBufferPointer(),code->GetBufferSize(),nullptr,&r->ps)))return nullptr;
    D3D11_SAMPLER_DESC sd{};sd.Filter=D3D11_FILTER_MIN_MAG_MIP_LINEAR;sd.AddressU=sd.AddressV=sd.AddressW=D3D11_TEXTURE_ADDRESS_CLAMP;sd.MaxLOD=D3D11_FLOAT32_MAX;
    if(FAILED(d->CreateSamplerState(&sd,&r->sampler)))return nullptr;
    D3D11_BUFFER_DESC bd{};bd.ByteWidth=16;bd.Usage=D3D11_USAGE_DEFAULT;bd.BindFlags=D3D11_BIND_CONSTANT_BUFFER;
    if(FAILED(d->CreateBuffer(&bd,nullptr,&r->buffer)))return nullptr;
    D3D11_RASTERIZER_DESC rd{};rd.FillMode=D3D11_FILL_SOLID;rd.CullMode=D3D11_CULL_NONE;rd.DepthClipEnable=TRUE;
    if(FAILED(d->CreateRasterizerState(&rd,&r->raster)))return nullptr;
    created++;return r.release();
}
void destroy(void *p){auto *r=static_cast<Resources *>(p);if(r->owner!=GetCurrentThreadId())wrong_thread++;released++;delete r;}
bool render(void *resource,const GlassEffectPaint &p){
    auto &r=*static_cast<Resources *>(resource);auto *ctx=p.context;
    ctx->OMSetRenderTargets(1,&p.output,nullptr);ctx->RSSetState(r.raster.Get());
    D3D11_VIEWPORT vp{0,0,static_cast<float>(p.output_width),static_cast<float>(p.output_height),0,1};ctx->RSSetViewports(1,&vp);
    ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    ctx->VSSetShader(r.vs.Get(),nullptr,0);ctx->PSSetShader(r.ps.Get(),nullptr,0);
    float values[4]={p.parameters[0]*p.opacity,0,0,0};ctx->UpdateSubresource(r.buffer.Get(),0,nullptr,values,0,0);
    auto *cb=r.buffer.Get();ctx->PSSetConstantBuffers(0,1,&cb);
    ctx->PSSetShaderResources(0,1,&p.input);auto *sampler=r.sampler.Get();ctx->PSSetSamplers(0,1,&sampler);ctx->Draw(3,0);
    draws++;return true;
}
const GlassEffectParameter parameters[]={{"amount",0,1,1}};
const GlassEffectType types[]={{"example.negative",2,0,parameters,1,0,create,destroy,render}};
const GlassEffectExtension extension=[] {GlassEffectExtension e;e.types=types;e.count=1;return e;}();
}
PYBIND11_MODULE(ctui_glass_effects,m){
    m.def("glass_effects",[]{return pybind11::capsule(&extension,glass_effect_extension_capsule);});
    m.def("stats",[]{pybind11::dict d;d["draws"]=draws.load();d["created"]=created.load();d["released"]=released.load();d["wrong_thread"]=wrong_thread.load();return d;});
}
