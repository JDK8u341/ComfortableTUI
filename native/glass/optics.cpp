#include "optics.hpp"
#include <d3dcompiler.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <map>
#include <vector>

namespace comfortable::glass_detail {
using namespace detail;
namespace {
const char *change_shader=R"(
cbuffer Bounds:register(b0){float4 area;};
Texture2D<float4> before:register(t0);Texture2D<float4> after:register(t1);
RWByteAddressBuffer changed:register(u0);
groupshared uint left,top,right,bottom;
[numthreads(16,16,1)]
void compare(uint3 thread:SV_DispatchThreadID,uint lane:SV_GroupIndex) {
 if(lane==0){left=top=0xffffffff;right=bottom=0;}GroupMemoryBarrierWithGroupSync();
 uint2 p=thread.xy+uint2(area.xy);
 if(thread.x<uint(area.z)&&thread.y<uint(area.w)) {
   if(any(abs(before.Load(int3(p,0)).rgb-after.Load(int3(p,0)).rgb)>.5/255.)){
     InterlockedMin(left,p.x);InterlockedMin(top,p.y);InterlockedMax(right,p.x+1);InterlockedMax(bottom,p.y+1);
   }
 }
 GroupMemoryBarrierWithGroupSync();
 if(lane==0&&left!=0xffffffff){uint ignored;
   changed.InterlockedMin(0,left,ignored);changed.InterlockedMin(4,top,ignored);
   changed.InterlockedMax(8,right,ignored);changed.InterlockedMax(12,bottom,ignored);
 }
}
)";
// Independent SDF lens. Background and UI are different compositor surfaces;
// text/images are never distorted along with the material behind them.
const char *optical_shader=R"(
cbuffer Material : register(b0) {
 float4 rect;       // output-local physical x/y and panel size
 float4 display;    // output width/height, blur radius, frosted flag
 float4 optical;    // corner radius, edge width, refraction, dispersion
 float4 tint;       // RGB + amount
 float4 pointer;    // local pointer x/y, interaction, unused
};
struct V {float4 p:SV_POSITION;float2 uv:TEXCOORD;};
V vs(uint id:SV_VertexID) {float2 p=float2((id<<1)&2,id&2);V o;o.p=float4(p.x*2-1,1-p.y*2,0,1);o.uv=p;return o;}
Texture2D desktop:register(t0);Texture2D blurred:register(t1);
SamplerState linearClamp:register(s0);
float2 desktopUV(float2 p) {return (rect.xy+p)/display.xy;}
float distanceToGlass(float2 p) {
 float r=min(optical.x,min(rect.z,rect.w)*.5);
 float2 q=abs(p-rect.zw*.5)-(rect.zw*.5-r);
 return length(max(q,0))+min(max(q.x,q.y),0)-r;
}
float4 blurH(V i):SV_TARGET {
 float2 p=i.uv*rect.zw;float4 c=0;float weight=0;
 [unroll]for(int n=-5;n<=5;n++){float w=exp(-float(n*n)/5.);c+=desktop.SampleLevel(linearClamp,desktopUV(p+float2(n*max(1.,display.z*.65),0)),0)*w;weight+=w;}
 return c/weight;
}
float4 blurV(V i):SV_TARGET {
 float4 c=0;float weight=0;uint w,h;blurred.GetDimensions(w,h);
 [unroll]for(int n=-5;n<=5;n++){float k=exp(-float(n*n)/5.);c+=blurred.SampleLevel(linearClamp,i.uv+float2(0,n*max(1.,display.z*.65)/rect.w),0)*k;weight+=k;}
 return c/weight;
}
float4 lens(V i):SV_TARGET {
 float2 p=i.uv*rect.zw;float sd=distanceToGlass(p);
 float alpha=1-smoothstep(-.8,.8,sd);if(alpha<=0)discard;
 float2 gradient=float2(distanceToGlass(p+float2(.5,0))-distanceToGlass(p-float2(.5,0)),
                        distanceToGlass(p+float2(0,.5))-distanceToGlass(p-float2(0,.5)));
 float2 normal=gradient/max(length(gradient),.001);
 float edge=saturate(1+sd/max(optical.y,1.));
 float bend=optical.z*edge*sqrt(max(.001,1-edge))*(1+pointer.z*.16);
 float2 shift=-normal*bend;
 float4 raw=desktop.SampleLevel(linearClamp,desktopUV(p+shift),0);
 float dispersion=optical.w*edge;
 raw.r=desktop.SampleLevel(linearClamp,desktopUV(p+shift+normal*dispersion),0).r;
 raw.b=desktop.SampleLevel(linearClamp,desktopUV(p+shift-normal*dispersion),0).b;
 if(display.z>.01) {
    float3 soft=blurred.SampleLevel(linearClamp,i.uv,0).rgb;
    raw.rgb=lerp(raw.rgb,soft,display.w>.5?1.:saturate(display.z/8.)*.22);
 }
 float3 light=lerp(raw.rgb,tint.rgb,tint.a);
 // Grazing reflection has opposing bright/dark arcs, rather than a white box.
 float rim=exp(-abs(sd+1.)*1.45);
 float directional=dot(normal,normalize(float2(-.55,-.83)));
 light+=rim*(.13+.30*pow(saturate(directional),2));
 light-=rim*.10*pow(saturate(-directional),2);
 light+=pow(edge,5)*.045;
 float glow=exp(-dot(p-pointer.xy,p-pointer.xy)/6500.)*step(.01,pointer.x)*.025;
 light+=glow;
 return float4(saturate(light)*alpha,alpha);
}
)";
struct Target {
    ComPtr<ID3D11Texture2D> texture;
    ComPtr<ID3D11ShaderResourceView> srv;
    ComPtr<ID3D11RenderTargetView> rtv;
};
Target target(ID3D11Device *d,UINT w,UINT h) {
    Target t;D3D11_TEXTURE2D_DESC td{};td.Width=w;td.Height=h;td.MipLevels=td.ArraySize=1;
    td.Format=DXGI_FORMAT_B8G8R8A8_UNORM;td.SampleDesc.Count=1;td.BindFlags=D3D11_BIND_SHADER_RESOURCE|D3D11_BIND_RENDER_TARGET;
    check(d->CreateTexture2D(&td,nullptr,&t.texture),"Glass optical texture");
    check(d->CreateShaderResourceView(t.texture.Get(),nullptr,&t.srv),"Glass optical SRV");
    check(d->CreateRenderTargetView(t.texture.Get(),nullptr,&t.rtv),"Glass optical RTV");return t;
}
bool overlaps(const RECT &a,const RECT &b) {return a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;}
}
struct OpticalPanel::Impl {
    FrameProvider frames;
    UINT width,height;
    Target horizontal,vertical;
    std::map<ID3D11Texture2D *,ComPtr<ID3D11RenderTargetView>> targets;
    RECT last{};
    bool ready{};
    Impl(ID3D11Device1 *d,UINT w,UINT h):frames(w,h,d),width(w),height(h){}
};
OpticalPanel::OpticalPanel(ID3D11Device1 *d,unsigned w,unsigned h):impl_(std::make_unique<Impl>(d,w,h)){}
OpticalPanel::~OpticalPanel()=default;
FrameDescriptor OpticalPanel::descriptor()const{return impl_->frames.descriptor();}

struct OpticalScene::Impl {
    HWND owner;
    HMONITOR monitor{};
    RECT bounds{};
    ComPtr<IDXGIOutput1> output;
    ComPtr<ID3D11Device1> device;
    ComPtr<ID3D11DeviceContext> context;
    ComPtr<IDXGIOutputDuplication> duplication;
    Target mirror;
    Target incoming;
    ComPtr<ID3D11VertexShader> vs;
    ComPtr<ID3D11PixelShader> lens,horizontal,vertical;
    ComPtr<ID3D11Buffer> constants;
    ComPtr<ID3D11SamplerState> sampler;
    ComPtr<ID3D11ComputeShader> compare;
    ComPtr<ID3D11Buffer> flag,flag_readback,compare_constants;
    ComPtr<ID3D11UnorderedAccessView> flag_uav;
    ComPtr<ID3D11Query> compared;
    std::vector<RECT> pending_rects;
    bool pending_compare{};
    std::vector<RECT> dirty;
    OpticalStats report;
    bool have_image{};
    double retry_at{};
    Impl(HWND hwnd):owner(hwnd) {
        monitor=MonitorFromWindow(owner,MONITOR_DEFAULTTONEAREST);
        ComPtr<IDXGIFactory1> factory;check(CreateDXGIFactory1(IID_PPV_ARGS(&factory)),"Glass capture factory");
        ComPtr<IDXGIAdapter1> chosen;
        for(UINT a=0;!output;a++) {
            ComPtr<IDXGIAdapter1> adapter;
            if(factory->EnumAdapters1(a,&adapter)==DXGI_ERROR_NOT_FOUND)break;
            for(UINT n=0;;n++) {
                ComPtr<IDXGIOutput> candidate;
                if(adapter->EnumOutputs(n,&candidate)==DXGI_ERROR_NOT_FOUND)break;
                DXGI_OUTPUT_DESC desc{};check(candidate->GetDesc(&desc),"Glass output description");
                if(desc.Monitor==monitor){check(candidate.As(&output),"Glass output duplication interface");chosen=adapter;bounds=desc.DesktopCoordinates;break;}
            }
        }
        if(!output)throw std::runtime_error("No GPU output for glass workspace monitor");
        DXGI_ADAPTER_DESC1 desc{};check(chosen->GetDesc1(&desc),"Glass adapter");
        device=create_device(desc.AdapterLuid.LowPart,desc.AdapterLuid.HighPart);device->GetImmediateContext(&context);
        ComPtr<ID3DBlob> code,errors;
        check(D3DCompile(optical_shader,std::strlen(optical_shader),nullptr,nullptr,nullptr,"vs","vs_4_0",D3DCOMPILE_OPTIMIZATION_LEVEL3,0,&code,&errors),"Glass lens vertex compile");
        check(device->CreateVertexShader(code->GetBufferPointer(),code->GetBufferSize(),nullptr,&vs),"Glass lens vertex");
        auto pixel=[&](const char *entry,ComPtr<ID3D11PixelShader> &shader) {
            code.Reset();errors.Reset();auto hr=D3DCompile(optical_shader,std::strlen(optical_shader),nullptr,nullptr,nullptr,entry,"ps_4_0",D3DCOMPILE_OPTIMIZATION_LEVEL3,0,&code,&errors);
            if(FAILED(hr))throw std::runtime_error(std::string("Glass shader ")+entry+": "+(errors?static_cast<const char *>(errors->GetBufferPointer()):"compile failed"));
            check(device->CreatePixelShader(code->GetBufferPointer(),code->GetBufferSize(),nullptr,&shader),"Glass optical pixel shader");
        };
        pixel("lens",lens);pixel("blurH",horizontal);pixel("blurV",vertical);
        D3D11_BUFFER_DESC bd{};bd.ByteWidth=80;bd.Usage=D3D11_USAGE_DEFAULT;bd.BindFlags=D3D11_BIND_CONSTANT_BUFFER;
        check(device->CreateBuffer(&bd,nullptr,&constants),"Glass optical constants");
        D3D11_SAMPLER_DESC sd{};sd.Filter=D3D11_FILTER_MIN_MAG_MIP_LINEAR;sd.AddressU=sd.AddressV=sd.AddressW=D3D11_TEXTURE_ADDRESS_CLAMP;sd.MaxLOD=D3D11_FLOAT32_MAX;
        check(device->CreateSamplerState(&sd,&sampler),"Glass optical sampler");
        code.Reset();errors.Reset();
        check(D3DCompile(change_shader,std::strlen(change_shader),nullptr,nullptr,nullptr,"compare","cs_5_0",D3DCOMPILE_OPTIMIZATION_LEVEL3,0,&code,&errors),"Glass GPU change detector compile");
        check(device->CreateComputeShader(code->GetBufferPointer(),code->GetBufferSize(),nullptr,&compare),"Glass GPU change detector");
        bd.ByteWidth=16;bd.BindFlags=D3D11_BIND_UNORDERED_ACCESS;bd.MiscFlags=D3D11_RESOURCE_MISC_BUFFER_ALLOW_RAW_VIEWS;
        check(device->CreateBuffer(&bd,nullptr,&flag),"Glass GPU change flag");
        D3D11_UNORDERED_ACCESS_VIEW_DESC uv{};uv.Format=DXGI_FORMAT_R32_TYPELESS;uv.ViewDimension=D3D11_UAV_DIMENSION_BUFFER;uv.Buffer.NumElements=4;uv.Buffer.Flags=D3D11_BUFFER_UAV_FLAG_RAW;
        check(device->CreateUnorderedAccessView(flag.Get(),&uv,&flag_uav),"Glass GPU change flag view");
        bd.BindFlags=0;bd.MiscFlags=0;bd.Usage=D3D11_USAGE_STAGING;bd.CPUAccessFlags=D3D11_CPU_ACCESS_READ;
        check(device->CreateBuffer(&bd,nullptr,&flag_readback),"Glass change metadata staging");
        bd.ByteWidth=16;bd.BindFlags=D3D11_BIND_CONSTANT_BUFFER;bd.Usage=D3D11_USAGE_DEFAULT;bd.CPUAccessFlags=0;
        check(device->CreateBuffer(&bd,nullptr,&compare_constants),"Glass comparison bounds");
        D3D11_QUERY_DESC query{D3D11_QUERY_EVENT,0};check(device->CreateQuery(&query,&compared),"Glass comparison completion");
    }
    void copy(const RECT &r,ID3D11Texture2D *to,ID3D11Texture2D *from) {
        if(r.right<=r.left||r.bottom<=r.top)return;
        D3D11_BOX box{static_cast<UINT>(r.left),static_cast<UINT>(r.top),0,static_cast<UINT>(r.right),static_cast<UINT>(r.bottom),1};
        context->CopySubresourceRegion(to,0,r.left,r.top,0,from,0,&box);
        report.copied_pixels+=static_cast<std::uint64_t>(r.right-r.left)*(r.bottom-r.top);
    }
    void acquire() {
        dirty.clear();
        if(pending_compare) {
            BOOL ready=FALSE;auto hr=context->GetData(compared.Get(),&ready,sizeof(ready),D3D11_ASYNC_GETDATA_DONOTFLUSH);
            check(hr,"Glass comparison completion");
            if(hr==S_FALSE||!ready)return;
            D3D11_MAPPED_SUBRESOURCE data{};check(context->Map(flag_readback.Get(),0,D3D11_MAP_READ,0,&data),"Glass change metadata");
        const auto *changed_bounds=static_cast<const UINT *>(data.pData);
        bool different=changed_bounds[0]!=UINT_MAX;
        RECT actual{};if(different)actual={static_cast<LONG>(changed_bounds[0]),static_cast<LONG>(changed_bounds[1]),static_cast<LONG>(changed_bounds[2]),static_cast<LONG>(changed_bounds[3])};
        context->Unmap(flag_readback.Get(),0);
        if(different){for(const auto &r:pending_rects)copy(r,mirror.texture.Get(),incoming.texture.Get());dirty={actual};report.captures++;}
            else report.unchanged++;
            pending_compare=false;pending_rects.clear();
        }
        if(!duplication) {
            double now=clock_ticks()/clock_frequency();if(now<retry_at)return;
            retry_at=now+1;
            auto hr=output->DuplicateOutput(device.Get(),&duplication);
            if(FAILED(hr)){report.error="Desktop Duplication unavailable (HRESULT "+std::to_string(static_cast<unsigned long>(hr))+")";return;}
            DXGI_OUTDUPL_DESC desc{};duplication->GetDesc(&desc);
            if(desc.Rotation!=DXGI_MODE_ROTATION_IDENTITY&&desc.Rotation!=DXGI_MODE_ROTATION_UNSPECIFIED) {
                duplication.Reset();report.error="Rotated desktop optical sampling is not supported";return;
            }
            report.active=true;report.error.clear();have_image=false;mirror={};incoming={};
        }
        DXGI_OUTDUPL_FRAME_INFO info{};ComPtr<IDXGIResource> resource;
        auto hr=duplication->AcquireNextFrame(0,&info,&resource);
        if(hr==DXGI_ERROR_WAIT_TIMEOUT)return;
        if(FAILED(hr)){duplication.Reset();report.active=false;report.error="Desktop sampling lost; retrying";return;}
        struct Release {IDXGIOutputDuplication *p;~Release(){p->ReleaseFrame();}} release{duplication.Get()};
        if(have_image&&!info.LastPresentTime.QuadPart)return;
        ComPtr<ID3D11Texture2D> image;check(resource.As(&image),"Glass desktop texture");
        D3D11_TEXTURE2D_DESC desc{};image->GetDesc(&desc);
        if(desc.Width>16384||desc.Height>16384||static_cast<double>(desc.Width)*desc.Height>16*1024*1024)
            throw std::runtime_error("Desktop optical texture exceeds 16-megapixel budget");
        if(!mirror.texture){mirror=target(device.Get(),desc.Width,desc.Height);incoming=target(device.Get(),desc.Width,desc.Height);}
        std::vector<RECT> changes;
        if(!have_image||info.TotalMetadataBufferSize>1024*1024)changes.push_back({0,0,static_cast<LONG>(desc.Width),static_cast<LONG>(desc.Height)});
        else {
            UINT bytes{};std::vector<DXGI_OUTDUPL_MOVE_RECT> moves(info.TotalMetadataBufferSize/sizeof(DXGI_OUTDUPL_MOVE_RECT)+1);
            hr=duplication->GetFrameMoveRects(static_cast<UINT>(moves.size()*sizeof(moves[0])),moves.data(),&bytes);
            bool moves_ok=SUCCEEDED(hr);
            if(moves_ok)for(UINT i=0;i<bytes/sizeof(moves[0]);i++)changes.push_back(moves[i].DestinationRect);
            std::vector<RECT> rects(info.TotalMetadataBufferSize/sizeof(RECT)+1);
            hr=duplication->GetFrameDirtyRects(static_cast<UINT>(rects.size()*sizeof(RECT)),rects.data(),&bytes);
            if(SUCCEEDED(hr)&&moves_ok)changes.insert(changes.end(),rects.begin(),rects.begin()+bytes/sizeof(RECT));
            else changes={{0,0,static_cast<LONG>(desc.Width),static_cast<LONG>(desc.Height)}};
        }
        for(auto &r:changes) {
            r.left=std::clamp<LONG>(r.left,0,desc.Width);r.right=std::clamp<LONG>(r.right,r.left,desc.Width);
            r.top=std::clamp<LONG>(r.top,0,desc.Height);r.bottom=std::clamp<LONG>(r.bottom,r.top,desc.Height);
            if(r.right==r.left||r.bottom==r.top)continue;
            copy(r,have_image?incoming.texture.Get():mirror.texture.Get(),image.Get());
        }
        if(!have_image){dirty=changes;have_image=true;report.captures++;return;}
        if(changes.empty())return;
        const UINT initial[4]={UINT_MAX,UINT_MAX,0,0};context->UpdateSubresource(flag.Get(),0,nullptr,initial,0,0);
        auto uav=flag_uav.Get();context->CSSetUnorderedAccessViews(0,1,&uav,nullptr);
        auto cb=compare_constants.Get();context->CSSetConstantBuffers(0,1,&cb);context->CSSetShader(compare.Get(),nullptr,0);
        ID3D11ShaderResourceView *sources[]={mirror.srv.Get(),incoming.srv.Get()};context->CSSetShaderResources(0,2,sources);
        for(const auto &r:changes) {
            if(r.right<=r.left||r.bottom<=r.top)continue;
            float area[4]={static_cast<float>(r.left),static_cast<float>(r.top),static_cast<float>(r.right-r.left),static_cast<float>(r.bottom-r.top)};
            context->UpdateSubresource(compare_constants.Get(),0,nullptr,area,0,0);
            context->Dispatch((r.right-r.left+15)/16,(r.bottom-r.top+15)/16,1);
        }
        ID3D11ShaderResourceView *empty[2]={};context->CSSetShaderResources(0,2,empty);uav=nullptr;context->CSSetUnorderedAccessViews(0,1,&uav,nullptr);
        context->CopyResource(flag_readback.Get(),flag.Get());context->End(compared.Get());context->Flush();
        pending_compare=true;pending_rects=std::move(changes);
    }
    void pass(ID3D11RenderTargetView *to,UINT w,UINT h,ID3D11PixelShader *shader,ID3D11ShaderResourceView *a,ID3D11ShaderResourceView *b) {
        ID3D11ShaderResourceView *empty[2]={};context->PSSetShaderResources(0,2,empty);
        context->OMSetRenderTargets(1,&to,nullptr);
        const float zero[4]={};context->ClearRenderTargetView(to,zero);
        D3D11_VIEWPORT viewport{0,0,static_cast<float>(w),static_cast<float>(h),0,1};context->RSSetViewports(1,&viewport);
        context->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        context->VSSetShader(vs.Get(),nullptr,0);context->PSSetShader(shader,nullptr,0);
        auto buffer=constants.Get();context->PSSetConstantBuffers(0,1,&buffer);
        auto samp=sampler.Get();context->PSSetSamplers(0,1,&samp);
        ID3D11ShaderResourceView *sources[]={a,b};context->PSSetShaderResources(0,2,sources);context->Draw(3,0);
        context->PSSetShaderResources(0,2,empty);context->OMSetRenderTargets(0,nullptr,nullptr);
    }
};
OpticalScene::OpticalScene(HWND owner):impl_(std::make_unique<Impl>(owner)){}
OpticalScene::~OpticalScene()=default;
std::unique_ptr<OpticalPanel> OpticalScene::panel(unsigned w,unsigned h) {
    return std::unique_ptr<OpticalPanel>(new OpticalPanel(impl_->device.Get(),w,h));
}
void OpticalScene::update(bool visible) {
    auto &p=*impl_;
    if(!visible){p.duplication.Reset();p.mirror={};p.incoming={};p.pending_compare=false;p.have_image=false;p.report.active=false;p.dirty.clear();return;}
    try{p.acquire();}catch(const std::exception &e){p.report.error=e.what();p.duplication.Reset();p.report.active=false;}
}
bool OpticalScene::render(OpticalPanel &panel,const RECT &screen,const GlassBackground &bg,float dpi,float px,float py,bool dragging,bool force) {
    auto &p=*impl_;auto &v=*panel.impl_;
    if(!p.have_image||!p.mirror.srv)return false;
    RECT local{screen.left-p.bounds.left,screen.top-p.bounds.top,screen.right-p.bounds.left,screen.bottom-p.bounds.top};
    float sigma=(bg.kind=="frosted"?std::max(12.f,bg.blur):bg.blur)*dpi;
    int margin=static_cast<int>(std::ceil(std::max(3.25f*sigma,(bg.refraction+bg.dispersion)*dpi)))+2;
    RECT affected{local.left-margin,local.top-margin,local.right+margin,local.bottom+margin};
    bool moved=std::memcmp(&screen,&v.last,sizeof(RECT))!=0;
    if(v.ready&&!force&&!moved&&std::none_of(p.dirty.begin(),p.dirty.end(),[&](const RECT &r){return overlaps(r,affected);}))return false;
    auto f=v.frames.begin_frame(0);if(!f)return false;
    auto texture=f.texture();
    if(!v.targets.count(texture))check(p.device->CreateRenderTargetView(texture,nullptr,&v.targets[texture]),"Glass frame target");
    float data[20]={static_cast<float>(local.left),static_cast<float>(local.top),static_cast<float>(v.width),static_cast<float>(v.height),
        static_cast<float>(p.bounds.right-p.bounds.left),static_cast<float>(p.bounds.bottom-p.bounds.top),sigma,bg.kind=="frosted"?1.f:0.f,
        bg.corner_radius*dpi,22*dpi,bg.kind=="liquid"?bg.refraction*dpi:0.f,bg.dispersion*dpi,
        ((bg.tint>>16)&255)/255.f,((bg.tint>>8)&255)/255.f,(bg.tint&255)/255.f,bg.opacity,px*dpi,py*dpi,dragging?1.f:0.f,0};
    p.context->UpdateSubresource(p.constants.Get(),0,nullptr,data,0,0);
    if(sigma>.01f) {
        UINT w=std::max(1u,v.width/4),h=std::max(1u,v.height/4);
        if(!v.horizontal.texture){v.horizontal=target(p.device.Get(),w,h);v.vertical=target(p.device.Get(),w,h);}
        p.pass(v.horizontal.rtv.Get(),w,h,p.horizontal.Get(),p.mirror.srv.Get(),nullptr);
        p.pass(v.vertical.rtv.Get(),w,h,p.vertical.Get(),nullptr,v.horizontal.srv.Get());
    }
    p.pass(v.targets.at(texture).Get(),v.width,v.height,p.lens.Get(),p.mirror.srv.Get(),v.vertical.srv.Get());
    f.publish();v.ready=true;v.last=screen;p.report.rendered++;return true;
}
OpticalStats OpticalScene::stats()const{auto result=impl_->report;result.image_ready=impl_->have_image;return result;}
LUID OpticalScene::adapter()const{return device_luid(impl_->device.Get());}
bool OpticalScene::exclude(HWND window) {
    // DDA-specific exclusion avoids imposing WDA_EXCLUDEFROMCAPTURE on all
    // capture clients. Failure must disable optical sampling, never recurse.
    struct Attribute {int attribute;void *data;SIZE_T bytes;};
    using Set=BOOL(WINAPI *)(HWND,const Attribute *);
    static auto set=reinterpret_cast<Set>(GetProcAddress(GetModuleHandleW(L"user32.dll"),"SetWindowCompositionAttribute"));
    BOOL yes=TRUE;Attribute a{24,&yes,sizeof(yes)};return set&&set(window,&a);
}
} // namespace comfortable::glass_detail
