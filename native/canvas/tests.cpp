#include "internal.hpp"
#include <iostream>
#include <cstring>
using namespace comfortable;
using namespace comfortable::detail;
static void require(bool condition, const char *message) {
    if (!condition)
        throw std::runtime_error(message);
}
static unsigned pixel(ID3D11Device *device, ID3D11Texture2D *texture) {
    D3D11_TEXTURE2D_DESC d{};
    texture->GetDesc(&d);
    d.Usage = D3D11_USAGE_STAGING;
    d.BindFlags = 0;
    d.MiscFlags = 0;
    d.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    ComPtr<ID3D11Texture2D> staging;
    check(device->CreateTexture2D(&d, nullptr, &staging), "test staging texture");
    ComPtr<ID3D11DeviceContext> context;
    device->GetImmediateContext(&context);
    context->CopyResource(staging.Get(), texture);
    D3D11_MAPPED_SUBRESOURCE map{};
    check(context->Map(staging.Get(), 0, D3D11_MAP_READ, 0, &map), "test GPU readback");
    unsigned value{};
    std::memcpy(&value, map.pData, 4);
    context->Unmap(staging.Get(), 0);
    return value;
}
static void fill(FrameProvider &p, float r, float g, float b) {
    auto f = p.begin_frame(100);
    require(bool(f), "could not acquire producer slot");
    const float c[] = {r, g, b, 1};
    p.context()->ClearRenderTargetView(f.target(), c);
    f.publish();
}
int main(int argc, char **argv) {
    try {
        if (argc == 3 && std::string(argv[1]) == "--child") {
            auto desc = describe(argv[2]);
            auto device = create_device(desc.adapter_low, desc.adapter_high);
            FrameConsumer consumer(desc, device.Get());
            auto frame = consumer.acquire_latest(0, 100);
            require(bool(frame), "child did not acquire a shared frame");
            require(pixel(device.Get(), frame.texture()) == 0xffff0000, "cross-process GPU pixel mismatch");
            return 0;
        }
        FrameProvider provider(64, 32);
        auto d = provider.descriptor();
        auto device = create_device(d.adapter_low, d.adapter_high);
        {
            auto aborted = provider.begin_frame();
            require(bool(aborted), "empty initial write");
        }
        fill(provider, 1, 0, 0);
        wchar_t executable[MAX_PATH]{};
        GetModuleFileNameW(nullptr, executable, MAX_PATH);
        auto command = std::wstring(L"\"") + executable + L"\" --child " + wide(d.name);
        STARTUPINFOW startup{};
        startup.cb = sizeof(startup);
        PROCESS_INFORMATION process{};
        require(CreateProcessW(nullptr, command.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr,
                               nullptr, &startup, &process) != FALSE,
                "CreateProcess test child");
        Handle child(process.hProcess), thread(process.hThread);
        require(WaitForSingleObject(child.value, 10000) == WAIT_OBJECT_0, "cross-process child timed out");
        DWORD exit{};
        GetExitCodeProcess(child.value, &exit);
        require(exit == 0, "cross-process consumer failed");
        {
            FrameConsumer consumer(d, device.Get());
            bool rejected = false;
            try {
                FrameConsumer duplicate(d, device.Get());
            } catch (const std::exception &) {
                rejected = true;
            }
            require(rejected, "second consumer was accepted");
            auto pinned = consumer.acquire_latest(0, 100);
            require(bool(pinned), "missing first frame");
            auto first = pinned.sequence();
            for (int i = 0; i < 30; i++)
                fill(provider, 0, 1, 0);
            require(pixel(device.Get(), pinned.texture()) == 0xffff0000,
                    "producer overwrote pinned GPU slot");
            auto newest = consumer.acquire_latest(first, 100);
            require(newest.sequence() == first + 30, "did not select newest completed frame");
            require(pixel(device.Get(), newest.texture()) == 0xff00ff00, "newest GPU frame is corrupt");
            pinned = ReadFrame();
            newest = ReadFrame();
            fill(provider, 0, 0, 1);
            require(!consumer.acquire_latest(first + 1000), "after_sequence was ignored");
        }
        {
            FrameConsumer reopened(d, device.Get());
            auto f = reopened.acquire_latest(0, 100);
            require(bool(f), "consumer could not reattach");
        }
        auto wrong = d;
        wrong.adapter_low ^= 1;
        bool rejected = false;
        try {
            FrameConsumer mismatch(wrong, device.Get());
        } catch (const std::exception &) {
            rejected = true;
        }
        require(rejected, "mismatched adapter accepted");
        rejected = false;
        try {
            describe("invalid");
        } catch (const std::exception &) {
            rejected = true;
        }
        require(rejected, "invalid descriptor accepted");
        std::cout << "{\"result\":\"passed\",\"checks\":[\"cross_process_gpu_pixels\",\"abort_write\","
                     "\"pinned_slot\",\"latest_mailbox\",\"single_consumer\",\"reattach\",\"adapter_"
                     "mismatch\",\"descriptor_validation\"]}"
                  << std::endl;
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << std::endl;
        return 1;
    }
}
