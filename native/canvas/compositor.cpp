#include "internal.hpp"
#include "comfortable/compositor.hpp"
#include <dcomp.h>
#include <d3dcompiler.h>
#include <algorithm>
#include <array>
#include <condition_variable>
#include <cmath>
#include <cstring>
#include <map>
#include <mutex>
#include <set>
#include <thread>

namespace comfortable {
using namespace detail;
namespace {
struct CpuHeader {
    char magic[8];
    std::int32_t published, reading;
    std::uint64_t sequence;
    double timestamp;
};
static_assert(sizeof(CpuHeader) == 32, "legacy frame ABI changed");
struct Source {
    NativeView spec;
    std::unique_ptr<FrameConsumer> consumer;
    ReadFrame held;
    Handle mapping, meta, reader, cpu_ready;
    Mapping memory;
    ComPtr<ID3D11Texture2D> cpu_texture;
    ComPtr<ID3D11ShaderResourceView> cpu_view;
    std::uint64_t sequence{}, frames{}, skipped{};
    double source_seconds{}, ready_seconds{}, acquire_ms{};
    bool closed{};
    bool active{};
    Source(const NativeView &s, ID3D11Device *device) : spec(s) {
        if (s.gpu) {
            consumer = std::make_unique<FrameConsumer>(s.source, device);
            return;
        }
        auto row = s.source.width * (s.pixel_format == "rgb8" ? 3ull : 4ull);
        auto bytes = 64ull + 3ull * s.stride * s.source.height;
        if (!s.source.width || !s.source.height || s.source.width > 16384 || s.source.height > 16384 ||
            s.stride < row || s.stride % 4 || bytes > 512ull * 1024 * 1024 ||
            (s.pixel_format != "rgb8" && s.pixel_format != "rgba8" && s.pixel_format != "bgra8" &&
             s.pixel_format != "bgra8_premultiplied"))
            throw std::invalid_argument("invalid CPU frame descriptor");
        auto name = wide(s.source.name);
        mapping = Handle(OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, name.c_str()));
        memory.map(mapping.value, static_cast<std::size_t>(bytes));
        if (std::memcmp(memory.address, "CTUIFR01", 8) != 0)
            throw std::runtime_error("CPU frame ABI mismatch");
        name = wide("Local\\" + s.source.name + "_meta");
        meta = Handle(OpenMutexW(SYNCHRONIZE | MUTEX_MODIFY_STATE, FALSE, name.c_str()));
        name = wide("Local\\" + s.source.name + "_reader");
        reader = Handle(OpenMutexW(SYNCHRONIZE | MUTEX_MODIFY_STATE, FALSE, name.c_str()));
        name = wide("Local\\" + s.source.name + "_ready");
        auto event = OpenEventW(SYNCHRONIZE, FALSE, name.c_str());
        if (event)
            cpu_ready = Handle(event);
        D3D11_TEXTURE2D_DESC d{};
        d.Width = s.source.width;
        d.Height = s.source.height;
        d.MipLevels = d.ArraySize = 1;
        d.Format = s.pixel_format == "rgba8" || s.pixel_format == "rgb8" ? DXGI_FORMAT_R8G8B8A8_UNORM
                                                                         : DXGI_FORMAT_B8G8R8A8_UNORM;
        d.SampleDesc.Count = 1;
        d.Usage = D3D11_USAGE_DYNAMIC;
        d.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
        d.BindFlags = D3D11_BIND_SHADER_RESOURCE;
        check(device->CreateTexture2D(&d, nullptr, &cpu_texture), "Create CPU upload texture");
        check(device->CreateShaderResourceView(cpu_texture.Get(), nullptr, &cpu_view), "CPU upload view");
    }
    bool refresh(ID3D11DeviceContext *context) {
        auto started = clock_ticks();
        std::uint64_t next{};
        if (consumer) {
            closed = consumer->closed();
            auto frame = consumer->acquire_latest(sequence, 1);
            if (!frame)
                return false;
            next = frame.sequence();
            source_seconds = frame.source_ticks() / clock_frequency();
            ready_seconds = frame.published_ticks() / clock_frequency();
            held = std::move(frame); // Retain GPU ownership until a newer frame replaces it.
        } else {
            Lock read(reader.value, 0);
            if (!read)
                return false;
            int slot = -1;
            double timestamp{};
            {
                Lock lock(meta.value, 0);
                if (!lock)
                    return false;
                CpuHeader h;
                std::memcpy(&h, memory.address, sizeof(h));
                if (std::memcmp(h.magic, "CTUIFR01", 8) || h.published < -1 || h.published > 2 ||
                    h.reading < -1 || h.reading > 2 || !std::isfinite(h.timestamp))
                    throw std::runtime_error("corrupt CPU frame metadata");
                if (h.sequence == sequence || h.published < 0)
                    return false;
                next = h.sequence;
                timestamp = h.timestamp;
                slot = h.published;
                static_cast<CpuHeader *>(memory.address)->reading = slot;
            }
            // WRITE_DISCARD lets the driver rename in-flight upload storage.
            // Copy directly from the pinned shared slot, without an intermediate
            // CPU resize or UpdateSubresource's optional extra copy.
            struct Unpin {
                Source *s;
                ~Unpin() {
                    Lock l(s->meta.value);
                    if (l)
                        static_cast<CpuHeader *>(s->memory.address)->reading = -1;
                }
            } unpin{this};
            auto *bytes = static_cast<unsigned char *>(memory.address) + 64 +
                          static_cast<std::size_t>(slot) * spec.stride * spec.source.height;
            D3D11_MAPPED_SUBRESOURCE mapped{};
            check(context->Map(cpu_texture.Get(), 0, D3D11_MAP_WRITE_DISCARD, 0, &mapped),
                  "Map CPU upload texture");
            for (UINT y = 0; y < spec.source.height; y++) {
                auto *to = static_cast<unsigned char *>(mapped.pData) +
                           static_cast<std::size_t>(y) * mapped.RowPitch;
                auto *from = bytes + static_cast<std::size_t>(y) * spec.stride;
                if (spec.pixel_format == "rgb8") {
                    for (UINT x = 0; x < spec.source.width; x++) {
                        to[x * 4] = from[x * 3];
                        to[x * 4 + 1] = from[x * 3 + 1];
                        to[x * 4 + 2] = from[x * 3 + 2];
                        to[x * 4 + 3] = 255;
                    }
                } else
                    std::memcpy(to, from, static_cast<std::size_t>(spec.source.width) * 4);
            }
            context->Unmap(cpu_texture.Get(), 0);
            source_seconds = ready_seconds = timestamp;
        }
        skipped += sequence ? next - sequence - 1 : 0;
        sequence = next;
        frames++;
        acquire_ms = (clock_ticks() - started) * 1000 / clock_frequency();
        return true;
    }
    ID3D11ShaderResourceView *view() const {
        return consumer ? held.view() : (sequence ? cpu_view.Get() : nullptr);
    }
};
const char *shader = R"(
cbuffer Params : register(b0) { float4 target; float4 uvRect; float4 options; };
struct V { float4 pos:SV_POSITION; float2 uv:TEXCOORD; };
V vs(uint id:SV_VertexID) {
    float2 p=float2((id<<1)&2,id&2);
    V o;float2 q=target.xy+p*target.zw;
    o.pos=float4(q.x*2-1,1-q.y*2,0,1);o.uv=uvRect.xy+p*uvRect.zw;return o;
}
Texture2D image:register(t0); SamplerState sampler0:register(s0);
float4 premul(float4 c){c.rgb*=c.a;return c;}
float4 ps(V v):SV_TARGET {
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
)";
} // namespace
struct Compositor::Impl {
    HWND hwnd;
    std::uint32_t low;
    std::int32_t high;
    Handle stop{CreateEventW(nullptr, TRUE, FALSE, nullptr)},
        changed{CreateEventW(nullptr, FALSE, FALSE, nullptr)};
    std::thread worker;
    mutable std::mutex mutex;
    std::condition_variable acknowledged;
    std::uint64_t submitted{}, applied{};
    std::vector<NativeView> requested;
    UINT width{1}, height{1};
    bool show{}, closed{};
    CompositorMetrics report;
    explicit Impl(std::uintptr_t h, std::uint32_t l, std::int32_t hi)
        : hwnd(reinterpret_cast<HWND>(h)), low(l), high(hi) {
        if (!IsWindow(hwnd))
            throw std::invalid_argument("invalid compositor HWND");
        // DComp must not be attached to a CPU WS_EX_LAYERED backing store.
        auto style = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
        SetWindowLongPtrW(hwnd, GWL_EXSTYLE, (style & ~WS_EX_LAYERED) | WS_EX_NOREDIRECTIONBITMAP);
        worker = std::thread([this] { run(); });
    }
    void run() noexcept {
        try {
            render();
        } catch (const std::exception &e) {
            std::lock_guard<std::mutex> lock(mutex);
            report.error = e.what();
            acknowledged.notify_all();
        }
    }
    void render() {
        auto device = create_device(low, high);
        ComPtr<ID3D11DeviceContext> context;
        device->GetImmediateContext(&context);
        ComPtr<IDXGIDevice> dx;
        check(device.As(&dx), "IDXGIDevice");
        ComPtr<IDXGIAdapter> adapter;
        check(dx->GetAdapter(&adapter), "GetAdapter");
        ComPtr<IDXGIFactory2> factory;
        check(adapter->GetParent(IID_PPV_ARGS(&factory)), "IDXGIFactory2");
        ComPtr<IDCompositionDevice> composition;
        check(DCompositionCreateDevice(dx.Get(), IID_PPV_ARGS(&composition)), "DCompositionCreateDevice");
        ComPtr<IDCompositionTarget> composition_target;
        check(composition->CreateTargetForHwnd(hwnd, TRUE, &composition_target), "CreateTargetForHwnd");
        ComPtr<IDCompositionVisual> visual;
        check(composition->CreateVisual(&visual), "CreateVisual");
        check(composition_target->SetRoot(visual.Get()), "SetRoot");
        ComPtr<ID3DBlob> vs_code, ps_code, errors;
        check(D3DCompile(shader, std::strlen(shader), nullptr, nullptr, nullptr, "vs", "vs_4_0",
                         D3DCOMPILE_OPTIMIZATION_LEVEL3, 0, &vs_code, &errors),
              "compile compositor vertex shader");
        check(D3DCompile(shader, std::strlen(shader), nullptr, nullptr, nullptr, "ps", "ps_4_0",
                         D3DCOMPILE_OPTIMIZATION_LEVEL3, 0, &ps_code, &errors),
              "compile compositor pixel shader");
        ComPtr<ID3D11VertexShader> vs;
        ComPtr<ID3D11PixelShader> ps;
        check(device->CreateVertexShader(vs_code->GetBufferPointer(), vs_code->GetBufferSize(), nullptr, &vs),
              "CreateVertexShader");
        check(device->CreatePixelShader(ps_code->GetBufferPointer(), ps_code->GetBufferSize(), nullptr, &ps),
              "CreatePixelShader");
        ComPtr<ID3D11Buffer> constants;
        D3D11_BUFFER_DESC bd{};
        bd.ByteWidth = 48;
        bd.Usage = D3D11_USAGE_DEFAULT;
        bd.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
        check(device->CreateBuffer(&bd, nullptr, &constants), "Create constants");
        ComPtr<ID3D11SamplerState> samplers[2];
        for (int i = 0; i < 2; i++) {
            D3D11_SAMPLER_DESC s{};
            s.Filter = i ? D3D11_FILTER_MIN_MAG_MIP_LINEAR : D3D11_FILTER_MIN_MAG_MIP_POINT;
            s.AddressU = s.AddressV = s.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
            s.MaxLOD = D3D11_FLOAT32_MAX;
            check(device->CreateSamplerState(&s, &samplers[i]), "CreateSamplerState");
        }
        ComPtr<ID3D11BlendState> blend;
        D3D11_BLEND_DESC blend_desc{};
        auto &b = blend_desc.RenderTarget[0];
        b.BlendEnable = TRUE;
        b.SrcBlend = b.SrcBlendAlpha = D3D11_BLEND_ONE;
        b.DestBlend = b.DestBlendAlpha = D3D11_BLEND_INV_SRC_ALPHA;
        b.BlendOp = b.BlendOpAlpha = D3D11_BLEND_OP_ADD;
        b.RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_ALL;
        check(device->CreateBlendState(&blend_desc, &blend), "CreateBlendState");
        D3D11_RASTERIZER_DESC raster_desc{};
        raster_desc.FillMode = D3D11_FILL_SOLID;
        raster_desc.CullMode = D3D11_CULL_NONE;
        raster_desc.ScissorEnable = TRUE;
        raster_desc.DepthClipEnable = TRUE;
        ComPtr<ID3D11RasterizerState> raster;
        check(device->CreateRasterizerState(&raster_desc, &raster), "CreateRasterizerState");
        ComPtr<IDXGISwapChain2> swap;
        Handle latency;
        UINT sw = 0, sh = 0;
        std::map<std::string, std::unique_ptr<Source>> sources;
        std::vector<NativeView> views;
        bool visible = false, dirty = true, have_config = false, capacity = false, pending = false;
        std::uint64_t presents = 0;
        for (;;) {
            std::vector<HANDLE> waits{stop.value, changed.value};
            bool cpu = false;
            if (visible)
                for (auto &entry : sources) {
                    auto &source = *entry.second;
                    if (!source.active)
                        continue;
                    if (source.consumer && !source.closed)
                        waits.push_back(source.consumer->ready_event());
                    else if (source.cpu_ready.value)
                        waits.push_back(source.cpu_ready.value);
                    else if (!source.consumer)
                        cpu = true;
                }
            if ((dirty || pending || cpu) && visible && latency.value && !capacity)
                waits.push_back(latency.value);
            DWORD result =
                WaitForMultipleObjects(static_cast<DWORD>(waits.size()), waits.data(), FALSE,
                                       !have_config ? INFINITE : (visible && (cpu || pending) ? 1 : 100));
            if (result == WAIT_OBJECT_0)
                break;
            if (result == WAIT_FAILED)
                throw std::runtime_error("native frame wait failed");
            if (result == WAIT_OBJECT_0 + 1 || !have_config) {
                UINT w, h;
                std::uint64_t serial;
                {
                    std::lock_guard<std::mutex> lock(mutex);
                    views = requested;
                    w = width;
                    h = height;
                    visible = show;
                    serial = submitted;
                }
                std::set<std::string> needed;
                for (const auto &v : views)
                    needed.insert(v.source.name);
                for (auto it = sources.begin(); it != sources.end();)
                    if (!needed.count(it->first))
                        it = sources.erase(it);
                    else
                        ++it;
                for (const auto &v : views)
                    if (!sources.count(v.source.name))
                        sources[v.source.name] = std::make_unique<Source>(v, device.Get());
                for (auto &entry : sources)
                    entry.second->active = false;
                for (const auto &v : views)
                    if (v.clip.width > 0 && v.clip.height > 0)
                        sources.at(v.source.name)->active = true;
                if (w != sw || h != sh) {
                    context->ClearState();
                    if (!swap) {
                        DXGI_SWAP_CHAIN_DESC1 d{};
                        d.Width = w;
                        d.Height = h;
                        d.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
                        d.SampleDesc.Count = 1;
                        d.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
                        d.BufferCount = 2;
                        d.Scaling = DXGI_SCALING_STRETCH;
                        d.SwapEffect = DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL;
                        d.AlphaMode = DXGI_ALPHA_MODE_PREMULTIPLIED;
                        d.Flags = DXGI_SWAP_CHAIN_FLAG_FRAME_LATENCY_WAITABLE_OBJECT;
                        ComPtr<IDXGISwapChain1> chain;
                        check(factory->CreateSwapChainForComposition(device.Get(), &d, nullptr, &chain),
                              "CreateSwapChainForComposition");
                        check(chain.As(&swap), "IDXGISwapChain2");
                        check(swap->SetMaximumFrameLatency(1), "SetMaximumFrameLatency");
                        latency = Handle(swap->GetFrameLatencyWaitableObject());
                        check(visual->SetContent(swap.Get()), "SetContent");
                        check(composition->Commit(), "DirectComposition Commit");
                    } else
                        check(swap->ResizeBuffers(2, w, h, DXGI_FORMAT_B8G8R8A8_UNORM,
                                                  DXGI_SWAP_CHAIN_FLAG_FRAME_LATENCY_WAITABLE_OBJECT),
                              "ResizeBuffers");
                    sw = w;
                    sh = h;
                }
                have_config = true;
                dirty = true;
                {
                    std::lock_guard<std::mutex> lock(mutex);
                    applied = serial;
                    acknowledged.notify_all();
                }
            }
            if (!visible || !swap)
                continue;
            if (result >= WAIT_OBJECT_0 + 2 && result < WAIT_OBJECT_0 + waits.size())
                pending = true;
            // Read the newest source only once DXGI has capacity, not before a
            // frame-queue wait. The worker never acquires the Python GIL.
            if (!capacity)
                capacity = WaitForSingleObject(latency.value, 0) == WAIT_OBJECT_0;
            // The wait may already have consumed the auto-reset latency signal.
            if (!waits.empty() && waits.back() == latency.value && result == WAIT_OBJECT_0 + waits.size() - 1)
                capacity = true;
            if (!capacity)
                continue;
            bool updated = false;
            for (auto &entry : sources)
                if (entry.second->active)
                    updated = entry.second->refresh(context.Get()) || updated;
            pending = false;
            for (auto &entry : sources)
                if (entry.second->active && entry.second->consumer)
                    pending = entry.second->consumer->pending(entry.second->sequence) || pending;
            if (!dirty && !updated) {
                std::lock_guard<std::mutex> lock(mutex);
                for (std::size_t i = 0; i < report.views.size() && i < views.size(); i++)
                    report.views[i].closed = sources.at(views[i].source.name)->closed;
                continue;
            }
            const auto started = clock_ticks();
            ComPtr<ID3D11Texture2D> back;
            check(swap->GetBuffer(0, IID_PPV_ARGS(&back)), "Get swap-chain buffer");
            ComPtr<ID3D11RenderTargetView> target;
            check(device->CreateRenderTargetView(back.Get(), nullptr, &target), "Create swap-chain target");
            const float clear[4] = {0, 0, 0, 0};
            context->ClearRenderTargetView(target.Get(), clear);
            auto *output = target.Get();
            context->OMSetRenderTargets(1, &output, nullptr);
            context->OMSetBlendState(blend.Get(), nullptr, 0xffffffff);
            context->RSSetState(raster.Get());
            D3D11_VIEWPORT vp{0, 0, static_cast<float>(sw), static_cast<float>(sh), 0, 1};
            context->RSSetViewports(1, &vp);
            context->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
            context->VSSetShader(vs.Get(), nullptr, 0);
            context->PSSetShader(ps.Get(), nullptr, 0);
            auto *cb = constants.Get();
            context->VSSetConstantBuffers(0, 1, &cb);
            context->PSSetConstantBuffers(0, 1, &cb);
            std::vector<ViewMetrics> metrics;
            for (const auto &v : views) {
                auto &source = *sources.at(v.source.name);
                auto *srv = source.view();
                if (srv && v.clip.width > 0 && v.clip.height > 0 && v.full.width > 0 && v.full.height > 0) {
                    float w = v.full.width, h = v.full.height;
                    if (v.fit != "stretch") {
                        float scale =
                            v.fit == "pixel"
                                ? 1.f
                                : (v.fit == "cover" ? std::max(w / v.source.width, h / v.source.height)
                                                    : std::min(w / v.source.width, h / v.source.height));
                        w = v.source.width * scale;
                        h = v.source.height * scale;
                    }
                    float values[12] = {
                        (v.full.x + (v.full.width - w) / 2) / sw,
                        (v.full.y + (v.full.height - h) / 2) / sh,
                        w / sw,
                        h / sh,
                        0,
                        0,
                        1,
                        1,
                        v.pixel_format == "bgra8_premultiplied" || v.pixel_format == "rgb8" ? 0.f : 1.f,
                        v.smooth ? 1.f : 0.f,
                        0,
                        0};
                    context->UpdateSubresource(constants.Get(), 0, nullptr, values, 0, 0);
                    D3D11_RECT clip{
                        std::clamp(static_cast<LONG>(std::round(v.clip.x)), 0L, static_cast<LONG>(sw)),
                        std::clamp(static_cast<LONG>(std::round(v.clip.y)), 0L, static_cast<LONG>(sh)),
                        std::clamp(static_cast<LONG>(std::round(v.clip.x + v.clip.width)), 0L,
                                   static_cast<LONG>(sw)),
                        std::clamp(static_cast<LONG>(std::round(v.clip.y + v.clip.height)), 0L,
                                   static_cast<LONG>(sh))};
                    context->RSSetScissorRects(1, &clip);
                    context->PSSetShaderResources(0, 1, &srv);
                    auto *sampler = samplers[v.smooth ? 1 : 0].Get();
                    context->PSSetSamplers(0, 1, &sampler);
                    context->Draw(3, 0);
                }
                double now = clock_ticks() / clock_frequency();
                metrics.push_back({v.id, source.sequence, source.frames, source.skipped,
                                   source.sequence ? (now - source.source_seconds) * 1000 : 0,
                                   source.sequence ? (now - source.ready_seconds) * 1000 : 0,
                                   source.acquire_ms, 0, source.closed, v.gpu});
            }
            ID3D11ShaderResourceView *none = nullptr;
            context->PSSetShaderResources(0, 1, &none);
            context->OMSetRenderTargets(0, nullptr, nullptr);
            check(swap->Present(0, 0), "DXGI Present");
            presents++;
            capacity = false;
            double ms = (clock_ticks() - started) * 1000 / clock_frequency();
            for (std::size_t i = 0; i < metrics.size(); i++) {
                auto &s = *sources.at(views[i].source.name);
                auto now = clock_ticks() / clock_frequency();
                metrics[i].source_age_ms = s.sequence ? (now - s.source_seconds) * 1000 : 0;
                metrics[i].ready_age_ms = s.sequence ? (now - s.ready_seconds) * 1000 : 0;
            }
            {
                std::lock_guard<std::mutex> lock(mutex);
                report.presents = presents;
                report.last_submit_ms = ms;
                report.views = std::move(metrics);
            }
            dirty = false;
        }
        context->ClearState();
        context->Flush();
        check(visual->SetContent(nullptr), "detach DComp content");
        check(composition->Commit(), "detach DComp Commit");
    }
};
Compositor::Compositor(std::uintptr_t h, std::uint32_t l, std::int32_t hi)
    : impl_(std::make_unique<Impl>(h, l, hi)) {}
Compositor::~Compositor() { close(); }
void Compositor::configure(std::vector<NativeView> views, std::uint32_t w, std::uint32_t h) {
    if (!w || !h || w > 16384 || h > 16384 || views.size() > 4096)
        throw std::invalid_argument("invalid native compositor dimensions/view count");
    std::set<std::string> sources;
    for (const auto &v : views) {
        if (!v.source.width || !v.source.height || v.source.width > 16384 || v.source.height > 16384)
            throw std::invalid_argument("invalid native source dimensions");
        sources.insert(v.source.name);
        if (v.fit != "contain" && v.fit != "cover" && v.fit != "stretch" && v.fit != "pixel")
            throw std::invalid_argument("invalid native fit mode");
        for (const auto &r : {v.full, v.clip})
            if (!std::isfinite(r.x) || !std::isfinite(r.y) || !std::isfinite(r.width) ||
                !std::isfinite(r.height) || r.width < 0 || r.height < 0 || std::abs(r.x) > 1e7 ||
                std::abs(r.y) > 1e7 || r.width > 1e7 || r.height > 1e7)
                throw std::invalid_argument("invalid native view rectangle");
    }
    if (sources.size() > 60)
        throw std::invalid_argument("native compositor supports up to 60 distinct visible sources");
    std::unique_lock<std::mutex> lock(impl_->mutex);
    impl_->requested = std::move(views);
    impl_->width = w;
    impl_->height = h;
    if (impl_->closed)
        throw std::runtime_error("native compositor is closed");
    const auto serial = ++impl_->submitted;
    SetEvent(impl_->changed.value);
    if (!impl_->acknowledged.wait_for(lock, std::chrono::seconds(5), [&] {
            return impl_->applied >= serial || !impl_->report.error.empty() || impl_->closed;
        }))
        throw std::runtime_error("native scene handshake timed out");
    if (impl_->closed)
        throw std::runtime_error("native compositor is closed");
    if (!impl_->report.error.empty())
        throw std::runtime_error(impl_->report.error);
}
void Compositor::visible(bool show) {
    {
        std::lock_guard<std::mutex> lock(impl_->mutex);
        if (impl_->closed)
            throw std::runtime_error("native compositor is closed");
        if (impl_->show == show)
            return;
        impl_->show = show;
    }
    SetEvent(impl_->changed.value);
}
CompositorMetrics Compositor::metrics() const {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    return impl_->report;
}
void Compositor::request_close() {
    if (impl_) {
        {
            std::lock_guard<std::mutex> lock(impl_->mutex);
            impl_->closed = true;
            impl_->acknowledged.notify_all();
        }
        SetEvent(impl_->stop.value);
    }
}
void Compositor::close() {
    request_close();
    if (impl_ && impl_->worker.joinable()) {
        impl_->worker.join();
    }
}
} // namespace comfortable
