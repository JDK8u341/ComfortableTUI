#include "comfortable/glass_effect.hpp"
#include "../canvas/internal.hpp"
#include <d3dcompiler.h>
#include <d3d11shader.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <cstring>
#include <map>
#include <mutex>
#include <set>
#include <stdexcept>

namespace comfortable {
using namespace detail;
namespace {
struct ShaderDefinition {
    std::string name;std::vector<std::string> names;
    std::vector<GlassEffectParameter> parameters;
    ComPtr<ID3DBlob> code;
};
struct Registry {
    std::mutex mutex;
    std::map<std::string, GlassEffectType> types;
    std::map<std::string,std::unique_ptr<ShaderDefinition>> shaders;
    Registry() {
        static const GlassEffectParameter tint[]={{"amount",0,1,.35f}};
        static const GlassEffectParameter blur[]={{"radius",0,24,6}};
        static const GlassEffectParameter liquid[]={{"refraction",0,48,12},{"dispersion",0,3,.35f}};
        static const GlassEffectParameter glow[]={{"intensity",0,4,1},{"radius",0,64,18}};
        // Builtins dispatch to real GPU programs below, not successful no-ops.
        types.emplace("tint",GlassEffectType{"tint",2,1,tint,1,0});
        types.emplace("blur",GlassEffectType{"blur",2,2,blur,1,24});
        types.emplace("liquid",GlassEffectType{"liquid",2,3,liquid,2,51});
        types.emplace("glow",GlassEffectType{"glow",2,4,glow,2,64});
    }
};
Registry &registry(){static Registry value;return value;}
std::atomic<std::uint64_t> shader_compilations{},program_sets{},surface_allocations{};
std::atomic<std::uint64_t> package_compilations{};
ComPtr<ID3DBlob> shader_code(const std::string &name){
    auto &r=registry();std::lock_guard<std::mutex> lock(r.mutex);return r.shaders.at(name)->code;
}
const char *program=R"(
cbuffer P:register(b0){float4 crop;float4 params;float4 tint;float4 info;};
Texture2D image:register(t0);Texture2D original:register(t1);
SamplerState sampleLinear:register(s0);
struct V{float4 pos:SV_POSITION;float2 uv:TEXCOORD;};
V vs(uint id:SV_VertexID){float2 p=float2((id<<1)&2,id&2);V o;o.pos=float4(p.x*2-1,1-p.y*2,0,1);o.uv=p;return o;}
float4 copyImage(V v):SV_TARGET{
 float2 uv=crop.xy+v.uv*crop.zw;
 if(info.w>.5&&(any(uv<0)||any(uv>1)))return 0;
 if(info.w>.5&&(any(v.uv<info.xy)||any(v.uv>info.xy+params.xy)))return 0;
 float4 c=image.SampleLevel(sampleLinear,uv,0);
 if(info.z>.5)c.rgb*=c.a;
 if(info.w>.5&&params.z>0){
  uint iw,ih;image.GetDimensions(iw,ih);float2 extent=crop.zw*float2(iw,ih),size=params.xy*extent;
  float r=min(params.z,min(size.x,size.y)*.5);float2 q=abs((v.uv-info.xy-params.xy*.5)*extent)-(size*.5-r);
  float d=length(max(q,0))+min(max(q.x,q.y),0)-r;c*=saturate(.5-d);
 }
 return c;
}
float4 tintImage(V v):SV_TARGET{
 float4 c=image.SampleLevel(sampleLinear,v.uv,0);
 if(info.z>.5){float a=c.a*saturate(params.x*tint.a);return float4(tint.rgb*a,a);}
 c.rgb=lerp(c.rgb,tint.rgb*c.a,saturate(params.x*tint.a));return c;
}
float4 blurImage(V v):SV_TARGET{
 float4 sum=0;float total=0;
 [unroll]for(int n=-12;n<=12;n++){
  float k=exp(-float(n*n)/32.);
  sum+=image.SampleLevel(sampleLinear,v.uv+info.xy*(params.x/12.)*float(n),0)*k;total+=k;
 }return sum/total;
}
float4 glowImage(V v):SV_TARGET{
 float4 c=original.SampleLevel(sampleLinear,v.uv,0);
 float a=saturate(image.SampleLevel(sampleLinear,v.uv,0).a*params.y*tint.a);
 float4 halo=float4(tint.rgb*a,a)*(1-c.a);return info.z>.5?halo:c+halo;
}
float sdf(float2 p){
 float r=min(20.*info.w,min(crop.z,crop.w)*.5);
 float2 q=abs(p-crop.xy-crop.zw*.5)-(crop.zw*.5-r);
 return length(max(q,0))+min(max(q.x,q.y),0)-r;
}
float4 liquidImage(V v):SV_TARGET{
 uint w,h;image.GetDimensions(w,h);float2 size=float2(w,h),p=v.uv*size;
 float d=sdf(p),edge=saturate(1+d/max(1.,24.*info.w));
 float2 normal=float2(sdf(p+float2(.5,0))-sdf(p-float2(.5,0)),
                      sdf(p+float2(0,.5))-sdf(p-float2(0,.5)));
 normal/=max(length(normal),.001);
 float2 shift=-normal*(params.x*info.w*edge*sqrt(max(0.,1-edge)))/size;
 float4 c=image.SampleLevel(sampleLinear,v.uv+shift,0);
 float2 chroma=normal*(params.y*info.w*edge)/size;
 c.r=image.SampleLevel(sampleLinear,v.uv+shift+chroma,0).r;
 c.b=image.SampleLevel(sampleLinear,v.uv+shift-chroma,0).b;
 c.rgb=min(c.rgb,c.aaa);return c;
}
)";
// Immutable compiled code is device-independent. Keep it for the process lifetime
// (six small blobs), including when the last card/device has been destroyed.
// Device shaders/constants remain separate; no output or instance state is shared.
struct Bytecodes {
    std::array<ComPtr<ID3DBlob>,6> code;
    Bytecodes(){
        const char *entries[]={"vs","copyImage","tintImage","blurImage","glowImage","liquidImage"};
        for(std::size_t i=0;i<code.size();i++){
            ComPtr<ID3DBlob> errors;
            auto hr=D3DCompile(program,std::strlen(program),nullptr,nullptr,nullptr,entries[i],i?"ps_4_0":"vs_4_0",
                              D3DCOMPILE_OPTIMIZATION_LEVEL3,0,&code[i],&errors);
            shader_compilations++;
            if(FAILED(hr))throw std::runtime_error(errors?static_cast<const char *>(errors->GetBufferPointer()):"effect shader compile failed");
        }
    }
};
const Bytecodes &bytecodes(){static const Bytecodes result;return result;}
struct Programs {
    ComPtr<ID3D11VertexShader> vs;
    ComPtr<ID3D11PixelShader> copy,tint,blur,glow,liquid;
    ComPtr<ID3D11Buffer> constants;
    ComPtr<ID3D11SamplerState> sampler;
    ComPtr<ID3D11RasterizerState> raster;
    std::map<std::string,ComPtr<ID3D11PixelShader>> packages;
    explicit Programs(ID3D11Device *device){
        const auto &code=bytecodes().code;
        check(device->CreateVertexShader(code[0]->GetBufferPointer(),code[0]->GetBufferSize(),nullptr,&vs),"effect VS");
        std::array<ComPtr<ID3D11PixelShader> *,5> pixels={std::addressof(copy),std::addressof(tint),std::addressof(blur),std::addressof(glow),std::addressof(liquid)};
        for(std::size_t i=0;i<pixels.size();i++){
            const auto &p=code[i+1];
            check(device->CreatePixelShader(p->GetBufferPointer(),p->GetBufferSize(),nullptr,pixels[i]->GetAddressOf()),"effect PS");
        }
        D3D11_BUFFER_DESC bd{};bd.ByteWidth=64;bd.Usage=D3D11_USAGE_DEFAULT;bd.BindFlags=D3D11_BIND_CONSTANT_BUFFER;
        check(device->CreateBuffer(&bd,nullptr,&constants),"effect constants");
        D3D11_SAMPLER_DESC sd{};sd.Filter=D3D11_FILTER_MIN_MAG_MIP_LINEAR;sd.AddressU=sd.AddressV=sd.AddressW=D3D11_TEXTURE_ADDRESS_CLAMP;sd.MaxLOD=D3D11_FLOAT32_MAX;
        check(device->CreateSamplerState(&sd,&sampler),"effect sampler");
        D3D11_RASTERIZER_DESC rd{};rd.FillMode=D3D11_FILL_SOLID;rd.CullMode=D3D11_CULL_NONE;rd.DepthClipEnable=TRUE;
        check(device->CreateRasterizerState(&rd,&raster),"effect rasterizer");
        program_sets++;
    }
};
std::shared_ptr<Programs> programs(ID3D11Device *device){
    bytecodes(); // Never compile while holding the device-cache mutex.
    static std::mutex mutex;
    static std::map<ID3D11Device *,std::weak_ptr<Programs>> cache;
    std::lock_guard<std::mutex> lock(mutex);
    for(auto it=cache.begin();it!=cache.end();)if(it->second.expired())it=cache.erase(it);else ++it;
    auto result=cache[device].lock();
    if(!result){result=std::make_shared<Programs>(device);cache[device]=result;}return result;
}
struct Target {
    ComPtr<ID3D11Texture2D> texture;
    ComPtr<ID3D11ShaderResourceView> srv;
    ComPtr<ID3D11RenderTargetView> rtv;
    UINT width{},height{};
    Target()=default;
    Target(ID3D11Device *device,UINT w,UINT h):width(w),height(h){
        D3D11_TEXTURE2D_DESC d{};d.Width=w;d.Height=h;d.MipLevels=d.ArraySize=1;
        d.Format=DXGI_FORMAT_B8G8R8A8_UNORM;d.SampleDesc.Count=1;d.BindFlags=D3D11_BIND_RENDER_TARGET|D3D11_BIND_SHADER_RESOURCE;
        check(device->CreateTexture2D(&d,nullptr,&texture),"effect texture");
        check(device->CreateShaderResourceView(texture.Get(),nullptr,&srv),"effect SRV");
        check(device->CreateRenderTargetView(texture.Get(),nullptr,&rtv),"effect RTV");
    }
};
std::atomic<std::uint64_t> surface_bytes{};
struct CustomResource {
    const GlassEffectType *type{};
    void *resource{};
    ~CustomResource(){if(resource&&type&&type->destroy_device_resources)type->destroy_device_resources(resource);}
};
class Chain final:public NativeImageProcessor {
    std::mutex mutex;
    std::vector<GlassEffectBinding> bindings;
    const bool decoration;
    const float corner;
    std::vector<const GlassEffectType *> types; // Registry entries are immutable and never removed.
    std::vector<ComPtr<ID3DBlob>> package_code;
    std::vector<ComPtr<ID3D11PixelShader>> package_shaders;
    float dpi;
    ComPtr<ID3D11Device> device;
    std::shared_ptr<Programs> shared;
    std::vector<std::unique_ptr<CustomResource>> custom;
    Target original,a,b,output;
    UINT pad{};
    UINT node_width{},node_height{};
    std::uint64_t bytes{};
    std::atomic<std::uint64_t> rendered{};
    void release(){
        custom.clear();package_shaders.clear();original={};a={};b={};output={};shared.reset();device.Reset();
        surface_bytes.fetch_sub(bytes);bytes=0;
        node_width=node_height=0;
    }
    void allocate(ID3D11Device *d,UINT w,UINT h){
        if(device.Get()==d&&node_width==w&&node_height==h)return;
        release();
        // Each pass' dependency radius adds to the preceding pass' input.
        float radius=0;
        for(const auto *type:types)radius+=type->sampling_radius*dpi;
        pad=static_cast<UINT>(std::ceil(radius));
        auto ew=w+2*pad,eh=h+2*pad;
        bool overlay=decoration;
        auto required=(3ull*ew*eh+(overlay?1ull*ew*eh:1ull*w*h))*4;
        if(ew>16384||eh>16384||required>64ull*1024*1024)
            throw std::invalid_argument("Glass effect chain exceeds 64 MiB local texture budget");
        const auto previous=surface_bytes.fetch_add(required);
        if(previous+required>256ull*1024*1024){surface_bytes.fetch_sub(required);throw std::runtime_error("Glass effects exceed 256 MiB process texture budget");}
        bytes=required;
        try {
            node_width=w;node_height=h;
            device=d;shared=programs(d);original=Target(d,ew,eh);a=Target(d,ew,eh);b=Target(d,ew,eh);output=Target(d,overlay?ew:w,overlay?eh:h);
            surface_allocations+=4;
            package_shaders.resize(bindings.size());
            for(std::size_t i=0;i<bindings.size();i++){
                const auto &type=*types[i];auto instance=std::make_unique<CustomResource>();instance->type=&type;
                if(type.shader_id==5){
                    auto &shader=shared->packages[bindings[i].type];const auto &code=package_code[i];
                    if(!shader)check(d->CreatePixelShader(code->GetBufferPointer(),code->GetBufferSize(),nullptr,&shader),"package pixel shader");
                    package_shaders[i]=shader;
                }
                if(type.create_device_resources){instance->resource=type.create_device_resources(d);if(!instance->resource)throw std::runtime_error("effect resource creation failed: "+bindings[i].type);}
                custom.push_back(std::move(instance));
            }
        }catch(...){release();throw;}
    }
    void draw(ID3D11DeviceContext *ctx,Target &to,ID3D11ShaderResourceView *from,ID3D11PixelShader *ps,
              const std::array<float,16> &values,ID3D11ShaderResourceView *other=nullptr){
        // ClearState also isolates trusted extension state; the caller rebinds its
        // own compositor state after process(), including viewport/scissor/blend.
        ctx->ClearState();
        auto *rt=to.rtv.Get();ctx->OMSetRenderTargets(1,&rt,nullptr);
        D3D11_VIEWPORT vp{0,0,static_cast<float>(to.width),static_cast<float>(to.height),0,1};ctx->RSSetViewports(1,&vp);
        ctx->RSSetState(shared->raster.Get());ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        ctx->VSSetShader(shared->vs.Get(),nullptr,0);ctx->PSSetShader(ps,nullptr,0);
        ctx->UpdateSubresource(shared->constants.Get(),0,nullptr,values.data(),0,0);
        auto *cb=shared->constants.Get();ctx->VSSetConstantBuffers(0,1,&cb);ctx->PSSetConstantBuffers(0,1,&cb);
        ID3D11ShaderResourceView *inputs[]={from,other};ctx->PSSetShaderResources(0,2,inputs);
        auto *sampler=shared->sampler.Get();ctx->PSSetSamplers(0,1,&sampler);ctx->Draw(3,0);
        ctx->ClearState();rendered++;
    }
  public:
    Chain(std::vector<GlassEffectBinding> e,float scale,float radius):bindings(std::move(e)),decoration(bindings.front().target=="overlay"),corner(radius),dpi(scale){
        for(auto &binding:bindings){
            types.push_back(&glass_effect_type(binding.type));
            package_code.push_back(types.back()->shader_id==5?shader_code(binding.type):ComPtr<ID3DBlob>{});
            binding.parameters=glass_effect_parameters(binding.type,binding.parameters);
        }
    }
    ~Chain()override{release();}
    std::uint64_t passes()const override{return rendered.load();}
    float outset()const override{return decoration?static_cast<float>(pad):0.f;}
    float shape_radius()const override{return corner;}
    void release_device_resources()override{std::lock_guard<std::mutex> lock(mutex);release();}
    bool update(const std::vector<GlassEffectBinding> &next){
        std::lock_guard<std::mutex> lock(mutex);
        if(next.size()!=bindings.size())return false;
        for(std::size_t i=0;i<next.size();i++)if(next[i].type!=bindings[i].type||next[i].target!=bindings[i].target)return false;
        auto normalized=next;
        for(auto &e:normalized)e.parameters=glass_effect_parameters(e.type,e.parameters);
        bindings=std::move(normalized);return true;
    }
    ID3D11ShaderResourceView *process(ID3D11Device *d,ID3D11DeviceContext *ctx,ID3D11ShaderResourceView *input,
        std::uint32_t w,std::uint32_t h,const PixelBox &r,bool straight)override{
        std::lock_guard<std::mutex> lock(mutex);
        if(!input||!w||!h||!std::isfinite(r.x)||!std::isfinite(r.y)||!std::isfinite(r.width)||!std::isfinite(r.height)||
           r.width<=0||r.height<=0||r.width>16384||r.height>16384)throw std::invalid_argument("invalid effect image");
        auto ow=static_cast<UINT>(std::ceil(r.width)),oh=static_cast<UINT>(std::ceil(r.height));allocate(d,ow,oh);
        float ew=static_cast<float>(original.width),eh=static_cast<float>(original.height);
        std::array<float,16> values{(r.x-pad)/w,(r.y-pad)/h,ew/w,eh/h};
        values[14]=straight?1.f:0.f;
        if(bindings.front().target!="background"){
            values[12]=pad/ew;values[13]=pad/eh;values[4]=ow/ew;values[5]=oh/eh;values[15]=1;
            values[6]=corner*dpi;
        }
        draw(ctx,original,input,shared->copy.Get(),values);
        auto *current=&original;
        for(std::size_t i=0;i<bindings.size();i++){
            const auto &e=bindings[i];const auto &type=*types[i];
            const auto &parameters=e.parameters;
            values={};values[0]=static_cast<float>(pad);values[1]=static_cast<float>(pad);values[2]=static_cast<float>(ow);values[3]=static_cast<float>(oh);
            values[4]=parameters.empty()?0:parameters[0];values[5]=parameters.size()>1?parameters[1]:0;
            values[6]=parameters.size()>2?parameters[2]:0;values[7]=parameters.size()>3?parameters[3]:0;
            values[8]=((e.tint>>16)&255)/255.f;values[9]=((e.tint>>8)&255)/255.f;values[10]=(e.tint&255)/255.f;values[11]=e.opacity;values[15]=dpi;
            values[14]=e.target=="overlay"?1.f:0.f;
            Target *next=current==&a?&b:&a;
            if(type.shader_id==1)draw(ctx,*next,current->srv.Get(),shared->tint.Get(),values);
            else if(type.shader_id==2||type.shader_id==4){
                float radius=type.shader_id==2?parameters[0]:parameters[1];
                if(radius==0&&type.shader_id==2)continue;
                // Keep the unblurred image for glow's premultiplied over.
                if(type.shader_id==4){
                    ctx->ClearState();if(current!=&original)ctx->CopyResource(original.texture.Get(),current->texture.Get());
                    current=&original;next=&a;
                }
                values[4]=radius*dpi;values[12]=1/ew;values[13]=0;
                draw(ctx,*next,current->srv.Get(),shared->blur.Get(),values);current=next;next=current==&a?&b:&a;
                values[12]=0;values[13]=1/eh;
                draw(ctx,*next,current->srv.Get(),shared->blur.Get(),values);current=next;
                if(type.shader_id==4){
                    next=current==&a?&b:&a;values[5]=parameters[0];
                    draw(ctx,*next,current->srv.Get(),shared->glow.Get(),values,original.srv.Get());
                } else {continue;}
            }else if(type.shader_id==3)draw(ctx,*next,current->srv.Get(),shared->liquid.Get(),values);
            else if(type.shader_id==5)draw(ctx,*next,current->srv.Get(),package_shaders[i].Get(),values);
            else {
                ctx->ClearState();const float clear[4]={};ctx->ClearRenderTargetView(next->rtv.Get(),clear);
                GlassEffectPaint p;p.context=ctx;p.input=current->srv.Get();p.output=next->rtv.Get();
                p.input_width=p.output_width=original.width;p.input_height=p.output_height=original.height;
                p.input_x=static_cast<std::int32_t>(std::floor(r.x))-static_cast<int>(pad);
                p.input_y=static_cast<std::int32_t>(std::floor(r.y))-static_cast<int>(pad);
                p.dpi=dpi;p.tint=e.tint;p.opacity=e.opacity;p.parameters=parameters.data();p.parameter_count=parameters.size();
                bool ok=false;
                try{ok=type.render(custom[i]->resource,p);}catch(...){ctx->ClearState();throw std::runtime_error("native effect callback threw: "+e.type);}
                ctx->ClearState();if(!ok)throw std::runtime_error("native effect render failed: "+e.type);rendered++;
            }
            current=next;
        }
        values={static_cast<float>(pad)/ew,static_cast<float>(pad)/eh,static_cast<float>(ow)/ew,static_cast<float>(oh)/eh};
        if(bindings.front().target=="overlay")values={0,0,1,1};
        draw(ctx,output,current->srv.Get(),shared->copy.Get(),values);
        return output.srv.Get();
    }
};
} // namespace
void register_glass_shader(const std::string &name,const std::string &source,const std::string &entry,
                          const std::vector<GlassEffectParameter> &parameters,float radius){
    if(name.empty()||name.size()>128||name.find('\0')!=std::string::npos||source.empty()||source.size()>128*1024||
       entry.empty()||entry.size()>64||entry.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")!=std::string::npos||
       !std::isfinite(radius)||radius<0||radius>128||parameters.size()>4)throw std::invalid_argument("invalid Glass HLSL package");
    auto &r=registry();
    {std::lock_guard<std::mutex> lock(r.mutex);if(r.types.count(name)||r.shaders.size()>=64)throw std::invalid_argument("duplicate shader type or package limit");}
    auto owned=std::make_unique<ShaderDefinition>();owned->name=name;owned->names.reserve(parameters.size());
    std::set<std::string> names;
    for(const auto &p:parameters){
        std::string n=p.name?p.name:"";
        if(n.empty()||n.size()>64||!names.insert(n).second||!std::isfinite(p.minimum)||!std::isfinite(p.maximum)||!std::isfinite(p.default_value)||
           p.minimum>p.maximum||p.default_value<p.minimum||p.default_value>p.maximum)throw std::invalid_argument("invalid HLSL parameter schema");
        owned->names.push_back(n);owned->parameters.push_back({owned->names.back().c_str(),p.minimum,p.maximum,p.default_value});
    }
    ComPtr<ID3DBlob> errors;
    auto hr=D3DCompile(source.data(),source.size(),name.c_str(),nullptr,nullptr,entry.c_str(),"ps_4_0",
        D3DCOMPILE_ENABLE_STRICTNESS|D3DCOMPILE_OPTIMIZATION_LEVEL3,0,&owned->code,&errors);package_compilations++;
    if(FAILED(hr))throw std::invalid_argument(errors?static_cast<const char *>(errors->GetBufferPointer()):"HLSL compilation failed");
    ComPtr<ID3D11ShaderReflection> reflection;
    check(D3DReflect(owned->code->GetBufferPointer(),owned->code->GetBufferSize(),IID_PPV_ARGS(&reflection)),"reflect HLSL package");
    D3D11_SHADER_DESC desc{};check(reflection->GetDesc(&desc),"HLSL description");
    for(UINT i=0;i<desc.BoundResources;i++){
        D3D11_SHADER_INPUT_BIND_DESC input{};check(reflection->GetResourceBindingDesc(i,&input),"HLSL resource");
        bool allowed=input.BindPoint==0&&input.BindCount==1&&
            ((input.Type==D3D_SIT_TEXTURE&&input.Dimension==D3D_SRV_DIMENSION_TEXTURE2D)||input.Type==D3D_SIT_SAMPLER||input.Type==D3D_SIT_CBUFFER);
        if(!allowed)throw std::invalid_argument("HLSL package only accepts t0 Texture2D, s0 sampler and b0 constants");
        if(input.Type==D3D_SIT_CBUFFER){D3D11_SHADER_BUFFER_DESC buffer{};
            check(reflection->GetConstantBufferByName(input.Name)->GetDesc(&buffer),"HLSL constants");
            if(buffer.Size>64)throw std::invalid_argument("HLSL constant buffer exceeds 64 bytes");
        }
    }
    if(desc.OutputParameters!=1)throw std::invalid_argument("HLSL package needs one float4 SV_Target output");
    D3D11_SIGNATURE_PARAMETER_DESC output{};check(reflection->GetOutputParameterDesc(0,&output),"HLSL output");
    if(output.SystemValueType!=D3D_NAME_TARGET||output.SemanticIndex!=0||output.ComponentType!=D3D_REGISTER_COMPONENT_FLOAT32||output.Mask!=15)
        throw std::invalid_argument("HLSL output must be float4 SV_Target0");
    for(UINT i=0;i<desc.InputParameters;i++){
        D3D11_SIGNATURE_PARAMETER_DESC input{};check(reflection->GetInputParameterDesc(i,&input),"HLSL input");
        if(input.ComponentType!=D3D_REGISTER_COMPONENT_FLOAT32||
           !(input.SystemValueType==D3D_NAME_POSITION||(_stricmp(input.SemanticName,"TEXCOORD")==0&&input.SemanticIndex==0&&input.Mask<=3)))
            throw std::invalid_argument("HLSL input only accepts SV_POSITION and float2 TEXCOORD0");
    }
    GlassEffectType type{name.c_str(),2,5,owned->parameters.data(),owned->parameters.size(),radius};
    type.name=owned->name.c_str();
    std::map<std::string,GlassEffectType> additions;additions.emplace(name,type);
    std::map<std::string,std::unique_ptr<ShaderDefinition>> shader_additions;shader_additions.emplace(name,std::move(owned));
    std::lock_guard<std::mutex> lock(r.mutex);
    if(r.types.count(name)||r.shaders.size()>=64)throw std::invalid_argument("duplicate shader type or package limit");
    r.shaders.merge(shader_additions);r.types.merge(additions);
}
void register_glass_effects(const GlassEffectExtension &extension) {
    GlassEffectExtension expected;
    if(extension.abi!=expected.abi||extension.structure_size!=expected.structure_size||
       extension.pointer_size!=expected.pointer_size||extension.compiler!=expected.compiler||
       extension.iterator_debug!=expected.iterator_debug||extension.dynamic_crt!=expected.dynamic_crt||
       extension.debug_crt!=expected.debug_crt||!extension.types||!extension.count||extension.count>32)
        throw std::invalid_argument("incompatible Glass effect extension ABI/toolchain");
    auto &r=registry();std::lock_guard<std::mutex> lock(r.mutex);std::map<std::string,GlassEffectType> additions;
    for(std::size_t i=0;i<extension.count;i++){
        auto type=extension.types[i];std::string name=type.name?type.name:"";
        if(name.empty()||name.size()>128||type.version!=2||!type.render||type.shader_id!=0||type.parameter_count>16||
           (type.parameter_count&&!type.parameters)||!std::isfinite(type.sampling_radius)||type.sampling_radius<0||type.sampling_radius>128||
           (type.create_device_resources==nullptr)!=(type.destroy_device_resources==nullptr)||r.types.count(name)||additions.count(name))
            throw std::invalid_argument("invalid or duplicate Glass effect type");
        std::set<std::string> names;
        for(std::size_t p=0;p<type.parameter_count;p++){
            const auto &s=type.parameters[p];std::string key=s.name?s.name:"";
            if(key.empty()||key.size()>64||!names.insert(key).second||!std::isfinite(s.minimum)||!std::isfinite(s.maximum)||
               !std::isfinite(s.default_value)||s.minimum>s.maximum||s.default_value<s.minimum||s.default_value>s.maximum)
                throw std::invalid_argument("invalid Glass effect parameter schema");
        }additions.emplace(name,type);
    }r.types.merge(additions);
}
const GlassEffectType &glass_effect_type(const std::string &name){
    auto &r=registry();std::lock_guard<std::mutex> lock(r.mutex);auto at=r.types.find(name);
    if(at==r.types.end())throw std::invalid_argument("unregistered Glass effect type: "+name);return at->second;
}
std::vector<float> glass_effect_parameters(const std::string &name,const std::vector<float> &input){
    const auto &type=glass_effect_type(name);
    if(input.size()>type.parameter_count)throw std::invalid_argument("too many Glass effect parameters");
    std::vector<float> result;
    for(std::size_t i=0;i<type.parameter_count;i++){
        auto v=i<input.size()?input[i]:type.parameters[i].default_value;
        if(!std::isfinite(v)||v<type.parameters[i].minimum||v>type.parameters[i].maximum)
            throw std::invalid_argument("Glass effect parameter outside schema: "+name);
        result.push_back(v);
    }return result;
}
void validate_glass_effect(const GlassEffectBinding &e){
    if(e.type.empty()||e.type.size()>128||(e.target!="background"&&e.target!="content"&&e.target!="overlay")||e.tint>0xffffff||
       !std::isfinite(e.opacity)||e.opacity<0||e.opacity>1)throw std::invalid_argument("invalid Glass effect binding");
    if(e.type=="liquid"&&e.target!="background")throw std::invalid_argument("liquid only supports background; no content hit-test mapping");
    if((e.type=="blur"||e.type=="liquid")&&e.opacity!=1)throw std::invalid_argument("blur/liquid opacity must be 1");
    glass_effect_parameters(e.type,e.parameters);
}
std::shared_ptr<NativeImageProcessor> make_glass_effect_chain(const std::vector<GlassEffectBinding> &effects,float dpi,float radius){
    if(effects.empty())return {};
    if(effects.size()>8||!std::isfinite(dpi)||dpi<=0||dpi>8||!std::isfinite(radius)||radius<0||radius>128)throw std::invalid_argument("invalid effect chain");
    for(const auto &e:effects)validate_glass_effect(e);
    for(const auto &e:effects)if(e.target!=effects.front().target)throw std::invalid_argument("effect chain must belong to one stage");
    return std::make_shared<Chain>(effects,dpi,radius);
}
bool update_glass_effect_chain(const std::shared_ptr<NativeImageProcessor> &chain,const std::vector<GlassEffectBinding> &effects){
    for(const auto &e:effects)validate_glass_effect(e);
    auto *value=dynamic_cast<Chain *>(chain.get());return value&&value->update(effects);
}
std::uint64_t glass_effect_surface_bytes(){return surface_bytes.load();}
GlassEffectDiagnostics glass_effect_diagnostics(){
    return {surface_bytes.load(),surface_allocations.load(),shader_compilations.load(),program_sets.load(),package_compilations.load()};
}
} // namespace comfortable
