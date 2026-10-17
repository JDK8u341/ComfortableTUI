// Telemetry is low-frequency control metadata; all pixel generation and frame
// submission run in C++, independently of Python and its GIL.
#include <comfortable/cpu_frame.hpp>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <exception>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#ifdef _WIN32
#include <comfortable/frame_provider.hpp>
#include <wrl/client.h>
#endif

namespace py = pybind11;
class Trend {
  public:
    Trend(unsigned w, unsigned h, unsigned fps) : width_(w), height_(h), fps_(fps) {
        if (w < 2 || h < 2 || w > 4096 || h > 4096 || !fps || fps > 240)
            throw std::invalid_argument("dimensions 2..4096, fps 1..240 required");
    }
    virtual ~Trend() { close(); }
    void set_samples(const std::vector<double>& values) {
        if (values.size() > 180)
            throw std::invalid_argument("at most 180 samples");
        for (double value : values)
            if (!std::isfinite(value)) throw std::invalid_argument("samples must be finite");
        std::lock_guard<std::mutex> lock(mutex_);
        targets_ = values;
        ++revision_;
        wake_.notify_all();
    }
    void close() {
        std::lock_guard<std::mutex> closing(close_mutex_);
        { std::lock_guard<std::mutex> lock(mutex_); stopping_ = true; }
        wake_.notify_all();
        if (worker_.joinable()) worker_.join();
    }
    std::uint64_t frames() const { return frames_.load(); }
    std::string error() const { std::lock_guard<std::mutex> lock(mutex_); return error_; }
  protected:
    void start() {
        worker_ = std::thread([this] { run(); });
        std::unique_lock<std::mutex> lock(mutex_);
        wake_.wait(lock, [this] { return ready_; });
        if (failure_) {
            auto error = failure_;
            lock.unlock();
            close();
            std::rethrow_exception(error);
        }
    }
    virtual void initialize() {}
    virtual bool begin() = 0;
    virtual void rect(int x, int y, int w, int h, std::uint32_t color) = 0;
    virtual void publish() = 0;
    unsigned width_, height_;
  private:
    void run() noexcept {
        try {
            initialize();
            { std::lock_guard<std::mutex> lock(mutex_); ready_ = true; }
            wake_.notify_all();
            std::vector<double> current;
            std::uint64_t seen = 0;
            bool dirty = true;
            const auto interval = std::chrono::duration<double>(1.0 / fps_);
            while (true) {
                const auto next = std::chrono::steady_clock::now() + interval;
                std::vector<double> targets;
                {
                    std::unique_lock<std::mutex> lock(mutex_);
                    if (!dirty) wake_.wait(lock, [this, seen] { return stopping_ || revision_ != seen; });
                    if (stopping_) break;
                    targets = targets_;
                    if (seen != revision_) { seen = revision_; dirty = true; }
                }
                if (current.size() != targets.size())
                    current.resize(targets.size(), current.empty() ? 0. : current.back());
                bool moving = false;
                for (std::size_t i = 0; i < current.size(); ++i) {
                    const double delta = targets[i] - current[i];
                    if (std::abs(delta) > .04) {
                        current[i] += delta * (1 - std::exp(-20.0 / fps_));
                        moving = true;
                    } else current[i] = targets[i];
                }
                if (begin()) {
                    rect(0, 0, width_, height_, 0xff111522);
                    for (int value : {0, 50, 100}) {
                        const int y = static_cast<int>((height_ - 2) * (1 - value / 100.0));
                        rect(0, y, width_, 1, 0xff283144);
                    }
                    for (std::size_t i = 0; i < current.size(); ++i) {
                        const int x = static_cast<int>(i * (width_ - 1.) / std::max<std::size_t>(1, current.size() - 1));
                        const int y = static_cast<int>((height_ - 2) * (1 - std::clamp(current[i], 0., 100.) / 100.));
                        if (i) {
                            const int px = static_cast<int>((i - 1) * (width_ - 1.) / std::max<std::size_t>(1, current.size() - 1));
                            const int py = static_cast<int>((height_ - 2) * (1 - std::clamp(current[i - 1], 0., 100.) / 100.));
                            const int steps = std::max(1, std::max(x - px, std::abs(y - py)));
                            for (int j = 0; j <= steps; ++j)
                                rect(px + (x - px) * j / steps, py + (y - py) * j / steps, 2, 2, 0xff77e0c2);
                        } else rect(x, y, 3, 3, 0xff77e0c2);
                    }
                    publish();
                    ++frames_;
                    dirty = moving;
                }
                std::unique_lock<std::mutex> lock(mutex_);
                wake_.wait_until(lock, next, [this] { return stopping_; });
                if (stopping_) break;
            }
        } catch (...) {
            std::lock_guard<std::mutex> lock(mutex_);
            failure_ = std::current_exception();
            try { std::rethrow_exception(failure_); }
            catch (const std::exception& e) { error_ = e.what(); }
            catch (...) { error_ = "unknown native renderer failure"; }
            ready_ = true;
            wake_.notify_all();
        }
    }
    unsigned fps_;
    mutable std::mutex mutex_;
    std::mutex close_mutex_;
    std::condition_variable wake_;
    std::thread worker_;
    std::vector<double> targets_;
    std::uint64_t revision_{};
    bool ready_{}, stopping_{};
    std::exception_ptr failure_;
    std::string error_;
    std::atomic<std::uint64_t> frames_{};
};

class CpuTrend final : public Trend {
  public:
    CpuTrend(const std::string& name, unsigned w, unsigned h, unsigned stride, unsigned fps)
        : Trend(w, h, fps), channel_({name, w, h, stride, "bgra8_premultiplied"}),
          stride_(stride), pixels_(stride * h) { start(); }
    ~CpuTrend() override { close(); }
  private:
    bool begin() override { return true; }
    void rect(int x, int y, int w, int h, std::uint32_t color) override {
        const int right = std::min<int>(width_, x + w), bottom = std::min<int>(height_, y + h);
        for (int yy = std::max(0, y); yy < bottom; ++yy)
            for (int xx = std::max(0, x); xx < right; ++xx) {
                const auto offset = yy * stride_ + xx * 4;
                pixels_[offset] = color & 255;
                pixels_[offset + 1] = (color >> 8) & 255;
                pixels_[offset + 2] = (color >> 16) & 255;
                pixels_[offset + 3] = 255;
            }
    }
    void publish() override { channel_.submit(pixels_.data(), stride_); }
    comfortable::CpuFrameChannel channel_;
    unsigned stride_;
    std::vector<std::uint8_t> pixels_;
};

#ifdef _WIN32
class GpuTrend final : public Trend {
  public:
    GpuTrend(unsigned w, unsigned h, unsigned fps) : Trend(w, h, fps) { start(); }
    ~GpuTrend() override { close(); }
    std::string channel_name() const { return provider_->descriptor().name; }
  private:
    void initialize() override {
        provider_ = std::make_unique<comfortable::FrameProvider>(width_, height_);
        if (FAILED(provider_->context()->QueryInterface(IID_PPV_ARGS(&context_))))
            throw std::runtime_error("D3D11.1 context required");
    }
    bool begin() override {
        frame_ = provider_->begin_frame(1);
        return static_cast<bool>(frame_);
    }
    void rect(int x, int y, int w, int h, std::uint32_t color) override {
        const float rgba[] = {((color >> 16) & 255) / 255.f, ((color >> 8) & 255) / 255.f,
                              (color & 255) / 255.f, 1.f};
        const D3D11_RECT r{x, y, x + w, y + h};
        context_->ClearView(frame_.target(), rgba, &r, 1);
    }
    void publish() override { frame_.publish(comfortable::clock_ticks()); }
    std::unique_ptr<comfortable::FrameProvider> provider_;
    Microsoft::WRL::ComPtr<ID3D11DeviceContext1> context_;
    comfortable::WriteFrame frame_;
};
#endif

PYBIND11_MODULE(_ctui_top_native, m) {
    py::class_<Trend>(m, "Trend")
        .def("set_samples", &Trend::set_samples)
        .def("close", &Trend::close, py::call_guard<py::gil_scoped_release>())
        .def_property_readonly("frames", &Trend::frames)
        .def_property_readonly("error", &Trend::error);
    py::class_<CpuTrend, Trend>(m, "CpuTrend")
        .def(py::init<const std::string&, unsigned, unsigned, unsigned, unsigned>(),
             py::call_guard<py::gil_scoped_release>());
#ifdef _WIN32
    py::class_<GpuTrend, Trend>(m, "GpuTrend")
        .def(py::init<unsigned, unsigned, unsigned>(), py::call_guard<py::gil_scoped_release>())
        .def_property_readonly("channel_name", &GpuTrend::channel_name);
#endif
}
