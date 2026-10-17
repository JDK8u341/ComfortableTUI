#pragma once
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <wrl/client.h>
#include <d3d11_1.h>
#include <dxgi1_3.h>
#include <stdexcept>
#include <sstream>
#include <string>
#include <utility>
#include "comfortable/frame_provider.hpp"

namespace comfortable::detail {
using Microsoft::WRL::ComPtr;
inline void check(HRESULT hr, const char *what) {
    if (FAILED(hr)) {
        std::ostringstream out;
        out << what << " (HRESULT 0x" << std::hex << static_cast<unsigned long>(hr) << ")";
        throw std::runtime_error(out.str());
    }
}
struct Handle {
    HANDLE value{};
    Handle() = default;
    explicit Handle(HANDLE h) : value(h) {
        if (!h || h == INVALID_HANDLE_VALUE)
            throw std::runtime_error("Win32 handle creation/open failed: " + std::to_string(GetLastError()));
    }
    ~Handle() {
        if (value)
            CloseHandle(value);
    }
    Handle(Handle &&o) noexcept : value(std::exchange(o.value, nullptr)) {}
    Handle &operator=(Handle &&o) noexcept {
        if (this != &o) {
            if (value)
                CloseHandle(value);
            value = std::exchange(o.value, nullptr);
        }
        return *this;
    }
    Handle(const Handle &) = delete;
};
struct Mapping {
    void *address{};
    ~Mapping() {
        if (address)
            UnmapViewOfFile(address);
    }
    void map(HANDLE h, std::size_t size) {
        address = MapViewOfFile(h, FILE_MAP_ALL_ACCESS, 0, 0, size);
        if (!address)
            throw std::runtime_error("MapViewOfFile failed");
    }
};
inline std::wstring wide(const std::string &s) { return std::wstring(s.begin(), s.end()); }
struct Lock {
    HANDLE mutex{};
    explicit Lock(HANDLE h, DWORD timeout = 1000) {
        DWORD r = WaitForSingleObject(h, timeout);
        if (r == WAIT_OBJECT_0 || r == WAIT_ABANDONED)
            mutex = h;
        else if (r != WAIT_TIMEOUT)
            throw std::runtime_error("frame metadata mutex wait failed");
    }
    ~Lock() {
        if (mutex)
            ReleaseMutex(mutex);
    }
    explicit operator bool() const { return mutex != nullptr; }
};
inline bool acquire(IDXGIKeyedMutex *mutex, DWORD timeout) {
    HRESULT r = mutex->AcquireSync(0, timeout);
    if (r == WAIT_TIMEOUT)
        return false;
    if (r == WAIT_ABANDONED)
        throw std::runtime_error("GPU keyed mutex abandoned; recreate and renegotiate the frame source");
    check(r, "AcquireSync");
    return true;
}
inline ComPtr<ID3D11Device1> create_device(std::uint32_t low = 0, std::int32_t high = 0) {
    ComPtr<IDXGIFactory1> factory;
    check(CreateDXGIFactory1(IID_PPV_ARGS(&factory)), "CreateDXGIFactory1");
    ComPtr<IDXGIAdapter1> chosen;
    if (low || high) {
        for (UINT i = 0;; i++) {
            ComPtr<IDXGIAdapter1> a;
            if (factory->EnumAdapters1(i, &a) == DXGI_ERROR_NOT_FOUND)
                break;
            DXGI_ADAPTER_DESC1 d{};
            check(a->GetDesc1(&d), "GetDesc1");
            if (d.AdapterLuid.LowPart == low && d.AdapterLuid.HighPart == high) {
                chosen = a;
                break;
            }
        }
        if (!chosen)
            throw std::runtime_error("requested GPU adapter LUID is unavailable");
    }
    ComPtr<ID3D11Device> device;
    check(D3D11CreateDevice(chosen.Get(), chosen ? D3D_DRIVER_TYPE_UNKNOWN : D3D_DRIVER_TYPE_HARDWARE,
                            nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0, D3D11_SDK_VERSION, &device,
                            nullptr, nullptr),
          "D3D11CreateDevice");
    ComPtr<ID3D11Device1> result;
    check(device.As(&result), "D3D11.1 required");
    return result;
}
inline LUID device_luid(ID3D11Device *device) {
    ComPtr<IDXGIDevice> dx;
    check(device->QueryInterface(IID_PPV_ARGS(&dx)), "IDXGIDevice");
    ComPtr<IDXGIAdapter> a;
    check(dx->GetAdapter(&a), "GetAdapter");
    DXGI_ADAPTER_DESC d{};
    check(a->GetDesc(&d), "GetDesc");
    return d.AdapterLuid;
}
} // namespace comfortable::detail
