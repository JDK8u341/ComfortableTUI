// Automated, offscreen D3D11 effect benchmark. No pixel readback or input injection.
#include "comfortable/glass_effect.hpp"
#include "../canvas/internal.hpp"
#include <dxgi1_4.h>
#include <array>
#include <algorithm>
#include <chrono>
#include <iostream>
#include <iomanip>
#include <thread>

using namespace comfortable;
using namespace comfortable::detail;
using Clock=std::chrono::steady_clock;
namespace {
void require(bool value,const char *what){if(!value)throw std::runtime_error(what);}
double elapsed(Clock::time_point start){return std::chrono::duration<double,std::milli>(Clock::now()-start).count();}
double percentile(std::vector<double> values,double p){
    if(values.empty())throw std::runtime_error("no timing samples");
    std::sort(values.begin(),values.end());return values[static_cast<std::size_t>((values.size()-1)*p)];
}
ComPtr<ID3D11Device> device(bool warp,ComPtr<ID3D11DeviceContext> &context){
    ComPtr<ID3D11Device> result;
    check(D3D11CreateDevice(nullptr,warp?D3D_DRIVER_TYPE_WARP:D3D_DRIVER_TYPE_HARDWARE,nullptr,
        D3D11_CREATE_DEVICE_BGRA_SUPPORT,nullptr,0,D3D11_SDK_VERSION,&result,nullptr,&context),"benchmark device");return result;
}
struct Image {
    ComPtr<ID3D11Texture2D> texture;ComPtr<ID3D11ShaderResourceView> view;
    Image(ID3D11Device *d,UINT w,UINT h){
        std::vector<unsigned> pixels(w*h);
        for(UINT y=0;y<h;y++)for(UINT x=0;x<w;x++)pixels[y*w+x]=((x/8+y/8)%2)?0xff70c0a0:0xff203040;
        D3D11_TEXTURE2D_DESC td{};td.Width=w;td.Height=h;td.MipLevels=td.ArraySize=1;
        td.Format=DXGI_FORMAT_B8G8R8A8_UNORM;td.SampleDesc.Count=1;td.BindFlags=D3D11_BIND_SHADER_RESOURCE;
        D3D11_SUBRESOURCE_DATA data{pixels.data(),w*4,0};
        check(d->CreateTexture2D(&td,&data,&texture),"benchmark image");
        check(d->CreateShaderResourceView(texture.Get(),nullptr,&view),"benchmark image view");
    }
};
// Bounded async ring. Query polling cannot flush the GPU or spin to completion.
// This offscreen harness submits batches explicitly (there is no Present).
struct Timings {
    struct Slot {ComPtr<ID3D11Query> disjoint,start,end;bool pending{};};
    std::array<Slot,8> slots;
    std::vector<double> samples;
    unsigned invalid{};
    explicit Timings(ID3D11Device *d){for(auto &slot:slots){
        D3D11_QUERY_DESC q{D3D11_QUERY_TIMESTAMP_DISJOINT,0};check(d->CreateQuery(&q,&slot.disjoint),"disjoint query");
        q.Query=D3D11_QUERY_TIMESTAMP;check(d->CreateQuery(&q,&slot.start),"start query");check(d->CreateQuery(&q,&slot.end),"end query");
    }}
    Slot *available(){for(auto &slot:slots)if(!slot.pending)return &slot;return nullptr;}
    bool pending()const{for(const auto &slot:slots)if(slot.pending)return true;return false;}
    void poll(ID3D11DeviceContext *ctx){for(auto &slot:slots)if(slot.pending){
        D3D11_QUERY_DATA_TIMESTAMP_DISJOINT disjoint{};UINT64 start{},end{};
        const auto flags=D3D11_ASYNC_GETDATA_DONOTFLUSH;
        auto hr=ctx->GetData(slot.disjoint.Get(),&disjoint,sizeof(disjoint),flags);check(hr,"read disjoint");if(hr!=S_OK)continue;
        hr=ctx->GetData(slot.start.Get(),&start,sizeof(start),flags);check(hr,"read start");if(hr!=S_OK)continue;
        hr=ctx->GetData(slot.end.Get(),&end,sizeof(end),flags);check(hr,"read end");if(hr!=S_OK)continue;
        slot.pending=false;
        if(disjoint.Disjoint||!disjoint.Frequency||end<start){invalid++;continue;}
        samples.push_back((end-start)*1000.0/disjoint.Frequency);
    }}
};
void memory(ID3D11Device *d){
    ComPtr<IDXGIDevice> dx;check(d->QueryInterface(IID_PPV_ARGS(&dx)),"DXGI device");
    ComPtr<IDXGIAdapter> adapter;check(dx->GetAdapter(&adapter),"benchmark adapter");
    ComPtr<IDXGIAdapter3> a3;DXGI_QUERY_VIDEO_MEMORY_INFO local{},nonlocal{};
    if(SUCCEEDED(adapter.As(&a3))&&SUCCEEDED(a3->QueryVideoMemoryInfo(0,DXGI_MEMORY_SEGMENT_GROUP_LOCAL,&local))&&
       SUCCEEDED(a3->QueryVideoMemoryInfo(0,DXGI_MEMORY_SEGMENT_GROUP_NON_LOCAL,&nonlocal)))
        std::cout<<"{\"available\":true,\"process_local_usage\":"<<local.CurrentUsage<<",\"local_budget\":"<<local.Budget
                 <<",\"process_nonlocal_usage\":"<<nonlocal.CurrentUsage<<"}";
    else std::cout<<"{\"available\":false}";
}
void run(ID3D11Device *d,ID3D11DeviceContext *ctx,unsigned nodes,float dpi,const std::string &kind,unsigned frames){
    const UINT w=static_cast<UINT>(256*dpi),h=static_cast<UINT>(144*dpi);
    Image input(d,w,h),copy(d,w,h);
    std::vector<GlassEffectBinding> bindings;
    if(kind=="tint")bindings={{"tint","content",{.3f},0x85e5d1}};
    if(kind=="blur_glow")bindings={{"blur","content",{6}},{"glow","content",{.6f,12},0x85e5d1}};
    if(kind=="liquid")bindings={{"blur","background",{6}},{"liquid","background",{18,.5f}}};
    std::vector<std::shared_ptr<NativeImageProcessor>> chains;
    const auto cold=Clock::now();
    for(unsigned i=0;i<nodes;i++)chains.push_back(make_glass_effect_chain(bindings,dpi));
    const auto draw=[&]{for(auto &chain:chains){if(chain)chain->process(d,ctx,input.view.Get(),w,h,{0,0,static_cast<float>(w),static_cast<float>(h)},false);
        else ctx->CopyResource(copy.texture.Get(),input.texture.Get());}};
    draw();const double cold_ms=elapsed(cold);
    const auto initial=glass_effect_diagnostics();
    std::uint64_t passes_before=0;for(const auto &chain:chains)if(chain)passes_before+=chain->passes();
    Timings timings(d);std::vector<double> cpu;unsigned submitted=0,throttle_polls=0;
    auto deadline=Clock::now()+std::chrono::seconds(60);
    while(submitted<frames||timings.pending()){
        require(Clock::now()<deadline,"GPU query completion deadline exceeded");
        timings.poll(ctx);
        auto *slot=timings.available();
        if(submitted<frames&&slot){
            ctx->Begin(slot->disjoint.Get());ctx->End(slot->start.Get());
            auto start=Clock::now();draw();cpu.push_back(elapsed(start));
            ctx->End(slot->end.Get());ctx->End(slot->disjoint.Get());slot->pending=true;submitted++;
            if(submitted%8==0||submitted==frames)ctx->Flush(); // test-only submission, outside GPU/CPU measured zone
        }else if(timings.pending()){
            ctx->Flush(); // A partial reused ring also needs submission in an offscreen test.
            throttle_polls++;std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
    }
    require(!timings.samples.empty(),"GPU timing unavailable/disjoint; do not report zero");
    const auto final=glass_effect_diagnostics();
    require(initial.surface_allocations==final.surface_allocations,"steady rendering allocated textures");
    require(initial.shader_compilations==final.shader_compilations,"steady rendering compiled shaders");
    std::uint64_t passes=0;for(const auto &chain:chains)if(chain)passes+=chain->passes();
    std::cout<<"{\"kind\":\""<<kind<<"\",\"nodes\":"<<nodes<<",\"dpi_scale\":"<<dpi
        <<",\"pixels_per_node\":["<<w<<","<<h<<"],\"frames\":"<<frames<<",\"samples\":"<<timings.samples.size()
        <<",\"invalid_samples\":"<<timings.invalid<<",\"cold_submit_ms\":"<<cold_ms
        <<",\"cpu_submit_p50_ms\":"<<percentile(cpu,.5)<<",\"cpu_submit_p95_ms\":"<<percentile(cpu,.95)
        <<",\"gpu_batch_p50_ms\":"<<percentile(timings.samples,.5)<<",\"gpu_batch_p95_ms\":"<<percentile(timings.samples,.95)
        <<",\"throttle_polls\":"<<throttle_polls<<",\"effect_passes\":"<<passes-passes_before
        <<",\"owned_effect_surface_bytes\":"<<final.surface_bytes<<",\"steady_allocations\":0,\"steady_shader_compiles\":0,\"driver_memory\":";
    memory(d);std::cout<<"}";
    chains.clear();ctx->ClearState();require(glass_effect_surface_bytes()==0,"benchmark leaked effect surfaces");
}
}
int main(int argc,char **argv){
    try{
        bool warp=false,smoke=false;unsigned frames=64;
        for(int i=1;i<argc;i++){
            std::string arg=argv[i];if(arg=="--warp")warp=true;else if(arg=="--smoke")smoke=true;
            else if(arg=="--frames"&&i+1<argc)frames=static_cast<unsigned>(std::stoul(argv[++i]));else throw std::invalid_argument("usage: --warp --smoke --frames N");
        }
        require(frames>=8&&frames<=10000,"frames must be 8..10000");
        std::cout<<std::fixed<<std::setprecision(4)<<"{\"backend\":\""<<(warp?"WARP":"hardware")<<"\",\"schema\":1,\"cold_devices\":[";
        for(unsigned i=0;i<(smoke?2u:12u);i++){
            ComPtr<ID3D11DeviceContext> ctx;auto d=device(warp,ctx);Image input(d.Get(),64,64);
            const auto before=glass_effect_diagnostics();auto start=Clock::now();
            auto chain=make_glass_effect_chain({{"tint","content",{.3f}}},1);
            chain->process(d.Get(),ctx.Get(),input.view.Get(),64,64,{0,0,64,64},false);
            auto after=glass_effect_diagnostics();if(i)std::cout<<",";
            std::cout<<"{\"first_effect_ms\":"<<elapsed(start)<<",\"shader_compiles\":"<<after.shader_compilations-before.shader_compilations<<"}";
            chain->release_device_resources();ctx->ClearState();ctx->Flush();
        }
        std::cout<<"],\"cases\":[";
        ComPtr<ID3D11DeviceContext> ctx;auto d=device(warp,ctx);
        ComPtr<IDXGIDevice> dx;check(d.As(&dx),"DXGI");ComPtr<IDXGIAdapter> adapter;check(dx->GetAdapter(&adapter),"adapter");
        DXGI_ADAPTER_DESC desc{};check(adapter->GetDesc(&desc),"adapter description");
        bool first=true;
        for(float dpi:smoke?std::vector<float>{1}:std::vector<float>{1,1.25f,1.5f,2})
            for(unsigned nodes:smoke?std::vector<unsigned>{1,4}:std::vector<unsigned>{1,4,12})
                for(const std::string kind:{"direct_copy","tint","blur_glow","liquid"}){
                    if(!first)std::cout<<",";first=false;run(d.Get(),ctx.Get(),nodes,dpi,kind,frames);
                }
        auto stats=glass_effect_diagnostics();
        require(stats.surface_bytes==0,"effect memory not released after all runs");
        std::cout<<"],\"adapter_luid\":["<<desc.AdapterLuid.LowPart<<","<<desc.AdapterLuid.HighPart<<"],\"adapter_vendor\":"<<desc.VendorId
                 <<",\"shader_compilations\":"<<stats.shader_compilations<<",\"program_sets\":"<<stats.program_sets
                 <<",\"surface_allocations\":"<<stats.surface_allocations<<",\"remaining_effect_bytes\":0,\"result\":\"passed\"}\n";
        return 0;
    }catch(const std::exception &e){std::cerr<<e.what()<<"\n";return 1;}
}
