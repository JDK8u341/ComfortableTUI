#pragma once
#include "frame_provider.hpp"

namespace comfortable {
struct PixelBox {
    float x{}, y{}, width{}, height{};
};
struct NativeView {
    std::uint64_t id{};
    FrameDescriptor source;
    bool gpu{true};
    std::uint32_t stride{};
    std::string pixel_format{"bgra8_premultiplied"};
    PixelBox full, clip;
    std::string fit{"contain"};
    bool smooth{true};
};
struct ViewMetrics {
    std::uint64_t id{}, sequence{}, frames{}, skipped{};
    double source_age_ms{}, ready_age_ms{}, acquire_ms{}, submit_ms{};
    bool closed{}, gpu{};
};
struct CompositorMetrics {
    std::uint64_t presents{};
    double last_submit_ms{};
    std::vector<ViewMetrics> views;
    std::string error;
};
class Compositor {
  public:
    // HWND remains owned by the UI thread. Rendering and frame transport run
    // entirely in a native worker; configure() is a control-plane snapshot.
    Compositor(std::uintptr_t hwnd, std::uint32_t adapter_low = 0, std::int32_t adapter_high = 0);
    ~Compositor();
    Compositor(const Compositor &) = delete;
    void configure(std::vector<NativeView> views, std::uint32_t width, std::uint32_t height);
    void visible(bool enabled);
    CompositorMetrics metrics() const;
    void close();

  private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace comfortable
