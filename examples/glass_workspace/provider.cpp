// Standalone GPU signal source. Shares the existing FrameProvider v1 textures.
#include "comfortable/frame_provider.hpp"
#include <d3dcompiler.h>
#include <wrl/client.h>
#include <windows.h>
#include <cstring>
#include <atomic>
#include <thread>
#include <memory>
#include <iostream>
#include <stdexcept>
using Microsoft::WRL::ComPtr;
void check(HRESULT hr){if(FAILED(hr))throw std::runtime_error("signal renderer HRESULT "+std::to_string(hr));}
const char *code=R"(
cbuffer Time:register(b0){float4 params;};
struct V{float4 position:SV_POSITION;float2 uv:TEXCOORD;};
V vs(uint i:SV_VertexID){float2 p=float2((i<<1)&2,i&2);V o;o.position=float4(p.x*2-1,1-p.y*2,0,1);o.uv=p;return o;}
float4 ps(V v):SV_TARGET{
 float2 p=v.uv;float t=params.x;
 float y=.5+params.y*(.25*sin(p.x*19+t*1.7)+.1*sin(p.x*38-t*2.3));
 float wave=exp(-abs(p.y-y)*210);
 float grid=pow(abs(cos(p.x*42)),48)*.035+pow(abs(cos(p.y*18)),48)*.035;
 float3 bg=float3(.025,.06,.105)+grid;
 float glow=exp(-abs(p.y-y)*18)*.14;
 return float4(bg+float3(.3,.85,.7)*(wave+glow),1);
})";
int main(){
 try{
    comfortable::FrameProvider provider(640,192);
    auto device=provider.device();auto ctx=provider.context();
    ComPtr<ID3DBlob> vertex,pixel,error;
    check(D3DCompile(code,std::strlen(code),nullptr,nullptr,nullptr,"vs","vs_4_0",0,0,&vertex,&error));
    check(D3DCompile(code,std::strlen(code),nullptr,nullptr,nullptr,"ps","ps_4_0",0,0,&pixel,&error));
    ComPtr<ID3D11VertexShader> vs;ComPtr<ID3D11PixelShader> ps;
    check(device->CreateVertexShader(vertex->GetBufferPointer(),vertex->GetBufferSize(),nullptr,&vs));
    check(device->CreatePixelShader(pixel->GetBufferPointer(),pixel->GetBufferSize(),nullptr,&ps));
    D3D11_BUFFER_DESC desc{};desc.ByteWidth=16;desc.Usage=D3D11_USAGE_DEFAULT;desc.BindFlags=D3D11_BIND_CONSTANT_BUFFER;
    ComPtr<ID3D11Buffer> constants;check(device->CreateBuffer(&desc,nullptr,&constants));
    auto d=provider.descriptor();
    std::cout<<"CTUI_GPU_V1 "<<d.name<<' '<<d.width<<' '<<d.height<<' '<<d.adapter_low<<' '<<d.adapter_high<<std::endl;
    HANDLE timer=CreateWaitableTimerExW(nullptr,nullptr,2,TIMER_ALL_ACCESS);
    if(!timer)timer=CreateWaitableTimerExW(nullptr,nullptr,0,TIMER_ALL_ACCESS);
    if(!timer)throw std::runtime_error("signal timer unavailable");
    auto start=comfortable::clock_ticks();
    struct State{std::atomic<bool> running{true};std::atomic<float> amplitude{.55f};};
    auto state=std::make_shared<State>();
    std::thread([state]{float amplitude;while(std::cin>>amplitude)if(amplitude>=0&&amplitude<=1)state->amplitude=amplitude;state->running=false;}).detach();
    for(;state->running;){
        auto stamp=comfortable::clock_ticks();auto frame=provider.begin_frame(0);
        if(frame){
            float params[4]={static_cast<float>((stamp-start)/comfortable::clock_frequency()),state->amplitude,0,0};
            ctx->UpdateSubresource(constants.Get(),0,nullptr,params,0,0);
            auto *target=frame.target();ctx->OMSetRenderTargets(1,&target,nullptr);
            D3D11_VIEWPORT viewport{0,0,640,192,0,1};ctx->RSSetViewports(1,&viewport);
            ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
            ctx->VSSetShader(vs.Get(),nullptr,0);ctx->PSSetShader(ps.Get(),nullptr,0);
            auto *cb=constants.Get();ctx->PSSetConstantBuffers(0,1,&cb);ctx->Draw(3,0);
            ctx->OMSetRenderTargets(0,nullptr,nullptr);frame.publish(stamp);
        }
        LARGE_INTEGER due{};due.QuadPart=-166666;SetWaitableTimer(timer,&due,0,nullptr,nullptr,FALSE);WaitForSingleObject(timer,INFINITE);
    }
    CloseHandle(timer);return 0;
 }catch(const std::exception &e){std::cerr<<e.what()<<std::endl;return 1;}
}
