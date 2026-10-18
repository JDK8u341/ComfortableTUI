#include "comfortable/glass.hpp"
#include "../canvas/internal.hpp"
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
float row_height(const std::string &kind) {
    if (kind == "canvas") return 128;
    if (kind == "sparkline") return 84;
    if (kind == "slider" || kind == "progress") return 58;
    if (kind == "text") return 40;
    return 46;
}
#include "accessibility.hpp"
float card_height(const GlassCard &c) {
    float h = 102;
    for (const auto &v : c.controls) h += row_height(v.kind);
    return h + 20;
}
std::string source_signature(const GlassCard &card) {
    std::string signature;float row=102;
    for(const auto &c:card.controls) {
        if(c.kind=="canvas") signature=c.canvas.source.name+":"+std::to_string(c.canvas.source.width)+":"+
            std::to_string(c.canvas.source.height)+":"+std::to_string(c.canvas.stride)+":"+c.canvas.pixel_format+
            ":"+c.fit+":"+std::to_string(row);
        row+=row_height(c.kind);
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
    UiSurface(unsigned w, unsigned h, ID2D1Factory1 *factory, IDWriteFactory *fonts, float scale,
              std::uint32_t low = 0, std::int32_t high = 0)
        : UiSurface(w, h, factory, fonts, scale, create_device(low, high)) {}
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
        dc->DrawTextLayout(D2D1::Point2F(x,y),text_cache.at(key).Get(),brush.Get(),D2D1_DRAW_TEXT_OPTIONS_CLIP);
    }
};
} // namespace

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
    std::deque<std::pair<std::string,std::string>> commands;
    std::deque<GlassEvent> output;
    GlassMetrics report;
    std::vector<std::unique_ptr<Card>> cards;
    ComPtr<ID2D1Factory1> factory;
    ComPtr<IDWriteFactory> write;
    RECT owner_rect{}, work{};
    float dpi{1};
    bool active{}, compact{}, contrast{}, system_animation{true}, backdrop_dismissed{};
    HWND last_external{}, last_group_foreground{};
    std::string last_selected;
    std::string effective_material;
    HWND background{};
    std::unique_ptr<UiSurface> background_ui;
    std::unique_ptr<Compositor> background_compositor;
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
        if (e.kind=="mouse_move" && !output.empty() && output.back().kind==e.kind &&
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
        DWORD kind=style=="mica"?2:style=="solid"?1:3;
        return SUCCEEDED(DwmSetWindowAttribute(hwnd,38,&kind,sizeof(kind))) && style!="solid";
    }
    void update_background(bool visible) {
        if (!visible || scene.backdrop=="none" || backdrop_dismissed) {
            if (background) ShowWindow(background,SW_HIDE);
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
            background_ui=std::make_unique<UiSurface>(1u,1u,factory.Get(),write.Get(),1.f);
            auto d=background_ui->provider.descriptor();
            background_compositor=std::make_unique<Compositor>(reinterpret_cast<std::uintptr_t>(background),d.adapter_low,d.adapter_high);
        }
        bool frosted=material(background,contrast||scene.backdrop=="dim"?"solid":scene.backdrop);
        auto f=background_ui->begin();
        if (f) {background_ui->dc->Clear(D2D1::ColorF(.025f,.035f,.06f,frosted?.34f:.82f));background_ui->end(f);}
        RECT r{};GetWindowRect(background,&r);
        if (r.left!=work.left||r.top!=work.top||r.right!=work.right||r.bottom!=work.bottom) {
            SetWindowPos(background,owner,work.left,work.top,work.right-work.left,work.bottom-work.top,
                         SWP_NOACTIVATE|SWP_NOOWNERZORDER);
            NativeView view;view.id=1;view.source=background_ui->provider.descriptor();view.fit="stretch";
            view.full=view.clip={0,0,static_cast<float>(work.right-work.left),static_cast<float>(work.bottom-work.top)};
            background_compositor->configure({view},work.right-work.left,work.bottom-work.top);
        }
        // Background is independent (owned windows must always be ABOVE owner).
        SetWindowPos(background,owner,0,0,0,0,SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE|SWP_SHOWWINDOW);
        background_compositor->visible(true);
    }
    struct Card {
        Impl *host;
        GlassCard spec;
        HWND hwnd{};
        std::unique_ptr<UiSurface> ui;
        std::unique_ptr<Compositor> compositor;
        Spring x,y;
        float width{},height{}, hover_x{},hover_y{}, reveal{};
        int hovered{-1}, focused{-1}, pressed{-1};
        bool dirty{true}, dragging{}, shown{}, native_material{};
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
            try {
                std::uint32_t low=0;std::int32_t high=0;
                for (const auto &c:s.controls) if(c.kind=="canvas"&&c.canvas.gpu) {
                    low=c.canvas.source.adapter_low;high=c.canvas.source.adapter_high;
                }
                ui=std::make_unique<UiSurface>(static_cast<unsigned>(std::round(width*h->dpi)),
                    static_cast<unsigned>(std::round(height*h->dpi)),h->factory.Get(),h->write.Get(),h->dpi,low,high);
                auto d=ui->provider.descriptor();
                compositor=std::make_unique<Compositor>(reinterpret_cast<std::uintptr_t>(hwnd),d.adapter_low,d.adapter_high);
                configure_canvas();
                x.x=x.target=(h->owner_rect.left+h->owner_rect.right)/2.f-width*h->dpi/2;
                y.x=y.target=h->owner_rect.top+42*h->dpi;
                apply_material();
                host->emit({"ready",spec.id,"",std::to_string(reinterpret_cast<std::uintptr_t>(hwnd))});
            } catch (...) {DestroyWindow(hwnd);hwnd=nullptr;throw;}
        }
        ~Card() {
            *accessibility=nullptr;
            if (compositor) compositor->close();
            ui.reset();
            if (hwnd) DestroyWindow(hwnd);
        }
        void apply_material() {
            std::string key=host->scene.material+(host->contrast?"hc":"")+(host->scene.reduced_transparency?"opaque":"");
            if(key==material_key)return;
            material_key=key;
            native_material=host->material(hwnd,host->contrast||host->scene.reduced_transparency?"solid":host->scene.material);
            host->effective_material=native_material?host->scene.material:"solid";
            dirty=true;
        }
        void configure_canvas() {
            NativeView ink;ink.id=1;ink.source=ui->provider.descriptor();ink.fit="stretch";
            ink.full=ink.clip={0,0,width*host->dpi,height*host->dpi};
            std::vector<NativeView> views{ink};
            float row=102;
            canvas_signature.clear();
            for (const auto &c:spec.controls) {
                if (c.kind=="canvas") {
                    auto v=c.canvas;v.id=2;v.fit=c.fit;
                    v.full=v.clip={24*host->dpi,(row+22)*host->dpi,(width-48)*host->dpi,96*host->dpi};
                    views.push_back(v);
                    host->emit({"transform",spec.id,c.id,"",host->dpi,width-48,96});
                }
                row+=row_height(c.kind);
            }
            compositor->configure(views,ink.source.width,ink.source.height);
            canvas_signature=source_signature(spec);
        }
        int hit(float px,float py) {
            if (px<20||px>width-20) return -1;
            float row=102;
            for (std::size_t i=0;i<spec.controls.size();i++) {
                if(py>=row&&py<row+row_height(spec.controls[i].kind)) return static_cast<int>(i);
                row+=row_height(spec.controls[i].kind);
            }
            return -1;
        }
        float row_top(int index) {
            float row=102;
            for(int i=0;i<index;i++) row+=row_height(spec.controls[i].kind);
            return row;
        }
        void canvas_event(const std::string &kind,int at,float px,float py) {
            auto &c=spec.controls[at];
            double w=width-48,h=96,sw=c.canvas.source.width,sh=c.canvas.source.height;
            double sx=w/sw,sy=h/sh,ox=0,oy=0;
            if(c.fit!="stretch") {
                double scale=c.fit=="pixel"?1/host->dpi:c.fit=="cover"?std::max(sx,sy):std::min(sx,sy);
                sx=sy=scale;ox=(w-sw*scale)/2;oy=(h-sh*scale)/2;
            }
            host->emit({kind,spec.id,c.id,"",0,(px-24-ox)/sx,(py-row_top(at)-22-oy)/sy});
        }
        void activate(int at,float px=0,float py=0) {
            if(at<0||at>=static_cast<int>(spec.controls.size())) return;
            auto &c=spec.controls[at];if(!c.enabled)return;
            focused=at;dirty=true;
            NotifyWinEvent(EVENT_OBJECT_FOCUS,hwnd,OBJID_CLIENT,at+1);
            if(c.kind=="button")host->emit({"click",spec.id,c.id});
            else if(c.kind=="toggle") {c.value=c.value>=.5?0:1;change(c);}
            else if(c.kind=="slider") {
                c.value=std::clamp((px-28)/(width-56),0.f,1.f);
                change(c);
            } else if(c.kind=="choice" && !c.options.empty()) {
                c.value=std::fmod(c.value+1,static_cast<double>(c.options.size()));
                change(c);
            } else if(c.kind=="canvas") canvas_event("mouse_down",at,px,py);
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
            switch(msg) {
            case WM_GETOBJECT:
                if(static_cast<LONG>(l)==OBJID_CLIENT){auto object=new GlassAccessibility<Card>(accessibility);auto result=LresultFromObject(IID_IAccessible,w,object);object->Release();return result;}
                break;
            case WM_ERASEBKGND:return 1;
            case WM_PAINT:{PAINTSTRUCT p;BeginPaint(hwnd,&p);EndPaint(hwnd,&p);return 0;}
            case WM_SETFOCUS:
                host->select(spec.id);host->emit({"focus",spec.id});dirty=true;return 0;
            case WM_KILLFOCUS:dirty=true;return 0;
            case WM_MOUSEMOVE: {
                if(hover_x==px&&hover_y==py)return 0;
                hover_x=px;hover_y=py;hovered=hit(px,py);dirty=true;
                TRACKMOUSEEVENT track{sizeof(track),TME_LEAVE,hwnd,0};TrackMouseEvent(&track);
                if(dragging&&pressed>=0&&spec.controls[pressed].kind=="slider")activate(pressed,px,py);
                if(hovered>=0&&spec.controls[hovered].enabled&&spec.controls[hovered].kind=="canvas")canvas_event("mouse_move",hovered,px,py);
                return 0;
            }
            case WM_MOUSELEAVE:hovered=-1;hover_x=hover_y=0;dirty=true;return 0;
            case WM_LBUTTONDOWN:
                SetFocus(hwnd);host->select(spec.id);pressed=hit(px,py);dragging=true;SetCapture(hwnd);
                if(py<38 && px>width-46){host->dismiss();return 0;}
                if(pressed>=0&&(spec.controls[pressed].kind=="slider"||spec.controls[pressed].kind=="canvas"))activate(pressed,px,py);
                dirty=true;return 0;
            case WM_LBUTTONUP: {
                // ReleaseCapture synchronously sends WM_CAPTURECHANGED, which
                // clears pressed. Save the click before releasing capture.
                int clicked=pressed;pressed=-1;dragging=false;ReleaseCapture();
                if(clicked>=0 && spec.controls[clicked].kind=="canvas"&&spec.controls[clicked].enabled)canvas_event("mouse_up",clicked,px,py);
                else if(clicked==hit(px,py)&&clicked>=0&&spec.controls[clicked].kind!="slider")activate(clicked,px,py);
                dirty=true;return 0;
            }
            case WM_CAPTURECHANGED:dragging=false;pressed=-1;return 0;
            case WM_MOUSEWHEEL:
                if(hovered>=0&&spec.controls[hovered].kind=="canvas") {
                    if(spec.controls[hovered].enabled)host->emit({"wheel",spec.id,spec.controls[hovered].id,"",GET_WHEEL_DELTA_WPARAM(w)/120.});
                }
                else host->cycle(GET_WHEEL_DELTA_WPARAM(w)>0?-1:1);
                return 0;
            case WM_KEYDOWN: {
                if(w==VK_ESCAPE){SetForegroundWindow(host->owner);host->emit({"escape",spec.id});return 0;}
                if(w=='Q'&&(GetKeyState(VK_CONTROL)&0x8000)){host->emit({"close_request",spec.id});return 0;}
                if(w==VK_TAB) {
                    int direction=(GetKeyState(VK_SHIFT)&0x8000)?-1:1;
                    if(GetKeyState(VK_CONTROL)&0x8000){host->cycle(direction);return 0;}
                    int n=static_cast<int>(spec.controls.size());
                    if(focused<0)focused=direction>0?-1:0;
                    for(int i=0;i<n;i++) {
                        focused=(focused+direction+n)%n;auto &c=spec.controls[focused];
                        if(c.enabled&&(c.kind=="button"||c.kind=="toggle"||c.kind=="slider"||c.kind=="input"||c.kind=="choice"||c.kind=="canvas"))break;
                    }
                    dirty=true;return 0;
                }
                if(focused>=0&&focused<static_cast<int>(spec.controls.size())) {
                    auto &c=spec.controls[focused];
                    if(!c.enabled)return 0;
                    if(c.kind=="canvas") {host->emit({"key_down",spec.id,c.id,std::to_string(w)});return 0;}
                    if(c.kind=="slider"&&(w==VK_LEFT||w==VK_RIGHT)) {
                        c.value=std::clamp(c.value+(w==VK_RIGHT?.02:-.02),0.,1.);dirty=true;
                        change(c);return 0;
                    }
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
                if(w==VK_F6){SetForegroundWindow(host->owner);return 0;}
                return 0;
            }
            case WM_KEYUP:
                if(focused>=0&&spec.controls[focused].enabled&&spec.controls[focused].kind=="canvas")host->emit({"key_up",spec.id,spec.controls[focused].id,std::to_string(w)});
                return 0;
            case WM_CHAR:
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
            case WM_CLOSE:host->dismiss();return 0;
            }
            return DefWindowProcW(hwnd,msg,w,l);
        }
        void draw() {
            auto f=ui->begin();if(!f)return;
            auto &p=*ui;bool selected=host->scene.selected==spec.id;
            bool solid=!native_material||host->contrast||host->scene.reduced_transparency;
            float alpha=solid?1:host->scene.material=="frosted"?.88f:.68f;
            // Match the system-rounded surface: a larger ink radius would
            // expose an untinted Acrylic wedge outside the self-drawn rim.
            constexpr float radius=8;
            p.fill(D2D1::RectF(0,0,width,height),0x121a2a,alpha,radius);
            // Wide grazing highlight, concentrated rim and a quiet accent pool.
            D2D1_GRADIENT_STOP stops[]={{0,color(0xffffff,.14f)},{.4f,color(0xffffff,.025f)},{1,color(0x86a3e9,.03f)}};
            ComPtr<ID2D1GradientStopCollection> collection;
            check(p.dc->CreateGradientStopCollection(stops,3,&collection),"Glass highlight stops");
            ComPtr<ID2D1LinearGradientBrush> gradient;
            check(p.dc->CreateLinearGradientBrush(D2D1::LinearGradientBrushProperties(D2D1::Point2F(0,0),D2D1::Point2F(width*.6f,height)),collection.Get(),&gradient),"Glass highlight");
            p.dc->FillRoundedRectangle(D2D1::RoundedRect(D2D1::RectF(1,1,width-1,height-1),radius-1,radius-1),gradient.Get());
            p.brush->SetColor(color(0xffffff,selected?.38f:.17f));
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
                const auto &c=spec.controls[i];float h=row_height(c.kind);
                bool over=hovered==static_cast<int>(i), focus=focused==static_cast<int>(i)&&GetFocus()==hwnd;
                float a=c.enabled?1.f:.42f;
                auto box=D2D1::RectF(20,row+2,width-20,row+h-6);
                if(over||focus)p.fill(box,host->scene.accent,(pressed==static_cast<int>(i)?.19f:.09f)*a,10);
                if(focus){p.brush->SetColor(color(host->scene.accent,.7f));p.dc->DrawRoundedRectangle(D2D1::RoundedRect(box,10,10),p.brush.Get(),1);}
                if(c.kind=="text") {
                    p.text(c.label,24,row+2,width-48,11,0x9db0c9,a);
                    p.text(c.text,24,row+17,width-48,14,0xe5efff,a);
                } else if(c.kind=="button") {
                    p.fill(box,host->scene.accent,(over?.24f:.14f)*a,10);
                    p.text(c.label,34,row+11,width-75,13,0xe4fff8,a,true);
                    p.text("↗",width-45,row+9,20,17,host->scene.accent,a);
                } else if(c.kind=="toggle") {
                    p.text(c.label,26,row+10,width-110,13,0xe0ebfb,a);
                    p.fill(D2D1::RectF(width-69,row+10,width-27,row+32),c.value>=.5?host->scene.accent:0x526078,.85f*a,11);
                    float knob=c.value>=.5?width-39:width-57;
                    p.brush->SetColor(color(c.value>=.5?0x12332f:0xdde8f9,a));
                    p.dc->FillEllipse(D2D1::Ellipse(D2D1::Point2F(knob,row+21),7,7),p.brush.Get());
                } else if(c.kind=="input"||c.kind=="choice") {
                    p.fill(box,0x060b14,.3f*a,10);
                    if(c.kind=="input"&&focus&&input_selected)p.fill(D2D1::RectF(28,row+9,width-28,row+32),host->scene.accent,.27f,2);
                    std::string content=c.kind=="choice"&&!c.options.empty()?c.options[static_cast<std::size_t>(c.value)]:c.text;
                    p.text(content.empty()?c.label:content,30,row+11,width-66,13,content.empty()?0x8a9cb4:0xe4efff,a);
                } else if(c.kind=="slider"||c.kind=="progress") {
                    p.text(c.label,26,row+3,width-100,12,0xb0c3da,a);
                    p.text(std::to_string(static_cast<int>(c.value*100))+"%",width-66,row+3,42,12,host->scene.accent,a,true);
                    p.fill(D2D1::RectF(28,row+32,width-28,row+36),0x60748d,.25f,2);
                    float at=28+(width-56)*static_cast<float>(c.value);
                    p.fill(D2D1::RectF(28,row+32,at,row+36),host->scene.accent,a,2);
                    if(c.kind=="slider") {p.brush->SetColor(color(0xe1fff7,a));p.dc->FillEllipse(D2D1::Ellipse(D2D1::Point2F(at,row+34),5,5),p.brush.Get());}
                } else if(c.kind=="sparkline") {
                    p.text(c.label,26,row+3,width-50,12,0xb0c3da,a);
                    p.fill(D2D1::RectF(24,row+25,width-24,row+72),0x061522,.32f,8);
                    p.brush->SetColor(color(host->scene.accent,a));
                    for(std::size_t j=1;j<c.series.size();j++) {
                        float x0=28+(width-56)*static_cast<float>(j-1)/(c.series.size()-1),x1=28+(width-56)*static_cast<float>(j)/(c.series.size()-1);
                        p.dc->DrawLine(D2D1::Point2F(x0,row+68-static_cast<float>(c.series[j-1])*36),D2D1::Point2F(x1,row+68-static_cast<float>(c.series[j])*36),p.brush.Get(),1.6f);
                    }
                } else if(c.kind=="canvas") {
                    p.text(c.label,26,row+2,width-50,11,0xb0c3da,a);
                    p.fill(D2D1::RectF(24,row+22,width-24,row+118),0x060c18,.7f,8);
                }
                row+=h;
            }
            p.fill(D2D1::RectF(24,height-12,width-24,height-11),host->scene.accent,selected?.28f:.08f);
            ui->end(f);dirty=false;
            std::lock_guard<std::mutex> lock(host->mutex);host->report.frames++;
        }
    };
    void select(const std::string &id) {
        if(scene.selected==id)return;
        scene.selected=id;
        for(auto &c:cards)c->dirty=true;
        emit({"selected",id});
    }
    void cycle(int delta) {
        if(cards.empty())return;
        auto found=std::find_if(cards.begin(),cards.end(),[&](const auto &c){return c->spec.id==scene.selected;});
        int at=found==cards.end()?0:static_cast<int>(found-cards.begin());
        select(cards[(at+delta+cards.size())%cards.size()]->spec.id);
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
    void apply(GlassScene next,std::uint64_t serial) {
        if(scene.backdrop!=next.backdrop||(!scene.visible&&next.visible))backdrop_dismissed=false;
        scene=std::move(next);
        float scale=GetDpiForWindow(owner)/96.f;
        if(std::abs(scale-dpi)>.01f) {cards.clear();dpi=scale;}
        double bytes=0;
        for(auto &c:scene.cards) {
            c.width=std::min(c.width,(work.right-work.left-24*dpi)/dpi);
            if(c.width<180||card_height(c)*dpi>work.bottom-work.top-24*dpi)
                throw std::invalid_argument("Glass card is too tall for this monitor; use fewer controls or shorter cards");
            bytes+=c.width*card_height(c)*dpi*dpi*4*5;
        }
        if(bytes>256*1024*1024)throw std::invalid_argument("Glass UI GPU surface budget exceeds 256 MiB");
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
                    auto old=std::find_if(old_controls.begin(),old_controls.end(),[&](const auto &v){return v.id==control.id&&v.kind==control.kind;});
                    if(old!=old_controls.end()) {
                        control.edit_serial=old->edit_serial;
                        if(control.acknowledged<old->edit_serial&&(control.kind!="choice"||control.options==old->options)) {
                            control.value=old->value;control.text=old->text;
                        }
                    }
                }
                for(std::size_t i=0;i<spec.controls.size();i++) {
                    if(spec.controls[i].id==focus_id)card.focused=static_cast<int>(i);
                    if(spec.controls[i].id==hover_id)card.hovered=static_cast<int>(i);
                    if(spec.controls[i].id==pressed_id)card.pressed=static_cast<int>(i);
                }
                card.dirty=true;card.apply_material();
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
        applied=serial;
        emit({"scene_applied","","",std::to_string(serial)});
    }
    void layout(double dt,bool show) {
        bounds();
        float gap=18*dpi, ww=static_cast<float>(work.right-work.left), wh=static_cast<float>(work.bottom-work.top);
        float maxw=0;for(const auto &c:cards)maxw=std::max(maxw,c->width*dpi);
        compact=owner_rect.right+maxw+gap>work.right && owner_rect.left-maxw-gap<work.left;
        std::string mode=compact?"deck":scene.layout;
        int selected=0;for(std::size_t i=0;i<cards.size();i++)if(cards[i]->spec.id==scene.selected)selected=static_cast<int>(i);
        bool reduced=scene.reduced_motion||!system_animation;
        bool visibility_changed=false;
        for(std::size_t i=0;i<cards.size();i++) {
            auto &c=*cards[i];float cw=c.width*dpi,ch=c.height*dpi;
            int relative=(static_cast<int>(i)-selected+static_cast<int>(cards.size()))%std::max(1,static_cast<int>(cards.size()));
            float tx=0,ty=0;
            if(mode=="orbit") {
                bool right=(i%2)==0;
                if(owner_rect.left-cw-gap<work.left)right=true;
                if(owner_rect.right+cw+gap>work.right)right=false;
                tx=right?owner_rect.right+gap:owner_rect.left-cw-gap;
                ty=i<2?owner_rect.top-64*dpi:owner_rect.bottom-110*dpi+(static_cast<float>(i/2)-1)*36*dpi;
            } else if(mode=="ribbon") {
                bool right=owner_rect.right+maxw+gap<=work.right;
                tx=right?owner_rect.right+gap:owner_rect.left-cw-gap;
                ty=(work.top+work.bottom)/2.f-ch/2+(static_cast<int>(i)-selected)*64*dpi;
            } else if(mode=="focus") {
                tx=relative==0?(owner_rect.left+owner_rect.right)/2.f-cw/2:owner_rect.right-cw+relative*16*dpi;
                ty=owner_rect.top+(relative==0?64:36+relative*28)*dpi;
            } else {
                tx=compact?owner_rect.right-cw-24*dpi:owner_rect.right+gap;
                tx+=relative*15*dpi;ty=owner_rect.top+(66+relative*24)*dpi;
            }
            tx=std::clamp(tx,work.left+12*dpi,std::max(work.left+12*dpi,work.right-cw-12*dpi));
            ty=std::clamp(ty,work.top+12*dpi,std::max(work.top+12*dpi,work.bottom-ch-12*dpi));
            if(!show) {
                tx=(owner_rect.left+owner_rect.right)/2.f-cw/2;ty=owner_rect.top+24*dpi;
            }
            c.x.target=tx;c.y.target=ty;
            bool motion=c.x.step(dt,reduced)|c.y.step(dt,reduced);
            // Keep a visible card moving back to its origin during dismissal.
            if(show||c.shown) {
                int nx=static_cast<int>(std::round(c.x.x)),ny=static_cast<int>(std::round(c.y.x));
                if(nx!=c.native_x||ny!=c.native_y) {
                    SetWindowPos(c.hwnd,nullptr,nx,ny,0,0,SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE|SWP_NOOWNERZORDER);
                    c.native_x=nx;c.native_y=ny;
                }
            }
            if(show&&!c.shown){ShowWindow(c.hwnd,SW_SHOWNOACTIVATE);c.compositor->visible(true);c.shown=true;c.dirty=true;visibility_changed=true;}
            else if(!show&&c.shown && (!motion || !scene.pane_visible || !active || IsIconic(owner))) {
                ShowWindow(c.hwnd,SW_HIDE);c.compositor->visible(false);c.shown=false;
            }
            if(c.shown&&c.dirty)c.draw();
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
            if(above==last_external&&last_selected==scene.selected&&!visibility_changed)return;
            last_external=above;last_selected=scene.selected;
            for(std::size_t n=cards.size();n>0;n--) {
                std::size_t index=(selected+n)%cards.size();
                auto h=cards[index]->hwnd;
                if(h==above)above=GetWindow(h,GW_HWNDPREV);
                if(above&&(GetWindowLongPtrW(above,GWL_EXSTYLE)&WS_EX_TOPMOST))above=HWND_TOP;
                SetWindowPos(h,above,0,0,0,0,SWP_NOSIZE|SWP_NOMOVE|SWP_NOACTIVATE|SWP_NOOWNERZORDER);
                above=h;
            }
        }
        (void)ww;(void)wh;
    }
    void run() {
        HWINEVENTHOOK hook{};
        try {
            current=this;thread_id=GetCurrentThreadId();
            SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
            CoInitializeEx(nullptr,COINIT_APARTMENTTHREADED);
            WNDCLASSW wc{};wc.hInstance=GetModuleHandleW(nullptr);wc.hCursor=LoadCursorW(nullptr,MAKEINTRESOURCEW(32512));
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
                GlassScene next;std::uint64_t serial{};std::deque<std::pair<std::string,std::string>> actions;
                {std::lock_guard<std::mutex> lock(mutex);if(stopping)break;
                    if(submitted>applied){next=requested;serial=submitted;}actions.swap(commands);}
                if(!IsWindow(owner))break;
                if(serial)apply(std::move(next),serial);
                for(const auto &a:actions) {
                    if(a.first=="next")cycle(1);else if(a.first=="previous")cycle(-1);
                    else if(a.first=="select")select(a.second);
                    else if(a.first=="focus_terminal")SetForegroundWindow(owner);
                    else if(a.first=="focus_card")for(auto &c:cards)if(c->spec.id==scene.selected)SetForegroundWindow(c->hwnd);
                    else if(a.first=="dismiss")dismiss();
                }
                double now=clock_ticks()/clock_frequency();
                if(now>=deadline||serial) {
                    double start=now;active=group_active();
                    DWORD cloaked{};DwmGetWindowAttribute(owner,14,&cloaked,sizeof(cloaked));
                    bool visible=scene.visible&&scene.pane_visible&&IsWindowVisible(owner)&&!IsIconic(owner)&&!cloaked;
                    if(!active&&last_active)backdrop_dismissed=false;
                    bounds();
                    std::string bg=visible&&active&&!backdrop_dismissed?scene.backdrop:"none";
                    bg+=std::to_string(work.left)+","+std::to_string(work.top)+","+std::to_string(work.right)+","+std::to_string(work.bottom);
                    if(bg!=last_background){update_background(visible&&active);last_background=bg;}
                    if(active!=last_active){emit({"activation","","",active?"active":"inactive"});last_active=active;}
                    layout(std::min(.1,std::max(0.,now-last)),visible);last=now;
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
                        report.system_animation=system_animation;report.material=effective_material;
                        report.backdrop_visible=background&&IsWindowVisible(background);
                        report.windows.clear();report.ids.clear();report.rects.clear();
                        for(const auto &c:cards){RECT r{};GetWindowRect(c->hwnd,&r);report.windows.push_back(reinterpret_cast<std::uintptr_t>(c->hwnd));report.ids.push_back(c->spec.id);report.rects.push_back({static_cast<float>(r.left),static_cast<float>(r.top),static_cast<float>(r.right-r.left),static_cast<float>(r.bottom-r.top)});}
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
        cards.clear();background_compositor.reset();background_ui.reset();if(background)DestroyWindow(background);
        {std::lock_guard<std::mutex> lock(mutex);stopping=true;report.closed=true;report.active=false;report.backdrop_visible=false;
            report.windows.clear();report.rects.clear();report.ids.clear();}
        current=nullptr;CoUninitialize();SetEvent(finished.value);
    }
};
thread_local GlassHost::Impl *GlassHost::Impl::current=nullptr;
GlassHost::GlassHost(std::uintptr_t owner,bool inspectable):impl_(std::make_unique<Impl>(owner,inspectable)){}
GlassHost::~GlassHost(){close();}
std::uint64_t GlassHost::configure(GlassScene scene) {
    static const std::set<std::string> layouts{"orbit","deck","ribbon","focus"},materials{"glass","frosted","mica","solid"},backdrops{"none","dim","frosted","mica"};
    static const std::set<std::string> kinds{"text","button","toggle","slider","input","choice","progress","sparkline","canvas"};
    if(scene.cards.size()>12||scene.accent>0xffffff||!layouts.count(scene.layout)||!materials.count(scene.material)||!backdrops.count(scene.backdrop))throw std::invalid_argument("invalid glass scene or resource limit");
    std::set<std::string> ids,channels;
    for(const auto &card:scene.cards) {
        if(card.id.empty()||card.id.size()>128||!ids.insert(card.id).second||card.controls.size()>32||
           !std::isfinite(card.width)||card.width<220||card.width>1600||card_height(card)>1200||
           card.title.size()>4096||card.subtitle.size()>4096||card.eyebrow.size()>256)throw std::invalid_argument("invalid glass card");
        std::set<std::string> controls;int canvases=0;
        for(const auto &c:card.controls) {
            if(!kinds.count(c.kind)||c.id.empty()||c.id.size()>128||!controls.insert(c.id).second||c.text.size()>8192||c.label.size()>4096||
               !std::isfinite(c.value)||c.series.size()>256||c.options.size()>16)throw std::invalid_argument("invalid glass control");
            if((c.kind=="slider"||c.kind=="progress"||c.kind=="toggle")&&(c.value<0||c.value>1))throw std::invalid_argument("control value outside 0..1");
            if(c.kind=="choice"&&(c.options.empty()||c.value<0||c.value>=c.options.size()||std::floor(c.value)!=c.value))throw std::invalid_argument("invalid glass choice");
            if(c.kind=="input"&&utf16(c.text).size()>2048)throw std::invalid_argument("input text too long");
            for(double v:c.series)if(!std::isfinite(v)||v<0||v>1)throw std::invalid_argument("invalid sparkline samples");
            for(const auto &s:c.options)if(s.size()>1024)throw std::invalid_argument("choice text too long");
            if(c.kind=="canvas") {
                if(++canvases>1||!channels.insert(c.canvas.source.name).second)throw std::invalid_argument("one canvas per card and one card per channel required");
                if(c.fit!="contain"&&c.fit!="cover"&&c.fit!="stretch"&&c.fit!="pixel")throw std::invalid_argument("invalid canvas fit");
                if(!c.canvas.source.width||!c.canvas.source.height||c.canvas.source.width>16384||c.canvas.source.height>16384)throw std::invalid_argument("invalid canvas dimensions");
            }
        }
    }
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if(impl_->stopping||!impl_->report.error.empty())throw std::runtime_error("GlassHost is closed or failed");
    impl_->requested=std::move(scene);auto serial=++impl_->submitted;SetEvent(impl_->changed.value);return serial;
}
void GlassHost::command(const std::string &action,const std::string &value) {
    static const std::set<std::string> allowed{"next","previous","select","focus_terminal","focus_card","dismiss"};
    if(!allowed.count(action)||value.size()>128)throw std::invalid_argument("invalid glass command");
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if(impl_->stopping)throw std::runtime_error("GlassHost is closed");
    if(impl_->commands.size()>=64)throw std::runtime_error("glass command queue full");
    impl_->commands.emplace_back(action,value);SetEvent(impl_->changed.value);
}
std::vector<GlassEvent> GlassHost::events(){std::lock_guard<std::mutex> lock(impl_->mutex);std::vector<GlassEvent> r(impl_->output.begin(),impl_->output.end());impl_->output.clear();return r;}
GlassMetrics GlassHost::metrics()const{std::lock_guard<std::mutex> lock(impl_->mutex);return impl_->report;}
void GlassHost::close(){if(impl_)impl_->close();}
} // namespace comfortable
