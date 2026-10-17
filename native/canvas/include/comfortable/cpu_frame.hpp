#pragma once
// Generic CTUIFR01 transport. No browser, application profile, Qt or Python dependency.
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>

namespace comfortable {
struct CpuFrameInfo {
    std::string name;
    std::uint32_t width{}, height{}, stride{};
    std::string pixel_format{"bgra8_premultiplied"};
};
class CpuReadFrame {
  public:
    CpuReadFrame();
    ~CpuReadFrame();
    CpuReadFrame(CpuReadFrame &&) noexcept;
    CpuReadFrame &operator=(CpuReadFrame &&) noexcept;
    explicit operator bool() const;
    const std::uint8_t *pixels() const;
    std::uint64_t sequence() const;
    double timestamp() const;

  private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
    friend class CpuFrameChannel;
};
class CpuFrameChannel {
  public:
    // Attach a buffer created by the existing FrameBuffer allocator.
    explicit CpuFrameChannel(const CpuFrameInfo &);
    ~CpuFrameChannel();
    CpuFrameChannel(const CpuFrameChannel &) = delete;
    const CpuFrameInfo &info() const;
    std::uint64_t sequence() const;
    CpuReadFrame read();
    void submit(const void *pixels, std::size_t stride, double timestamp = 0);

  private:
    struct Impl;
    std::shared_ptr<Impl> impl_;
    friend class CpuReadFrame;
};
double monotonic_seconds();
} // namespace comfortable
