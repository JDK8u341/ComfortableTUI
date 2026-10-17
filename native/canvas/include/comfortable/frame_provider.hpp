#pragma once
// Windows SDK for local, single-producer/single-consumer GPU frame streams.
// No Python or Qt headers, callbacks, pixel serialization or CPU readback.
#include <d3d11_1.h>
#include <dxgi1_2.h>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace comfortable {
struct FrameDescriptor {
    std::string name;
    std::uint32_t width{}, height{};
    std::uint32_t adapter_low{};
    std::int32_t adapter_high{};
    std::uint32_t version{1};
};
struct AdapterInfo {
    std::string name;
    std::uint32_t low{};
    std::int32_t high{};
    bool software{};
};
std::vector<AdapterInfo> adapters();
std::int64_t clock_ticks();
double clock_frequency();
FrameDescriptor describe(const std::string &name);

class WriteFrame {
  public:
    WriteFrame();
    ~WriteFrame();
    WriteFrame(WriteFrame &&) noexcept;
    WriteFrame &operator=(WriteFrame &&) noexcept;
    WriteFrame(const WriteFrame &) = delete;
    explicit operator bool() const;
    ID3D11Texture2D *texture() const;
    ID3D11RenderTargetView *target() const;
    // Publish only after all rendering commands have been issued on the
    // provider's immediate context. source_ticks uses QueryPerformanceCounter.
    std::uint64_t publish(std::int64_t source_ticks = 0);

  private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
    friend class FrameProvider;
};

class FrameProvider {
  public:
    // Optional existing device must support D3D11.1 and BGRA. All producer
    // context access must be serialized by the application through WriteFrame.
    FrameProvider(std::uint32_t width, std::uint32_t height, ID3D11Device *device = nullptr);
    ~FrameProvider();
    FrameProvider(const FrameProvider &) = delete;
    const FrameDescriptor &descriptor() const;
    ID3D11Device *device() const;
    ID3D11DeviceContext *context() const;
    WriteFrame begin_frame(std::uint32_t timeout_ms = 0);
    // Native CPU sources may upload BGRA premultiplied rows once; transfer to
    // the display process remains GPU shared textures.
    bool submit_bgra(const void *data, std::size_t stride, std::int64_t source_ticks = 0);

  private:
    struct Impl;
    std::shared_ptr<Impl> impl_;
    friend class WriteFrame;
};

class ReadFrame {
  public:
    ReadFrame();
    ~ReadFrame();
    ReadFrame(ReadFrame &&) noexcept;
    ReadFrame &operator=(ReadFrame &&) noexcept;
    ReadFrame(const ReadFrame &) = delete;
    explicit operator bool() const;
    ID3D11Texture2D *texture() const;
    ID3D11ShaderResourceView *view() const;
    std::uint64_t sequence() const;
    std::int64_t source_ticks() const;
    std::int64_t published_ticks() const;

  private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
    friend class FrameConsumer;
};

class FrameConsumer {
  public:
    // Construct/use/destroy on the same thread. One active consumer per channel;
    // one consumer can display the same source in arbitrarily many views.
    FrameConsumer(const FrameDescriptor &, ID3D11Device *device);
    ~FrameConsumer();
    FrameConsumer(const FrameConsumer &) = delete;
    HANDLE ready_event() const;
    bool closed() const;
    bool pending(std::uint64_t after_sequence) const;
    ReadFrame acquire_latest(std::uint64_t after_sequence = 0, std::uint32_t timeout_ms = 1);

  private:
    struct Impl;
    std::shared_ptr<Impl> impl_;
    friend class ReadFrame;
};
} // namespace comfortable
