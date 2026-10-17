// Compatibility benchmark source for the existing CTUIFR01 CPU buffer ABI.
// The new SDK API is FrameProvider; this tool makes old/new presenters comparable
// with exactly the same native CPU producer, cadence, pixels and timestamps.
#include "internal.hpp"
#include <algorithm>
#include <cmath>
#include <iostream>
using namespace comfortable;
using namespace comfortable::detail;
struct Header {
    char magic[8];
    std::int32_t published, reading;
    std::uint64_t sequence;
    double timestamp;
};
int main(int argc, char **argv) {
    try {
        if (argc != 7)
            throw std::invalid_argument("usage: ctui_cpu_producer name width height stride fps seconds");
        std::string name = argv[1];
        UINT w = std::stoul(argv[2]), h = std::stoul(argv[3]), stride = std::stoul(argv[4]),
             fps = std::stoul(argv[5]);
        double seconds = std::stod(argv[6]);
        if (!w || !h || !fps || fps > 1000 || stride < w * 4ull || stride % 4 ||
            64ull + 3ull * stride * h > 512ull * 1024 * 1024)
            throw std::invalid_argument("invalid CPU source dimensions");
        auto tag = wide(name);
        Handle mapping(OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, tag.c_str()));
        Mapping memory;
        memory.map(mapping.value, 64ull + 3ull * stride * h);
        auto *header = static_cast<Header *>(memory.address);
        if (std::string(header->magic, 8) != "CTUIFR01")
            throw std::runtime_error("CPU buffer ABI mismatch");
        tag = wide("Local\\" + name + "_meta");
        Handle meta(OpenMutexW(SYNCHRONIZE | MUTEX_MODIFY_STATE, FALSE, tag.c_str()));
        tag = wide("Local\\" + name + "_writer");
        Handle writer(OpenMutexW(SYNCHRONIZE | MUTEX_MODIFY_STATE, FALSE, tag.c_str()));
        tag = wide("Local\\" + name + "_ready");
        Handle ready(CreateEventW(nullptr, FALSE, FALSE, tag.c_str()));
        HANDLE timer = CreateWaitableTimerExW(nullptr, nullptr, 2, TIMER_ALL_ACCESS);
        if (!timer)
            timer = CreateWaitableTimerExW(nullptr, nullptr, 0, TIMER_ALL_ACCESS);
        Handle clock(timer);
        std::cout << "CTUI_CPU_READY" << std::endl;
        auto start = clock_ticks(), deadline = start;
        auto frequency = clock_frequency();
        while (!seconds || (clock_ticks() - start) / frequency < seconds) {
            auto stamp = clock_ticks();
            double t = (stamp - start) / frequency;
            unsigned r = static_cast<unsigned>(128 + 100 * std::sin(t)),
                     g = static_cast<unsigned>(128 + 100 * std::sin(t + 2)),
                     b = static_cast<unsigned>(128 + 100 * std::sin(t + 4));
            unsigned color = (255u << 24) | (r << 16) | (g << 8) | b;
            {
                Lock write(writer.value, 1);
                if (write) {
                    int slot = -1;
                    {
                        Lock lock(meta.value, 10);
                        if (lock)
                            for (int i = 0; i < 3; i++)
                                if (i != header->published && i != header->reading) {
                                    slot = i;
                                    break;
                                }
                    }
                    if (slot >= 0) {
                        auto *pixels = static_cast<unsigned char *>(memory.address) + 64 +
                                       static_cast<std::size_t>(slot) * stride * h;
                        std::fill_n(reinterpret_cast<unsigned *>(pixels),
                                    static_cast<std::size_t>(stride) * h / 4, color);
                        {
                            Lock lock(meta.value, 10);
                            if (lock) {
                                header->published = slot;
                                header->sequence++;
                                header->timestamp = stamp / frequency;
                                SetEvent(ready.value);
                            }
                        }
                    }
                }
            }
            deadline += static_cast<std::int64_t>(frequency / fps);
            auto wait = deadline - clock_ticks();
            if (wait > 0) {
                LARGE_INTEGER due{};
                due.QuadPart = -static_cast<LONGLONG>(wait * 10000000 / frequency);
                SetWaitableTimer(clock.value, &due, 0, nullptr, nullptr, FALSE);
                WaitForSingleObject(clock.value, INFINITE);
            } else if (wait < -frequency / fps)
                deadline = clock_ticks();
        }
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << std::endl;
        return 1;
    }
}
