#include "comfortable/glass.hpp"
#include "../canvas/internal.hpp"
#include "optics.hpp"
#include <d2d1_1.h>
#include <dwrite.h>
#include <dwmapi.h>
#include <oleacc.h>
#include <windowsx.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <deque>
#include <functional>
#include <map>
#include <mutex>
#include <set>
#include <thread>

namespace comfortable {
using namespace detail;
namespace {
constexpr UINT wake_message = WM_APP + 81;
std::wstring utf16(const std::string &s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s.data(), static_cast<int>(s.size()), nullptr, 0);
    if (!n) throw std::invalid_argument("invalid UTF-8 glass text");
    std::wstring out(n, 0);
    MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), out.data(), n);
    return out;
}
std::string utf8(const std::wstring &s) {
    if (s.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0, nullptr, nullptr);
    std::string out(n, 0);
    WideCharToMultiByte(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), out.data(), n, nullptr, nullptr);
    return out;
}
float row_height(const GlassControl &c) {
    auto h=glass_control_type(c).measure(c);
    if(!std::isfinite(h)||h<16||h>1056)throw std::invalid_argument("invalid measured control height");
    return h;
}
std::vector<std::string> modifiers() {
    std::vector<std::string> result;
    if(GetKeyState(VK_CONTROL)&0x8000)result.push_back("ctrl");
    if(GetKeyState(VK_SHIFT)&0x8000)result.push_back("shift");
    if(GetKeyState(VK_MENU)&0x8000)result.push_back("alt");
    return result;
}
#include "accessibility.hpp"
float card_height(const GlassCard &c) {
    float h = 102;
    for (const auto &v : c.controls) h += row_height(v);
    return h + 20;
}
std::string source_signature(const GlassCard &card) {
    std::string signature;float row=102;
    for(const auto &c:card.controls) {
        if(c.kind=="canvas") signature=c.canvas.source.name+":"+std::to_string(c.canvas.source.width)+":"+
            std::to_string(c.canvas.source.height)+":"+std::to_string(c.canvas.stride)+":"+c.canvas.pixel_format+
            ":"+c.fit+":"+std::to_string(row)+":"+std::to_string(c.canvas_height);
        row+=row_height(c);
    }
    return signature;
}
D2D1_COLOR_F color(std::uint32_t rgb, float alpha = 1) {
    return D2D1::ColorF((rgb >> 16) / 255.f, ((rgb >> 8) & 255) / 255.f, (rgb & 255) / 255.f, alpha);
}
struct Spring {
    double x{}, v{}, target{};
    bool step(double dt, bool reduced) {
        if (reduced) { x = target; v = 0; return false; }
        double offset = x - target;
        if (std::abs(offset) < .08 && std::abs(v) < .1) { x = target; v = 0; return false; }
        constexpr double w = 17.5, z = .76;
        const double b = w * std::sqrt(1 - z*z), a = z*w;
        double c = std::cos(b*dt), s = std::sin(b*dt), d = std::exp(-a*dt), old = offset;
        offset = d * (offset*c + (v+a*offset)/b*s);
        v = d * (v*c - (a*v+w*w*old)/b*s);
        x = target+offset;
        return true;
    }
};
struct UiSurface {
    FrameProvider provider;
    ComPtr<ID2D1Device> device;
    ComPtr<ID2D1DeviceContext> dc;
    ComPtr<ID2D1SolidColorBrush> brush;
    std::map<ID3D11Texture2D *, ComPtr<ID2D1Bitmap1>> targets;
    std::map<std::string, ComPtr<IDWriteTextLayout>> text_cache;
    IDWriteFactory *write{};
    float dpi{1};
    bool glyph_shadow{};
    UiSurface(unsigned w, unsigned h, ID2D1Factory1 *factory, IDWriteFactory *fonts, float scale,
              ComPtr<ID3D11Device1> d3d)
        : provider(w,h,d3d.Get()), write(fonts), dpi(scale) {
        ComPtr<IDXGIDevice> dx;
        check(d3d.As(&dx), "Glass IDXGIDevice");
        check(factory->CreateDevice(dx.Get(), &device), "Glass D2D device");
        check(device->CreateDeviceContext(D2D1_DEVICE_CONTEXT_OPTIONS_NONE, &dc), "Glass D2D context");
        dc->SetDpi(96*scale,96*scale);
        dc->SetTextAntialiasMode(D2D1_TEXT_ANTIALIAS_MODE_GRAYSCALE);
        check(dc->CreateSolidColorBrush(color(0xffffff), &brush), "Glass brush");
    }
    WriteFrame begin() {
        auto frame = provider.begin_frame(0);
        if (!frame) return frame;
        auto texture = frame.texture();
        if (!targets.count(texture)) {
            ComPtr<IDXGISurface> surface;
            check(texture->QueryInterface(IID_PPV_ARGS(&surface)), "Glass DXGI surface");
            ComPtr<ID2D1Bitmap1> bitmap;
            auto props = D2D1::BitmapProperties1(D2D1_BITMAP_OPTIONS_TARGET | D2D1_BITMAP_OPTIONS_CANNOT_DRAW,
                D2D1::PixelFormat(DXGI_FORMAT_B8G8R8A8_UNORM,D2D1_ALPHA_MODE_PREMULTIPLIED),96*dpi,96*dpi);
            check(dc->CreateBitmapFromDxgiSurface(surface.Get(), &props, &bitmap), "Glass target bitmap");
            targets[texture] = bitmap;
        }
        dc->SetTarget(targets.at(texture).Get());
        dc->BeginDraw();
        dc->Clear(D2D1::ColorF(0,0,0,0));
        return frame;
    }
    void end(WriteFrame &frame) {
        check(dc->EndDraw(), "Glass EndDraw");
        dc->SetTarget(nullptr);
        frame.publish();
    }
    void fill(D2D1_RECT_F rect, std::uint32_t rgb, float alpha=1, float radius=0) {
        brush->SetColor(color(rgb,alpha));
        if (radius) dc->FillRoundedRectangle(D2D1::RoundedRect(rect,radius,radius),brush.Get());
        else dc->FillRectangle(rect,brush.Get());
    }
    void text(const std::string &str, float x, float y, float width, float size,
              std::uint32_t rgb, float alpha=1, bool bold=false) {
        std::string key = std::to_string(size)+":"+std::to_string(width)+(bold?"b:":":")+str;
        if (!text_cache.count(key)) {
            if (text_cache.size() >= 256) text_cache.clear();
            ComPtr<IDWriteTextFormat> format;
            check(write->CreateTextFormat(L"Segoe UI", nullptr,bold?DWRITE_FONT_WEIGHT_SEMI_BOLD:DWRITE_FONT_WEIGHT_NORMAL,
                DWRITE_FONT_STYLE_NORMAL,DWRITE_FONT_STRETCH_NORMAL,size,L"",&format),"Glass text format");
            format->SetWordWrapping(DWRITE_WORD_WRAPPING_NO_WRAP);
            DWRITE_TRIMMING trim{DWRITE_TRIMMING_GRANULARITY_CHARACTER,0,0};
            ComPtr<IDWriteInlineObject> ellipsis;
            write->CreateEllipsisTrimmingSign(format.Get(),&ellipsis);
            format->SetTrimming(&trim,ellipsis.Get());
            auto value = utf16(str);
            ComPtr<IDWriteTextLayout> layout;
            check(write->CreateTextLayout(value.data(),static_cast<UINT32>(value.size()),format.Get(),
                                          std::max(1.f,width),size*1.6f,&layout),"Glass text layout");
            text_cache[key]=layout;
        }
        brush->SetColor(color(rgb,alpha));
        if(glyph_shadow) {
            brush->SetColor(color(0x02050a,.72f*alpha));
            dc->DrawTextLayout(D2D1::Point2F(x,y+1),text_cache.at(key).Get(),brush.Get(),D2D1_DRAW_TEXT_OPTIONS_CLIP);
            brush->SetColor(color(rgb,alpha));
        }
        dc->DrawTextLayout(D2D1::Point2F(x,y),text_cache.at(key).Get(),brush.Get(),D2D1_DRAW_TEXT_OPTIONS_CLIP);
    }
};
} // namespace

namespace {
struct ControlPainter final : GlassPainter {
    UiSurface &p;float ox,oy;
    ControlPainter(UiSurface &surface,float x,float y):p(surface),ox(x),oy(y){}
    void fill(float x,float y,float w,float h,std::uint32_t rgb,float a,float radius)override {
        if(w>0&&h>0)p.fill(D2D1::RectF(ox+x,oy+y,ox+x+w,oy+y+h),rgb,a,radius);
    }
    void text(const std::string &s,float x,float y,float w,float size,std::uint32_t rgb,float a,bool bold)override {p.text(s,ox+x,oy+y,w,size,rgb,a,bold);}
    void line(float x,float y,float x2,float y2,std::uint32_t rgb,float a,float thickness)override {
        p.brush->SetColor(color(rgb,a));p.dc->DrawLine(D2D1::Point2F(ox+x,oy+y),D2D1::Point2F(ox+x2,oy+y2),p.brush.Get(),thickness);
    }
    void circle(float x,float y,float radius,std::uint32_t rgb,float a)override {
        p.brush->SetColor(color(rgb,a));p.dc->FillEllipse(D2D1::Ellipse(D2D1::Point2F(ox+x,oy+y),radius,radius),p.brush.Get());
    }
};
}
struct GlassHost::Impl {
    struct Card;
    HWND owner{};
    bool inspectable_windows{};
    DWORD thread_id{};
    Handle changed{CreateEventW(nullptr,FALSE,FALSE,nullptr)};
    Handle finished{CreateEventW(nullptr,TRUE,FALSE,nullptr)};
    std::thread worker;
    mutable std::mutex mutex;
    std::condition_variable started;
    bool ready{}, stopping{};
    GlassScene requested, scene;
    std::uint64_t submitted{}, applied{}, event_serial{};
    struct Action {std::string kind,id;std::uint64_t token{};};
    std::deque<Action> commands;
    struct Presentation {std::string card,control;std::uint64_t generation{};float opacity{1};};
    std::vector<Presentation> presentations;
    std::deque<GlassEvent> output;
    GlassMetrics report;
    std::vector<std::unique_ptr<Card>> cards;
    // All card ink is drawn on this worker. Share a producer device per GPU,
    // instead of creating/tearing down an independent D3D device per card.
    std::map<std::pair<std::uint32_t,std::int32_t>,ComPtr<ID3D11Device1>> ui_devices;
    ComPtr<ID2D1Factory1> factory;
    ComPtr<IDWriteFactory> write;
    RECT owner_rect{}, work{};
    float dpi{1};
    bool active{}, compact{}, contrast{}, system_animation{true}, backdrop_dismissed{};
    bool retain_positions{};
    RECT layout_work{};
    LONG layout_owner_width{},layout_owner_height{};
    HWND last_external{}, last_group_foreground{};
    std::vector<std::string> root_stack,card_stack,last_stack;
    std::string effective_material;
    HWND background{};
    std::unique_ptr<UiSurface> background_ui;
    std::unique_ptr<Compositor> background_compositor;
    std::unique_ptr<glass_detail::OpticalScene> optics;
    std::unique_ptr<glass_detail::OpticalPanel> background_optical;
    bool sampling_allowed{true};
    double close_join_ms{},close_surfaces_ms{},close_windows_ms{};
    HMONITOR optical_monitor{};
    static thread_local Impl *current;

    explicit Impl(std::uintptr_t h,bool inspectable) : owner(reinterpret_cast<HWND>(h)),inspectable_windows(inspectable) {
        if (!IsWindow(owner)) throw std::invalid_argument("GlassHost requires a valid owner HWND");
        worker=std::thread([this]{run();});
        std::unique_lock<std::mutex> lock(mutex);
        started.wait(lock,[this]{return ready;});
        if (!report.error.empty()) {
            lock.unlock(); worker.join(); throw std::runtime_error(report.error);
        }
    }
    ~Impl() { close(); }
    ComPtr<ID3D11Device1> ui_device(std::uint32_t low,std::int32_t high) {
        auto &device=ui_devices[{low,high}];
        if(!device)device=create_device(low,high);
        return device;
    }
    void close() {
        {std::lock_guard<std::mutex> lock(mutex);stopping=true;}
        SetEvent(changed.value);
        if (worker.joinable()) {
            // A same-process host may own windows on the caller's UI thread.
            // Activation/destruction can synchronously send to that thread;
            // a plain join would prevent those messages from completing.
            // Dispatch only sent messages, never re-enter posted business work.
            while(WaitForSingleObject(finished.value,0)!=WAIT_OBJECT_0) {
                auto status=MsgWaitForMultipleObjectsEx(1,&finished.value,INFINITE,QS_SENDMESSAGE,MWMO_INPUTAVAILABLE);
                if(status==WAIT_OBJECT_0+1) {MSG message{};PeekMessageW(&message,nullptr,0,0,PM_NOREMOVE|PM_QS_SENDMESSAGE);}
                else if(status==WAIT_FAILED)break;
            }
            worker.join();
        }
    }
    std::uint64_t emit(GlassEvent e) {
        std::lock_guard<std::mutex> lock(mutex);
        auto serial=e.sequence=++event_serial;
        for(const auto &card:scene.cards)if(card.id==e.card) {
            e.generation=card.generation;
            if(!e.control.empty())for(const auto &control:card.controls)if(control.id==e.control)e.generation=control.generation;
            break;
        }
        if ((e.kind=="mouse_move"||e.kind=="pointer_move"||e.kind=="drag") && !output.empty() && output.back().kind==e.kind &&
            output.back().card==e.card && output.back().control==e.control) output.back()=std::move(e);
        else {
            if (output.size()>=1024) {output.pop_front();report.dropped_events++;}
            output.push_back(std::move(e));
        }
        return serial;
    }
    bool group_active() const {
        HWND fg=GetForegroundWindow();
        return fg==owner || GetAncestor(fg,GA_ROOTOWNER)==owner ||
            std::any_of(cards.begin(),cards.end(),[&](const auto &c){return c->hwnd==fg;});
    }
    void bounds() {
        if (FAILED(DwmGetWindowAttribute(owner,DWMWA_EXTENDED_FRAME_BOUNDS,&owner_rect,sizeof(owner_rect))))
            GetWindowRect(owner,&owner_rect);
        MONITORINFO mi{sizeof(mi)};
        GetMonitorInfoW(MonitorFromWindow(owner,MONITOR_DEFAULTTONEAREST),&mi);work=mi.rcWork;
    }
    static LRESULT CALLBACK background_proc(HWND hwnd,UINT msg,WPARAM w,LPARAM l) {
        auto self=reinterpret_cast<Impl *>(GetWindowLongPtrW(hwnd,GWLP_USERDATA));
        if (msg==WM_NCCREATE) {
            self=static_cast<Impl *>(reinterpret_cast<CREATESTRUCTW *>(l)->lpCreateParams);
            SetWindowLongPtrW(hwnd,GWLP_USERDATA,reinterpret_cast<LONG_PTR>(self));
        }
        if (msg==WM_MOUSEACTIVATE) return MA_NOACTIVATE;
        if (msg==WM_LBUTTONDOWN && self) {
            self->backdrop_dismissed=true;ShowWindow(hwnd,SW_HIDE);
            self->emit({"backdrop_dismissed"});return 0;
        }
        if (msg==WM_ERASEBKGND) return 1;
        if (msg==WM_PAINT) {PAINTSTRUCT p;BeginPaint(hwnd,&p);EndPaint(hwnd,&p);return 0;}
        return DefWindowProcW(hwnd,msg,w,l);
    }
    bool material(HWND hwnd,const std::string &style) {
        MARGINS margins{-1,-1,-1,-1};
        DwmExtendFrameIntoClientArea(hwnd,&margins);
        DWORD dark=1, corner=2, border=0xfffffffe;
        DwmSetWindowAttribute(hwnd,20,&dark,sizeof(dark));
        DwmSetWindowAttribute(hwnd,33,&corner,sizeof(corner));
        DwmSetWindowAttribute(hwnd,34,&border,sizeof(border));
        DWORD kind=style=="mica"?2:style=="acrylic"?3:1;
        return SUCCEEDED(DwmSetWindowAttribute(hwnd,38,&kind,sizeof(kind))) && style!="solid";
    }
    void update_background(bool visible) {
        if (!visible || cards.empty() || scene.backdrop=="none" || backdrop_dismissed) {
            if (background) {ShowWindow(background,SW_HIDE);background_compositor->visible(false);}
            if(background_optical){background_compositor->configure({},1,1);background_optical.reset();}
            {std::lock_guard<std::mutex> lock(mutex);report.backdrop_material="none";}
            return;
        }
        if (!background) {
            if(static_cast<double>(work.right-work.left)*(work.bottom-work.top)>8*1024*1024) {
                emit({"backdrop_unavailable","","","monitor exceeds the 8-megapixel backdrop budget"});return;
            }
            background=CreateWindowExW(WS_EX_TOOLWINDOW|WS_EX_NOACTIVATE|WS_EX_NOREDIRECTIONBITMAP,
                L"ComfortableGlassBackdrop",L"Comfortable Glass / focus backdrop",WS_POPUP,
                0,0,1,1,nullptr,nullptr,GetModuleHandleW(nullptr),this);
            if (!background) throw std::runtime_error("Cannot create glass backdrop");
            if(!glass_detail::OpticalScene::exclude(background))sampling_allowed=false;
            auto adapter=optics?optics->adapter():LUID{};
            background_ui=std::make_unique<UiSurface>(1u,1u,factory.Get(),write.Get(),1.f,ui_device(adapter.LowPart,adapter.HighPart));
            auto d=background_ui->provider.descriptor();
            background_compositor=std::make_unique<Compositor>(reinterpret_cast<std::uintptr_t>(background),d.adapter_low,d.adapter_high);
        }
        bool blur=scene.backdrop=="frosted"&&!contrast&&!scene.reduced_transparency&&optics&&sampling_allowed&&optics->stats().image_ready;
        bool mica=material(background,scene.backdrop=="mica"&&!contrast&&!scene.reduced_transparency?"mica":"solid");
        auto f=background_ui->begin();
        if (f) {background_ui->dc->Clear(D2D1::ColorF(.025f,.035f,.06f,blur?0.f:mica?.10f:.36f));background_ui->end(f);}
        RECT r{};GetWindowRect(background,&r);
        bool size_change=r.left!=work.left||r.top!=work.top||r.right!=work.right||r.bottom!=work.bottom;
        if (size_change) {
            SetWindowPos(background,owner,work.left,work.top,work.right-work.left,work.bottom-work.top,
                         SWP_NOACTIVATE|SWP_NOOWNERZORDER);
        }
        if(blur&&(!background_optical||size_change))background_optical=optics->panel(work.right-work.left,work.bottom-work.top);
        NativeView view;view.id=1;view.source=blur?background_optical->descriptor():background_ui->provider.descriptor();view.fit="stretch";
        view.full=view.clip={0,0,static_cast<float>(work.right-work.left),static_cast<float>(work.bottom-work.top)};
        background_compositor->configure({view},work.right-work.left,work.bottom-work.top);
        // Background is independent (owned windows must always be ABOVE owner).
        SetWindowPos(background,owner,0,0,0,0,SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE|SWP_SHOWWINDOW);
        background_compositor->visible(true);
        {std::lock_guard<std::mutex> lock(mutex);report.backdrop_material=blur?"frosted":mica?"mica":"dim";}
    }
    struct Card {
        Impl *host;
        GlassCard spec;
        HWND hwnd{};
        std::unique_ptr<UiSurface> ui;
        std::unique_ptr<Compositor> compositor;
        std::unique_ptr<glass_detail::OpticalPanel> optical;
        GlassBackground background;
        std::string actual_material;
        Spring x,y;
        float width{},height{}, hover_x{},hover_y{}, reveal{};
        int hovered{-1}, focused{-1}, pressed{-1};
        bool dirty{true}, dragging{}, shown{}, native_material{}, close_pressed{};
        bool moving{}, pinned{}, group_visible{true}, group_enabled{true}, before_pinned{};
        bool layout_positioned{};
        float layout_offset_x{},layout_offset_y{};
        double offset_x{},offset_y{},before_offset_x{},before_offset_y{},drag_start_x{},drag_start_y{};
        POINT grab{},last_pointer{};
        std::uint64_t pointer_sequence{};
        bool optical_dirty{true};
        std::string canvas_signature, material_key;
        int native_x{-100000},native_y{-100000};
        std::shared_ptr<Card *> accessibility=std::make_shared<Card *>(this);
        bool input_selected{};
        wchar_t surrogate{};
        Card(Impl *h,const GlassCard &s):host(h),spec(s),width(s.width),height(card_height(s)) {
            hwnd=CreateWindowExW((h->inspectable_windows?WS_EX_APPWINDOW:WS_EX_TOOLWINDOW)|WS_EX_NOREDIRECTIONBITMAP,L"ComfortableGlassCard",
                utf16("Comfortable Glass / "+s.title).c_str(),WS_POPUP|WS_CLIPCHILDREN,
                0,0,static_cast<int>(std::round(width*h->dpi)),static_cast<int>(std::round(height*h->dpi)),
                h->inspectable_windows?nullptr:h->owner,nullptr,GetModuleHandleW(nullptr),this);
            if (!hwnd) throw std::runtime_error("Cannot create glass card");
            if(!glass_detail::OpticalScene::exclude(hwnd))h->sampling_allowed=false;
            try {
                std::uint32_t low=0;std::int32_t high=0;
                if(h->optics){auto adapter=h->optics->adapter();low=adapter.LowPart;high=adapter.HighPart;}
                for (const auto &c:s.controls) if(c.kind=="canvas"&&c.canvas.gpu) {
                    low=c.canvas.source.adapter_low;high=c.canvas.source.adapter_high;
                }
                ui=std::make_unique<UiSurface>(static_cast<unsigned>(std::round(width*h->dpi)),
                    static_cast<unsigned>(std::round(height*h->dpi)),h->factory.Get(),h->write.Get(),h->dpi,h->ui_device(low,high));
                auto d=ui->provider.descriptor();
                compositor=std::make_unique<Compositor>(reinterpret_cast<std::uintptr_t>(hwnd),d.adapter_low,d.adapter_high);
                x.x=x.target=(h->owner_rect.left+h->owner_rect.right)/2.f-width*h->dpi/2;
                y.x=y.target=h->owner_rect.top+42*h->dpi;
                apply_material();
                configure_canvas();
                host->emit({"ready",spec.id,"",std::to_string(reinterpret_cast<std::uintptr_t>(hwnd))});
            } catch (...) {DestroyWindow(hwnd);hwnd=nullptr;throw;}
        }
        ~Card() {
            *accessibility=nullptr;
            if (hwnd) ShowWindow(hwnd,SW_HIDE);
            auto start=clock_ticks();
            if (compositor) compositor->close();
            auto joined=clock_ticks();
            optical.reset();
            ui.reset();
            auto surfaces=clock_ticks();
            if (hwnd) DestroyWindow(hwnd);
            auto end=clock_ticks();
            host->close_join_ms+=(joined-start)*1000/clock_frequency();
            host->close_surfaces_ms+=(surfaces-joined)*1000/clock_frequency();
            host->close_windows_ms+=(end-surfaces)*1000/clock_frequency();
        }
        void apply_material() {
            background=spec.background;
            if(!spec.has_background) {
                background.kind=host->scene.material=="glass"?"liquid":host->scene.material;
                if(background.kind=="frosted"){background.blur=18;background.opacity=.12f;}
                if(background.kind=="solid"){background.tint=0x192130;background.opacity=1;}
            }
            if(host->contrast||host->scene.reduced_transparency){background.kind="solid";background.tint=0x192130;background.opacity=1;}
            actual_material=background.kind;
            bool wants_optical=background.kind=="liquid"||background.kind=="frosted";
            if(wants_optical&&host->optics&&host->sampling_allowed) {
                auto d=ui->provider.descriptor();auto adapter=host->optics->adapter();
                if(d.adapter_low==adapter.LowPart&&d.adapter_high==adapter.HighPart) {
                    if(!optical)optical=host->optics->panel(d.width,d.height);
                } else actual_material="solid";
            } else if(wants_optical)actual_material="solid";
            bool use_optical=wants_optical&&actual_material!="solid";
            if(actual_material=="solid"&&background.kind!="solid"){background.tint=0x192130;background.opacity=1;}
            if(!use_optical&&optical){optical.reset();canvas_signature.clear();}
            native_material=host->material(hwnd,actual_material=="mica"?"mica":"solid");
            if(actual_material=="mica"&&!native_material)actual_material="solid";
            host->effective_material=actual_material;
            // The shader owns the shape. Its region also owns hit testing.
            int radius=static_cast<int>(std::round(background.corner_radius*host->dpi*2));
            auto region=CreateRoundRectRgn(0,0,static_cast<int>(std::round(width*host->dpi))+1,
                static_cast<int>(std::round(height*host->dpi))+1,radius,radius);
            if(region&&!SetWindowRgn(hwnd,region,TRUE))DeleteObject(region);
            dirty=optical_dirty=true;
        }
        void configure_canvas() {
            NativeView ink;ink.id=1;ink.source=ui->provider.descriptor();ink.fit="stretch";
            ink.full=ink.clip={0,0,width*host->dpi,height*host->dpi};
            std::vector<NativeView> views;
            if(optical){auto lens=ink;lens.id=3;lens.source=optical->descriptor();views.push_back(lens);}
            views.push_back(ink);
            float row=102;
            canvas_signature.clear();
            for (const auto &c:spec.controls) {
                if (c.kind=="canvas") {
                    auto v=c.canvas;v.id=2;v.fit=c.fit;
                    v.full=v.clip={24*host->dpi,(row+22)*host->dpi,(width-48)*host->dpi,c.canvas_height*host->dpi};
                    views.push_back(v);
                    host->emit({"transform",spec.id,c.id,"",host->dpi,width-48,c.canvas_height});
                }
                row+=row_height(c);
            }
            compositor->configure(views,ink.source.width,ink.source.height);
            canvas_signature=source_signature(spec)+(optical?":optical":"");
        }
        void pointer(const std::string &kind,float px,float py) {
            POINT point{static_cast<LONG>(std::round(px*host->dpi)),static_cast<LONG>(std::round(py*host->dpi))};
            ClientToScreen(hwnd,&point);last_pointer=point;
            auto token=host->emit({kind,spec.id,"",py<96?"header":"",0,px,py});
            if(kind=="pointer_down"){pointer_sequence=token;grab={static_cast<LONG>(std::round(px*host->dpi)),static_cast<LONG>(std::round(py*host->dpi))};}
        }
        void move_drag() {
            if(!moving)return;
            float cw=width*host->dpi,ch=height*host->dpi;
            double nx=std::clamp<double>(last_pointer.x-grab.x,host->work.left+4*host->dpi,std::max<double>(host->work.left,host->work.right-cw-4*host->dpi));
            double ny=std::clamp<double>(last_pointer.y-grab.y,host->work.top+4*host->dpi,std::max<double>(host->work.top,host->work.bottom-ch-4*host->dpi));
            x.x=x.target=nx;y.x=y.target=ny;x.v=y.v=0;
            pinned=true;offset_x=nx-host->owner_rect.left;offset_y=ny-host->owner_rect.top;
            host->emit({"drag",spec.id,"","",0,offset_x/host->dpi,offset_y/host->dpi});optical_dirty=true;
        }
        void begin_drag(std::uint64_t token) {
            if(!spec.draggable||!group_enabled||!group_visible||!dragging||pressed>=0||token!=pointer_sequence||GetCapture()!=hwnd)return;
            moving=true;before_pinned=pinned;before_offset_x=offset_x;before_offset_y=offset_y;
            drag_start_x=x.x;drag_start_y=y.x;
            host->emit({"drag_start",spec.id,"","",0,(x.x-host->owner_rect.left)/host->dpi,(y.x-host->owner_rect.top)/host->dpi});
            move_drag();
        }
        void stop_drag(bool cancel) {
            if(moving) {
                if(cancel){x.x=x.target=drag_start_x;y.x=y.target=drag_start_y;pinned=before_pinned;offset_x=before_offset_x;offset_y=before_offset_y;}
                host->emit({cancel?"drag_cancel":"drag_end",spec.id,"","",0,(x.x-host->owner_rect.left)/host->dpi,(y.x-host->owner_rect.top)/host->dpi});
            }
            moving=false;pointer_sequence=0;optical_dirty=true;
        }
        int hit(float px,float py) {
            if (px<20||px>width-20) return -1;
            float row=102;
            for (std::size_t i=0;i<spec.controls.size();i++) {
                auto &c=spec.controls[i];float h=row_height(c);
                if(py>=row&&py<row+h&&glass_control_type(c).hit_test(c,px-20,py-row,width-40,h))return static_cast<int>(i);
                row+=row_height(spec.controls[i]);
            }
            return -1;
        }
        float row_top(int index) {
            float row=102;
            for(int i=0;i<index;i++) row+=row_height(spec.controls[i]);
            return row;
        }
        void canvas_event(const std::string &kind,int at,float px,float py,WPARAM flags=0,const std::string &button="left",int clicks=1,double value=0) {
            auto &c=spec.controls[at];
            double w=width-48,h=c.canvas_height,sw=c.canvas.source.width,sh=c.canvas.source.height;
            double sx=w/sw,sy=h/sh,ox=0,oy=0;
            if(c.fit!="stretch") {
                double scale=c.fit=="pixel"?1/host->dpi:c.fit=="cover"?std::max(sx,sy):std::min(sx,sy);
                sx=sy=scale;ox=(w-sw*scale)/2;oy=(h-sh*scale)/2;
            }
            GlassEvent event{kind,spec.id,c.id,"",value,(px-24-ox)/sx,(py-row_top(at)-22-oy)/sy};
            event.source_name=c.canvas.source.name;
            event.modifiers=modifiers();event.button=button;event.clicks=clicks;
            event.buttons=((flags&MK_LBUTTON)?1:0)|((flags&MK_RBUTTON)?2:0)|((flags&MK_MBUTTON)?4:0);
            event.inside_source=px>=24&&px<width-24&&py>=row_top(at)+22&&py<row_top(at)+22+h&&event.x>=0&&event.x<sw&&event.y>=0&&event.y<sh;
            host->emit(std::move(event));
        }
        void control_pointer(const char *kind,int at,float px,float py,WPARAM flags=0) {
            if(at<0||at>=static_cast<int>(spec.controls.size()))return;
            auto &c=spec.controls[at];if(!c.enabled||c.kind=="canvas")return;
            GlassEvent event{kind,spec.id,c.id,"",0,px-20,py-row_top(at)};
            event.buttons=((flags&MK_LBUTTON)?1:0)|((flags&MK_RBUTTON)?2:0)|((flags&MK_MBUTTON)?4:0);
            event.modifiers=modifiers();host->emit(std::move(event));
        }
        bool interactive(int at)const {
            if(at<0||at>=static_cast<int>(spec.controls.size()))return false;
            const auto &c=spec.controls[at];return c.enabled&&glass_control_type(c).focusable;
        }
        void focus_event(const std::string &kind) {
            if(focused>=0&&focused<static_cast<int>(spec.controls.size()))host->emit({kind,spec.id,spec.controls[focused].id});
        }
        void focus_control(int at) {
            if(at==focused)return;
            if(GetFocus()==hwnd)focus_event("control_focus_out");
            focused=at;surrogate=0;input_selected=false;dirty=true;
            if(GetFocus()==hwnd)focus_event("control_focus_in");
            NotifyWinEvent(EVENT_OBJECT_FOCUS,hwnd,OBJID_CLIENT,at+1);
        }
        void tab(int direction) {
            int n=static_cast<int>(spec.controls.size());
            for(int at=focused+direction;at>=0&&at<n;at+=direction)if(interactive(at)){focus_control(at);return;}
            if(direction<0&&focused>=0){focus_control(-1);return;}
            host->cycle(direction,direction<0?-2:-1);
        }
        void key_event(const std::string &kind,WPARAM key,LPARAM flags) {
            GlassEvent event{kind,spec.id,spec.controls[focused].id,std::to_string(key)};
            event.source_name=spec.controls[focused].canvas.source.name;
            event.modifiers=modifiers();event.scan_code=static_cast<std::int32_t>(flags);event.repeat=kind=="key_down"&&(flags&(1LL<<30))!=0;
            host->emit(std::move(event));
        }
        void activate(int at,float px=0,float py=0) {
            if(at<0||at>=static_cast<int>(spec.controls.size())) return;
            auto &c=spec.controls[at];if(!c.enabled)return;
            focus_control(at);dirty=true;
            if(c.kind=="canvas") {canvas_event("mouse_down",at,px,py,MK_LBUTTON);return;}
            dispatch_input(at,{"activate",px-20,py-row_top(at),width-40,row_height(c),0});
        }
        bool dispatch_input(int at,const GlassControlInput &input) {
            auto &c=spec.controls[at];auto &type=glass_control_type(c);
            auto result=type.input?type.input(c,input):GlassControlAction::none;
            if(!std::isfinite(c.value))throw std::invalid_argument("control input produced non-finite value");
            if(result==GlassControlAction::change){dirty=true;change(c);}
            if(result==GlassControlAction::click)host->emit({"click",spec.id,c.id});
            return result!=GlassControlAction::none;
        }
        void change(GlassControl &c) {
            std::string text=c.kind=="choice"?c.options[static_cast<std::size_t>(c.value)]:c.text;
            c.edit_serial=host->emit({"change",spec.id,c.id,text,c.value});
        }
        static LRESULT CALLBACK proc(HWND hwnd,UINT msg,WPARAM w,LPARAM l) {
            auto self=reinterpret_cast<Card *>(GetWindowLongPtrW(hwnd,GWLP_USERDATA));
            if(msg==WM_NCCREATE) {
            self=static_cast<Card *>(reinterpret_cast<CREATESTRUCTW *>(l)->lpCreateParams);
                self->hwnd=hwnd;
                SetWindowLongPtrW(hwnd,GWLP_USERDATA,reinterpret_cast<LONG_PTR>(self));
            }
            if(!self)return DefWindowProcW(hwnd,msg,w,l);
            try {return self->message(msg,w,l);}catch(const std::exception &e){
                std::lock_guard<std::mutex> lock(self->host->mutex);self->host->report.error=e.what();return 0;
            }
        }
        LRESULT message(UINT msg,WPARAM w,LPARAM l) {
            float px=GET_X_LPARAM(l)/host->dpi,py=GET_Y_LPARAM(l)/host->dpi;
            if(!group_enabled&&((msg>=WM_MOUSEFIRST&&msg<=WM_MOUSELAST)||msg==WM_KEYDOWN||msg==WM_KEYUP||msg==WM_CHAR))return 0;
            switch(msg) {
            case WM_GETOBJECT:
                if(static_cast<LONG>(l)==OBJID_CLIENT){auto object=new GlassAccessibility<Card>(accessibility);auto result=LresultFromObject(IID_IAccessible,w,object);object->Release();return result;}
                break;
            case WM_ERASEBKGND:return 1;
            case WM_PAINT:{PAINTSTRUCT p;BeginPaint(hwnd,&p);EndPaint(hwnd,&p);return 0;}
            case WM_SETFOCUS:
                host->select(spec.id);host->emit({"focus",spec.id});focus_event("control_focus_in");dirty=true;return 0;
            case WM_KILLFOCUS:focus_event("control_focus_out");dirty=true;return 0;
            case WM_MOUSEMOVE: {
                if(hover_x==px&&hover_y==py)return 0;
                int previous_hover=hovered;
                hover_x=px;hover_y=py;hovered=hit(px,py);dirty=true;
                if(previous_hover!=hovered){control_pointer("pointer_leave",previous_hover,px,py,w);control_pointer("pointer_enter",hovered,px,py,w);}
                control_pointer("pointer_move",hovered,px,py,w);
                if(dragging&&pressed<0&&(w&MK_LBUTTON)){pointer("pointer_move",px,py);move_drag();}
                optical_dirty=true;
                TRACKMOUSEEVENT track{sizeof(track),TME_LEAVE,hwnd,0};TrackMouseEvent(&track);
                if(dragging&&pressed>=0&&spec.controls[pressed].kind=="slider")activate(pressed,px,py);
                int target=dragging&&pressed>=0&&spec.controls[pressed].kind=="canvas"?pressed:hovered;
                if(target>=0&&spec.controls[target].enabled&&spec.controls[target].kind=="canvas")canvas_event("mouse_move",target,px,py,w);
                return 0;
            }
            case WM_MOUSELEAVE:
                control_pointer("pointer_leave",hovered,hover_x,hover_y);
                if(hovered>=0&&spec.controls[hovered].enabled&&spec.controls[hovered].kind=="canvas")canvas_event("mouse_leave",hovered,hover_x,hover_y);
                hovered=-1;hover_x=hover_y=0;dirty=true;return 0;
            case WM_LBUTTONDOWN:
            case WM_LBUTTONDBLCLK:
                pressed=hit(px,py);control_pointer("pointer_down",pressed,px,py,w);focus_control(interactive(pressed)?pressed:-1);
                SetFocus(hwnd);host->select(spec.id);dragging=true;SetCapture(hwnd);
                if(py>=0&&py<38&&px>width-46&&px<width){dragging=false;close_pressed=true;return 0;}
                if(pressed<0&&py<96)pointer("pointer_down",px,py);
                if(pressed>=0&&spec.controls[pressed].enabled&&spec.controls[pressed].kind=="canvas")canvas_event("mouse_down",pressed,px,py,w,"left",msg==WM_LBUTTONDBLCLK?2:1);
                else if(pressed>=0&&spec.controls[pressed].kind=="slider")activate(pressed,px,py);
                dirty=true;return 0;
            case WM_LBUTTONUP: {
                if(close_pressed) {
                    close_pressed=false;pressed=-1;dragging=false;ReleaseCapture();
                    if(py>=0&&py<38&&px>width-46&&px<width)host->emit({"card_close_request",spec.id});
                    return 0;
                }
                // ReleaseCapture synchronously sends WM_CAPTURECHANGED, which
                // clears pressed. Save the click before releasing capture.
                if(pointer_sequence){pointer("pointer_up",px,py);stop_drag(false);}
                control_pointer("pointer_up",pressed,px,py,w);
                int clicked=pressed;pressed=-1;dragging=false;ReleaseCapture();
                if(clicked>=0 && spec.controls[clicked].kind=="canvas"&&spec.controls[clicked].enabled)canvas_event("mouse_up",clicked,px,py,w);
                else if(clicked==hit(px,py)&&clicked>=0&&spec.controls[clicked].kind!="slider")activate(clicked,px,py);
                dirty=true;return 0;
            }
            case WM_CAPTURECHANGED:
                close_pressed=false;
                control_pointer("pointer_cancel",pressed,hover_x,hover_y);
                if(pointer_sequence){host->emit({"pointer_cancel",spec.id});stop_drag(true);}
                if(pressed>=0&&spec.controls[pressed].kind=="canvas")host->emit({"mouse_cancel",spec.id,spec.controls[pressed].id});
                dragging=false;pressed=-1;return 0;
            case WM_RBUTTONDOWN:case WM_MBUTTONDOWN:case WM_RBUTTONUP:case WM_MBUTTONUP: {
                bool down=msg==WM_RBUTTONDOWN||msg==WM_MBUTTONDOWN;
                int at=!down&&pressed>=0?pressed:hit(px,py);if(at<0||!spec.controls[at].enabled||spec.controls[at].kind!="canvas")return 0;
                if(down){focus_control(at);SetFocus(hwnd);host->select(spec.id);pressed=at;dragging=true;SetCapture(hwnd);}
                canvas_event(down?"mouse_down":"mouse_up",at,px,py,w,(msg==WM_RBUTTONDOWN||msg==WM_RBUTTONUP)?"right":"middle");
                if(!down&&!(w&(MK_LBUTTON|MK_RBUTTON|MK_MBUTTON))){pressed=-1;dragging=false;ReleaseCapture();}return 0;
            }
            case WM_MOUSEWHEEL:case WM_MOUSEHWHEEL:
                if(hovered>=0&&spec.controls[hovered].kind=="canvas") {
                    if(spec.controls[hovered].enabled){POINT point{GET_X_LPARAM(l),GET_Y_LPARAM(l)};ScreenToClient(hwnd,&point);
                        canvas_event(msg==WM_MOUSEHWHEEL?"wheel_horizontal":"wheel",hovered,point.x/host->dpi,point.y/host->dpi,w,"left",1,GET_WHEEL_DELTA_WPARAM(w)/120.);}
                }
                else host->cycle(GET_WHEEL_DELTA_WPARAM(w)>0?-1:1);
                return 0;
            case WM_KEYDOWN: {
                if(w==VK_ESCAPE){if(moving){stop_drag(true);dragging=false;ReleaseCapture();return 0;}
                    if(interactive(focused)&&spec.controls[focused].kind=="canvas"&&spec.controls[focused].capture_escape){key_event("key_down",w,l);return 0;}
                    SetForegroundWindow(host->owner);host->emit({"escape",spec.id});return 0;}
                if(w=='Q'&&(GetKeyState(VK_CONTROL)&0x8000)){host->emit({"close_request",spec.id});return 0;}
                if(w==VK_F6){SetForegroundWindow(host->owner);return 0;}
                if(w==VK_TAB) {
                    int direction=(GetKeyState(VK_SHIFT)&0x8000)?-1:1;
                    if(GetKeyState(VK_CONTROL)&0x8000){host->cycle(direction);return 0;}
                    tab(direction);return 0;
                }
                if(focused>=0&&focused<static_cast<int>(spec.controls.size())) {
                    auto &c=spec.controls[focused];
                    if(!c.enabled)return 0;
                    if(c.kind=="canvas") {key_event("key_down",w,l);return 0;}
                    if(dispatch_input(focused,{"key",0,0,width-40,row_height(c),static_cast<unsigned>(w)}))return 0;
                    if(c.kind=="input") {
                        if(w=='A'&&(GetKeyState(VK_CONTROL)&0x8000)){input_selected=true;dirty=true;return 0;}
                        if(w=='V'&&(GetKeyState(VK_CONTROL)&0x8000)&&OpenClipboard(hwnd)) {
                            auto memory=GetClipboardData(CF_UNICODETEXT);auto ptr=memory?static_cast<const wchar_t *>(GlobalLock(memory)):nullptr;
                            if(ptr){auto text=utf16(c.text);if(input_selected)text.clear();
                                std::size_t length=0;while(ptr[length]&&length<2048)length++;
                                text.append(ptr,std::min(length,2048-text.size()));c.text=utf8(text);input_selected=false;dirty=true;change(c);GlobalUnlock(memory);}
                            CloseClipboard();return 0;
                        }
                        if(w==VK_DELETE&&input_selected){c.text.clear();input_selected=false;dirty=true;change(c);return 0;}
                        if(w==VK_RETURN)host->emit({"submit",spec.id,c.id,c.text});
                        return 0;
                    }
                    if(w==VK_RETURN||w==VK_SPACE){activate(focused,28+static_cast<float>(c.value)*(width-56),0);return 0;}
                }
                if(w==VK_LEFT||w==VK_RIGHT){host->cycle(w==VK_LEFT?-1:1);return 0;}
                if(w==VK_UP||w==VK_DOWN){if(focused<0)host->cycle(w==VK_UP?-1:1);else tab(w==VK_UP?-1:1);return 0;}
                return 0;
            }
            case WM_KEYUP:
                if(interactive(focused)&&spec.controls[focused].kind=="canvas"&&w!=VK_TAB&&w!=VK_F6)key_event("key_up",w,l);
                return 0;
            case WM_CHAR:
                if(interactive(focused)&&spec.controls[focused].kind=="canvas") {
                    if(w>=0xd800&&w<=0xdbff){surrogate=static_cast<wchar_t>(w);return 0;}
                    if(w>=32){std::wstring value;if(surrogate&&w>=0xdc00&&w<=0xdfff)value.push_back(surrogate);value.push_back(static_cast<wchar_t>(w));
                        GlassEvent event{"text",spec.id,spec.controls[focused].id,utf8(value)};event.source_name=spec.controls[focused].canvas.source.name;host->emit(std::move(event));}
                    surrogate=0;return 0;
                }
                if(focused>=0&&spec.controls[focused].kind=="input"&&spec.controls[focused].enabled) {
                    auto &c=spec.controls[focused];auto text=utf16(c.text);
                    if(w>=0xd800&&w<=0xdbff){surrogate=static_cast<wchar_t>(w);return 0;}
                    if(w<32&&w!=8)return 0;
                    if(input_selected){text.clear();input_selected=false;}
                    if(w==8&&!text.empty()) {wchar_t end=text.back();text.pop_back();if(end>=0xdc00&&end<=0xdfff&&!text.empty())text.pop_back();}
                    else if(w>=32&&text.size()<2047){if(surrogate&&w>=0xdc00&&w<=0xdfff)text.push_back(surrogate);text.push_back(static_cast<wchar_t>(w));}
                    surrogate=0;
                    c.text=utf8(text);dirty=true;change(c);
                }
                return 0;
            case WM_CLOSE:host->emit({"card_close_request",spec.id});return 0;
            }
            return DefWindowProcW(hwnd,msg,w,l);
        }
        void draw() {
            auto f=ui->begin();if(!f)return;
            auto &p=*ui;bool selected=host->scene.selected==spec.id;
            bool solid=actual_material=="solid";
            float alpha=solid?1:actual_material=="mica"?.06f:0;
            // Match the system-rounded surface: a larger ink radius would
            // expose an untinted Acrylic wedge outside the self-drawn rim.
            float radius=background.corner_radius;
            p.glyph_shadow=!solid;
            p.fill(D2D1::RectF(0,0,width,height),solid?(background.kind=="solid"?background.tint:0x192130):0x121a2a,alpha,radius);
            // Local readability treatment; the card centre stays clear.
            if(!solid){p.fill(D2D1::RectF(18,40,width-18,95),0x08101b,.18f,14);}
            // Wide grazing highlight, concentrated rim and a quiet accent pool.
            D2D1_GRADIENT_STOP stops[]={{0,color(0xffffff,.065f)},{.4f,color(0xffffff,0)},{1,color(0xffffff,.018f)}};
            ComPtr<ID2D1GradientStopCollection> collection;
            check(p.dc->CreateGradientStopCollection(stops,3,&collection),"Glass highlight stops");
            ComPtr<ID2D1LinearGradientBrush> gradient;
            check(p.dc->CreateLinearGradientBrush(D2D1::LinearGradientBrushProperties(D2D1::Point2F(0,0),D2D1::Point2F(width*.6f,height)),collection.Get(),&gradient),"Glass highlight");
            p.dc->FillRoundedRectangle(D2D1::RoundedRect(D2D1::RectF(1,1,width-1,height-1),radius-1,radius-1),gradient.Get());
            p.brush->SetColor(color(0xffffff,selected?.42f:.24f));
            p.dc->DrawRoundedRectangle(D2D1::RoundedRect(D2D1::RectF(.8f,.8f,width-.8f,height-.8f),radius-.8f,radius-.8f),p.brush.Get(),1);
            if(hover_x>0) {
                p.brush->SetColor(color(host->scene.accent,.17f));
                p.dc->DrawLine(D2D1::Point2F(std::max(16.f,hover_x-42),1.7f),D2D1::Point2F(std::min(width-16,hover_x+42),1.7f),p.brush.Get(),2.2f);
            }
            p.fill(D2D1::RectF(24,23,29,28),host->scene.accent,1,2.5f);
            p.text(spec.eyebrow.empty()?"COMFORTABLE / WORKSPACE":spec.eyebrow,38,18,width-92,10,0xa8bacd,1,true);
            p.text("×",width-36,14,20,19,0xb7c7da);
            p.text(spec.title,24,41,width-48,23,0xf1f7ff,1,true);
            p.text(spec.subtitle,24,74,width-48,11,0xa0b1c8);
            float row=102;
            for(std::size_t i=0;i<spec.controls.size();i++) {
                const auto &c=spec.controls[i];float h=row_height(c);
                bool over=hovered==static_cast<int>(i), focus=focused==static_cast<int>(i)&&GetFocus()==hwnd;
                float a=(c.enabled&&group_enabled?1.f:.42f)*c.opacity;
                auto box=D2D1::RectF(20,row+2,width-20,row+h-6);
                if(over||focus)p.fill(box,host->scene.accent,(pressed==static_cast<int>(i)?.19f:.09f)*a,10);
                if(focus){p.brush->SetColor(color(host->scene.accent,.7f));p.dc->DrawRoundedRectangle(D2D1::RoundedRect(box,10,10),p.brush.Get(),1);}
                ControlPainter painter(p,20,row);
                GlassControlPaint state{width-40,h,host->dpi,a,host->scene.accent,over,focus,pressed==static_cast<int>(i),input_selected,
                    host->scene.reduced_motion||!host->system_animation};
                // Keep extension ink local: visual overdraw never changes hit/semantics.
                p.dc->PushAxisAlignedClip(D2D1::RectF(20,row,width-20,row+h),D2D1_ANTIALIAS_MODE_PER_PRIMITIVE);
                glass_control_type(c).paint(painter,c,state);
                p.dc->PopAxisAlignedClip();
                row+=h;
            }
            p.fill(D2D1::RectF(24,height-12,width-24,height-11),host->scene.accent,selected?.28f:.08f);
            ui->end(f);dirty=false;
            std::lock_guard<std::mutex> lock(host->mutex);host->report.frames++;
        }
    };
    std::string layout_root(const Card &card) const {
        auto root=card.spec.id,parent=card.spec.group;
        while(!parent.empty()) {
            auto group=std::find_if(scene.groups.begin(),scene.groups.end(),[&](const auto &g){return g.id==parent;});
            if(group==scene.groups.end())break;
            root=group->id;parent=group->parent;
        }
        return root;
    }
    void selection_layout(const std::string &previous,const std::string &selected) {
        if(scene.layout=="ribbon")retain_positions=false;
        else if(scene.layout=="focus"&&retain_positions) {
            std::vector<std::string> roots;
            for(const auto &c:cards)if(c->spec.id==previous||c->spec.id==selected)roots.push_back(layout_root(*c));
            if(roots.size()==2&&roots.front()==roots.back())return;
            for(auto &c:cards)if(std::find(roots.begin(),roots.end(),layout_root(*c))!=roots.end())c->layout_positioned=false;
        }
    }
    void select(const std::string &id) {
        if(scene.selected==id)return;
        auto target=std::find_if(cards.begin(),cards.end(),[&](const auto &c){return c->spec.id==id;});
        if(target==cards.end()||!(*target)->group_visible||!(*target)->group_enabled)return;
        auto previous=scene.selected;scene.selected=id;
        selection_layout(previous,id);
        for(auto &c:cards)if(c->spec.id==previous||c->spec.id==id)c->dirty=true;
        emit({"selected",id});
    }
    void focus_card(Card &card,int entry=-1) {
        int at=-1;
        if(entry==-2)for(int i=static_cast<int>(card.spec.controls.size())-1;i>=0;i--)if(card.interactive(i)){at=i;break;}
        card.focus_control(at);select(card.spec.id);
        SetForegroundWindow(card.hwnd);SetFocus(card.hwnd);
    }
    void cycle(int delta,int entry=-1) {
        if(cards.empty())return;
        auto found=std::find_if(cards.begin(),cards.end(),[&](const auto &c){return c->spec.id==scene.selected;});
        int at=found==cards.end()?0:static_cast<int>(found-cards.begin());
        for(std::size_t n=1;n<=cards.size();n++) {
            auto index=(at+delta*static_cast<int>(n)+static_cast<int>(cards.size()))%static_cast<int>(cards.size());
            if(cards[index]->shown&&cards[index]->group_visible&&cards[index]->group_enabled){focus_card(*cards[index],entry);return;}
        }
    }
    void dismiss() {
        scene.visible=false;
        update_background(false);SetForegroundWindow(owner);emit({"dismissed"});
    }
    void follow_owner() {
        RECT old=owner_rect;bounds();
        int dx=owner_rect.left-old.left,dy=owner_rect.top-old.top;
        if(!dx&&!dy)return;
        for(auto &c:cards) {
            c->x.x+=dx;c->x.target+=dx;c->y.x+=dy;c->y.target+=dy;
            c->native_x=static_cast<int>(std::round(c->x.x));c->native_y=static_cast<int>(std::round(c->y.x));
            SetWindowPos(c->hwnd,nullptr,c->native_x,c->native_y,0,0,
                         SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE|SWP_NOOWNERZORDER);
        }
    }
    static void CALLBACK location(HWINEVENTHOOK,DWORD,HWND hwnd,LONG object,LONG child,DWORD,DWORD) {
        if(current&&hwnd==current->owner&&object==OBJID_WINDOW&&child==CHILDID_SELF)current->follow_owner();
    }
    // Removing nodes leaves gaps. Content/value updates are not layout edits.
    // Compare the surviving topology so nested containers obey the same rule.
    static bool compatible_layout(const GlassScene &before,const GlassScene &after) {
        if(before.layout!=after.layout)return false;
        auto live=[&](const std::string &id){
            return std::any_of(after.cards.begin(),after.cards.end(),[&](const auto &c){return c.id==id;})||
                   std::any_of(after.groups.begin(),after.groups.end(),[&](const auto &g){return g.id==id;});
        };
        auto filtered=[&](const std::vector<std::string> &ids){
            std::vector<std::string> result;for(const auto &id:ids)if(live(id))result.push_back(id);return result;
        };
        if(filtered(before.roots)!=after.roots)return false;
        std::vector<std::string> old_ids,new_ids;
        for(const auto &c:before.cards)if(live(c.id))old_ids.push_back(c.id);
        for(const auto &c:after.cards) {
            new_ids.push_back(c.id);
            auto old=std::find_if(before.cards.begin(),before.cards.end(),[&](const auto &v){return v.id==c.id;});
            if(old==before.cards.end()||old->group!=c.group||old->width!=c.width||card_height(*old)!=card_height(c))return false;
        }
        if(old_ids!=new_ids)return false;
        for(const auto &g:after.groups) {
            auto old=std::find_if(before.groups.begin(),before.groups.end(),[&](const auto &v){return v.id==g.id;});
            if(old==before.groups.end()||old->parent!=g.parent||old->layout!=g.layout||old->gap!=g.gap||
               old->padding!=g.padding||filtered(old->children)!=g.children)return false;
        }
        return true;
    }
    void apply(GlassScene next,std::uint64_t serial) {
        if(scene.backdrop!=next.backdrop||(!scene.visible&&next.visible))backdrop_dismissed=false;
        bool reflow=next.layout!=scene.layout;
        if(!optics) {
            try{optics=std::make_unique<glass_detail::OpticalScene>(owner);optical_monitor=MonitorFromWindow(owner,MONITOR_DEFAULTTONEAREST);}
            catch(const std::exception &e){std::lock_guard<std::mutex> lock(mutex);report.optics_error=e.what();}
        }
        float scale=GetDpiForWindow(owner)/96.f;
        if(std::abs(scale-dpi)>.01f) {cards.clear();dpi=scale;retain_positions=false;}
        double bytes=0;
        for(auto &c:next.cards) {
            c.width=std::min(c.width,(work.right-work.left-24*dpi)/dpi);
            if(c.width<180||card_height(c)*dpi>work.bottom-work.top-24*dpi)
                throw std::invalid_argument("Glass card is too tall for this monitor; use fewer controls or shorter cards");
            bytes+=c.width*card_height(c)*dpi*dpi*4*5;
        }
        if(bytes>256*1024*1024)throw std::invalid_argument("Glass UI GPU surface budget exceeds 256 MiB");
        bool compatible=compatible_layout(scene,next);
        bool removing=compatible&&next.cards.size()<scene.cards.size();
        auto previous_selected=scene.selected;
        if(!compatible)retain_positions=false;
        else if(removing)retain_positions=true;
        scene=std::move(next);
        for(auto it=cards.begin();it!=cards.end();) {
            if(std::none_of(scene.cards.begin(),scene.cards.end(),[&](const auto &c){return c.id==(*it)->spec.id;}))it=cards.erase(it);
            else ++it;
        }
        for(const auto &spec:scene.cards) {
            auto it=std::find_if(cards.begin(),cards.end(),[&](const auto &c){return c->spec.id==spec.id;});
            std::string signature=source_signature(spec);
            if(it!=cards.end() && ((*it)->width!=spec.width||(*it)->height!=card_height(spec))) {
                it=cards.erase(it);it=cards.end();
            }
            if(it==cards.end())cards.push_back(std::make_unique<Card>(this,spec));
            else {
                auto &card=**it;
                auto id=[&](int at){return at>=0&&at<static_cast<int>(card.spec.controls.size())?card.spec.controls[at].id:"";};
                auto focus_id=id(card.focused),hover_id=id(card.hovered),pressed_id=id(card.pressed);
                auto old_controls=card.spec.controls;
                card.spec=spec;card.focused=-1;card.hovered=-1;card.pressed=-1;
                for(auto &control:card.spec.controls) {
                    auto old=std::find_if(old_controls.begin(),old_controls.end(),[&](const auto &v){return v.id==control.id&&v.kind==control.kind&&v.generation==control.generation;});
                    if(old!=old_controls.end()) {
                        control.edit_serial=old->edit_serial;
                        if(control.acknowledged<old->edit_serial&&(control.kind!="choice"||control.options==old->options)) {
                            control.value=old->value;control.text=old->text;
                        }
                    }
                }
                for(std::size_t i=0;i<spec.controls.size();i++) {
                    if(spec.controls[i].id==focus_id&&std::any_of(old_controls.begin(),old_controls.end(),[&](const auto &old){return old.id==focus_id&&old.generation==spec.controls[i].generation;}))card.focused=static_cast<int>(i);
                    if(spec.controls[i].id==hover_id&&std::any_of(old_controls.begin(),old_controls.end(),[&](const auto &old){return old.id==hover_id&&old.generation==spec.controls[i].generation;}))card.hovered=static_cast<int>(i);
                    if(spec.controls[i].id==pressed_id&&std::any_of(old_controls.begin(),old_controls.end(),[&](const auto &old){return old.id==pressed_id&&old.generation==spec.controls[i].generation;}))card.pressed=static_cast<int>(i);
                }
                if(card.focused>=0&&!card.interactive(card.focused))card.focus_control(-1);
                card.dirty=true;card.apply_material();
                if(reflow)card.pinned=false;
                if(!card.spec.draggable&&card.dragging){card.stop_drag(true);card.dragging=false;if(GetCapture()==card.hwnd)ReleaseCapture();}
                signature+=card.optical?":optical":"";
                if(signature!=card.canvas_signature)card.configure_canvas();
            }
        }
        std::vector<std::unique_ptr<Card>> ordered;
        for(const auto &spec:scene.cards) {
            auto it=std::find_if(cards.begin(),cards.end(),[&](const auto &c){return c&&c->spec.id==spec.id;});
            ordered.push_back(std::move(*it));
        }
        cards=std::move(ordered);
        if(std::none_of(cards.begin(),cards.end(),[&](const auto &c){return c->spec.id==scene.selected;})&&!cards.empty())scene.selected=cards.front()->spec.id;
        if(!removing&&previous_selected!=scene.selected)selection_layout(previous_selected,scene.selected);
        applied=serial;
        emit({"scene_applied","","",std::to_string(serial)});
    }
    struct Unit {
        float width{},height{};
        std::vector<std::pair<Card *,D2D1_POINT_2F>> cards;
        std::string root;
    };
    Unit measure(const std::string &id,bool visible=true,bool enabled=true) {
        auto leaf=std::find_if(cards.begin(),cards.end(),[&](const auto &c){return c->spec.id==id;});
        if(leaf!=cards.end()) {
            auto &c=**leaf;c.group_visible=visible;
            if(c.group_enabled!=enabled){
                if(!enabled&&c.dragging){c.stop_drag(true);c.dragging=false;if(GetCapture()==c.hwnd)ReleaseCapture();}
                c.group_enabled=enabled;EnableWindow(c.hwnd,enabled);c.dirty=true;
            }
            return {c.width,c.height,{{&c,D2D1::Point2F(0,0)}},id};
        }
        auto at=std::find_if(scene.groups.begin(),scene.groups.end(),[&](const auto &g){return g.id==id;});
        if(at==scene.groups.end())return {};
        const auto &group=*at;Unit result;float x=group.padding,y=group.padding,line_height=0;
        std::vector<Unit> children;
        float available=(work.right-work.left)/dpi-24;
        for(const auto &child:group.children) {
            auto u=measure(child,visible&&group.visible,enabled&&group.enabled);
            if(group.layout=="row"&&x>group.padding&&x+u.width+group.padding>available){x=group.padding;y+=line_height+group.gap;line_height=0;}
            for(auto entry:u.cards){entry.second.x+=x;entry.second.y+=y;result.cards.push_back(entry);}
            result.width=std::max(result.width,x+u.width+group.padding);result.height=std::max(result.height,y+u.height+group.padding);
            if(group.layout=="row"){x+=u.width+group.gap;line_height=std::max(line_height,u.height);}
            else if(group.layout=="column")y+=u.height+group.gap;
            children.push_back(std::move(u));
        }
        float ah=(work.bottom-work.top)/dpi-24;
        if((result.width>available||result.height>ah)&&!children.empty()) {
            result={};float mw=0,mh=0;for(const auto &u:children){mw=std::max(mw,u.width);mh=std::max(mh,u.height);}
            float count=static_cast<float>(std::max<std::size_t>(1,children.size()-1));
            float dx=std::clamp((available-mw)/count,0.f,16.f),dy=std::clamp((ah-mh)/count,0.f,24.f);
            for(std::size_t i=0;i<children.size();i++) {
                for(auto entry:children[i].cards){entry.second.x+=i*dx;entry.second.y+=i*dy;result.cards.push_back(entry);}
                result.width=std::max(result.width,children[i].width+i*dx);result.height=std::max(result.height,children[i].height+i*dy);
            }
        }
        result.root=id;return result;
    }
    void layout(double dt,bool show) {
        bounds();
        LONG ow=owner_rect.right-owner_rect.left,oh=owner_rect.bottom-owner_rect.top;
        if(ow!=layout_owner_width||oh!=layout_owner_height||!EqualRect(&work,&layout_work))retain_positions=false;
        layout_owner_width=ow;layout_owner_height=oh;layout_work=work;
        std::vector<Unit> units;
        if(scene.roots.empty()){for(const auto &c:cards)if(c->spec.group.empty())units.push_back(measure(c->spec.id));}
        else for(const auto &id:scene.roots)units.push_back(measure(id));
        std::map<Card *,std::pair<std::size_t,D2D1_POINT_2F>> slots;
        for(std::size_t i=0;i<units.size();i++)for(auto entry:units[i].cards)slots[entry.first]={i,entry.second};
        float gap=18*dpi;
        float maxw=0;for(const auto &u:units)maxw=std::max(maxw,u.width*dpi);
        compact=owner_rect.right+maxw+gap>work.right && owner_rect.left-maxw-gap<work.left;
        // Compact orbit uses fixed deck slots; explicit focus/ribbon remain
        // opt-in selection-driven transitions, bounded by the work area.
        std::string mode=compact&&scene.layout=="orbit"?"deck":scene.layout;
        int selected=0;for(const auto &c:cards)if(c->spec.id==scene.selected&&slots.count(c.get()))selected=static_cast<int>(slots[c.get()].first);
        bool reduced=scene.reduced_motion||!system_animation;
        bool visibility_changed=false;
        for(std::size_t i=0;i<cards.size();i++) {
            auto &c=*cards[i];float cw=c.width*dpi,ch=c.height*dpi;
            auto slot=slots[&c];auto unit=slot.first<units.size()?&units[slot.first]:nullptr;
            float uw=unit?unit->width*dpi:cw,uh=unit?unit->height*dpi:ch;
            std::size_t index=slot.first;
            float tx=0,ty=0;
            if(mode=="orbit") {
                bool right=(index%2)==0;
                if(owner_rect.left-uw-gap<work.left)right=true;
                if(owner_rect.right+uw+gap>work.right)right=false;
                tx=right?owner_rect.right+gap:owner_rect.left-uw-gap;
                ty=index<2?owner_rect.top-64*dpi:owner_rect.bottom-110*dpi+(static_cast<float>(index/2)-1)*36*dpi;
            } else if(mode=="ribbon") {
                bool right=owner_rect.right+maxw+gap<=work.right;
                tx=right?owner_rect.right+gap:owner_rect.left-uw-gap;
                ty=(work.top+work.bottom)/2.f-uh/2+(static_cast<int>(index)-selected)*64*dpi;
            } else if(mode=="focus") {
                bool focal=static_cast<int>(index)==selected;
                tx=focal?(owner_rect.left+owner_rect.right)/2.f-uw/2:owner_rect.right-uw+index*16*dpi;
                ty=owner_rect.top+(focal?64:36+index*28)*dpi;
            } else {
                tx=compact?owner_rect.right-uw-24*dpi:owner_rect.right+gap;
                tx+=index*15*dpi;ty=owner_rect.top+(66+index*24)*dpi;
            }
            tx=std::clamp(tx,work.left+12*dpi,std::max(work.left+12*dpi,work.right-uw-12*dpi))+slot.second.x*dpi;
            ty=std::clamp(ty,work.top+12*dpi,std::max(work.top+12*dpi,work.bottom-uh-12*dpi))+slot.second.y*dpi;
            // Retain only the live card's owner-relative layout anchor, not a manual
            // drag pin. Owner translation still follows natively; explicit
            // reflow, geometry edits and resize can lay out the cards again.
            if(retain_positions&&c.layout_positioned) {
                tx=owner_rect.left+c.layout_offset_x;ty=owner_rect.top+c.layout_offset_y;
            }
            c.layout_offset_x=tx-owner_rect.left;c.layout_offset_y=ty-owner_rect.top;c.layout_positioned=true;
            if(c.pinned){tx=static_cast<float>(owner_rect.left+c.offset_x);ty=static_cast<float>(owner_rect.top+c.offset_y);}
            tx=std::clamp(tx,work.left+4*dpi,std::max(work.left+4*dpi,work.right-cw-4*dpi));
            ty=std::clamp(ty,work.top+4*dpi,std::max(work.top+4*dpi,work.bottom-ch-4*dpi));
            bool card_show=show&&c.group_visible;
            if(!card_show) {
                if(c.dragging){c.stop_drag(true);c.dragging=false;if(GetCapture()==c.hwnd)ReleaseCapture();}
                tx=(owner_rect.left+owner_rect.right)/2.f-cw/2;ty=owner_rect.top+24*dpi;
            }
            c.x.target=tx;c.y.target=ty;
            bool motion=c.x.step(dt,reduced)|c.y.step(dt,reduced);
            // Keep a visible card moving back to its origin during dismissal.
            if(card_show||c.shown) {
                int nx=static_cast<int>(std::round(c.x.x)),ny=static_cast<int>(std::round(c.y.x));
                if(nx!=c.native_x||ny!=c.native_y) {
                    SetWindowPos(c.hwnd,nullptr,nx,ny,0,0,SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE|SWP_NOOWNERZORDER);
                    c.native_x=nx;c.native_y=ny;
                }
            }
            if(card_show&&!c.shown){ShowWindow(c.hwnd,SW_SHOWNOACTIVATE);c.compositor->visible(true);c.shown=true;c.dirty=true;visibility_changed=true;}
            else if(!card_show&&c.shown && (!motion || !c.group_visible || !scene.pane_visible || !active || IsIconic(owner))) {
                ShowWindow(c.hwnd,SW_HIDE);c.compositor->visible(false);c.shown=false;
            }
            if(c.shown&&c.dirty)c.draw();
            if(c.shown&&c.optical&&optics) {
                RECT rect{c.native_x,c.native_y,c.native_x+static_cast<LONG>(std::round(cw)),c.native_y+static_cast<LONG>(std::round(ch))};
                optics->render(*c.optical,rect,c.background,dpi,c.hover_x,c.hover_y,c.moving,c.optical_dirty);c.optical_dirty=false;
            }
        }
        if(show&&!cards.empty()) {
            if(inspectable_windows&&active) {
                HWND foreground=GetForegroundWindow();
                if(foreground!=last_group_foreground&&std::any_of(cards.begin(),cards.end(),[&](const auto &c){return c->hwnd==foreground;})) {
                    SetWindowPos(owner,foreground,0,0,0,0,SWP_NOSIZE|SWP_NOMOVE|SWP_NOACTIVATE|SWP_NOOWNERZORDER);
                    visibility_changed=true;
                }
                last_group_foreground=foreground;
            }
            // Keep normal-band owner ordering. No globally topmost windows.
            HWND above=GetWindow(owner,GW_HWNDPREV);
            for(int i=0;i<64&&above;i++) {
                bool ours=std::any_of(cards.begin(),cards.end(),[&](const auto &c){return c->hwnd==above;});
                if(!ours)break;
                above=GetWindow(above,GW_HWNDPREV);
            }
            // Promote the selected container/card while preserving every
            // other sibling's relative order, including inside old groups.
            auto promote=[](std::vector<std::string> &stack,const std::vector<std::string> &ids,const std::string &active_id) {
                stack.erase(std::remove_if(stack.begin(),stack.end(),[&](const auto &id){return std::find(ids.begin(),ids.end(),id)==ids.end();}),stack.end());
                for(const auto &id:ids)if(std::find(stack.begin(),stack.end(),id)==stack.end())stack.push_back(id);
                auto at=std::find(stack.begin(),stack.end(),active_id);
                if(at!=stack.end())std::rotate(stack.begin(),at,at+1);
            };
            std::vector<std::string> root_ids,card_ids;
            for(const auto &u:units)root_ids.push_back(u.root);
            for(const auto &c:cards)card_ids.push_back(c->spec.id);
            promote(root_stack,root_ids,units.empty()?"":units[selected].root);
            promote(card_stack,card_ids,scene.selected);
            std::vector<Card *> order;
            std::vector<std::string> order_ids;
            for(const auto &root:root_stack) {
                auto unit=std::find_if(units.begin(),units.end(),[&](const auto &u){return u.root==root;});
                for(const auto &id:card_stack)for(auto entry:unit->cards)if(entry.first->spec.id==id){order.push_back(entry.first);order_ids.push_back(id);}
            }
            if(above==last_external&&order_ids==last_stack&&!visibility_changed)return;
            last_external=above;last_stack=std::move(order_ids);
            // hWndInsertAfter inserts below its argument: visit front to back.
            for(auto card:order) {
                auto h=card->hwnd;
                if(h==above)above=GetWindow(h,GW_HWNDPREV);
                if(above&&(GetWindowLongPtrW(above,GWL_EXSTYLE)&WS_EX_TOPMOST))above=HWND_TOP;
                SetWindowPos(h,above,0,0,0,0,SWP_NOSIZE|SWP_NOMOVE|SWP_NOACTIVATE|SWP_NOOWNERZORDER);
                above=h;
            }
        }
    }
    void run() {
        HWINEVENTHOOK hook{};
        try {
            current=this;thread_id=GetCurrentThreadId();
            SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
            CoInitializeEx(nullptr,COINIT_APARTMENTTHREADED);
            WNDCLASSW wc{};wc.style=CS_DBLCLKS;wc.hInstance=GetModuleHandleW(nullptr);wc.hCursor=LoadCursorW(nullptr,MAKEINTRESOURCEW(32512));
            wc.lpfnWndProc=Card::proc;wc.lpszClassName=L"ComfortableGlassCard";RegisterClassW(&wc);
            wc.lpfnWndProc=background_proc;wc.lpszClassName=L"ComfortableGlassBackdrop";RegisterClassW(&wc);
            check(D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED,IID_PPV_ARGS(&factory)),"Glass D2D factory");
            check(DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED,__uuidof(IDWriteFactory),reinterpret_cast<IUnknown **>(write.GetAddressOf())),"Glass DirectWrite");
            HIGHCONTRASTW hc{sizeof(hc)};SystemParametersInfoW(SPI_GETHIGHCONTRAST,sizeof(hc),&hc,0);contrast=(hc.dwFlags&HCF_HIGHCONTRASTON)!=0;
            BOOL animation=TRUE;SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION,0,&animation,0);system_animation=animation!=FALSE;
            bounds();dpi=GetDpiForWindow(owner)/96.f;
            DWORD pid{};GetWindowThreadProcessId(owner,&pid);
            hook=SetWinEventHook(EVENT_OBJECT_LOCATIONCHANGE,EVENT_OBJECT_LOCATIONCHANGE,nullptr,location,pid,0,WINEVENT_OUTOFCONTEXT);
            MSG msg{};PeekMessageW(&msg,nullptr,0,0,PM_NOREMOVE);
            {std::lock_guard<std::mutex> lock(mutex);ready=true;}started.notify_all();
            Handle timer(CreateWaitableTimerExW(nullptr,nullptr,2,TIMER_ALL_ACCESS));
            if(!timer.value)timer.value=CreateWaitableTimerW(nullptr,FALSE,nullptr);
            if(!timer.value)throw std::runtime_error("Cannot create glass animation timer");
            double last=clock_ticks()/clock_frequency(),deadline=last;
            double metrics_time=last;
            std::string last_background;
            bool last_active=false;
            for(;;) {
                while(PeekMessageW(&msg,nullptr,0,0,PM_REMOVE)){TranslateMessage(&msg);DispatchMessageW(&msg);}
                GlassScene next;std::uint64_t serial{};std::deque<Action> actions;std::vector<Presentation> deltas;
                {std::lock_guard<std::mutex> lock(mutex);if(stopping)break;
                    if(submitted>applied){next=requested;serial=submitted;}actions.swap(commands);deltas.swap(presentations);}
                if(!IsWindow(owner))break;
                if(optics&&MonitorFromWindow(owner,MONITOR_DEFAULTTONEAREST)!=optical_monitor) {
                    cards.clear();retain_positions=false;background_compositor.reset();background_optical.reset();background_ui.reset();
                    ui_devices.clear();
                    if(background){DestroyWindow(background);background=nullptr;}optics.reset();last_background.clear();
                    bounds();
                    if(!serial){next=scene;serial=applied;}
                }
                if(serial)apply(std::move(next),serial);
                for(const auto &delta:deltas)for(auto &card:cards)if(card->spec.id==delta.card)for(auto &control:card->spec.controls)
                    if(control.id==delta.control&&control.generation==delta.generation&&control.opacity!=delta.opacity){control.opacity=delta.opacity;card->dirty=true;}
                for(const auto &a:actions) {
                    if(a.kind=="next")cycle(1);else if(a.kind=="previous")cycle(-1);
                    else if(a.kind=="next_control"||a.kind=="previous_control") {
                        for(auto &c:cards)if(c->spec.id==scene.selected&&c->shown&&c->group_enabled) {
                            if(GetFocus()!=c->hwnd){SetForegroundWindow(c->hwnd);SetFocus(c->hwnd);}
                            c->tab(a.kind=="next_control"?1:-1);
                            break;
                        }
                    }
                    else if(a.kind=="select")select(a.id);
                    else if(a.kind=="focus_terminal")SetForegroundWindow(owner);
                    else if(a.kind=="focus_card"){for(auto &c:cards)if(c->spec.id==scene.selected&&c->shown&&c->group_enabled)focus_card(*c);}
                    else if(a.kind=="dismiss")dismiss();
                    else if(a.kind=="reflow"){retain_positions=false;for(auto &c:cards)c->pinned=false;}
                    else if(a.kind=="begin_drag"){for(auto &c:cards)if(c->spec.id==a.id)c->begin_drag(a.token);}
                }
                double now=clock_ticks()/clock_frequency();
                if(now>=deadline||serial) {
                    double start=now;active=group_active();
                    DWORD cloaked{};DwmGetWindowAttribute(owner,14,&cloaked,sizeof(cloaked));
                    bool visible=scene.visible&&scene.pane_visible&&IsWindowVisible(owner)&&!IsIconic(owner)&&!cloaked;
                    if(!active&&last_active)backdrop_dismissed=false;
                    bounds();
                    bool sample=visible&&sampling_allowed&&(std::any_of(cards.begin(),cards.end(),[](const auto &c){return c->optical&&c->group_visible;})||
                        (!cards.empty()&&active&&scene.backdrop=="frosted"&&!backdrop_dismissed));
                    if(optics)optics->update(sample);
                    if(optics) {
                        auto state=optics->stats();bool optical_ready=state.active&&state.image_ready;
                        for(auto &card:cards)if(card->optical) {
                            auto actual=optical_ready?card->background.kind:"solid";
                            if(actual!=card->actual_material){card->actual_material=actual;card->dirty=true;}
                        }
                    }
                    std::string bg=visible&&active&&!cards.empty()&&!backdrop_dismissed?scene.backdrop:"none";
                    bg+=std::to_string(work.left)+","+std::to_string(work.top)+","+std::to_string(work.right)+","+std::to_string(work.bottom)+(scene.reduced_transparency?"opaque":"");
                    if(optics&&optics->stats().image_ready)bg+="ready";
                    if(bg!=last_background){update_background(visible&&active);last_background=bg;}
                    if(active!=last_active){emit({"activation","","",active?"active":"inactive"});last_active=active;}
                    layout(std::min(.1,std::max(0.,now-last)),visible);last=now;
                    if(optics&&background_optical&&background&&IsWindowVisible(background)&&scene.backdrop=="frosted") {
                        GlassBackground frosted;frosted.kind="frosted";frosted.blur=22;frosted.opacity=.12f;frosted.corner_radius=0;
                        optics->render(*background_optical,work,frosted,1,0,0,false);
                    }
                    if(now-metrics_time>=.5) {
                        std::uint64_t presents=0,frames=0;
                        for(const auto &c:cards){auto stats=c->compositor->metrics();if(!stats.error.empty())throw std::runtime_error(stats.error);
                            presents+=stats.presents;for(const auto &v:stats.views)if(v.id==2)frames+=v.frames;}
                        std::lock_guard<std::mutex> lock(mutex);report.presents=presents;report.canvas_frames=frames;metrics_time=now;
                    }
                    auto owner_dpi=GetDpiForWindow(owner);
                    if(owner_dpi&&owner_dpi/96.f!=dpi&&applied)apply(scene,applied);
                    {
                        std::lock_guard<std::mutex> lock(mutex);
                        report.applied=applied;report.active=active;report.compact=compact;report.high_contrast=contrast;
                        if(!cards.empty()){effective_material=cards.front()->actual_material;for(const auto &c:cards)if(c->actual_material!=effective_material){effective_material="mixed";break;}}
                        report.system_animation=system_animation;report.material=effective_material;
                        report.backdrop_visible=background&&IsWindowVisible(background);
                        report.windows.clear();report.ids.clear();report.rects.clear();report.materials.clear();report.pinned.clear();report.draggable.clear();report.visible.clear();report.enabled.clear();report.focused_control.clear();
                        for(const auto &c:cards){RECT r{};GetWindowRect(c->hwnd,&r);report.windows.push_back(reinterpret_cast<std::uintptr_t>(c->hwnd));report.ids.push_back(c->spec.id);report.rects.push_back({static_cast<float>(r.left),static_cast<float>(r.top),static_cast<float>(r.right-r.left),static_cast<float>(r.bottom-r.top)});
                            report.materials.push_back(c->actual_material);report.pinned.push_back(c->pinned);report.draggable.push_back(c->spec.draggable);
                            report.visible.push_back(c->shown);report.enabled.push_back(c->group_enabled);
                            report.focused_control.push_back(GetFocus()==c->hwnd&&c->focused>=0?c->spec.controls[c->focused].id:"");}
                        if(optics){auto s=optics->stats();report.capture_active=s.active;report.capture_frames=s.captures;report.optical_frames=s.rendered;report.copied_pixels=s.copied_pixels;
                            report.unchanged_capture_frames=s.unchanged;
                            report.optics_error=sampling_allowed?s.error:"DDA-only self-exclusion unavailable; optical sampling disabled";}
                        report.tick_ms=(clock_ticks()/clock_frequency()-start)*1000;
                    }
                    deadline=now+1./60;
                }
                LARGE_INTEGER due{};due.QuadPart=-std::max<LONGLONG>(1,static_cast<LONGLONG>((deadline-clock_ticks()/clock_frequency())*10000000));
                SetWaitableTimer(timer.value,&due,0,nullptr,nullptr,FALSE);
                HANDLE waits[]={changed.value,timer.value};
                MsgWaitForMultipleObjectsEx(2,waits,INFINITE,QS_ALLINPUT,MWMO_INPUTAVAILABLE);
            }
        } catch(const std::exception &e) {
            // Destruction may race a queued resize/scene. A vanished owner is
            // normal teardown, not a failed attempt to create a new card.
            std::lock_guard<std::mutex> lock(mutex);if(IsWindow(owner))report.error=e.what();ready=true;started.notify_all();
        }
        if(hook)UnhookWinEvent(hook);
        auto close_start=clock_ticks();
        close_join_ms=close_surfaces_ms=close_windows_ms=0;
        // Make the entire workspace disappear before GPU/COM teardown, and
        // wake every compositor before joining any of them. Sequential close
        // would otherwise accumulate a device/DComp teardown per visible card.
        auto batch=BeginDeferWindowPos(static_cast<int>(cards.size())+(background?1:0));
        constexpr UINT hide_flags=SWP_HIDEWINDOW|SWP_NOMOVE|SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE|SWP_NOOWNERZORDER;
        for(auto &card:cards)if(batch&&card->hwnd)batch=DeferWindowPos(batch,card->hwnd,nullptr,0,0,0,0,hide_flags);
        if(batch&&background)batch=DeferWindowPos(batch,background,nullptr,0,0,0,0,hide_flags);
        if(!batch||!EndDeferWindowPos(batch)) {
            for(auto &card:cards)if(card->hwnd)ShowWindow(card->hwnd,SW_HIDE);
            if(background)ShowWindow(background,SW_HIDE);
        }
        auto hidden=clock_ticks();
        for(auto &card:cards)if(card->compositor)card->compositor->request_close();
        if(background_compositor)background_compositor->request_close();
        cards.clear();background_compositor.reset();background_optical.reset();background_ui.reset();optics.reset();ui_devices.clear();if(background)DestroyWindow(background);
        auto released=clock_ticks();
        {std::lock_guard<std::mutex> lock(mutex);stopping=true;report.closed=true;report.active=false;report.backdrop_visible=false;
            report.close_hide_ms=(hidden-close_start)*1000/clock_frequency();
            report.close_join_ms=close_join_ms;report.close_surfaces_ms=close_surfaces_ms;report.close_windows_ms=close_windows_ms;
            report.close_resources_ms=(released-hidden)*1000/clock_frequency();
            report.windows.clear();report.rects.clear();report.ids.clear();report.materials.clear();report.pinned.clear();report.draggable.clear();report.visible.clear();report.enabled.clear();report.focused_control.clear();report.capture_active=false;}
        current=nullptr;CoUninitialize();SetEvent(finished.value);
    }
};
thread_local GlassHost::Impl *GlassHost::Impl::current=nullptr;
GlassHost::GlassHost(std::uintptr_t owner,bool inspectable):impl_(std::make_unique<Impl>(owner,inspectable)){}
GlassHost::~GlassHost(){close();}
std::uint64_t GlassHost::configure(GlassScene scene) {
    static const std::set<std::string> layouts{"orbit","deck","ribbon","focus"},materials{"glass","liquid","frosted","mica","solid","transparent"},backdrops{"none","dim","frosted","mica"};
    if(scene.cards.size()>12||scene.accent>0xffffff||!layouts.count(scene.layout)||!materials.count(scene.material)||!backdrops.count(scene.backdrop))throw std::invalid_argument("invalid glass scene or resource limit");
    std::set<std::string> ids,channels;
    for(auto &card:scene.cards) {
        for(auto &control:card.controls)control.type=&glass_control_type(control.kind);
        if(card.id.empty()||card.id.size()>128||!ids.insert(card.id).second||card.controls.size()>32||
           !std::isfinite(card.width)||card.width<220||card.width>1600||card_height(card)>1200||
           card.title.size()>4096||card.subtitle.size()>4096||card.eyebrow.size()>256)throw std::invalid_argument("invalid glass card");
        std::set<std::string> controls;int canvases=0;
        const auto &b=card.background;
        if(card.has_background&&(!materials.count(b.kind)||b.kind=="glass"||b.tint>0xffffff||
            !std::isfinite(b.opacity)||b.opacity<0||b.opacity>1||!std::isfinite(b.blur)||b.blur<0||b.blur>40||
            !std::isfinite(b.refraction)||b.refraction<0||b.refraction>48||!std::isfinite(b.dispersion)||b.dispersion<0||b.dispersion>3||
            !std::isfinite(b.corner_radius)||b.corner_radius<0||b.corner_radius>64))throw std::invalid_argument("invalid card background");
        for(const auto &c:card.controls) {
            if(c.id.empty()||c.id.size()>128||!controls.insert(c.id).second||c.text.size()>8192||c.label.size()>4096||
               !std::isfinite(c.opacity)||c.opacity<0||c.opacity>1||!std::isfinite(c.value)||c.series.size()>256||c.options.size()>16||!std::isfinite(c.canvas_height)||c.canvas_height<16||c.canvas_height>1024)throw std::invalid_argument("invalid glass control");
            if((c.kind=="slider"||c.kind=="progress"||c.kind=="toggle")&&(c.value<0||c.value>1))throw std::invalid_argument("control value outside 0..1");
            if(c.kind=="choice"&&(c.options.empty()||c.value<0||c.value>=c.options.size()||std::floor(c.value)!=c.value))throw std::invalid_argument("invalid glass choice");
            if(c.kind=="input"&&utf16(c.text).size()>2048)throw std::invalid_argument("input text too long");
            for(double v:c.series)if(!std::isfinite(v)||v<0||v>1)throw std::invalid_argument("invalid sparkline samples");
            for(const auto &s:c.options)if(s.size()>1024)throw std::invalid_argument("choice text too long");
            if(c.kind=="canvas") {
                if(c.opacity!=1)throw std::invalid_argument("Canvas opacity requires compositor effects");
                if(++canvases>1||!channels.insert(c.canvas.source.name).second)throw std::invalid_argument("one canvas per card and one card per channel required");
                if(c.fit!="contain"&&c.fit!="cover"&&c.fit!="stretch"&&c.fit!="pixel")throw std::invalid_argument("invalid canvas fit");
                if(!c.canvas.source.width||!c.canvas.source.height||c.canvas.source.width>16384||c.canvas.source.height>16384)throw std::invalid_argument("invalid canvas dimensions");
            }
        }
    }
    if(scene.groups.size()>24)throw std::invalid_argument("too many card groups");
    for(const auto &g:scene.groups) {
        if(g.id.empty()||g.id.size()>128||!ids.insert(g.id).second||g.children.size()>36||
            (g.layout!="row"&&g.layout!="column"&&g.layout!="stack")||!std::isfinite(g.gap)||g.gap<0||g.gap>128||
            !std::isfinite(g.padding)||g.padding<0||g.padding>128)throw std::invalid_argument("invalid card group");
    }
    if(scene.roots.empty()) {
        for(const auto &c:scene.cards)if(c.group.empty())scene.roots.push_back(c.id);
        for(const auto &g:scene.groups)if(g.parent.empty())scene.roots.push_back(g.id);
    }
    std::set<std::string> visited;
    std::function<void(const std::string &,const std::string &,int)> visit;
    visit=[&](const std::string &id,const std::string &parent,int depth) {
        if(depth>8||!ids.count(id)||!visited.insert(id).second)throw std::invalid_argument("invalid card group tree");
        auto card=std::find_if(scene.cards.begin(),scene.cards.end(),[&](const auto &c){return c.id==id;});
        if(card!=scene.cards.end()){if(card->group!=parent)throw std::invalid_argument("card group parent mismatch");return;}
        auto group=std::find_if(scene.groups.begin(),scene.groups.end(),[&](const auto &g){return g.id==id;});
        if(group->parent!=parent)throw std::invalid_argument("group parent mismatch");
        for(const auto &child:group->children)visit(child,id,depth+1);
    };
    for(const auto &id:scene.roots)visit(id,"",0);
    if(visited.size()!=ids.size())throw std::invalid_argument("unreachable card/group node");
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if(impl_->stopping||!impl_->report.error.empty())throw std::runtime_error("GlassHost is closed or failed");
    impl_->requested=std::move(scene);auto serial=++impl_->submitted;SetEvent(impl_->changed.value);return serial;
}
void GlassHost::command(const std::string &action,const std::string &value) {
    static const std::set<std::string> allowed{"next","previous","next_control","previous_control","select","focus_terminal","focus_card","dismiss","reflow"};
    if(!allowed.count(action)||value.size()>128)throw std::invalid_argument("invalid glass command");
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if(impl_->stopping)throw std::runtime_error("GlassHost is closed");
    if(impl_->commands.size()>=64)throw std::runtime_error("glass command queue full");
    impl_->commands.push_back({action,value});SetEvent(impl_->changed.value);
}
void GlassHost::begin_drag(const std::string &card,std::uint64_t sequence) {
    if(card.empty()||card.size()>128||!sequence)throw std::invalid_argument("invalid card pointer handshake");
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if(impl_->stopping)throw std::runtime_error("GlassHost is closed");
    if(impl_->commands.size()>=64)throw std::runtime_error("glass command queue full");
    impl_->commands.push_back({"begin_drag",card,sequence});SetEvent(impl_->changed.value);
}
void GlassHost::present(const std::string &card,const std::string &control,std::uint64_t generation,float opacity) {
    if(card.empty()||card.size()>128||control.empty()||control.size()>128||!generation||!std::isfinite(opacity)||opacity<0||opacity>1)
        throw std::invalid_argument("invalid Glass presentation delta");
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if(impl_->stopping)throw std::runtime_error("GlassHost is closed");
    for(const auto &c:impl_->requested.cards)if(c.id==card)for(const auto &v:c.controls)if(v.id==control) {
        if(v.kind=="canvas")throw std::invalid_argument("Canvas opacity requires compositor effects; not available in control ink deltas");
        if(v.generation!=generation)return;
        for(auto &pending:impl_->presentations)if(pending.card==card&&pending.control==control){pending.generation=generation;pending.opacity=opacity;SetEvent(impl_->changed.value);return;}
        if(impl_->presentations.size()>=384)throw std::runtime_error("Glass presentation queue full");
        impl_->presentations.push_back({card,control,generation,opacity});SetEvent(impl_->changed.value);return;
    }
}
std::vector<GlassEvent> GlassHost::events(){std::lock_guard<std::mutex> lock(impl_->mutex);std::vector<GlassEvent> r(impl_->output.begin(),impl_->output.end());impl_->output.clear();return r;}
GlassMetrics GlassHost::metrics()const{std::lock_guard<std::mutex> lock(impl_->mutex);return impl_->report;}
void GlassHost::close(){if(impl_)impl_->close();}
} // namespace comfortable
