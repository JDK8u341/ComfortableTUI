#pragma once
// CEF-specific adapter above the unchanged generic Windows frame SDK.
// Borrowed CEF textures never escape the paint callback. No CPU pixel mapping.
#include "comfortable/frame_provider.hpp"
#include <d3dcompiler.h>
#include <wrl/client.h>
#include <algorithm>
#include <chrono>
#include <cstring>
#include <stdexcept>
#include <thread>

namespace browser_gpu {
using Microsoft::WRL::ComPtr;
inline void check(HRESULT hr, const char *operation) {
  if (FAILED(hr)) throw std::runtime_error(std::string(operation) + " failed (" + std::to_string(hr) + ")");
}
class Frames {
public:
  Frames(unsigned width, unsigned height, ID3D11Device *device = nullptr)
      : output(width, height, device), width_(width), height_(height) {
    check(output.device()->QueryInterface(IID_PPV_ARGS(&device_)), "D3D11 device1");
    context_ = output.context();
    // Shader bytecode is immutable and compiled once per helper, not per resize.
    static const auto bytecode = [] {
      const char *shader =
        "struct V{float4 p:SV_POSITION;float2 uv:TEXCOORD0;};"
        "V vs(uint i:SV_VertexID){V v;v.uv=float2((i<<1)&2,i&2);"
        "v.p=float4(v.uv.x*2-1,1-v.uv.y*2,0,1);return v;}"
        "Texture2D image:register(t0);SamplerState smp:register(s0);"
        "float4 ps(V v):SV_TARGET{return image.Sample(smp,v.uv);}";
      ComPtr<ID3DBlob> vs, ps, errors;
      check(D3DCompile(shader, strlen(shader), nullptr, nullptr, nullptr, "vs", "vs_4_0", 0, 0, &vs, &errors), "Compile browser vertex shader");
      check(D3DCompile(shader, strlen(shader), nullptr, nullptr, nullptr, "ps", "ps_4_0", 0, 0, &ps, &errors), "Compile browser pixel shader");
      return std::make_pair(vs, ps);
    }();
    const auto &vs = bytecode.first, &ps = bytecode.second;
    check(device_->CreateVertexShader(vs->GetBufferPointer(), vs->GetBufferSize(), nullptr, &vertex_), "Browser vertex shader");
    check(device_->CreatePixelShader(ps->GetBufferPointer(), ps->GetBufferSize(), nullptr, &pixel_), "Browser pixel shader");
    D3D11_SAMPLER_DESC sampler{};
    sampler.Filter = D3D11_FILTER_MIN_MAG_MIP_POINT;
    sampler.AddressU = sampler.AddressV = sampler.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    sampler.MaxLOD = D3D11_FLOAT32_MAX;
    check(device_->CreateSamplerState(&sampler, &sampler_), "Browser sampler");
    D3D11_BLEND_DESC blend{};
    auto &b = blend.RenderTarget[0];
    b.BlendEnable = TRUE;
    b.SrcBlend = b.SrcBlendAlpha = D3D11_BLEND_ONE;
    b.DestBlend = b.DestBlendAlpha = D3D11_BLEND_INV_SRC_ALPHA;
    b.BlendOp = b.BlendOpAlpha = D3D11_BLEND_OP_ADD;
    b.RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_ALL;
    check(device_->CreateBlendState(&blend, &blend_), "Browser premultiplied blend");
    D3D11_RASTERIZER_DESC raster{};
    raster.FillMode = D3D11_FILL_SOLID;
    raster.CullMode = D3D11_CULL_NONE;
    raster.ScissorEnable = TRUE;
    raster.DepthClipEnable = TRUE;
    check(device_->CreateRasterizerState(&raster, &raster_), "Browser rasterizer");
    D3D11_QUERY_DESC query{D3D11_QUERY_EVENT, 0};
    check(device_->CreateQuery(&query, &copied_), "Browser copy completion query");
  }
  void paint(bool popup, const CefAcceleratedPaintInfo &info, const CefRect &rect) {
    if (failed_) throw std::runtime_error("Browser GPU source failed; recreate the page");
    ComPtr<ID3D11Texture2D> borrowed;
    check(device_->OpenSharedResource1(info.shared_texture_handle, IID_PPV_ARGS(&borrowed)),
          "Open CEF shared texture on the provider adapter");
    D3D11_TEXTURE2D_DESC desc{};
    borrowed->GetDesc(&desc);
    if ((desc.Format != DXGI_FORMAT_B8G8R8A8_UNORM && desc.Format != DXGI_FORMAT_R8G8B8A8_UNORM) ||
        desc.ArraySize != 1 || desc.SampleDesc.Count != 1)
      throw std::runtime_error("Unsupported CEF GPU texture format");
    int x = 0, y = 0, w = width_, h = height_, sx = 0, sy = 0;
    const auto &visible = info.extra.visible_rect;
    if (!popup && (visible.width != int(width_) || visible.height != int(height_))) return;
    if (popup) {
      x = std::max(0, rect.x); y = std::max(0, rect.y);
      sx = x - rect.x; sy = y - rect.y;
      w = std::min<int>(width_, rect.x + rect.width) - x;
      h = std::min<int>(height_, rect.y + rect.height) - y;
      if (w <= 0 || h <= 0) { popup_.Reset(); return; }
    }
    sx += visible.x; sy += visible.y;
    if (sx < 0 || sy < 0 || desc.Width < unsigned(sx + w) || desc.Height < unsigned(sy + h)) return;
    auto &texture = popup ? popup_ : base_;
    auto &view = popup ? popup_view_ : base_view_;
    D3D11_TEXTURE2D_DESC old{};
    if (texture) texture->GetDesc(&old);
    if (!texture || old.Width != unsigned(w) || old.Height != unsigned(h) || old.Format != desc.Format) {
      texture.Reset(); view.Reset();
      desc.Width = w; desc.Height = h; desc.MipLevels = desc.ArraySize = 1;
      desc.Usage = D3D11_USAGE_DEFAULT; desc.CPUAccessFlags = desc.MiscFlags = 0;
      desc.BindFlags = D3D11_BIND_SHADER_RESOURCE;
      check(device_->CreateTexture2D(&desc, nullptr, &texture), "Browser owned texture");
      check(device_->CreateShaderResourceView(texture.Get(), nullptr, &view), "Browser owned texture view");
    }
    ID3D11ShaderResourceView *none = nullptr;
    context_->PSSetShaderResources(0, 1, &none);
    D3D11_BOX box{unsigned(sx), unsigned(sy), 0, unsigned(sx + w), unsigned(sy + h), 1};
    context_->CopySubresourceRegion(texture.Get(), 0, 0, 0, 0, borrowed.Get(), 0, &box);
    // Flush submits work; it does NOT establish completion. CEF may recycle its
    // allocation on callback return, so observe completion before returning.
    context_->End(copied_.Get());
    context_->Flush();
    const auto start = std::chrono::steady_clock::now();
    HRESULT status;
    while ((status = context_->GetData(copied_.Get(), nullptr, 0, D3D11_ASYNC_GETDATA_DONOTFLUSH)) == S_FALSE) {
      if (std::chrono::steady_clock::now() - start > std::chrono::seconds(5)) {
        // Never return borrowed storage to CEF while a GPU copy still uses it.
        // A wedged driver terminates this isolated helper; the host reports exit.
        TerminateProcess(GetCurrentProcess(), 3);
      }
      SwitchToThread();
    }
    check(status, "Complete CEF GPU copy");
    copy_wait_ms += std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
    ++accelerated_paints;
    if (popup) { popup_x_ = x; popup_y_ = y; popup_width_ = w; popup_height_ = h; ++popup_paints; }
    publish();
  }
  void popup_visible(bool show) {
    show_popup_ = show;
    if (!show) { popup_.Reset(); popup_view_.Reset(); publish(); }
  }
  void fail() { failed_ = true; }
  void publish() {
    if (!base_ || failed_) return;
    auto frame = output.begin_frame();
    if (!frame) { ++dropped_frames; return; }
    auto *target = frame.target();
    context_->OMSetRenderTargets(1, &target, nullptr);
    const float clear[4] = {0, 0, 0, 0};
    context_->ClearRenderTargetView(target, clear);
    context_->IASetInputLayout(nullptr);
    context_->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    context_->VSSetShader(vertex_.Get(), nullptr, 0);
    context_->PSSetShader(pixel_.Get(), nullptr, 0);
    auto *sampler = sampler_.Get();
    context_->PSSetSamplers(0, 1, &sampler);
    context_->RSSetState(raster_.Get());
    D3D11_RECT clip{0, 0, LONG(width_), LONG(height_)};
    context_->RSSetScissorRects(1, &clip);
    context_->OMSetBlendState(nullptr, nullptr, 0xffffffff);
    draw(base_view_.Get(), 0, 0, width_, height_);
    if (show_popup_ && popup_) {
      context_->OMSetBlendState(blend_.Get(), nullptr, 0xffffffff);
      draw(popup_view_.Get(), popup_x_, popup_y_, popup_width_, popup_height_);
    }
    ID3D11ShaderResourceView *none = nullptr;
    context_->PSSetShaderResources(0, 1, &none);
    context_->OMSetRenderTargets(0, nullptr, nullptr);
    frame.publish();
    ++published_frames;
  }
  comfortable::FrameProvider output;
  std::uint64_t accelerated_paints{}, published_frames{}, popup_paints{}, dropped_frames{};
  double copy_wait_ms{};
private:
  void draw(ID3D11ShaderResourceView *view, int x, int y, int w, int h) {
    D3D11_VIEWPORT viewport{float(x), float(y), float(w), float(h), 0, 1};
    context_->RSSetViewports(1, &viewport);
    context_->PSSetShaderResources(0, 1, &view);
    context_->Draw(3, 0);
  }
  unsigned width_, height_;
  int popup_x_{}, popup_y_{}, popup_width_{}, popup_height_{};
  bool show_popup_{}, failed_{};
  ComPtr<ID3D11Device1> device_;
  ComPtr<ID3D11DeviceContext> context_;
  ComPtr<ID3D11Texture2D> base_, popup_;
  ComPtr<ID3D11ShaderResourceView> base_view_, popup_view_;
  ComPtr<ID3D11VertexShader> vertex_;
  ComPtr<ID3D11PixelShader> pixel_;
  ComPtr<ID3D11SamplerState> sampler_;
  ComPtr<ID3D11BlendState> blend_;
  ComPtr<ID3D11RasterizerState> raster_;
  ComPtr<ID3D11Query> copied_;
};
} // namespace browser_gpu
