#include "comfortable/cpu_frame.hpp"
#include <algorithm>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <thread>
#ifdef _WIN32
#include <windows.h>
#else
#include <fcntl.h>
#include <sys/file.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace comfortable {
namespace {
struct Header {
    char magic[8];
    std::int32_t published, reading;
    std::uint64_t sequence;
    double timestamp;
};
static_assert(sizeof(Header) == 32);
constexpr std::size_t offset = 64;
class NamedMutex {
  public:
    explicit NamedMutex(const std::string &name) {
#ifdef _WIN32
        handle_ = CreateMutexA(nullptr, FALSE, ("Local\\" + name).c_str());
        if (!handle_)
            throw std::runtime_error("Cannot open frame mutex");
#else
        const auto path = std::filesystem::temp_directory_path() / (name + ".lock");
        handle_ = open(path.c_str(), O_RDWR | O_CREAT | O_CLOEXEC, 0600);
        if (handle_ < 0)
            throw std::runtime_error("Cannot open frame lock file");
#endif
    }
    ~NamedMutex() {
#ifdef _WIN32
        CloseHandle(handle_);
#else
        close(handle_);
#endif
    }
    void lock() {
        local_.lock();
#ifdef _WIN32
        const auto result = WaitForSingleObject(handle_, 2000);
        if (result == WAIT_OBJECT_0 || result == WAIT_ABANDONED)
            return;
#else
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        do {
            if (flock(handle_, LOCK_EX | LOCK_NB) == 0)
                return;
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        } while (std::chrono::steady_clock::now() < deadline);
#endif
        local_.unlock();
        throw std::runtime_error("Frame mutex timed out");
    }
    void unlock() {
#ifdef _WIN32
        ReleaseMutex(handle_);
#else
        flock(handle_, LOCK_UN);
#endif
        local_.unlock();
    }

  private:
    std::mutex local_;
#ifdef _WIN32
    HANDLE handle_{};
#else
    int handle_{-1};
#endif
};
void validate(const CpuFrameInfo &d) {
    if (d.name.size() < 6 || d.name.size() > 80 || d.name.rfind("ctui_", 0) != 0 ||
        d.name.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_") !=
            std::string::npos)
        throw std::invalid_argument("Invalid frame name");
    const auto bpp = d.pixel_format == "rgb8" ? 3u : 4u;
    if ((d.pixel_format != "rgb8" && d.pixel_format != "rgba8" && d.pixel_format != "bgra8" &&
         d.pixel_format != "bgra8_premultiplied") ||
        !d.width || !d.height || d.width > 16384 || d.height > 16384 ||
        d.stride < std::uint64_t(d.width) * bpp || d.stride % 4 ||
        std::uint64_t(d.stride) * d.height * 3 + offset > 512ull * 1024 * 1024)
        throw std::invalid_argument("Invalid frame dimensions/format");
}
} // namespace

double monotonic_seconds() {
#ifdef _WIN32
    LARGE_INTEGER ticks, frequency;
    QueryPerformanceCounter(&ticks);
    QueryPerformanceFrequency(&frequency);
    return static_cast<double>(ticks.QuadPart) / frequency.QuadPart;
#else
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
#endif
}
struct CpuFrameChannel::Impl {
    CpuFrameInfo descriptor;
    NamedMutex meta, reader, writer;
    void *mapping{};
    std::size_t size{}, frame_bytes{};
#ifdef _WIN32
    HANDLE handle{};
#else
    int handle{-1};
#endif
    explicit Impl(const CpuFrameInfo &d)
        : descriptor(d), meta(d.name + "_meta"), reader(d.name + "_reader"), writer(d.name + "_writer") {
        frame_bytes = std::size_t(d.stride) * d.height;
        size = offset + frame_bytes * 3;
#ifdef _WIN32
        handle = OpenFileMappingA(FILE_MAP_ALL_ACCESS, FALSE, d.name.c_str());
        if (handle)
            mapping = MapViewOfFile(handle, FILE_MAP_ALL_ACCESS, 0, 0, size);
        if (!mapping) {
            if (handle)
                CloseHandle(handle);
            throw std::runtime_error("Cannot attach frame mapping");
        }
#else
        handle = shm_open(("/" + d.name).c_str(), O_RDWR | O_CLOEXEC, 0600);
        struct stat s{};
        if (handle < 0 || fstat(handle, &s) || static_cast<std::size_t>(s.st_size) < size) {
            if (handle >= 0)
                close(handle);
            throw std::runtime_error("Frame mapping is missing or too small");
        }
        mapping = mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, handle, 0);
        if (mapping == MAP_FAILED) {
            mapping = nullptr;
            close(handle);
            throw std::runtime_error("mmap failed");
        }
#endif
    }
    ~Impl() {
#ifdef _WIN32
        UnmapViewOfFile(mapping);
        CloseHandle(handle);
#else
        munmap(mapping, size);
        close(handle);
#endif
    }
    Header *header() const {
        auto *h = static_cast<Header *>(mapping);
        if (std::memcmp(h->magic, "CTUIFR01", 8) || h->published < -1 || h->published > 2 ||
            h->reading < -1 || h->reading > 2)
            throw std::runtime_error("Invalid frame header");
        return h;
    }
    std::uint8_t *pixels(int slot) {
        return static_cast<std::uint8_t *>(mapping) + offset + frame_bytes * slot;
    }
};
struct CpuReadFrame::Impl {
    std::shared_ptr<CpuFrameChannel::Impl> channel;
    std::unique_lock<NamedMutex> reader;
    int slot{-1};
    std::uint64_t sequence{};
    double timestamp{};
    explicit Impl(std::shared_ptr<CpuFrameChannel::Impl> c)
        : channel(std::move(c)), reader(channel->reader) {}
    ~Impl() {
        if (slot >= 0) {
            try {
                std::lock_guard<NamedMutex> lock(channel->meta);
                channel->header()->reading = -1;
            } catch (...) {
            }
        }
    }
};
CpuFrameChannel::CpuFrameChannel(const CpuFrameInfo &d) {
    validate(d);
    impl_ = std::make_shared<Impl>(d);
    std::lock_guard<NamedMutex> lock(impl_->meta);
    impl_->header();
}
CpuFrameChannel::~CpuFrameChannel() = default;
const CpuFrameInfo &CpuFrameChannel::info() const { return impl_->descriptor; }
std::uint64_t CpuFrameChannel::sequence() const {
    std::lock_guard<NamedMutex> lock(impl_->meta);
    return impl_->header()->sequence;
}
CpuReadFrame CpuFrameChannel::read() {
    CpuReadFrame result;
    auto lease = std::make_unique<CpuReadFrame::Impl>(impl_);
    std::lock_guard<NamedMutex> lock(impl_->meta);
    auto *h = impl_->header();
    lease->slot = h->published;
    lease->sequence = h->sequence;
    lease->timestamp = h->timestamp;
    if (lease->slot >= 0)
        h->reading = lease->slot;
    result.impl_ = std::move(lease);
    return result;
}
void CpuFrameChannel::submit(const void *pixels, std::size_t stride, double timestamp) {
    const auto &d = info();
    if (!pixels || stride < d.stride)
        throw std::invalid_argument("Invalid frame source stride");
    std::lock_guard<NamedMutex> write(impl_->writer);
    int slot = 0;
    {
        std::lock_guard<NamedMutex> lock(impl_->meta);
        const auto *h = impl_->header();
        while (slot == h->published || slot == h->reading)
            ++slot;
    }
    for (unsigned y = 0; y < d.height; ++y)
        std::memcpy(impl_->pixels(slot) + y * d.stride,
                    static_cast<const std::uint8_t *>(pixels) + y * stride, d.stride);
    {
        std::lock_guard<NamedMutex> lock(impl_->meta);
        auto *h = impl_->header();
        h->published = slot;
        ++h->sequence;
        h->timestamp = timestamp ? timestamp : monotonic_seconds();
    }
}
CpuReadFrame::CpuReadFrame() = default;
CpuReadFrame::~CpuReadFrame() = default;
CpuReadFrame::CpuReadFrame(CpuReadFrame &&) noexcept = default;
CpuReadFrame &CpuReadFrame::operator=(CpuReadFrame &&) noexcept = default;
CpuReadFrame::operator bool() const { return impl_ && impl_->slot >= 0; }
const std::uint8_t *CpuReadFrame::pixels() const {
    return *this ? impl_->channel->pixels(impl_->slot) : nullptr;
}
std::uint64_t CpuReadFrame::sequence() const { return impl_ ? impl_->sequence : 0; }
double CpuReadFrame::timestamp() const { return impl_ ? impl_->timestamp : 0; }
} // namespace comfortable
