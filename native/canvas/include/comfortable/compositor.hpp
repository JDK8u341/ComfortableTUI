#pragma once
#include "frame_provider.hpp"

namespace comfortable {
struct PixelBox {
    float x{}, y{}, width{}, height{};
};
// Optional native image service. Invoked only on the compositor's render thread,
// after the source's single consumer has acquired its frame. Returned SRV stays
// owned by the processor until its next call; never calls Python or publishes.
class NativeImageProcessor {
  public:
    virtual ~NativeImageProcessor() = default;
    virtual ID3D11ShaderResourceView *process(ID3D11Device *, ID3D11DeviceContext *,
        ID3D11ShaderResourceView *, std::uint32_t width, std::uint32_t height,
        const PixelBox &region, bool straight_alpha) = 0;
    virtual std::uint64_t passes() const = 0;
    virtual void release_device_resources() = 0;
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
    std::shared_ptr<NativeImageProcessor> content_effect;
    std::shared_ptr<NativeImageProcessor> background_effect;
    // Optional source subrectangle; zero width means the complete source.
    PixelBox sample;
};
struct ViewMetrics {
    std::uint64_t id{}, sequence{}, frames{}, skipped{};
    double source_age_ms{}, ready_age_ms{}, acquire_ms{}, submit_ms{};
    bool closed{}, gpu{};
};
struct CompositorMetrics {
    std::uint64_t presents{};
    std::uint64_t effect_passes{};
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
    void configure(std::vector<NativeView> views, std::uint32_t width, std::uint32_t height,
                   std::shared_ptr<NativeImageProcessor> effect = nullptr);
    void visible(bool enabled);
    void invalidate();
    CompositorMetrics metrics() const;
    // Signal before joining a batch of compositors so their workers can stop
    // concurrently. close() still joins and releases consumer ownership.
    void request_close();
    void close();

  private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace comfortable
