#include "comfortable/frame_provider.hpp"
#include <windows.h>
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <string>

int main(int argc, char **argv) {
    try {
        unsigned width = 640, height = 360, fps = 120;
        double seconds = 0;
        std::string pattern = "motion";
        for (int i = 1; i < argc; i++) {
            std::string key = argv[i];
            if (i + 1 >= argc)
                throw std::invalid_argument("missing option value");
            auto value = argv[++i];
            if (key == "--width")
                width = std::stoul(value);
            else if (key == "--height")
                height = std::stoul(value);
            else if (key == "--fps")
                fps = std::stoul(value);
            else if (key == "--seconds")
                seconds = std::stod(value);
            else if (key == "--pattern")
                pattern = value;
            else
                throw std::invalid_argument("unknown producer option");
        }
        if (!fps || fps > 1000 || seconds < 0)
            throw std::invalid_argument("fps must be 1..1000 and seconds >=0");
        comfortable::FrameProvider provider(width, height);
        const auto &d = provider.descriptor();
        std::cout << "CTUI_GPU_V1 " << d.name << ' ' << d.width << ' ' << d.height << ' ' << d.adapter_low
                  << ' ' << d.adapter_high << std::endl;
        HANDLE timer = CreateWaitableTimerExW(nullptr, nullptr, 2, TIMER_ALL_ACCESS);
        if (!timer)
            timer = CreateWaitableTimerExW(nullptr, nullptr, 0, TIMER_ALL_ACCESS);
        if (!timer)
            throw std::runtime_error("CreateWaitableTimerEx failed");
        const auto start = comfortable::clock_ticks();
        auto deadline = start;
        const double frequency = comfortable::clock_frequency();
        while (!seconds || (comfortable::clock_ticks() - start) / frequency < seconds) {
            auto stamp = comfortable::clock_ticks();
            double t = (stamp - start) / frequency;
            auto frame = provider.begin_frame(1);
            if (frame) {
                float color[4] = {static_cast<float>(.5 + .4 * std::sin(t * 1.3)),
                                  static_cast<float>(.5 + .4 * std::sin(t * 1.7 + 2)),
                                  static_cast<float>(.5 + .4 * std::sin(t * .9 + 4)), 1};
                provider.context()->ClearRenderTargetView(frame.target(), color);
                if (pattern == "test") {
                    ID3D11DeviceContext1 *context{};
                    if (FAILED(provider.context()->QueryInterface(IID_PPV_ARGS(&context))))
                        throw std::runtime_error("ID3D11DeviceContext1 required for test pattern");
                    const float colors[4][4] = {{1, 0, 0, 1}, {0, 1, 0, 1}, {0, 0, 1, 1}, {1, 1, 0, 1}};
                    for (unsigned i = 0; i < 4; i++) {
                        D3D11_RECT r{static_cast<LONG>((i % 2) * width / 2),
                                     static_cast<LONG>((i / 2) * height / 2),
                                     static_cast<LONG>((i % 2 + 1) * width / 2),
                                     static_cast<LONG>((i / 2 + 1) * height / 2)};
                        context->ClearView(frame.target(), colors[i], &r, 1);
                    }
                    context->Release();
                }
                frame.publish(stamp);
            }
            deadline += static_cast<std::int64_t>(frequency / fps);
            auto remaining = deadline - comfortable::clock_ticks();
            if (remaining > 0) {
                LARGE_INTEGER due{};
                due.QuadPart = -static_cast<LONGLONG>(remaining * 10000000 / frequency);
                SetWaitableTimer(timer, &due, 0, nullptr, nullptr, FALSE);
                WaitForSingleObject(timer, INFINITE);
            } else if (remaining < -frequency / fps)
                deadline = comfortable::clock_ticks();
        }
        CloseHandle(timer);
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << std::endl;
        return 1;
    }
}
