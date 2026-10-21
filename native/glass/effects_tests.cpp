// Pixel-level WARP tests. Readback is deliberately confined to this executable.
#include "comfortable/glass_effect.hpp"
#include "../canvas/internal.hpp"
#include <d3d11sdklayers.h>
#include <cmath>
#include <iostream>
#include <limits>
#include <functional>
#include <cstring>

using namespace comfortable;
using namespace comfortable::detail;
namespace {
void require(bool value,const char *what){if(!value)throw std::runtime_error(what);}
unsigned created=0,destroyed=0,called=0;
void *create(ID3D11Device *){created++;return new int(1);}
void destroy(void *p){destroyed++;delete static_cast<int *>(p);}
bool render(void *,const GlassEffectPaint &p){
    called++;const float color[]={0,static_cast<float>(p.parameters[0]),0,1};
    p.context->ClearRenderTargetView(p.output,color);return true;
}
bool failure(void *,const GlassEffectPaint &){return false;}
bool throwing(void *,const GlassEffectPaint &){throw std::runtime_error("extension fault");}
ComPtr<ID3D11Device> device(ComPtr<ID3D11DeviceContext> &ctx){
    ComPtr<ID3D11Device> d;
    auto hr=D3D11CreateDevice(nullptr,D3D_DRIVER_TYPE_WARP,nullptr,D3D11_CREATE_DEVICE_BGRA_SUPPORT|D3D11_CREATE_DEVICE_DEBUG,
        nullptr,0,D3D11_SDK_VERSION,&d,nullptr,&ctx);
    if(FAILED(hr))check(D3D11CreateDevice(nullptr,D3D_DRIVER_TYPE_WARP,nullptr,D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        nullptr,0,D3D11_SDK_VERSION,&d,nullptr,&ctx),"WARP device");
    return d;
}
ComPtr<ID3D11ShaderResourceView> image(ID3D11Device *d,unsigned w,unsigned h,const std::vector<unsigned> &pixels){
    D3D11_TEXTURE2D_DESC td{};td.Width=w;td.Height=h;td.MipLevels=td.ArraySize=1;td.Format=DXGI_FORMAT_B8G8R8A8_UNORM;
    td.SampleDesc.Count=1;td.Usage=D3D11_USAGE_DEFAULT;td.BindFlags=D3D11_BIND_SHADER_RESOURCE;
    D3D11_SUBRESOURCE_DATA data{pixels.data(),w*4,0};ComPtr<ID3D11Texture2D> texture;
    check(d->CreateTexture2D(&td,&data,&texture),"test image");
    ComPtr<ID3D11ShaderResourceView> view;check(d->CreateShaderResourceView(texture.Get(),nullptr,&view),"test SRV");return view;
}
std::vector<unsigned> read(ID3D11Device *d,ID3D11DeviceContext *ctx,ID3D11ShaderResourceView *srv){
    ComPtr<ID3D11Resource> resource;srv->GetResource(&resource);ComPtr<ID3D11Texture2D> texture;check(resource.As(&texture),"test texture");
    D3D11_TEXTURE2D_DESC desc{};texture->GetDesc(&desc);desc.Usage=D3D11_USAGE_STAGING;desc.BindFlags=desc.MiscFlags=0;desc.CPUAccessFlags=D3D11_CPU_ACCESS_READ;
    ComPtr<ID3D11Texture2D> staging;check(d->CreateTexture2D(&desc,nullptr,&staging),"test staging");
    ctx->CopyResource(staging.Get(),texture.Get());D3D11_MAPPED_SUBRESOURCE mapped{};check(ctx->Map(staging.Get(),0,D3D11_MAP_READ,0,&mapped),"test readback");
    std::vector<unsigned> result(desc.Width*desc.Height);
    for(unsigned y=0;y<desc.Height;y++)std::memcpy(result.data()+y*desc.Width,static_cast<const char *>(mapped.pData)+mapped.RowPitch*y,desc.Width*4);
    ctx->Unmap(staging.Get(),0);return result;
}
void bad(const std::function<void()> &fn){bool rejected=false;try{fn();}catch(const std::exception &){rejected=true;}require(rejected,"invalid input was accepted");}
}
int main(){
    try{
        ComPtr<ID3D11DeviceContext> ctx;auto d=device(ctx);
        auto solid=image(d.Get(),64,64,std::vector<unsigned>(64*64,0xffffffff));
        PixelBox full{0,0,64,64};
        auto tint=make_glass_effect_chain({{"tint","content",{1},0xff0000,1}},1);
        auto independent=make_glass_effect_chain({{"tint","content",{1},0x0000ff,1}},1);
        require(read(d.Get(),ctx.Get(),tint->process(d.Get(),ctx.Get(),solid.Get(),64,64,full,false))[2080]==0xffff0000,"tint must change source pixels");
        require(read(d.Get(),ctx.Get(),independent->process(d.Get(),ctx.Get(),solid.Get(),64,64,full,false))[2080]==0xff0000ff,"instances must have independent parameters");
        require(update_glass_effect_chain(tint,{{"tint","content",{0},0xff0000,1}}),"parameter update");
        require(read(d.Get(),ctx.Get(),tint->process(d.Get(),ctx.Get(),solid.Get(),64,64,full,false))[2080]==0xffffffff,"parameter update must affect output");
        std::vector<unsigned> square(64*64);
        for(unsigned y=24;y<40;y++)for(unsigned x=24;x<40;x++)square[y*64+x]=0xffffffff;
        auto shape=image(d.Get(),64,64,square);
        auto blur=make_glass_effect_chain({{"blur","content",{8}}},1);
        auto blurred=read(d.Get(),ctx.Get(),blur->process(d.Get(),ctx.Get(),shape.Get(),64,64,full,false));
        require((blurred[32*64+22]>>24)>0&&(blurred[32*64+24]>>24)<255,"blur must spread and soften the edge");
        auto glow=make_glass_effect_chain({{"glow","content",{1,12},0x00ff00}},1);
        auto glowing=read(d.Get(),ctx.Get(),glow->process(d.Get(),ctx.Get(),shape.Get(),64,64,full,false));
        require(((glowing[32*64+20]>>8)&255)>0&&(glowing[32*64+20]&255)==0,"glow must extend colored alpha outside the source");
        require(glowing[32*64+32]==0xffffffff,"glow must retain source ink");
        for(unsigned pixel:glowing)require((pixel&255)<=(pixel>>24)&&((pixel>>8)&255)<=(pixel>>24)&&((pixel>>16)&255)<=(pixel>>24),"premultiplied alpha contract");
        std::vector<unsigned> checker(64*64);
        for(unsigned y=0;y<64;y++)for(unsigned x=0;x<64;x++)checker[y*64+x]=((x/3+y/3)%2)?0xff102030:0xfff0c080;
        auto pattern=image(d.Get(),64,64,checker);
        auto liquid=make_glass_effect_chain({{"liquid","background",{24,1}}},1);
        auto refracted=read(d.Get(),ctx.Get(),liquid->process(d.Get(),ctx.Get(),pattern.Get(),64,64,full,false));
        require(refracted!=checker,"liquid must refract actual sampled pixels");
        // A local background samples neighbors, while isolated content must not.
        auto local_content=make_glass_effect_chain({{"blur","content",{8}}},1);
        auto local_background=make_glass_effect_chain({{"blur","background",{8}}},1);
        auto isolated=read(d.Get(),ctx.Get(),local_content->process(d.Get(),ctx.Get(),solid.Get(),64,64,{16,16,32,32},false));
        auto prefix=read(d.Get(),ctx.Get(),local_background->process(d.Get(),ctx.Get(),solid.Get(),64,64,{16,16,32,32},false));
        require((isolated[16*32]>>24)<255&&prefix[16*32]==0xffffffff,"background dependency and isolated content boundaries");
        auto edge=read(d.Get(),ctx.Get(),local_background->process(d.Get(),ctx.Get(),solid.Get(),64,64,full,false));
        require(edge.front()==0xffffffff&&edge.back()==0xffffffff,"background texture edges must clamp without black alpha fringes");
        auto two=make_glass_effect_chain({{"blur","content",{4}},{"tint","content",{1},0xff0000}},1);
        auto twice=read(d.Get(),ctx.Get(),two->process(d.Get(),ctx.Get(),shape.Get(),64,64,full,false));
        require((twice[32*64+22]&0xffff)==0,"ordered chained passes");
        auto rgba=image(d.Get(),1,1,{0x80ffffff});
        auto premul=read(d.Get(),ctx.Get(),tint->process(d.Get(),ctx.Get(),rgba.Get(),1,1,{0,0,1,1},true));
        require((premul[0]&255)==128,"straight alpha conversion occurs once");
        static const GlassEffectParameter schema[]={{"green",0,1,.4f}};
        static const GlassEffectType types[]={
            {"test.clear",2,0,schema,1,0,create,destroy,render},
            {"test.failure",2,0,nullptr,0,0,nullptr,nullptr,failure},
            {"test.throw",2,0,nullptr,0,0,nullptr,nullptr,throwing}};
        GlassEffectExtension extension;extension.types=types;extension.count=3;register_glass_effects(extension);
        auto incompatible=extension;incompatible.abi=1;bad([&]{register_glass_effects(incompatible);});
        bad([&]{register_glass_effects(extension);});
        static const GlassEffectType atomic_types[]={
            {"test.never_added",2,0,nullptr,0,0,nullptr,nullptr,render},
            {"tint",2,0,nullptr,0,0,nullptr,nullptr,render}};
        auto atomic=extension;atomic.types=atomic_types;atomic.count=2;
        bad([&]{register_glass_effects(atomic);});bad([&]{glass_effect_type("test.never_added");});
        auto custom=make_glass_effect_chain({{"test.clear","content",{.8f}}},1);
        auto green=read(d.Get(),ctx.Get(),custom->process(d.Get(),ctx.Get(),solid.Get(),64,64,full,false));
        require(((green[2080]>>8)&255)>=203&&called==1&&created==1,"custom callback must render real pixels");
        custom->process(d.Get(),ctx.Get(),solid.Get(),64,64,full,false);require(created==1,"reuse instance device resources");
        custom->process(d.Get(),ctx.Get(),solid.Get(),64,64,{0,0,32,32},false);require(created==2&&destroyed==1,"resize releases instance resources");
        ComPtr<ID3D11DeviceContext> ctx2;auto d2=device(ctx2);auto solid2=image(d2.Get(),8,8,std::vector<unsigned>(64,0xffffffff));
        custom->process(d2.Get(),ctx2.Get(),solid2.Get(),8,8,{0,0,8,8},false);require(created==3&&destroyed==2,"device replacement rebuilds effect resources");
        custom.reset();require(destroyed==created,"detach releases custom resources");
        bad([&]{glass_effect_parameters("blur",{25});});
        bad([&]{glass_effect_parameters("tint",{std::numeric_limits<float>::quiet_NaN()});});
        bad([&]{make_glass_effect_chain({{"liquid","content",{12,1}}},1);});
        bad([&]{make_glass_effect_chain({{"blur","invalid",{4}}},1);});
        bad([&]{auto large=make_glass_effect_chain({{"blur","content",{4}}},1);large->process(d.Get(),ctx.Get(),solid.Get(),64,64,{0,0,9000,9000},false);});
        bad([&]{auto fail=make_glass_effect_chain({{"test.failure","content",{}}},1);fail->process(d.Get(),ctx.Get(),solid.Get(),64,64,full,false);});
        bad([&]{auto fail=make_glass_effect_chain({{"test.throw","content",{}}},1);fail->process(d.Get(),ctx.Get(),solid.Get(),64,64,full,false);});
        // Subsequent draws still work after a failing extension.
        tint->process(d.Get(),ctx.Get(),solid.Get(),64,64,full,false);
        require(glass_effect_surface_bytes()>0,"effect texture accounting");
        tint.reset();independent.reset();blur.reset();glow.reset();liquid.reset();
        local_content.reset();local_background.reset();two.reset();
        require(glass_effect_surface_bytes()==0,"all effect textures must retire");
        ComPtr<ID3D11InfoQueue> queue;
        if(SUCCEEDED(d.As(&queue))){
            for(UINT64 i=0;i<queue->GetNumStoredMessages();i++){
                SIZE_T size=0;queue->GetMessage(i,nullptr,&size);std::vector<char> bytes(size);
                auto *message=reinterpret_cast<D3D11_MESSAGE *>(bytes.data());queue->GetMessage(i,message,&size);
                if(message->Severity<=D3D11_MESSAGE_SEVERITY_WARNING)throw std::runtime_error(message->pDescription);
            }
        }
        std::cout<<"{\"result\":\"passed\",\"backend\":\"D3D11 WARP\",\"pixel_checks\":true,\"debug_layer\":"<<(queue?"true":"false")<<"}\n";
        return 0;
    }catch(const std::exception &e){std::cerr<<e.what()<<"\n";return 1;}
}
