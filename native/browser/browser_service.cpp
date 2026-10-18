// Optional browser service. Profiles and business messages never enter frame
// metadata.
#include "comfortable/cpu_frame.hpp"
#include "include/cef_app.h"
#include "include/cef_browser.h"
#include "include/cef_client.h"
#include "include/cef_command_line.h"
#include "include/cef_command_handler.h"
#include "include/cef_dialog_handler.h"
#include "include/cef_download_handler.h"
#include "include/cef_jsdialog_handler.h"
#include "include/cef_id_mappers.h"
#include "include/cef_print_handler.h"
#include "include/cef_permission_handler.h"
#include "include/cef_parser.h"
#include "include/cef_request_context.h"
#include "include/cef_request_context_handler.h"
#include "include/cef_resource_request_handler.h"
#include "include/cef_version.h"
#include "include/wrapper/cef_helpers.h"
#include "include/wrapper/cef_message_router.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <filesystem>
#include <functional>
#include <iostream>
#include <limits>
#include <map>
#include <mutex>
#include <set>
#include <stdexcept>
#include <thread>
#include <vector>
#ifdef _WIN32
#include "include/cef_sandbox_win.h"
#endif

namespace {
constexpr std::size_t control_limit = 1024 * 1024;
constexpr std::size_t frame_limit = 512ull * 1024 * 1024;
using Dict = CefRefPtr<CefDictionaryValue>;
Dict dict() { return CefDictionaryValue::Create(); }
CefRange invalid_range() {
  return CefRange(std::numeric_limits<uint32_t>::max(),
                  std::numeric_limits<uint32_t>::max());
}
std::string utf8_path(const std::filesystem::path &p) {
  const auto s = p.u8string();
  return {reinterpret_cast<const char *>(s.data()), s.size()};
}
std::mutex output_mutex;
std::string json(Dict d) {
  auto v = CefValue::Create();
  v->SetDictionary(d);
  return CefWriteJSON(v, JSON_WRITER_DEFAULT).ToString();
}
Dict parse(const std::string &s) {
  auto v = CefParseJSON(s, JSON_PARSER_RFC);
  if (!v || v->GetType() != VTYPE_DICTIONARY)
    throw std::invalid_argument("Expected JSON object");
  return v->GetDictionary();
}
void emit(Dict d) {
  auto s = json(d);
  if (s.size() > control_limit) {
    auto bounded = dict();
    if (d->HasKey("id")) {
      bounded->SetInt("id", d->GetInt("id"));
      bounded->SetBool("ok", false);
      bounded->SetString("error", "Native response exceeds 1 MiB");
    } else {
      bounded->SetString("event", "security_denied");
      bounded->SetString("view", d->GetString("view"));
      bounded->SetString("reason", "event_size");
    }
    s = json(bounded);
  }
  std::lock_guard<std::mutex> lock(output_mutex);
  std::cout << s << std::endl;
}
void reply(int id, Dict result = nullptr, const std::string &error = {}) {
  auto d = dict();
  d->SetInt("id", id);
  d->SetBool("ok", error.empty());
  if (result)
    d->SetDictionary("result", result);
  if (!error.empty())
    d->SetString("error", error);
  emit(d);
}
void event(const std::string &kind, const std::string &view,
           Dict data = nullptr) {
  auto d = dict();
  d->SetString("event", kind);
  d->SetString("view", view);
  if (data)
    d->SetDictionary("data", data);
  emit(d);
}
std::string text(Dict d, const char *k) { return d->GetString(k).ToString(); }
std::set<std::string> strings(Dict d, const char *k) {
  std::set<std::string> result;
  if (auto list = d->GetList(k))
    for (std::size_t i = 0; i < list->GetSize(); ++i)
      result.insert(list->GetString(i).ToString());
  return result;
}
std::string origin(const std::string &url) {
  CefURLParts parts;
  if (!CefParseURL(url, parts))
    return {};
  auto value = CefString(&parts.origin).ToString();
  while (!value.empty() && value.back() == '/')
    value.pop_back();
  return value;
}
bool identifier(const std::string &s) {
  return !s.empty() && s.size() <= 128 &&
         s.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVW"
                             "XYZ0123456789_-.") == std::string::npos;
}
bool network_url(const std::string &url) {
  CefURLParts parts;
  if (url.size() > 8192 || url.find('\\') != std::string::npos ||
      std::any_of(url.begin(), url.end(), [](unsigned char c) { return c <= 32 || c == 127; }) ||
      !CefParseURL(url, parts)) return false;
  const auto scheme = CefString(&parts.scheme).ToString();
  return (scheme == "https" || scheme == "http") && CefString(&parts.username).empty() && CefString(&parts.password).empty();
}
std::string canonical_origin(const std::string &url, bool exact, bool insecure) {
    if (!network_url(url)) throw std::invalid_argument("Only credential-free HTTP(S) URLs are supported");
    CefURLParts parts;
    CefParseURL(url, parts);
    if (exact && ((!CefString(&parts.path).empty() && CefString(&parts.path) != "/") ||
        !CefString(&parts.query).empty() || !CefString(&parts.fragment).empty()))
      throw std::invalid_argument("Origin grants cannot contain paths, queries or fragments");
    const auto host = CefString(&parts.host).ToString();
    if (host.empty() || host.find_first_of("*%") != std::string::npos || CefString(&parts.port) == "0")
      throw std::invalid_argument("Invalid origin host or port");
    unsigned a, b, c, e; char trailing;
    const bool loopback = host == "localhost" || host == "::1" || host == "[::1]" ||
        (std::sscanf(host.c_str(), "%u.%u.%u.%u%c", &a, &b, &c, &e, &trailing) == 4 &&
         a == 127 && b <= 255 && c <= 255 && e <= 255);
    if (CefString(&parts.scheme) == "http" && !insecure && !loopback)
      throw std::invalid_argument("Cleartext HTTP requires explicit policy");
    return origin(url);
}
std::set<std::string> origin_grants(Dict d, const char *key, bool insecure) {
  std::set<std::string> result;
  auto list = d->GetList(key);
  if (d->HasKey(key) && !list) throw std::invalid_argument("Origins must be a list");
  if (list) {
    if (list->GetSize() > 64) throw std::invalid_argument("Too many origin grants");
    for (std::size_t i = 0; i < list->GetSize(); ++i) {
      if (list->GetType(i) != VTYPE_STRING) throw std::invalid_argument("Origin must be a string");
      result.insert(canonical_origin(list->GetString(i).ToString(), true, insecure));
    }
  }
  return result;
}
void set_strings(Dict d, const char *key, const std::set<std::string> &values) {
  auto list = CefListValue::Create();
  for (const auto &value : values) list->SetString(list->GetSize(), value);
  d->SetList(key, list);
}
bool bounded_line(std::istream &input, std::string &line, bool &overflow) {
  line.clear();
  overflow = false;
  for (;;) {
    const auto c = input.rdbuf()->sbumpc();
    if (c == std::char_traits<char>::eof())
      return !line.empty() || overflow;
    if (c == '\n')
      return true;
    if (line.size() < control_limit)
      line.push_back(static_cast<char>(c));
    else
      overflow = true;
  }
}
comfortable::CpuFrameInfo frame_info(Dict d) {
  if (!d)
    throw std::invalid_argument("Missing frame descriptor");
  return {text(d, "name"), static_cast<unsigned>(d->GetInt("width")),
          static_cast<unsigned>(d->GetInt("height")),
          static_cast<unsigned>(d->GetInt("stride")), text(d, "pixel_format")};
}
bool bounded_payload(CefRefPtr<CefValue> value, unsigned depth, unsigned &nodes) {
  if (!value || depth > 32 || ++nodes > 4096) return false;
  if (value->GetType() == VTYPE_DICTIONARY) {
    CefDictionaryValue::KeyList keys;
    auto d = value->GetDictionary(); d->GetKeys(keys);
    for (const auto &key : keys) if (!bounded_payload(d->GetValue(key), depth + 1, nodes)) return false;
  } else if (value->GetType() == VTYPE_LIST) {
    auto list = value->GetList();
    for (std::size_t i = 0; i < list->GetSize(); ++i)
      if (!bounded_payload(list->GetValue(i), depth + 1, nodes)) return false;
  }
  return true;
}
class FunctionTask : public CefTask {
public:
  explicit FunctionTask(std::function<void()> f) : fn_(std::move(f)) {}
  void Execute() override { fn_(); }

private:
  std::function<void()> fn_;
  IMPLEMENT_REFCOUNTING(FunctionTask);
};
void post(std::function<void()> f) {
  CefPostTask(TID_UI, new FunctionTask(std::move(f)));
}

class Service;
// Background requests have no page identity. Never borrow a page's grants for
// service workers or arbitrary CefURLRequest traffic.
class BackgroundRequests : public CefRequestContextHandler,
                           public CefResourceRequestHandler {
public:
  CefRefPtr<CefResourceRequestHandler> GetResourceRequestHandler(
      CefRefPtr<CefBrowser>, CefRefPtr<CefFrame>, CefRefPtr<CefRequest>, bool,
      bool, const CefString &, bool &) override { return this; }
  ReturnValue OnBeforeResourceLoad(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame>,
      CefRefPtr<CefRequest>, CefRefPtr<CefCallback>) override { return RV_CANCEL; }
private:
  IMPLEMENT_REFCOUNTING(BackgroundRequests);
};
class View : public CefClient,
             public CefLifeSpanHandler,
             public CefRenderHandler,
             public CefLoadHandler,
             public CefDisplayHandler,
             public CefRequestHandler,
             public CefContextMenuHandler,
             public CefPermissionHandler,
             public CefDialogHandler,
             public CefDownloadHandler,
             public CefJSDialogHandler,
             public CefCommandHandler,
             public CefPrintHandler,
             public CefResourceRequestHandler,
             public CefMessageRouterBrowserSide::Handler {
public:
  View(Service *owner, Dict config, CefRefPtr<CefRequestContext> context,
       int request);
  CefRefPtr<CefLifeSpanHandler> GetLifeSpanHandler() override { return this; }
  CefRefPtr<CefRenderHandler> GetRenderHandler() override { return this; }
  CefRefPtr<CefLoadHandler> GetLoadHandler() override { return this; }
  CefRefPtr<CefDisplayHandler> GetDisplayHandler() override { return this; }
  CefRefPtr<CefRequestHandler> GetRequestHandler() override { return this; }
  CefRefPtr<CefContextMenuHandler> GetContextMenuHandler() override {
    return this;
  }
  CefRefPtr<CefPermissionHandler> GetPermissionHandler() override { return this; }
  CefRefPtr<CefDialogHandler> GetDialogHandler() override { return this; }
  CefRefPtr<CefDownloadHandler> GetDownloadHandler() override { return this; }
  CefRefPtr<CefJSDialogHandler> GetJSDialogHandler() override { return this; }
  CefRefPtr<CefCommandHandler> GetCommandHandler() override { return this; }
  CefRefPtr<CefPrintHandler> GetPrintHandler() override { return this; }
  bool OnChromeCommand(CefRefPtr<CefBrowser>, int command, cef_window_open_disposition_t) override {
    static const std::set<int> editing = {cef_id_for_command_id_name("IDC_COPY"),
        cef_id_for_command_id_name("IDC_CUT"), cef_id_for_command_id_name("IDC_PASTE"),
        cef_id_for_command_id_name("IDC_SELECT_ALL"), cef_id_for_command_id_name("IDC_UNDO"),
        cef_id_for_command_id_name("IDC_REDO")};
    return !editing.count(command); // Chrome UI/devtools/print/save/open are not app capabilities.
  }
  void OnPrintStart(CefRefPtr<CefBrowser>) override {}
  void OnPrintSettings(CefRefPtr<CefBrowser>, CefRefPtr<CefPrintSettings>, bool) override {}
  bool OnPrintDialog(CefRefPtr<CefBrowser>, bool, CefRefPtr<CefPrintDialogCallback>) override { return false; }
  bool OnPrintJob(CefRefPtr<CefBrowser>, const CefString &, const CefString &,
                  CefRefPtr<CefPrintJobCallback>) override { return false; }
  void OnPrintReset(CefRefPtr<CefBrowser>) override {}
  bool OnRequestMediaAccessPermission(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame>,
      const CefString &, uint32_t, CefRefPtr<CefMediaAccessCallback> cb) override {
    ++denied_permissions_;
    cb->Cancel(); return true;
  }
  bool OnShowPermissionPrompt(CefRefPtr<CefBrowser>, uint64_t, const CefString &,
      uint32_t, CefRefPtr<CefPermissionPromptCallback> cb) override {
    ++denied_permissions_;
    cb->Continue(CEF_PERMISSION_RESULT_DENY); return true;
  }
  bool OnFileDialog(CefRefPtr<CefBrowser>, FileDialogMode, const CefString &,
      const CefString &, const std::vector<CefString> &, const std::vector<CefString> &,
      const std::vector<CefString> &, CefRefPtr<CefFileDialogCallback> cb) override {
    ++denied_dialogs_;
    cb->Cancel(); return true;
  }
  bool CanDownload(CefRefPtr<CefBrowser>, const CefString &, const CefString &) override {
    ++denied_downloads_; return false;
  }
  bool OnBeforeDownload(CefRefPtr<CefBrowser>, CefRefPtr<CefDownloadItem>, const CefString &,
      CefRefPtr<CefBeforeDownloadCallback>) override { return true; }
  bool OnJSDialog(CefRefPtr<CefBrowser>, const CefString &, JSDialogType,
      const CefString &, const CefString &, CefRefPtr<CefJSDialogCallback>, bool &suppress) override {
    ++denied_dialogs_;
    suppress = true; return false;
  }
  bool OnBeforeUnloadDialog(CefRefPtr<CefBrowser>, const CefString &, bool,
      CefRefPtr<CefJSDialogCallback> cb) override { cb->Continue(true, ""); return true; }
  CefRefPtr<CefResourceRequestHandler> GetResourceRequestHandler(CefRefPtr<CefBrowser>,
      CefRefPtr<CefFrame>, CefRefPtr<CefRequest>, bool, bool, const CefString &, bool &) override { return this; }
  ReturnValue OnBeforeResourceLoad(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame> frame,
      CefRefPtr<CefRequest> request, CefRefPtr<CefCallback>) override {
    if (frame && allowed(request->GetURL(), resources_)) return RV_CONTINUE;
    ++denied_resources_; return RV_CANCEL;
  }
  void OnResourceRedirect(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame>, CefRefPtr<CefRequest>,
      CefRefPtr<CefResponse>, CefString &url) override {
    if (!allowed(url, resources_)) url = "about:blank";
  }
  void OnProtocolExecution(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame>, CefRefPtr<CefRequest>,
      bool &allow_os_execution) override { allow_os_execution = false; }
  bool OnSelectClientCertificate(CefRefPtr<CefBrowser>, bool, const CefString &, int,
      const X509CertificateList &, CefRefPtr<CefSelectClientCertificateCallback> cb) override {
    cb->Select(nullptr); return true;
  }
  void GetViewRect(CefRefPtr<CefBrowser>, CefRect &r) override {
    r = CefRect(0, 0, width_, height_);
  }
  bool GetScreenInfo(CefRefPtr<CefBrowser>, CefScreenInfo &info) override {
    info.device_scale_factor = 1;
    info.rect = CefRect(0, 0, width_, height_);
    info.available_rect = info.rect;
    return true;
  }
  void OnAfterCreated(CefRefPtr<CefBrowser> b) override;
  void OnBeforeClose(CefRefPtr<CefBrowser> b) override;
  bool DoClose(CefRefPtr<CefBrowser>) override { return false; }
  bool OnBeforePopup(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame>, int,
                     const CefString &url, const CefString &,
                     cef_window_open_disposition_t, bool,
                     const CefPopupFeatures &, CefWindowInfo &,
                     CefRefPtr<CefClient> &, CefBrowserSettings &,
                     CefRefPtr<CefDictionaryValue> &, bool *) override {
    auto d = dict();
    d->SetString("url", url);
    event("new_window", id_, d);
    return true;
  }
  void OnBeforeContextMenu(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame>,
                           CefRefPtr<CefContextMenuParams>,
                           CefRefPtr<CefMenuModel> menu) override {
    menu->Clear();
  }
  void OnPaint(CefRefPtr<CefBrowser>, PaintElementType type, const RectList &,
               const void *pixels, int w, int h) override;
  void OnPopupShow(CefRefPtr<CefBrowser>, bool show) override {
    popup_visible_ = show;
    if (!show) {
      popup_.clear();
      publish();
    }
  }
  void OnPopupSize(CefRefPtr<CefBrowser>, const CefRect &rect) override {
    popup_rect_ = rect;
  }
  void OnImeCompositionRangeChanged(CefRefPtr<CefBrowser>,
                                    const CefRange &range,
                                    const RectList &bounds) override {
    auto d = dict();
    d->SetInt("selection_start", range.from);
    d->SetInt("selection_end", range.to);
    if (!bounds.empty()) {
      auto b = bounds.back();
      auto l = CefListValue::Create();
      l->SetInt(0, b.x);
      l->SetInt(1, b.y);
      l->SetInt(2, b.width);
      l->SetInt(3, b.height);
      d->SetList("cursor", l);
    }
    event("ime", id_, d);
  }
  void OnLoadEnd(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame> frame,
                 int code) override {
    if (!frame->IsMain())
      return;
    if (bootstrapping_ && frame->GetURL() == "about:blank" && text(config_, "url") != "about:blank") return;
    bootstrapping_ = false;
    auto d = dict();
    d->SetInt("status", code);
    d->SetString("url", frame->GetURL());
    event("loaded", id_, d);
  }
  void OnLoadError(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame> f, ErrorCode code,
                   const CefString &description,
                   const CefString &url) override {
    if (!f->IsMain() || code == ERR_ABORTED)
      return;
    auto d = dict();
    d->SetInt("code", code);
    d->SetString("message", description);
    d->SetString("url", url);
    event("load_error", id_, d);
  }
  void OnTitleChange(CefRefPtr<CefBrowser>, const CefString &title) override {
    auto d = dict();
    d->SetString("title", title);
    event("title", id_, d);
  }
  bool OnBeforeBrowse(CefRefPtr<CefBrowser> b, CefRefPtr<CefFrame> f,
                      CefRefPtr<CefRequest> request, bool, bool) override {
    if (!allowed(request->GetURL(), navigation_)) {
      ++denied_navigations_;
      auto d = dict(); d->SetString("reason", "navigation_origin");
      event("security_denied", id_, d);
      return true;
    }
    router_->OnBeforeBrowse(b, f);
    if (f->IsMain()) {
      cancel_evaluations("Document navigated");
      subscriptions_.clear();
      ++document_;
      auto d = dict();
      d->SetInt("document", document_);
      event("navigating", id_, d);
    }
    return false;
  }
  void OnRenderProcessTerminated(CefRefPtr<CefBrowser> b,
                                 TerminationStatus status, int code,
                                 const CefString &description) override {
    router_->OnRenderProcessTerminated(b);
    cancel_evaluations("Renderer exited");
    auto d = dict();
    d->SetInt("status", status);
    d->SetInt("code", code);
    d->SetString("message", description);
    event("crashed", id_, d);
  }
  bool OnProcessMessageReceived(CefRefPtr<CefBrowser> b, CefRefPtr<CefFrame> f,
                                CefProcessId p,
                                CefRefPtr<CefProcessMessage> m) override {
    return router_->OnProcessMessageReceived(b, f, p, m);
  }
  bool OnQuery(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame> frame, int64_t query,
               const CefString &request, bool persistent,
               CefRefPtr<Callback> callback) override;
  void OnQueryCanceled(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame>,
                       int64_t query) override {
    subscriptions_.erase(query);
  }
  void command(Dict d);
  int deliver(const std::string &topic, CefRefPtr<CefValue> payload,
              const std::string &sender);
  void close() {
    if (browser_)
      browser_->GetHost()->CloseBrowser(true);
  }
  const std::string &scope() const { return scope_; }
  const std::string &id() const { return id_; }
  CefRefPtr<CefBrowser> browser() const { return browser_; }
  std::size_t allocation() const { return allocation_; }
  int close_request{};

private:
  bool allowed(const std::string &url, const std::set<std::string> &grants) const {
    return url == "about:blank" || (network_url(url) && grants.count(origin(url)));
  }
  bool bridge_authorized(CefRefPtr<CefFrame> frame) const {
    return frame && frame->IsMain() && network_url(frame->GetURL()) && origins_.count(origin(frame->GetURL()));
  }
  bool admit_query() {
    const auto now = std::chrono::steady_clock::now();
    tokens_ = std::min(240., tokens_ + std::chrono::duration<double>(now - token_time_).count() * 120.);
    token_time_ = now;
    if (tokens_ < 1) return false;
    --tokens_; return true;
  }
  void publish();
  void cancel_evaluations(const char *reason) {
    for (const int request : evaluations_)
      reply(request, nullptr, reason);
    evaluations_.clear();
  }
  Service *owner_;
  Dict config_;
  std::string id_, scope_;
  std::set<std::string> origins_, publish_topics_, subscribe_topics_;
  std::set<std::string> navigation_, resources_;
  bool allow_evaluate_{};
  bool bootstrapping_{true};
  std::atomic<unsigned> denied_resources_{0};
  unsigned denied_navigations_{}, denied_permissions_{}, denied_downloads_{}, denied_dialogs_{};
  std::size_t allocation_{};
  std::size_t check_frame(const comfortable::CpuFrameInfo &info);
  double tokens_{240};
  std::chrono::steady_clock::time_point token_time_{std::chrono::steady_clock::now()};
  CefRefPtr<CefBrowser> browser_;
  CefRefPtr<CefRequestContext> context_;
  CefRefPtr<CefMessageRouterBrowserSide> router_;
  std::unique_ptr<comfortable::CpuFrameChannel> frames_;
  int width_{}, height_{}, request_{}, document_{};
  std::vector<std::uint8_t> base_, popup_, composed_;
  CefRect popup_rect_;
  int popup_width_{}, popup_height_{};
  bool popup_visible_{};
  struct Subscription {
    std::string topic;
    CefRefPtr<Callback> callback;
  };
  std::map<int64_t, Subscription> subscriptions_;
  std::set<int> evaluations_;
  IMPLEMENT_REFCOUNTING(View);
};

class Service : public CefApp,
                public CefBrowserProcessHandler,
                public CefRenderProcessHandler {
public:
  explicit Service(std::filesystem::path root) : root_(std::move(root)) {}
  CefRefPtr<CefBrowserProcessHandler> GetBrowserProcessHandler() override {
    return this;
  }
  CefRefPtr<CefRenderProcessHandler> GetRenderProcessHandler() override {
    return this;
  }
  void
  OnBeforeCommandLineProcessing(const CefString &process,
                                CefRefPtr<CefCommandLine> command) override {
    if (process.empty()) {
      // This is an embedded OSR service, not a standalone Chrome installation.
      // Chromium 151+ enables the Linux first-run EULA dialog by default.
      // Fresh isolated roots must work without creating interactive browser UI.
      command->RemoveSwitch("force-first-run");
      command->AppendSwitch("no-first-run");
      command->AppendSwitch("no-default-browser-check");
    }
    // Explicit CPU OSR baseline. GPU transport remains independently optional.
    command->AppendSwitch("disable-gpu-shader-disk-cache");
    if (command->HasSwitch("single-process"))
      command->RemoveSwitch("single-process");
  }
  void OnContextInitialized() override {
    auto d = dict();
    d->SetString("event", "service_ready");
    d->SetInt("protocol", 1);
    d->SetInt("security_protocol", 2);
    d->SetBool("sandbox_requested", true);
    d->SetBool("permissions_default_deny", true);
    d->SetString("cef_version", CEF_VERSION);
    d->SetString("frame_backend", "native_cpu");
    d->SetBool(
        "startup_ui_suppressed",
        CefCommandLine::GetGlobalCommandLine()->HasSwitch("no-first-run"));
    emit(d);
    reader_ = std::thread([this] {
      std::string line;
      bool overflow = false;
      while (bounded_line(std::cin, line, overflow)) {
        if (overflow) {
          post([] { reply(0, nullptr, "Control message exceeds 1 MiB"); });
          continue;
        }
        try {
          auto input = parse(line);
          const bool shutdown = text(input, "op") == "shutdown";
          post([this, input] {
            try {
              handle(input);
            } catch (const std::exception &e) {
              reply(input->GetInt("id"), nullptr, e.what());
            }
          });
          if (shutdown)
            return;
        } catch (const std::exception &e) {
          auto error = std::string(e.what());
          post([error] { reply(0, nullptr, error); });
        }
      }
      post([this] { shutdown(); });
    });
  }
  void join() {
    if (reader_.joinable())
      reader_.join();
  }
  void OnWebKitInitialized() override {
    renderer_router_ =
        CefMessageRouterRendererSide::Create(CefMessageRouterConfig());
  }
  void OnContextCreated(CefRefPtr<CefBrowser> b, CefRefPtr<CefFrame> f,
                        CefRefPtr<CefV8Context> c) override {
    renderer_router_->OnContextCreated(b, f, c);
    if (f->IsMain())
      f->ExecuteJavaScript(R"JS(
            window.comfortable = Object.freeze({
              publish(topic,payload,target='') { return new Promise((resolve,reject)=>window.cefQuery({
                request:JSON.stringify({op:'publish',topic,payload,target}),
                onSuccess:r=>resolve(JSON.parse(r)),onFailure:(c,m)=>reject(new Error(m))})); },
              subscribe(topic,callback) { const query=window.cefQuery({persistent:true,
                request:JSON.stringify({op:'subscribe',topic}),
                onSuccess:r=>callback(JSON.parse(r)),onFailure:()=>{}});
                return ()=>window.cefQueryCancel(query); }
            });
        )JS",
                           f->GetURL(), 0);
  }
  void OnContextReleased(CefRefPtr<CefBrowser> b, CefRefPtr<CefFrame> f,
                         CefRefPtr<CefV8Context> c) override {
    renderer_router_->OnContextReleased(b, f, c);
  }
  bool OnProcessMessageReceived(CefRefPtr<CefBrowser> b, CefRefPtr<CefFrame> f,
                                CefProcessId p,
                                CefRefPtr<CefProcessMessage> m) override {
    return renderer_router_->OnProcessMessageReceived(b, f, p, m);
  }
  int broadcast(const std::string &scope, const std::string &sender,
                const std::string &topic, CefRefPtr<CefValue> payload,
                const std::string &target = {}) {
    int delivered = 0;
    for (auto &[id, view] : views_)
      if (view->scope() == scope && (target.empty() || target == id))
        delivered += view->deliver(topic, payload, sender);
    return delivered;
  }
  void admit_frame(const std::string &id, std::size_t bytes, Dict limits) {
    if (!limits) throw std::invalid_argument("Missing browser limits");
    const int pages = limits->GetInt("max_pages"), total = limits->GetInt("max_total_frame_bytes");
    if (pages < 1 || pages > 64 || total < 1 || total > 1024 * 1024 * 1024)
      throw std::invalid_argument("Invalid browser limits");
    if (!views_.count(id) && views_.size() >= static_cast<std::size_t>(pages))
      throw std::runtime_error("Browser page budget exceeded");
    for (const auto &[key, view] : views_) if (key != id) bytes += view->allocation();
    if (bytes > static_cast<std::size_t>(total)) throw std::runtime_error("Browser frame budget exceeded");
  }
  void prepare_profile(const std::string &key, const std::set<std::string> &origins) {
    CEF_REQUIRE_UI_THREAD();
    auto &profile = profiles_.at(key);
    const auto context = profile.context;
    if (!profile.initializations) {
      auto disabled = CefValue::Create(); disabled->SetBool(false);
      for (const auto *preference : {"printing.enabled", "credentials_enable_service", "credentials_enable_autosignin"}) {
        CefString error;
        if (!context->SetPreference(preference, disabled, error))
          throw std::runtime_error(std::string("Required browser preference unavailable: ") + preference);
        auto actual = context->GetPreference(preference);
        if (!actual || actual->GetType() != VTYPE_BOOL || actual->GetBool())
          throw std::runtime_error("Required browser preference was not disabled");
      }
      ++profile.initializations;
    }
    // A request context owns these settings. Reuse completed work across all
    // its pages; new origin pairs are still denied before their document loads.
    const cef_content_setting_types_t blocked[] = {
        CEF_CONTENT_SETTING_TYPE_GEOLOCATION, CEF_CONTENT_SETTING_TYPE_NOTIFICATIONS,
        CEF_CONTENT_SETTING_TYPE_MEDIASTREAM_MIC, CEF_CONTENT_SETTING_TYPE_MEDIASTREAM_CAMERA,
        CEF_CONTENT_SETTING_TYPE_USB_GUARD, CEF_CONTENT_SETTING_TYPE_HID_GUARD,
        CEF_CONTENT_SETTING_TYPE_SERIAL_GUARD, CEF_CONTENT_SETTING_TYPE_BLUETOOTH_GUARD,
        CEF_CONTENT_SETTING_TYPE_FILE_SYSTEM_READ_GUARD, CEF_CONTENT_SETTING_TYPE_FILE_SYSTEM_WRITE_GUARD,
        CEF_CONTENT_SETTING_TYPE_CLIPBOARD_READ_WRITE,
        CEF_CONTENT_SETTING_TYPE_LOCAL_FONTS, CEF_CONTENT_SETTING_TYPE_MIDI_SYSEX,
        CEF_CONTENT_SETTING_TYPE_KEYBOARD_LOCK, CEF_CONTENT_SETTING_TYPE_POINTER_LOCK,
        CEF_CONTENT_SETTING_TYPE_WINDOW_MANAGEMENT, CEF_CONTENT_SETTING_TYPE_SENSORS};
    const cef_content_setting_types_t saved_devices[] = {
        CEF_CONTENT_SETTING_TYPE_USB_CHOOSER_DATA, CEF_CONTENT_SETTING_TYPE_HID_CHOOSER_DATA,
        CEF_CONTENT_SETTING_TYPE_SERIAL_CHOOSER_DATA, CEF_CONTENT_SETTING_TYPE_BLUETOOTH_CHOOSER_DATA,
        CEF_CONTENT_SETTING_TYPE_FILE_SYSTEM_ACCESS_CHOOSER_DATA};
    for (const auto &requesting : origins) {
      if (!profile.cleared_origins.count(requesting)) {
        for (const auto type : saved_devices) context->SetWebsiteSetting(requesting, "", type, nullptr);
        profile.cleared_origins.insert(requesting);
      }
      for (const auto &top : origins) {
        const auto pair = std::make_pair(requesting, top);
        if (profile.permission_pairs.count(pair)) continue;
        for (const auto type : saved_devices) context->SetWebsiteSetting(requesting, top, type, nullptr);
        for (const auto type : blocked) {
          context->SetContentSetting(requesting, top, type, CEF_CONTENT_SETTING_VALUE_BLOCK);
          if (context->GetContentSetting(requesting, top, type) != CEF_CONTENT_SETTING_VALUE_BLOCK)
            throw std::runtime_error("Required native content permission was not blocked");
        }
        profile.permission_pairs.insert(pair);
      }
    }
  }
  void profile_state(const std::string &key, Dict result) const {
    const auto &profile = profiles_.at(key);
    result->SetInt("profile_initializations", profile.initializations);
    result->SetInt("profile_permission_pairs", static_cast<int>(profile.permission_pairs.size()));
  }
  void closed(const std::string &id) {
    views_.erase(id);
    if (closing_ && views_.empty()) {
      profiles_.clear();
      CefQuitMessageLoop();
    }
  }

private:
  void shutdown() {
    closing_ = true;
    if (views_.empty()) {
      profiles_.clear();
      CefQuitMessageLoop();
      return;
    }
    auto copy = views_;
    for (auto &[id, v] : copy)
      v->close();
  }
  void handle(Dict d) {
    CEF_REQUIRE_UI_THREAD();
    const int request = d->GetInt("id");
    const auto op = text(d, "op"), id = text(d, "view");
    if (op == "shutdown") {
      reply(request);
      shutdown();
      return;
    }
    if (closing_)
      throw std::runtime_error("Service is closing");
    if (op == "open") {
      const auto security = d->GetDictionary("security");
      if (!security || security->GetInt("protocol") != 2)
        throw std::invalid_argument("Security protocol 2 policy is required");
      if (views_.size() >= 64)
        throw std::runtime_error("At most 64 active pages are supported");
      if (!identifier(id) || views_.count(id))
        throw std::invalid_argument("Invalid or duplicate webview id");
      const auto key = text(d, "profile");
      if (!identifier(key))
        throw std::invalid_argument("Invalid profile");
      const bool persistent = d->GetBool("persistent");
      auto it = profiles_.find(key);
      CefRefPtr<CefRequestContext> context;
      if (it == profiles_.end()) {
        if (profiles_.size() >= 64) throw std::runtime_error("Browser profile budget exceeded");
        CefRequestContextSettings settings;
        if (persistent)
          CefString(&settings.cache_path) =
              utf8_path(root_ / ("profile-" + key));
        settings.persist_session_cookies =
            d->GetBool("persist_session_cookies");
        context = CefRequestContext::CreateContext(settings, new BackgroundRequests());
        if (!context)
          throw std::runtime_error("Cannot create profile");
        profiles_[key] = {context, persistent,
                          d->GetBool("persist_session_cookies")};
      } else {
        if (it->second.persistent != persistent ||
            it->second.cookies != d->GetBool("persist_session_cookies"))
          throw std::invalid_argument(
              "Profile settings cannot change during a session");
        context = it->second.context;
      }
      auto view = CefRefPtr<View>(new View(this, d, context, request));
      views_[id] = view;
      return;
    }
    if (op == "publish") {
      auto r = dict();
      r->SetInt("delivered",
                broadcast(text(d, "scope"), "host", text(d, "topic"),
                          d->GetValue("payload"), text(d, "target")));
      reply(request, r);
      return;
    }
    if (op == "contexts") {
      auto r = dict();
      for (const auto &[key, p] : profiles_)
        r->SetString(key, p.context->GetCachePath());
      reply(request, r);
      return;
    }
    auto found = views_.find(id);
    if (found == views_.end())
      throw std::invalid_argument("Unknown webview");
    if (op == "close") {
      found->second->close_request = request;
      found->second->close();
      return;
    }
    found->second->command(d);
  }
  struct Profile {
    CefRefPtr<CefRequestContext> context;
    bool persistent, cookies;
    unsigned initializations{};
    std::set<std::string> cleared_origins;
    std::set<std::pair<std::string, std::string>> permission_pairs;
  };
  std::filesystem::path root_;
  std::map<std::string, Profile> profiles_;
  std::map<std::string, CefRefPtr<View>> views_;
  CefRefPtr<CefMessageRouterRendererSide> renderer_router_;
  std::thread reader_;
  bool closing_{};
  IMPLEMENT_REFCOUNTING(Service);
};

View::View(Service *owner, Dict c, CefRefPtr<CefRequestContext> context,
           int request)
    : owner_(owner), config_(c), id_(text(c, "view")), scope_(text(c, "scope")),
      origins_(strings(c, "origins")),
      publish_topics_(strings(c, "publish_topics")),
      subscribe_topics_(strings(c, "subscribe_topics")), context_(context),
      request_(request) {
  auto security = c->GetDictionary("security");
  const bool insecure = security->GetBool("allow_insecure_http");
  navigation_ = origin_grants(security, "navigation_origins", insecure);
  if (navigation_.size() > 8) throw std::invalid_argument("At most 8 navigation origins per page");
  const auto url = text(c, "url");
  if (url != "about:blank") {
    const auto initial = canonical_origin(url, false, insecure);
    if (navigation_.empty()) navigation_.insert(initial);
    if (!navigation_.count(initial)) throw std::invalid_argument("Initial navigation denied");
  }
  resources_ = origin_grants(security, "resource_origins", insecure);
  if (resources_.empty()) resources_ = navigation_;
  origins_ = origin_grants(c, "origins", insecure);
  for (const auto &value : origins_)
    if (!navigation_.count(value)) throw std::invalid_argument("Bridge origin is outside navigation policy");
  allow_evaluate_ = security->GetBool("allow_evaluate");
  set_strings(security, "navigation_origins", navigation_);
  set_strings(security, "resource_origins", resources_);
  if (!allowed(text(c, "url"), navigation_))
    throw std::invalid_argument("Initial navigation denied");
  if (publish_topics_.size() > 64 || subscribe_topics_.size() > 64)
    throw std::invalid_argument("Too many topic grants");
  for (const auto &topic : publish_topics_) if (!identifier(topic)) throw std::invalid_argument("Invalid topic");
  for (const auto &topic : subscribe_topics_) if (!identifier(topic)) throw std::invalid_argument("Invalid topic");
  const auto info = frame_info(c->GetDictionary("source"));
  if (info.pixel_format != "bgra8_premultiplied")
    throw std::invalid_argument("Browser output requires premultiplied BGRA");
  allocation_ = check_frame(info);
  frames_ = std::make_unique<comfortable::CpuFrameChannel>(info);
  width_ = info.width;
  height_ = info.height;
  router_ = CefMessageRouterBrowserSide::Create(CefMessageRouterConfig());
  router_->AddHandler(this, false);
  CefWindowInfo window;
  window.SetAsWindowless(0);
  CefBrowserSettings settings;
  settings.windowless_frame_rate = std::clamp(c->GetInt("fps"), 1, 240);
  settings.background_color = CefColorSetARGB(255, 17, 21, 34);
  settings.javascript_access_clipboard = STATE_DISABLED;
  settings.javascript_dom_paste = STATE_DISABLED;
  if (!CefBrowserHost::CreateBrowser(window, this, "about:blank", settings,
                                     nullptr, context_))
    throw std::runtime_error("CEF browser creation failed");
}
void View::OnAfterCreated(CefRefPtr<CefBrowser> b) {
  browser_ = b;
  try {
    owner_->prepare_profile(text(config_, "profile"), navigation_);
  } catch (const std::exception &e) {
    reply(request_, nullptr, e.what());
    close(); return;
  }
  auto d = dict();
  d->SetString("view", id_);
  d->SetString("frame_backend", "native_cpu");
  d->SetString("cache_path", context_->GetCachePath());
  d->SetDictionary("security", config_->GetDictionary("security"));
  reply(request_, d);
  if (text(config_, "url") != "about:blank") browser_->GetMainFrame()->LoadURL(text(config_, "url"));
}
std::size_t View::check_frame(const comfortable::CpuFrameInfo &info) {
  auto limits = config_->GetDictionary("limits");
  if (!limits) throw std::invalid_argument("Missing browser limits");
  const int maximum = limits->GetInt("max_frame_bytes");
  const auto bytes = 64ull + std::uint64_t(info.stride) * info.height * 3;
  if (!info.width || !info.height || info.width > 4096 || info.height > 4096 ||
      info.pixel_format != "bgra8_premultiplied" || maximum < 1 ||
      static_cast<std::size_t>(maximum) > frame_limit || bytes > static_cast<std::size_t>(maximum))
    throw std::invalid_argument("Browser frame exceeds limits");
  owner_->admit_frame(id_, bytes, limits);
  return bytes;
}
void View::OnBeforeClose(CefRefPtr<CefBrowser> b) {
  cancel_evaluations("Browser closed");
  CefRefPtr<View> keep = this;
  router_->OnBeforeClose(b);
  router_->RemoveHandler(this);
  subscriptions_.clear();
  browser_ = nullptr;
  frames_.reset();
  context_ = nullptr;
  if (close_request)
    reply(close_request);
  event("closed", id_);
  owner_->closed(id_);
}
void View::OnPaint(CefRefPtr<CefBrowser>, PaintElementType type,
                   const RectList &, const void *pixels, int w, int h) {
  try {
    if (type == PET_VIEW) {
      if (w != width_ || h != height_)
        return;
      base_.assign(static_cast<const std::uint8_t *>(pixels),
                   static_cast<const std::uint8_t *>(pixels) +
                       std::size_t(w) * h * 4);
    } else {
      if (w < 1 || h < 1 || w > 16384 || h > 16384)
        return;
      popup_width_ = w;
      popup_height_ = h;
      popup_.assign(static_cast<const std::uint8_t *>(pixels),
                    static_cast<const std::uint8_t *>(pixels) +
                        std::size_t(w) * h * 4);
    }
    publish();
  } catch (const std::exception &e) {
    auto d = dict();
    d->SetString("message", e.what());
    event("error", id_, d);
  }
}
void View::publish() {
  if (base_.empty() || !frames_)
    return;
  if (!popup_visible_ || popup_.empty()) {
    frames_->submit(base_.data(), width_ * 4);
    return;
  }
  composed_ = base_;
  for (int y = 0; y < popup_height_; ++y)
    for (int x = 0; x < popup_width_; ++x) {
      const int dx = x + popup_rect_.x, dy = y + popup_rect_.y;
      if (dx < 0 || dy < 0 || dx >= width_ || dy >= height_)
        continue;
      const auto *s = popup_.data() + (std::size_t(y) * popup_width_ + x) * 4;
      auto *d = composed_.data() + (std::size_t(dy) * width_ + dx) * 4;
      for (int c = 0; c < 4; ++c)
        d[c] = static_cast<std::uint8_t>(
            std::min(255u, unsigned(s[c]) +
                               (unsigned(d[c]) * (255 - s[3]) + 127) / 255));
    }
  frames_->submit(composed_.data(), width_ * 4);
}
int View::deliver(const std::string &topic, CefRefPtr<CefValue> payload,
                  const std::string &sender) {
  if (!browser_ || !bridge_authorized(browser_->GetMainFrame())) return 0;
  int count = 0;
  auto d = dict();
  d->SetString("topic", topic);
  d->SetString("sender", sender);
  d->SetInt("document", document_);
  d->SetValue("payload", payload ? payload->Copy() : CefValue::Create());
  const auto result = json(d);
  for (auto &[q, s] : subscriptions_)
    if (s.topic == topic) {
      s.callback->Success(result);
      ++count;
    }
  return count;
}
bool View::OnQuery(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame> frame,
                   int64_t query, const CefString &raw, bool persistent,
                   CefRefPtr<Callback> cb) {
  try {
    if (!bridge_authorized(frame))
      throw std::runtime_error("Bridge origin/frame is not authorized");
    if (!admit_query()) throw std::runtime_error("Bridge rate limit exceeded");
    if (raw.length() > 65536) throw std::runtime_error("Business message exceeds 64 KiB");
    const auto request = raw.ToString();
    if (request.size() > 65536)
      throw std::runtime_error("Business message exceeds 64 KiB");
    auto d = parse(request);
    auto value = CefValue::Create(); value->SetDictionary(d);
    unsigned nodes = 0;
    if (!bounded_payload(value, 0, nodes)) throw std::runtime_error("Bridge payload nesting/node budget exceeded");
    const auto op = text(d, "op"), topic = text(d, "topic");
    if (op == "eval_result") {
      if (!evaluations_.erase(d->GetInt("request")))
        throw std::runtime_error(
            "No matching evaluation in this view/document");
      auto data = dict();
      data->SetValue("value", d->GetValue("value"));
      data->SetString("error", text(d, "error"));
      reply(d->GetInt("request"), data);
      cb->Success("{}");
      return true;
    }
    if (op == "subscribe") {
      if (!persistent || !subscribe_topics_.count(topic))
        throw std::runtime_error("Subscription is not authorized");
      if (subscriptions_.size() >= 64)
        throw std::runtime_error("Too many subscriptions");
      subscriptions_[query] = {topic, cb};
      return true;
    }
    if (op == "publish") {
      if (!publish_topics_.count(topic))
        throw std::runtime_error("Publish topic is not authorized");
      auto r = dict();
      r->SetInt("delivered",
                owner_->broadcast(scope_, id_, topic, d->GetValue("payload"),
                                  text(d, "target")));
      cb->Success(json(r));
      auto e = dict();
      e->SetString("topic", topic);
      e->SetValue("payload", d->GetValue("payload"));
      event("message", id_, e);
      return true;
    }
    throw std::runtime_error("Unknown bridge operation");
  } catch (const std::exception &e) {
    cb->Failure(403, e.what());
    return true;
  }
}
void View::command(Dict d) {
  const auto op = text(d, "op");
  const int request = d->GetInt("id");
  if (!browser_)
    throw std::runtime_error("Browser is not ready");
  auto host = browser_->GetHost();
  if (op == "security_state") {
    auto result = dict();
    result->SetInt("denied_resources", denied_resources_.load());
    result->SetInt("denied_navigations", denied_navigations_);
    result->SetInt("denied_permissions", denied_permissions_);
    result->SetInt("denied_downloads", denied_downloads_);
    result->SetInt("denied_dialogs", denied_dialogs_);
    result->SetInt("subscriptions", static_cast<int>(subscriptions_.size()));
    result->SetInt("document", document_);
    result->SetBool("printing_disabled", !context_->GetPreference("printing.enabled")->GetBool());
    result->SetBool("password_manager_disabled", !context_->GetPreference("credentials_enable_service")->GetBool());
    result->SetBool("content_permissions_blocked", true); // Verified before loading the first document.
    result->SetBool("audio_muted", host->IsAudioMuted());
    owner_->profile_state(text(config_, "profile"), result);
    reply(request, result); return;
  } else if (op == "navigate") {
    if (!allowed(text(d, "url"), navigation_)) throw std::runtime_error("Navigation origin denied");
    bootstrapping_ = false;
    browser_->GetMainFrame()->LoadURL(text(d, "url"));
  }
  else if (op == "back")
    browser_->GoBack();
  else if (op == "forward")
    browser_->GoForward();
  else if (op == "reload")
    browser_->Reload();
  else if (op == "visible")
    host->WasHidden(!d->GetBool("visible"));
  else if (op == "muted")
    host->SetAudioMuted(d->GetBool("muted"));
  else if (op == "resize") {
    auto info = frame_info(d->GetDictionary("source"));
    const auto bytes = check_frame(info);
    auto replacement = std::make_unique<comfortable::CpuFrameChannel>(info);
    frames_ = std::move(replacement);
    allocation_ = bytes;
    width_ = info.width;
    height_ = info.height;
    base_.clear();
    popup_.clear();
    host->WasResized();
  } else if (op == "cancel_eval") {
    evaluations_.erase(d->GetInt("request"));
  } else if (op == "eval") {
    if (!allow_evaluate_ || !bridge_authorized(browser_->GetMainFrame()))
      throw std::runtime_error("Evaluation requires an authorized origin");
    if (evaluations_.size() >= 256)
      throw std::runtime_error("Too many pending evaluations");
    evaluations_.insert(request);
    auto code = dict();
    code->SetString("code", text(d, "code"));
    const auto encoded = json(code);
    std::string script =
        "(async()=>{let value=null,error='';try{value=await (0,eval)(" +
        encoded +
        ".code);}catch(e){error=String(e);}window.cefQuery({request:JSON."
        "stringify({op:'eval_result',"
        "request:" +
        std::to_string(request) +
        ",value:value===undefined?null:value,error}),onSuccess:()=>{},"
        "onFailure:()=>{}});})()";
    browser_->GetMainFrame()->ExecuteJavaScript(
        script, browser_->GetMainFrame()->GetURL(), 0);
    return;
  } else if (op == "input") {
    const auto kind = text(d, "kind");
    auto a = d->GetDictionary("data");
    if (!a)
      throw std::invalid_argument("Missing input data");
    int flags = 0;
    for (const auto &m : strings(a, "modifiers")) {
      if (m == "ctrl")
        flags |= EVENTFLAG_CONTROL_DOWN;
      if (m == "shift")
        flags |= EVENTFLAG_SHIFT_DOWN;
      if (m == "alt")
        flags |= EVENTFLAG_ALT_DOWN;
      if (m == "meta")
        flags |= EVENTFLAG_COMMAND_DOWN;
    }
    CefMouseEvent mouse;
    const int buttons = a->GetInt("buttons");
    if (buttons & 1)
      flags |= EVENTFLAG_LEFT_MOUSE_BUTTON;
    if (buttons & 2)
      flags |= EVENTFLAG_RIGHT_MOUSE_BUTTON;
    if (buttons & 4)
      flags |= EVENTFLAG_MIDDLE_MOUSE_BUTTON;
    mouse.x = a->GetInt("x");
    mouse.y = a->GetInt("y");
    mouse.modifiers = flags;
    if (kind == "mouse_move" || kind == "mouse_leave")
      host->SendMouseMoveEvent(mouse, kind == "mouse_leave");
    else if (kind == "mouse_down" || kind == "mouse_up") {
      host->SetFocus(true);
      auto button = text(a, "button");
      host->SendMouseClickEvent(mouse,
                                button == "right"    ? MBT_RIGHT
                                : button == "middle" ? MBT_MIDDLE
                                                     : MBT_LEFT,
                                kind == "mouse_up",
                                std::max(1, a->GetInt("clicks")));
    } else if (kind == "wheel")
      host->SendMouseWheelEvent(mouse, a->GetInt("dx"), a->GetInt("dy"));
    else if (kind == "focus_in" || kind == "focus_out")
      host->SetFocus(kind == "focus_in");
    else if (kind == "text") {
      host->ImeCommitText(text(a, "commit"), invalid_range(), 0);
    } else if (kind == "ime") {
      const auto pre = text(a, "preedit"), commit = text(a, "commit");
      if (!commit.empty())
        host->ImeCommitText(commit, invalid_range(), 0);
      if (pre.empty())
        host->ImeCancelComposition();
      else {
        CefString s(pre);
        CefCompositionUnderline u;
        u.range = CefRange(0, static_cast<uint32_t>(s.length()));
        u.color = CefColorSetARGB(255, 255, 255, 255);
        host->ImeSetComposition(pre, {u}, invalid_range(),
                                CefRange(static_cast<uint32_t>(s.length()),
                                         static_cast<uint32_t>(s.length())));
      }
    } else if (kind == "key_down" || kind == "key_up") {
      CefKeyEvent key;
      key.type = kind == "key_up" ? KEYEVENT_KEYUP : KEYEVENT_RAWKEYDOWN;
      key.windows_key_code = a->GetInt("key_code");
      key.native_key_code = a->GetInt("scan_code");
      key.modifiers = flags;
      if (a->GetBool("repeat"))
        key.modifiers |= EVENTFLAG_IS_REPEAT;
      host->SendKeyEvent(key);
      if (kind == "key_down" &&
          !(flags & (EVENTFLAG_CONTROL_DOWN | EVENTFLAG_ALT_DOWN |
                     EVENTFLAG_COMMAND_DOWN))) {
        CefString chars(text(a, "text"));
        for (std::size_t i = 0; i < chars.length(); ++i) {
          key.type = KEYEVENT_CHAR;
          key.character = chars.c_str()[i];
          key.unmodified_character = chars.c_str()[i];
          host->SendKeyEvent(key);
        }
      }
    }
  } else
    throw std::invalid_argument("Unknown view operation");
  reply(request);
}

int run(int argc, char **argv, void *sandbox) {
  // CEF child processes use CEF IPC, never the broker's JSON control endpoints.
  bool subprocess = false;
  for (int i = 1; i < argc; ++i)
    if (std::string(argv[i]).rfind("--type=", 0) == 0 && std::string(argv[i]).size() > 7) subprocess = true;
  if (subprocess) {
#ifdef _WIN32
    if (!std::freopen("NUL", "r", stdin) || !std::freopen("NUL", "w", stdout)) return 1;
#else
    if (!std::freopen("/dev/null", "r", stdin) || !std::freopen("/dev/null", "w", stdout)) return 1;
#endif
  }
#ifdef _WIN32
  CefMainArgs main_args(GetModuleHandle(nullptr));
#else
  CefMainArgs main_args(argc, argv);
#endif
  std::filesystem::path root;
  bool invalid_switch = false;
  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
    if (arg.rfind("--ctui-root=", 0) == 0)
      root = std::filesystem::u8path(arg.substr(12));
    const auto key = arg.substr(0, arg.find('='));
    if (key != "--ctui-root" && key != "--no-first-run" && key != "--no-default-browser-check" &&
        key != "--ozone-platform") invalid_switch = true;
    if (key == "--ozone-platform" && arg != "--ozone-platform=x11" && arg != "--ozone-platform=wayland")
      invalid_switch = true;
  }
  CefRefPtr<Service> app = new Service(root);
  const int child = CefExecuteProcess(main_args, app, sandbox);
  if (child >= 0)
    return child;
  if (invalid_switch)
    throw std::invalid_argument("Unsupported or unsafe browser launch switch");
  if (root.empty() || !root.is_absolute())
    throw std::invalid_argument(
        "--ctui-root must be an absolute application-private path");
  std::filesystem::create_directories(root);
  CefSettings settings;
  settings.windowless_rendering_enabled = true;
  settings.no_sandbox = false;
  CefString(&settings.root_cache_path) = utf8_path(root);
  CefString(&settings.log_file) = utf8_path(root / "cef.log");
  settings.log_severity = LOGSEVERITY_DISABLE;
  if (!CefInitialize(main_args, settings, app, sandbox))
    return CefGetExitCode();
  CefRunMessageLoop();
  app->join();
  app = nullptr;
  CefShutdown();
  return 0;
}
} // namespace
#ifdef _WIN32
int RunConsoleMain(int argc, char *argv[], void *sandbox_info,
                   cef_version_info_t *) {
  try {
    return run(argc, argv, sandbox_info);
  } catch (const std::exception &e) {
    std::cerr << e.what() << std::endl;
    return 1;
  }
}
#else
int main(int argc, char **argv) {
  try {
    return run(argc, argv, nullptr);
  } catch (const std::exception &e) {
    std::cerr << e.what() << std::endl;
    return 1;
  }
}
#endif
