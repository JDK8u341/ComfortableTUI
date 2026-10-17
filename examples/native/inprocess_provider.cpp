// Example adapter: embed the SDK in a Python process without per-frame Python calls.
#include <atomic>
#include <chrono>
#include <cmath>
#include <comfortable/frame_provider.hpp>
#include <condition_variable>
#include <exception>
#include <mutex>
#include <pybind11/pybind11.h>
#include <stdexcept>
#include <string>
#include <thread>
#include <wrl/client.h>

namespace py = pybind11;
using Microsoft::WRL::ComPtr;

class InprocessProvider {
  public:
    InprocessProvider(unsigned width, unsigned height, unsigned fps) {
        if (!fps || fps > 1000)
            throw std::invalid_argument("fps must be 1..1000");
        worker_ = std::thread([this, width, height, fps] { run(width, height, fps); });
        std::unique_lock<std::mutex> lock(mutex_);
        wake_.wait(lock, [this] { return initialized_; });
        if (failure_) {
            const auto error = failure_;
            lock.unlock();
            worker_.join();
            std::rethrow_exception(error);
        }
    }
    ~InprocessProvider() { close(); }
    InprocessProvider(const InprocessProvider &) = delete;
    comfortable::FrameDescriptor descriptor() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return descriptor_;
    }
    std::uint64_t frames() const { return frames_.load(); }
    std::string error() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return error_;
    }
    void close() {
        // Also serialize concurrent close() calls made with the GIL released.
        std::lock_guard<std::mutex> closing(close_mutex_);
        {
            std::lock_guard<std::mutex> lock(mutex_);
            stopping_ = true;
        }
        wake_.notify_all();
        if (worker_.joinable())
            worker_.join();
    }

  private:
    void run(unsigned width, unsigned height, unsigned fps) noexcept {
        try {
            // Device, context and all rendering stay on this one C++ thread.
            comfortable::FrameProvider provider(width, height);
            ComPtr<ID3D11DeviceContext1> context;
            if (FAILED(provider.context()->QueryInterface(IID_PPV_ARGS(&context))))
                throw std::runtime_error("D3D11.1 context required");
            {
                std::lock_guard<std::mutex> lock(mutex_);
                descriptor_ = provider.descriptor();
                initialized_ = true;
            }
            wake_.notify_all();
            using Clock = std::chrono::steady_clock;
            const auto interval =
                std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(1.0 / fps));
            auto deadline = Clock::now();
            const auto start = comfortable::clock_ticks();
            const auto frequency = comfortable::clock_frequency();
            for (;;) {
                {
                    std::lock_guard<std::mutex> lock(mutex_);
                    if (stopping_)
                        break;
                }
                const auto stamp = comfortable::clock_ticks();
                const double t = (stamp - start) / frequency;
                auto frame = provider.begin_frame(1);
                if (frame) {
                    const float background[] = {0.025f, 0.04f, 0.075f, 1.f};
                    context->ClearRenderTargetView(frame.target(), background);
                    const LONG size = static_cast<LONG>((width < height ? width : height) / 4 + 1);
                    const LONG x = static_cast<LONG>((width - size) * (0.5 + 0.5 * std::sin(t * 1.4)));
                    const LONG y = static_cast<LONG>((height - size) * (0.5 + 0.5 * std::cos(t * 1.1)));
                    const D3D11_RECT rect{x, y, x + size, y + size};
                    const float accent[] = {1.f, 0.2f, 0.55f, 1.f};
                    context->ClearView(frame.target(), accent, &rect, 1);
                    frame.publish(stamp);
                    ++frames_;
                }
                deadline += interval;
                if (deadline < Clock::now() - interval)
                    deadline = Clock::now();
                std::unique_lock<std::mutex> lock(mutex_);
                if (wake_.wait_until(lock, deadline, [this] { return stopping_; }))
                    break;
            }
            // Destroy shared resources only after the caller has closed its App.
        } catch (...) {
            std::lock_guard<std::mutex> lock(mutex_);
            failure_ = std::current_exception();
            try {
                std::rethrow_exception(failure_);
            } catch (const std::exception &e) {
                error_ = e.what();
            } catch (...) {
                error_ = "unknown C++ renderer failure";
            }
            initialized_ = true;
            wake_.notify_all();
        }
    }
    mutable std::mutex mutex_;
    std::mutex close_mutex_;
    std::condition_variable wake_;
    std::thread worker_;
    bool initialized_{}, stopping_{};
    std::exception_ptr failure_;
    comfortable::FrameDescriptor descriptor_;
    std::string error_;
    std::atomic<std::uint64_t> frames_{};
};

PYBIND11_MODULE(_ctui_inprocess_example, m) {
    py::class_<InprocessProvider>(m, "InprocessProvider")
        .def(py::init<unsigned, unsigned, unsigned>(), py::arg("width") = 1280, py::arg("height") = 720,
             py::arg("fps") = 120, py::call_guard<py::gil_scoped_release>())
        .def_property_readonly("channel_name", [](const InprocessProvider &p) { return p.descriptor().name; })
        .def_property_readonly("frames", &InprocessProvider::frames)
        .def_property_readonly("error", &InprocessProvider::error)
        .def("close", &InprocessProvider::close, py::call_guard<py::gil_scoped_release>());
}
