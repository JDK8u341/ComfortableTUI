#include "internal.hpp"
#include <bcrypt.h>
#include <array>
#include <cstring>
#include <mutex>
#include <limits>

namespace comfortable {
using namespace detail;
namespace {
constexpr std::uint64_t magic = 0x3155504749555443ull; // CTUIGPU1, little endian
enum State : std::uint32_t { Free, Writing, Ready, Reading };
struct Slot {
    std::uint32_t state{}, reserved{};
    std::uint64_t sequence{};
    std::int64_t source{}, published{};
};
struct Control {
    std::uint64_t magic_value{};
    std::uint32_t version{}, bytes{}, width{}, height{}, adapter_low{};
    std::int32_t adapter_high{};
    std::uint32_t owner_pid{}, closed{};
    std::uint64_t sequence{};
    Slot slots[3];
};
static_assert(sizeof(Slot) == 32 && sizeof(Control) == 144, "GPU transport ABI changed");
void validate_name(const std::string &name) {
    const std::string prefix = "Local\\CTUI_GPU_";
    if (name.size() != prefix.size() + 32 || name.compare(0, prefix.size(), prefix) != 0 ||
        name.find_first_not_of("0123456789abcdef", prefix.size()) != std::string::npos)
        throw std::invalid_argument("invalid native GPU channel name");
}
void validate(const Control *c) {
    if (c->magic_value != magic || c->version != 1 || c->bytes != sizeof(Control) || !c->width ||
        !c->height || c->width > 16384 || c->height > 16384)
        throw std::runtime_error("incompatible native GPU frame protocol");
}
std::string random_name() {
    std::array<unsigned char, 16> bytes{};
    if (BCryptGenRandom(nullptr, bytes.data(), static_cast<ULONG>(bytes.size()),
                        BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0)
        throw std::runtime_error("BCryptGenRandom failed");
    std::string name = "Local\\CTUI_GPU_";
    const char hex[] = "0123456789abcdef";
    for (auto b : bytes) {
        name += hex[b >> 4];
        name += hex[b & 15];
    }
    return name;
}
struct Channel {
    Handle mapping, meta, ready;
    Mapping view;
    Control *control{};
    explicit Channel(const std::string &name, bool create) {
        validate_name(name);
        auto m = wide(name + ".meta"), r = wide(name + ".ready"), c = wide(name + ".control");
        if (create) {
            meta = Handle(CreateMutexW(nullptr, FALSE, m.c_str()));
            ready = Handle(CreateEventW(nullptr, FALSE, FALSE, r.c_str()));
            mapping = Handle(CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0,
                                                sizeof(Control), c.c_str()));
            if (GetLastError() == ERROR_ALREADY_EXISTS)
                throw std::runtime_error("GPU channel already exists");
        } else {
            meta = Handle(OpenMutexW(SYNCHRONIZE | MUTEX_MODIFY_STATE, FALSE, m.c_str()));
            ready = Handle(OpenEventW(SYNCHRONIZE | EVENT_MODIFY_STATE, FALSE, r.c_str()));
            mapping = Handle(OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, c.c_str()));
        }
        view.map(mapping.value, sizeof(Control));
        control = static_cast<Control *>(view.address);
        if (!create)
            validate(control);
    }
};
} // namespace

std::int64_t clock_ticks() {
    LARGE_INTEGER n{};
    QueryPerformanceCounter(&n);
    return n.QuadPart;
}
double clock_frequency() {
    static double f = [] {
        LARGE_INTEGER n{};
        QueryPerformanceFrequency(&n);
        return static_cast<double>(n.QuadPart);
    }();
    return f;
}
std::vector<AdapterInfo> adapters() {
    ComPtr<IDXGIFactory1> f;
    check(CreateDXGIFactory1(IID_PPV_ARGS(&f)), "CreateDXGIFactory1");
    std::vector<AdapterInfo> result;
    for (UINT i = 0;; i++) {
        ComPtr<IDXGIAdapter1> a;
        if (f->EnumAdapters1(i, &a) == DXGI_ERROR_NOT_FOUND)
            break;
        DXGI_ADAPTER_DESC1 d{};
        check(a->GetDesc1(&d), "GetDesc1");
        int n = WideCharToMultiByte(CP_UTF8, 0, d.Description, -1, nullptr, 0, nullptr, nullptr);
        std::string text(n, '\0');
        WideCharToMultiByte(CP_UTF8, 0, d.Description, -1, text.data(), n, nullptr, nullptr);
        text.pop_back();
        result.push_back({text, d.AdapterLuid.LowPart, d.AdapterLuid.HighPart,
                          (d.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) != 0});
    }
    return result;
}
FrameDescriptor describe(const std::string &name) {
    Channel c(name, false);
    Lock lock(c.meta.value);
    if (!lock)
        throw std::runtime_error("GPU metadata is busy");
    const auto *p = c.control;
    validate(p);
    if (p->closed)
        throw std::runtime_error("GPU frame provider is closed");
    return {name, p->width, p->height, p->adapter_low, p->adapter_high, 1};
}

struct FrameProvider::Impl {
    FrameDescriptor descriptor;
    std::unique_ptr<Channel> channel;
    ComPtr<ID3D11Device1> device;
    ComPtr<ID3D11DeviceContext> context;
    ComPtr<ID3D11Texture2D> textures[3];
    ComPtr<IDXGIKeyedMutex> keys[3];
    ComPtr<ID3D11RenderTargetView> targets[3];
    Handle handles[3];
    std::mutex writer;
    ~Impl() {
        if (channel) {
            try {
                Lock lock(channel->meta.value, 100);
                if (lock)
                    channel->control->closed = 1;
            } catch (...) {
            }
            SetEvent(channel->ready.value);
        }
    }
};
struct WriteFrame::Impl {
    std::shared_ptr<FrameProvider::Impl> provider;
    std::unique_lock<std::mutex> writer;
    int slot{-1};
    bool owned{};
    std::int64_t started{};
    explicit Impl(std::shared_ptr<FrameProvider::Impl> p)
        : provider(std::move(p)), writer(provider->writer, std::try_to_lock), started(clock_ticks()) {}
    ~Impl() {
        if (owned) {
            provider->context->Flush();
            provider->keys[slot]->ReleaseSync(0);
        }
        if (slot >= 0) {
            try {
                Lock lock(provider->channel->meta.value, 100);
                if (lock)
                    provider->channel->control->slots[slot].state = Free;
            } catch (...) {
            }
        }
    }
};
FrameProvider::FrameProvider(std::uint32_t w, std::uint32_t h, ID3D11Device *existing)
    : impl_(std::make_shared<Impl>()) {
    if (!w || !h || w > 16384 || h > 16384)
        throw std::invalid_argument("GPU frame size must be 1..16384");
    auto &p = *impl_;
    if (existing)
        check(existing->QueryInterface(IID_PPV_ARGS(&p.device)), "D3D11.1 producer device required");
    else
        p.device = create_device();
    p.device->GetImmediateContext(&p.context);
    auto luid = device_luid(p.device.Get());
    p.descriptor = {random_name(), w, h, luid.LowPart, luid.HighPart, 1};
    p.channel = std::make_unique<Channel>(p.descriptor.name, true);
    std::memset(p.channel->control, 0, sizeof(Control));
    auto *c = p.channel->control;
    c->version = 1;
    c->bytes = sizeof(Control);
    c->width = w;
    c->height = h;
    c->adapter_low = luid.LowPart;
    c->adapter_high = luid.HighPart;
    c->owner_pid = GetCurrentProcessId();
    for (int i = 0; i < 3; i++) {
        D3D11_TEXTURE2D_DESC d{};
        d.Width = w;
        d.Height = h;
        d.MipLevels = d.ArraySize = 1;
        d.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
        d.SampleDesc.Count = 1;
        d.Usage = D3D11_USAGE_DEFAULT;
        d.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
        d.MiscFlags = D3D11_RESOURCE_MISC_SHARED_NTHANDLE | D3D11_RESOURCE_MISC_SHARED_KEYEDMUTEX;
        check(p.device->CreateTexture2D(&d, nullptr, &p.textures[i]), "Create shared GPU texture");
        check(p.device->CreateRenderTargetView(p.textures[i].Get(), nullptr, &p.targets[i]),
              "CreateRenderTargetView");
        check(p.textures[i].As(&p.keys[i]), "IDXGIKeyedMutex");
        ComPtr<IDXGIResource1> resource;
        check(p.textures[i].As(&resource), "IDXGIResource1");
        HANDLE shared{};
        auto name = wide(p.descriptor.name + ".tex" + std::to_string(i));
        check(resource->CreateSharedHandle(nullptr, DXGI_SHARED_RESOURCE_READ | DXGI_SHARED_RESOURCE_WRITE,
                                           name.c_str(), &shared),
              "CreateSharedHandle");
        p.handles[i] = Handle(shared);
    }
    c->magic_value = magic;
}
FrameProvider::~FrameProvider() = default;
const FrameDescriptor &FrameProvider::descriptor() const { return impl_->descriptor; }
ID3D11Device *FrameProvider::device() const { return impl_->device.Get(); }
ID3D11DeviceContext *FrameProvider::context() const { return impl_->context.Get(); }
WriteFrame FrameProvider::begin_frame(std::uint32_t timeout) {
    WriteFrame result;
    auto lease = std::make_unique<WriteFrame::Impl>(impl_);
    if (!lease->writer.owns_lock())
        return result;
    {
        Lock lock(impl_->channel->meta.value, timeout);
        if (!lock)
            return result;
        auto *c = impl_->channel->control;
        int selected = -1;
        for (int i = 0; i < 3; i++)
            if (c->slots[i].state == Free) {
                selected = i;
                break;
            }
        if (selected < 0)
            for (int i = 0; i < 3; i++)
                if (c->slots[i].state == Ready &&
                    (selected < 0 || c->slots[i].sequence < c->slots[selected].sequence))
                    selected = i;
        if (selected < 0)
            return result;
        lease->slot = selected;
        c->slots[selected].state = Writing;
    }
    if (!acquire(impl_->keys[lease->slot].Get(), timeout))
        return result;
    lease->owned = true;
    result.impl_ = std::move(lease);
    return result;
}
bool FrameProvider::submit_bgra(const void *data, std::size_t stride, std::int64_t ticks) {
    if (!data || stride < descriptor().width * 4ull || stride > std::numeric_limits<UINT>::max())
        throw std::invalid_argument("invalid BGRA source stride");
    auto lease = begin_frame();
    if (!lease)
        return false;
    context()->UpdateSubresource(lease.texture(), 0, nullptr, data, static_cast<UINT>(stride), 0);
    lease.publish(ticks);
    return true;
}
WriteFrame::WriteFrame() = default;
WriteFrame::~WriteFrame() = default;
WriteFrame::WriteFrame(WriteFrame &&) noexcept = default;
WriteFrame &WriteFrame::operator=(WriteFrame &&) noexcept = default;
WriteFrame::operator bool() const { return impl_ && impl_->owned; }
ID3D11Texture2D *WriteFrame::texture() const {
    if (!*this)
        throw std::runtime_error("empty write lease");
    return impl_->provider->textures[impl_->slot].Get();
}
ID3D11RenderTargetView *WriteFrame::target() const {
    if (!*this)
        throw std::runtime_error("empty write lease");
    return impl_->provider->targets[impl_->slot].Get();
}
std::uint64_t WriteFrame::publish(std::int64_t ticks) {
    if (!*this)
        throw std::runtime_error("empty/published write lease");
    auto &p = *impl_->provider;
    p.context->Flush();
    check(p.keys[impl_->slot]->ReleaseSync(0), "producer ReleaseSync");
    impl_->owned = false;
    std::uint64_t sequence{};
    {
        Lock lock(p.channel->meta.value);
        if (!lock)
            throw std::runtime_error("GPU publish metadata timed out");
        auto *c = p.channel->control;
        auto &s = c->slots[impl_->slot];
        s.sequence = ++c->sequence;
        s.source = ticks ? ticks : impl_->started;
        s.published = clock_ticks();
        s.state = Ready;
        sequence = s.sequence;
        impl_->slot = -1;
    }
    SetEvent(p.channel->ready.value);
    impl_.reset();
    return sequence;
}

struct FrameConsumer::Impl {
    FrameDescriptor descriptor;
    std::unique_ptr<Channel> channel;
    Handle ownership, process;
    bool claimed{};
    ComPtr<ID3D11DeviceContext> context;
    ComPtr<ID3D11Texture2D> textures[3];
    ComPtr<IDXGIKeyedMutex> keys[3];
    ComPtr<ID3D11ShaderResourceView> views[3];
    ~Impl() {
        if (claimed)
            ReleaseMutex(ownership.value);
    }
};
struct ReadFrame::Impl {
    std::shared_ptr<FrameConsumer::Impl> consumer;
    int slot{-1};
    bool owned{};
    Slot metadata;
    ~Impl() {
        if (owned) {
            consumer->context->Flush();
            consumer->keys[slot]->ReleaseSync(0);
        }
        if (slot >= 0) {
            try {
                Lock lock(consumer->channel->meta.value, 100);
                if (lock) {
                    auto *c = consumer->channel->control;
                    c->slots[slot].state = !owned || c->slots[slot].sequence == c->sequence ? Ready : Free;
                }
            } catch (...) {
            }
        }
    }
};
FrameConsumer::FrameConsumer(const FrameDescriptor &d, ID3D11Device *device)
    : impl_(std::make_shared<Impl>()) {
    if (!device || d.version != 1)
        throw std::invalid_argument("invalid GPU consumer/device");
    auto luid = device_luid(device);
    if (luid.LowPart != d.adapter_low || luid.HighPart != d.adapter_high)
        throw std::runtime_error("GPU adapter LUID mismatch; use the provider's adapter");
    auto &p = *impl_;
    p.descriptor = d;
    p.channel = std::make_unique<Channel>(d.name, false);
    device->GetImmediateContext(&p.context);
    auto *c = p.channel->control;
    if (c->width != d.width || c->height != d.height || c->adapter_low != d.adapter_low ||
        c->adapter_high != d.adapter_high)
        throw std::runtime_error("GPU descriptor mismatch");
    auto name = wide(d.name + ".consumer");
    p.ownership = Handle(CreateMutexW(nullptr, FALSE, name.c_str()));
    if (GetLastError() == ERROR_ALREADY_EXISTS)
        throw std::runtime_error("GPU channel already has an active consumer/lease");
    DWORD acquired = WaitForSingleObject(p.ownership.value, 0);
    if (acquired != WAIT_OBJECT_0 && acquired != WAIT_ABANDONED)
        throw std::runtime_error("GPU channel already has an active consumer");
    p.claimed = true;
    {
        Lock lock(p.channel->meta.value);
        if (!lock)
            throw std::runtime_error("GPU consumer metadata timed out");
        for (const auto &s : c->slots)
            if (s.state == Reading)
                throw std::runtime_error(
                    "prior GPU consumer exited with a pinned texture; recreate provider and renegotiate");
    }
    HANDLE process = OpenProcess(SYNCHRONIZE, FALSE, c->owner_pid);
    if (process)
        p.process = Handle(process);
    ComPtr<ID3D11Device1> dev;
    check(device->QueryInterface(IID_PPV_ARGS(&dev)), "D3D11.1 consumer device required");
    for (int i = 0; i < 3; i++) {
        name = wide(d.name + ".tex" + std::to_string(i));
        check(dev->OpenSharedResourceByName(name.c_str(),
                                            DXGI_SHARED_RESOURCE_READ | DXGI_SHARED_RESOURCE_WRITE,
                                            IID_PPV_ARGS(&p.textures[i])),
              "OpenSharedResourceByName");
        D3D11_TEXTURE2D_DESC actual{};
        p.textures[i]->GetDesc(&actual);
        if (actual.Width != d.width || actual.Height != d.height ||
            actual.Format != DXGI_FORMAT_B8G8R8A8_UNORM || actual.MipLevels != 1 || actual.ArraySize != 1 ||
            actual.SampleDesc.Count != 1)
            throw std::runtime_error("shared texture does not match negotiated BGRA8 descriptor");
        check(p.textures[i].As(&p.keys[i]), "consumer IDXGIKeyedMutex");
        check(device->CreateShaderResourceView(p.textures[i].Get(), nullptr, &p.views[i]),
              "CreateShaderResourceView");
    }
}
FrameConsumer::~FrameConsumer() = default;
HANDLE FrameConsumer::ready_event() const { return impl_->channel->ready.value; }
bool FrameConsumer::closed() const {
    Lock lock(impl_->channel->meta.value, 0);
    return (lock && impl_->channel->control->closed) ||
           (impl_->process.value && WaitForSingleObject(impl_->process.value, 0) == WAIT_OBJECT_0);
}
bool FrameConsumer::pending(std::uint64_t after) const {
    Lock lock(impl_->channel->meta.value, 0);
    if (!lock)
        return true;
    for (const auto &slot : impl_->channel->control->slots)
        if (slot.state == Ready && slot.sequence > after)
            return true;
    return false;
}
ReadFrame FrameConsumer::acquire_latest(std::uint64_t after, std::uint32_t timeout) {
    ReadFrame result;
    auto lease = std::make_unique<ReadFrame::Impl>();
    lease->consumer = impl_;
    {
        Lock lock(impl_->channel->meta.value, timeout);
        if (!lock)
            return result;
        auto *c = impl_->channel->control;
        int selected = -1;
        for (int i = 0; i < 3; i++)
            if (c->slots[i].state == Ready && c->slots[i].sequence > after &&
                (selected < 0 || c->slots[i].sequence > c->slots[selected].sequence))
                selected = i;
        if (selected < 0)
            return result;
        lease->metadata = c->slots[selected];
        lease->slot = selected;
        c->slots[selected].state = Reading;
    }
    if (!acquire(impl_->keys[lease->slot].Get(), timeout))
        return result;
    lease->owned = true;
    result.impl_ = std::move(lease);
    return result;
}
ReadFrame::ReadFrame() = default;
ReadFrame::~ReadFrame() = default;
ReadFrame::ReadFrame(ReadFrame &&) noexcept = default;
ReadFrame &ReadFrame::operator=(ReadFrame &&) noexcept = default;
ReadFrame::operator bool() const { return impl_ && impl_->owned; }
ID3D11Texture2D *ReadFrame::texture() const {
    return *this ? impl_->consumer->textures[impl_->slot].Get() : nullptr;
}
ID3D11ShaderResourceView *ReadFrame::view() const {
    return *this ? impl_->consumer->views[impl_->slot].Get() : nullptr;
}
std::uint64_t ReadFrame::sequence() const { return *this ? impl_->metadata.sequence : 0; }
std::int64_t ReadFrame::source_ticks() const { return *this ? impl_->metadata.source : 0; }
std::int64_t ReadFrame::published_ticks() const { return *this ? impl_->metadata.published : 0; }
} // namespace comfortable
