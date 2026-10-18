# WebView 与跨平台附加层：完整调研与实施计划

> 2026-10-18 状态更新：本文涉及的 Linux/WSLg 图形测试是历史自有窗口结果。Linux 图像混合支持现已暂停，不能据此宣称外部终端兼容；[当前决策](linux-support-decision.md)。

调研日期：2026-10-17。WebView 调研 8 组、附加层补充调研 17 组、会话与消息调研
5 组，共使用 Exa 检索 150 条候选结果，按完整 URL 去重为 141 条。候选数包含旧
资料及重复内容的不同 URL，不等于 141 个独立验证结论。浏览器部分取得 19 个官方页面正文；附加层
部分核对 Apple 文档及 DocC JSON、Qt 上游源码、X.Org、Wayland、Linux/Khronos
规范、终端和桌面环境官方接口。论坛和未取得正文的示例文件未作为决定性依据。
检索清单及抓取限制保存在仓库
`exa-results/webview-2026-10-17/sources.json`。

**结论：采用 CEF 离屏渲染、平台化帧 SDK、共享场景和可替换宿主后端。**
Windows 延续现有 D3D11/DComp；X11 可设计原生附加层；macOS 的 AppKit/Metal
研究保留作背景，但按用户后续要求本轮不支持。Wayland 不能承诺任意终端原生附加层，需提供终端
图像协议、自有窗口或明确限定桌面环境的协作后端。浏览器跨平台、图像传输跨平台、
外部终端嵌入跨平台是三个不同交付项。本报告合并选型、架构、能力矩阵、风险与
同一套实施计划。后续实施已经提供 CEF 服务和 Linux 自有窗口 CPU 基线，实际状态
与局限见下节、[使用指南](webview.md)及 docs/validation/webview/ 的原始验证记录。

## 范围与现状

### 实施更新与首次启动弹窗（2026-10-17）

按用户后续要求，本轮实施范围调整为 Windows 和 Linux；macOS 暂不实现、不列为
已支持。Linux 的 X11 与原生 Wayland 分别验证，Xwayland 的结果不能替代 Wayland。
下文选型与里程碑保留研究背景；实施状态以本节及后续验证记录为准。

CEF 152 原生服务、应用私有根目录、独立/共享 profile、授权主题消息以及原生 CPU
共享帧基线已进入实现。Windows 和 WSL2/WSLg X11 的服务隔离与消息测试已跑通；
这不等于 Linux 附加层或 GPU 共享纹理已经验证。

用户反馈测试启动时反复出现“Google 附加条款”窗口，现已将处理优先于后续桌面
自动化。Exa 补充检索 2 组、11 条候选，读取固定 Chromium 版本源码核对：

- Chromium M151 的上游变更默认启用 Linux 首次启动条款窗口；当前固定版本
  152.0.7977.167 的 `MasterPrefs.eula_required` 仍默认为 true。
- Chromium 按用户数据目录中的 `First Run` sentinel 判断是否首次运行。测试每次
  创建全新的隔离根目录，因此上次点击不会覆盖下个测试实例；匿名服务同样使用
  临时根。这是与本次反复弹窗吻合的触发路径，不需要合并应用目录来解决。
- 原生宿主现在在浏览器进程初始化命令行中设置 `no-first-run` 与
  `no-default-browser-check`，移除冲突的 `force-first-run`。Python 启动参数也设置
  前两项，覆盖初始化早期和直接运行原生宿主这两种入口。
- `no-first-run` 是 Chromium 自身提供的跳过首次启动任务开关；不模拟点击、
  不写入虚假的条款接受记录，也不屏蔽网页自己的登录、权限或服务条款。
- 验收新增：持久化根重启、两个不同全新应用根、连续两个匿名临时根均须无人值守
  完成握手与首帧。能力握手报告实际 `startup_ui_suppressed` 状态；仍保留应用/profile
  隔离测试。新窗口宿主的 X11/Wayland 验证在此问题通过后继续。

修复后实测：Windows、Linux/Xvfb X11、WSLg 原生 Wayland 三种启动配置分别通过
10 项服务集成检查，均无需人工点击；其中每种配置均包含连续两个全新匿名根目录。
验证期间沙箱保持默认开启。服务测试不等于原生附加层/桌面输入验收；后续宿主验证
仍需单独记录实际窗口后端。自动化脚本为 `examples/verify_webview.py`。

依据：[M151 上游变更](https://chromium.googlesource.com/chromium/src/+/ae9b17ac975d4b10af762e9344083d858f1347ce)、
[152 首次启动声明](https://chromium.googlesource.com/chromium/src/+/152.0.7977.167/chrome/browser/first_run/first_run.h)、
[sentinel 与开关判定](https://chromium.googlesource.com/chromium/src/+/152.0.7977.167/chrome/browser/first_run/first_run.cc)、
[启动开关注释](https://chromium.googlesource.com/chromium/src/+/152.0.7977.167/chrome/common/chrome_switches.cc)。
检索与抓取清单见 `exa-results/webview-2026-10-17/first-run.json`。

| 能力 | 仓库现状 | 本计划目标 |
| --- | --- | --- |
| TUI 布局及内嵌画布 | 已有列表、表格、Text、Tabs、裁剪与事件 | 复用布局语义，浏览器与普通图像统一作为源 |
| C++ 图像数据平面 | Windows D3D11 共享纹理及 CPU 兼容输入 | 抽象资源、同步和传输，保留 Windows v1 兼容 |
| 共享附加层 | Windows，一个 App 一个辅助层窗口 | 同宿主一份场景，新增经过验证的宿主后端 |
| 原生输入 | Windows 画布事件及 IME 数据已有 | 补浏览器输入适配，按模式报告完整或受限能力 |
| WebView | 只有研究，无浏览器实现 | CEF provider、导航/JS 控制接口、可运行示例 |
| 应用数据隔离与画布联动 | 尚无浏览器会话/主题总线 | 作为独立上层服务实现，不扩展图像传输协议 |
| macOS / X11 / Wayland 画布 | 尚无可运行后端 | 逐项原型和真实桌面验收，不以编译成功代替支持 |

## 选型比较

| 候选 | 与图像画布的契合点 | 主要代价或限制 | 本项目判断 |
| --- | --- | --- | --- |
| CEF / Chromium | 专门的 windowless 模式；OnPaint 输出 BGRA，OnAcceleratedPaint 输出平台 GPU 资源；宿主转发输入 | Chromium 运行时与辅助进程、各平台资源同步、打包和消息循环 | 首选，适合通用网页控件 |
| Qt WebEngine + Qt Quick | Chromium 图像进入 Qt Quick；QQuickRenderControl 可将场景导向自有纹理 | 需额外 WebEngine/Quick 依赖；本项目当前 Qt 仅管理窗口和输入；具体组合须验证 | 备选，适合统一改造为 Qt Quick 合成器 |
| Ultralight | 专门的 CPU Surface / GPUDriver，离屏纹理设计直接 | Web 功能有明显取舍，版本及授权等级影响能力 | 适合自有 HTML UI，通用网页兼容性次于本项目需求 |
| Wry / 系统 WebView | 多平台窗口嵌入接口 | 不同系统不同引擎，公开接口以原生窗口/控件为中心 | 不作为本次帧提供者首选 |
| WPE WebKit | 面向嵌入式，可接收 GPU/共享内存缓冲区及输入 | 需平台集成；未找到可直接替代本项目三平台 SDK 的统一交付路径 | Linux 专用后端候选 |

依据：[CEF RenderHandler](https://cef-builds.spotifycdn.com/docs/150.0/classCefRenderHandler.html)、
[Qt WebEngine 图形管线](https://doc.qt.io/qt-6/qtwebengine-features.html)、
[QQuickRenderControl](https://doc.qt.io/qt-6/qquickrendercontrol.html)、
[Ultralight GPU](https://ultralig.ht/docs/2.0/gpu-renderer-overview)、
[Wry API](https://docs.rs/wry/latest/wry/)、
[WPE 架构](https://wpewebkit.org/about/architecture.html)。

## 分层边界：图像显示与高级服务独立

WebView 是基于画布显示能力制作的可选服务。Cookie、应用身份、账号 profile 和
跨画布业务消息属于这个服务层，不能加入原有 FrameProvider/Consumer 的数据格式。
底层既不知道画面来自浏览器，也不要求普通图像提供者链接 CEF 或消息总线。

| 层次 | 负责 | 不应承担 |
| --- | --- | --- |
| 帧传输 SDK | 图像资源、格式、设备、帧同步、序号与租约 | Cookie、URL、账号、主题订阅、JS 权限 |
| 画布/附加层 | 位置、缩放、裁剪、合成、命中和原生事件 | 浏览器持久化目录、登录会话、业务消息解释 |
| WebView 服务 | CEF、ProfileRegistry、导航、JS 桥接、输入/IME 到浏览器的适配 | 改写底层通用图像协议来表达浏览器状态 |
| 应用消息服务 | 授权的 WebView/控件/提供者之间的业务联动 | 传输逐帧图像、自动共享 Cookie、默认跨应用广播 |

WebView 服务输出一个普通帧源供 OverlayCanvas 显示，并另外暴露服务控制端点。
应用消息服务在图像通道旁独立存在，可以只用其中一项。部署时两者可在同一进程中，
但模块、协议、生命周期与测试边界独立；共享一个进程不等于共享 Cookie 或业务权限。
后文 SDK v2 的变化仅为跨平台资源与同步，不能因增加上层服务而把浏览器概念塞入 SDK。

## CEF 服务怎样使用现有数据平面

推荐逻辑链路：

```text
Python WebView 控件（拟议）：URL、布局、可见性、导航与业务事件
                  ↓ 控制消息
C++ 浏览器宿主：CEF UI 消息循环、页面实例、输入与网页回调
                  ↓ OnPaint / OnAcceleratedPaint
C++ 帧适配器：复制到自有纹理或自有共享内存、格式处理、发布
                  ↓ 原生帧通知
C++ 合成器：采样、缩放、裁剪、合成 popup
                  ↓
终端附加层窗口 / 将来的其他呈现后端
```

Python 不读取逐帧像素，不把每次 OnPaint 暴露成 Python 绘图回调。现有
FrameProvider 可以承接 Windows 版本的帧发布；需要新增 BrowserProvider，而不是
让 producer.cpp 执行网页截图。BrowserProvider 是 WebView 服务到图像 SDK 的适配器，
不是图像 SDK 自己承担浏览器功能。普通页面事件和 URL/标题变化可经独立服务通道
按需通知 Python。

CEF 的 OnPaint 给出整个图像的 BGRA 缓冲区与 dirtyRects。回调返回后应使用自己的
存储。当前三槽 SDK 不保证下一次写到的槽包含上一帧，因此不能把 dirtyRects 直接
贴到一个任意旧槽：首版采用完整帧复制；后续可维护完整的最新底图，再做脏区更新。
[接口定义](https://raw.githubusercontent.com/chromiumembedded/cef/master/include/cef_render_handler.h)。

OnAcceleratedPaint 提供 Windows 共享纹理 HANDLE、macOS IOSurface、Linux 原生
缓冲区 plane/fd。**这些资源属于 CEF 的池，不能缓存到回调以外继续使用。**
Windows 路径应在回调约束内打开 CEF 纹理，复制/转换到 FrameProvider 持有的
BGRA8 纹理，然后发布；必要时用 shader 处理颜色格式。预期避免 GPU→CPU 回读，
但不宣称完全零拷贝。需要根据固定 CEF 版本验证跨设备同步，Flush 本身不证明 GPU
已经完成读取。macOS/Linux 同样需要自己的资源和可靠同步。
[资源寿命约定](https://cef-builds.spotifycdn.com/docs/150.0/classCefRenderHandler.html#details)。

每种后端均须协商适配器、尺寸、格式、预乘 alpha 和色彩空间。Chromium 使用的 GPU
若与合成器不同，不能沿用当前同 LUID 共享纹理路径。CPU 路径用于明确的兼容后备，
向调用者报告实际后端，不能悄悄退化后仍显示 GPU 模式。

### 文档版本冲突

CEF 的部分 general_usage OSR 描述仍称不支持加速；部分平台的
shared_texture_enabled 头文件注释仍写 Windows-only。但 RenderHandler 合约和
平台结构已包含 IOSurface 和 Linux plane/fd，不能用旧概述否定整个 GPU 接口，
也不能用一个结构体存在就承诺每个已发行二进制都正常工作。实施时必须锁定 CEF
版本、构建号和对应源码，并逐平台跑 GPU/软件模式验证。
参考：[上游接口](https://raw.githubusercontent.com/chromiumembedded/cef/master/include/cef_render_handler.h)、
[Windows 类型](https://raw.githubusercontent.com/chromiumembedded/cef/master/include/internal/cef_types_win.h)、
[macOS 类型](https://raw.githubusercontent.com/chromiumembedded/cef/master/include/internal/cef_types_mac.h)、
[Linux 类型](https://raw.githubusercontent.com/chromiumembedded/cef/master/include/internal/cef_types_linux.h)。

本轮读取的生成式 API 参考标记为 CEF 150.0.1；这是所检查文档的版本，不表示本项目
已下载、编译或测试该版本，也不表示它就是所有平台应使用的最新稳定版。

## WebView 上层服务：应用数据隔离与消息联动

**CEF 提供机制，库需要实现默认策略。** 存储隔离采用 CefRequestContext；页面到
C++ 的异步消息采用 CefMessageRouter/CefProcessMessage。在此之上由可选的
BrowserService、ProfileRegistry 和 AppMessageBus 组织应用语义。它们不属于图像 SDK，
本节描述设计基线；当前已实现的 API 和范围以 [WebView 指南](webview.md) 为准。

### 应用与 profile 是数据边界，画布不是

| 标识 | 上层含义 |
| --- | --- |
| application_id | 逻辑应用身份和独立的数据根；不能从所有应用共同使用的 python.exe 推导 |
| profile_id | 应用内的账号/存储分区，对应显式 CefRequestContext；可持久化或临时 |
| webview_id | 一个真正的 browser 实例，拥有页面、导航、滚动和 JS 上下文 |
| canvas_id | 图像的一个显示位置；一个 WebView 可以输出到多个画布，仍是同一个页面 |
| document_token | 当前网页文档实例，刷新/导航后更换，用于丢弃旧消息和订阅 |

计划默认按应用隔离，应用内使用命名 profile 显式选择共享或独立账号。同一 profile
可以复用同一个 RequestContext；需要隔离时创建非共享 context。不同 app 即使打开
相同 URL，也不能自动继承另一 app 的 Cookie。不能因两个画布相邻、使用同一附加层
或由同一 Python 库创建就共享数据。

CEF 的 CreateContext(settings, handler) 可以建立独立 context，
CreateContext(other, handler) 则明确共享存储，IsSharingWith 用于判断共享关系。
GetCookieManager 从相应 context 取得；不使用跨应用的 GetGlobalContext 或全局
CookieManager 默认值。创建 Browser/URLRequest 时必须显式传 context，弹窗和
新页面也不得意外回落到全局 context。
[官方 RequestContext](https://raw.githubusercontent.com/chromiumembedded/cef/master/include/cef_request_context.h)。

Cookie、localStorage、IndexedDB、站点缓存、Service Worker 和站点权限均应纳入
profile 隔离验收，不能只测 document.cookie。具体存储仍遵守同源和 Chromium
分区规则；同一 profile 不意味着不同 origin 能读彼此数据，也不意味着每个页面的
sessionStorage 自动共享。profile 不是防范任意同用户恶意本机进程的 OS 安全边界。

### 持久化目录与多实例

BrowserService 显式配置每应用的 CefSettings.root_cache_path，避免 CEF 的公共
默认目录。持久化 profile 使用该应用根下面的独立、规范化 cache_path，并验证
实际 GetCachePath/IsSharingWith。根目录也会保存安装级数据，仅设置 context 的
空 cache_path 不等于整个 CEF runtime 从不落盘。
[CEF 当前设置定义](https://raw.githubusercontent.com/chromiumembedded/cef/master/include/internal/cef_types.h)。

推荐形态（示意路径，与帧共享通道名称无关）：

```text
<用户数据目录>/<application-id 的稳定映射>/cef-root/   ← runtime root_cache_path
    profile-main/                                    ← RequestContext.cache_path
    profile-account2/                                ← 另一独立 context
```

profile 路径由宿主注册表产生，不接受网页给出的任意目录。当前头文件允许根/子目录，
已检查的 Chromium context 实现对直接子 profile 有专门路径，因此首版使用扁平的
直接子目录，并对固定 CEF 版本测试，不假设任意多层目录都等价。
[上游 profile 创建实现](https://raw.githubusercontent.com/chromiumembedded/cef/master/libcef/browser/chrome/chrome_browser_context.cc)。

临时模式创建独立内存 context；未提供稳定 application_id 时默认使用临时服务
实例，不偷偷复用通用持久化目录。需要持久化时要求调用方提供稳定应用身份和明确
选项。会话 Cookie 的跨重启恢复由 persist_session_cookies 单独决定，不把账号
长期 Cookie 和会话 Cookie 的保留策略混成一个开关。

CEF 120 起存在基于 root_cache_path 的进程单例保护；同一目录不能由多个原生
browser runtime 并发写入。同应用多实例要么连接该应用已有 browser host，要么
使用独立的临时实例/独立 profile 根并明确不共享；不能以文件锁失败后改用公共
context 的方式“修复”。默认 host 可供同应用多个 WebView 使用，不是所有应用共享
一个数据根的全局浏览器守护进程。正常关闭应等待浏览器/待处理存储操作结束，再
释放 context；清 Cookie、清站点数据与删除整个 profile 是不同操作，不能对仍在用
的目录直接删除。
[root_cache_path/单例约定](https://raw.githubusercontent.com/chromiumembedded/cef/master/include/internal/cef_types.h)、
[Cookie FlushStore](https://raw.githubusercontent.com/chromiumembedded/cef/master/include/cef_cookie.h)。

root_cache_path 属于整个 CEF runtime，不能为同进程每个页面重新设置。因而进程内
模式应限定一个 runtime/应用身份，多页面用其下的独立 context。若同一个 Python
宿主需要承载多个逻辑应用，默认给各应用启动独立的原生 browser host，或拒绝在
已有 runtime 上切换身份；不能反复 CefInitialize 来模拟多个独立根。这是上层服务
部署约束，与进程内/进程间图像提供者的选择无关。

当前 inprocess 提供者示例不等于 CEF 的 --single-process 模式。CEF 头文件说明
single-process 模式会使浏览器使用首个 context、忽略其他 context，因此本服务
不得用该模式实现所谓“进程内浏览器”；跨平台进程内嵌入仍保留正常的 CEF 多进程模型。
[官方限制](https://raw.githubusercontent.com/chromiumembedded/cef/master/include/cef_request_context.h)。

### 跨画布联动：独立的应用消息服务

Cookie 共享与消息互通是两个独立开关。相同应用中使用两个隔离 profile 的页面，
仍可由应用授权交换某个业务主题；这不应打开彼此 Cookie 访问。不同应用默认既不
共享 profile，也不共享主题命名空间。普通 TUI 控件、非浏览器画布或其他 C++ 提供者
可选择接入同一上层业务总线，底层图像模块无须认识它。

```text
页面 A 的 JS ↔ CEF renderer 消息 ↔ BrowserService / AppMessageBus
                                      ↕ 授权路由
页面 B 的 JS ↔ CEF renderer 消息 ↔ 对应页面订阅
                                      ↕ 可选
                                Python 业务 / 其他控件服务

图像：CEF → BrowserProvider → 原有帧 SDK → 画布（另一条独立通路）
```

CEF MessageRouter 已提供异步请求/响应和 persistent query，官方也给出广播、订阅
的使用模式。但 topic 注册、按应用授权、跨 browser 路由和生命周期由本服务实现。
Router 的 per-context 指 JS 执行上下文，不是一个自动识别 application_id 或
CefRequestContext 的权限系统。
[官方 MessageRouter](https://raw.githubusercontent.com/chromiumembedded/cef/master/include/wrapper/cef_message_router.h)。

计划使用发布/订阅、定向消息和请求/响应，典型主题例如 selection.changed：表格
选中一个业务对象，侧边详情页收到其 ID 后更新。宿主从实际 browser/frame、已注册
服务会话和 document_token 推导发送者身份；不信任 JS 自报的 app/profile/origin。
默认只向授权 origin/文档暴露桥接，并对发布/订阅主题和跨 profile 联动分别授权。
消息是可验证的业务数据，不把 payload 拼接为任意 JS 执行，也不自动转发登录凭据。

共享同一图像源的多个画布本来就是同一页面的多处显示，不需要制造两份消息订阅。
两个独立 WebView 才有各自文档和订阅；canvas_id 可以标识交互来自哪个显示位置，
但不能替代 webview_id/document_token 作为消息接收端。

网页自己的 BroadcastChannel 可用于满足相同存储分区/同源条件的页面；它不是跨
profile、跨应用或跨任意 origin 的通用总线。window.postMessage 也需要目标窗口
引用并按 targetOrigin 限制，不会自动连接两个独立的 CEF browser。
因此使用标准 Web API 的条件满足时可直接用，不满足时走显式授权的宿主消息桥。
[WHATWG 消息规范](https://html.spec.whatwg.org/multipage/web-messaging.html)。

### 消息和存储的完成语义

CEF 的 SendProcessMessage 不保证在关闭、导航或进程崩溃时必达。服务层需自有
request_id、超时、取消、必要的 ACK 和幂等规则，不能承诺任意消息恰好一次。
OnQueryCanceled、OnBeforeBrowse、OnContextReleased、OnBeforeClose 和 renderer
崩溃路径要注销订阅，页面重载后以新的 document_token 重新订阅；不能把排队消息
送给已复用的 browser/canvas ID。需要恢复状态时由应用提供快照，而非无限积压消息。
[Frame 消息约定](https://raw.githubusercontent.com/chromiumembedded/cef/master/include/cef_frame.h)、
[Router 生命周期](https://raw.githubusercontent.com/chromiumembedded/cef/master/include/wrapper/cef_message_router.h)。

业务通道有独立的消息大小、队列上限、速率及背压策略。纯 C++ 转发不必等待 Python；
用户选择 Python 业务处理器时，其处理时延不能阻塞原生帧消费/呈现。主题消息不装
图像帧，不借用 FrameProvider 的序号表示业务 ACK，也不复用 SDK 的元数据区存储
应用凭据。服务失效应有服务级错误，不能因注销一个消息订阅就销毁仍被画布使用的
共享帧；关闭浏览器仍需遵守画布 detached/读租约释放的图像寿命约定。

CEF 上游有 context 共享/非共享测试可供参考，但本轮只读取源码，没有执行 CEF
测试。检查到的 BasicCreateSharedOnDisk 位于 #if 0 中，不能把该测试代码存在当
本项目持久化已验证。实施阶段应对固定版本编写应用 A/B 同站点、多个 profile、
临时与持久化重启、并发实例及消息授权的实际验收。
[上游测试](https://raw.githubusercontent.com/chromiumembedded/cef/master/tests/ceftests/request_context_unittest.cc)。

## 必须补上的交互和控件逻辑

以下是接入工作范围，不是当前已经存在的 WebView 功能：

| 项目 | 设计要求 |
| --- | --- |
| 坐标 | 从屏幕/画布坐标反算网页 DIP，处理 fit、留边、裁剪、DPI 和页面缩放；不能直接把终端单元格坐标发给网页 |
| 鼠标与键盘 | 转发移动、按下/释放、滚轮、双击、修饰键、焦点、文本；网页先处理其滚动，宿主快捷键有明确优先级 |
| 中文 IME | composition 与 commit 分开；把网页字符边界变换到屏幕坐标放置候选窗，不仅转发最终字符串 |
| popup | PET_VIEW 和 PET_POPUP 分开管理；处理 OnPopupShow/OnPopupSize；下拉选择不能因漏掉 popup 帧而不可见 |
| 原生对话框 | 文件选择、JS 对话框、右键菜单、新窗口各有宿主策略，不属于自动得到的一张网页纹理 |
| 标签页隐藏 | 通知浏览器隐藏与节流，恢复时更新尺寸并请求有效帧；隐藏控件不等于销毁会话 |
| 分辨率变化 | 区分只拉伸图像和改变网页 viewport；后者触发响应式重排，创建新帧源并按现有 attached/detached 约定切换 |
| 虚拟列表/表格 | 复用或限制活跃 browser 数量，不能为 1000 行立即创建 1000 个网页实例 |
| 同源多视图 | 可以显示同一帧，但共享一个网页会话、滚动位置和焦点；独立交互内容需要独立 browser |
| 业务桥接 | 导航、加载状态、标题、JS 消息、权限与下载走控制通道；浏览器 renderer 崩溃和恢复有显式状态 |

CEF 提供相应的渲染、屏幕、popup、输入和 IME 接口；表内的控制策略和坐标适配
仍需本项目实现。依据：[RenderHandler](https://cef-builds.spotifycdn.com/docs/150.0/classCefRenderHandler.html)、
[BrowserHost API](https://raw.githubusercontent.com/chromiumembedded/cef/master/include/cef_browser.h)。

当前 inprocess 示例只接入单向帧供应，没有把画布输入/缩放事件送回 C++。因此它
可以复用作渲染线程结构参考，不能原样改几行就得到完整的交互浏览器。

## 附加层：平台方案与支持边界

以下“可设计”均指资料支持相应机制，不代表本项目已经在该平台测试通过。
“一个共享视觉窗口、多个逻辑画布”继续是原生附加层目标；不恢复每控件一个窗口。

| 模式 | 定位与可见性 | 呈现与输入 | 交付判断 |
| --- | --- | --- | --- |
| Windows 原生层 | HWND/UIA/显式 viewport，沿用现有绑定 | D3D11/DComp，矩形区域命中、Qt 输入 | 已有基础，接 CEF 后需回归 |
| macOS 原生层 | AX 宿主识别及跟踪，AppKit 排序 | IOSurface/Metal；透明命中需原型 | 条件支持，关键验证通过才承诺 |
| Linux X11 原生层 | XID、窗口事件、EWMH，AT-SPI/校准定位文本区 | DMA-BUF/EGL，SHAPE/XFixes 输入区域 | 有实现路径，受 WM/合成器影响 |
| Wayland 通用原生层 | 缺少任意外部窗口坐标覆盖能力 | 不能仅靠 Qt 或 Vulkan 补齐 | 不作为通用承诺 |
| Wayland 桌面协作层 | Sway/KWin/GNOME 等显式适配 | 按协议/扩展提供区域与输入 | 可选插件，分别标版本和能力 |
| 终端图像协议 | 终端自行定位、滚动和遮挡 | Kitty 像素传输；终端输入事件 | 兼容模式，非共享 GPU 的等价方案 |
| 自有窗口宿主 | 本程序拥有整个窗口与坐标 | 同窗口绘制 TUI 单元格和 WebView | 功能完整的跨平台方向，但不是外部终端附加层 |

### Windows：保留当前机制

使用 owned HWND 维持终端相对层级，DComp 合成共享纹理；不以全局 topmost 代替
终端附加关系。ConPTY 的 GetConsoleWindow 不是可见终端句柄，仍需识别实际宿主。
文本区域、分屏与隐藏标签继续由 UIA/显式校准负责，网页加入不会改变该约束。
输入区域与渲染 alpha 分开处理；DComp 画面透明不自动等于不接收点击。
[窗口关系](https://learn.microsoft.com/en-us/windows/win32/winmsg/window-features)、
[DComp](https://learn.microsoft.com/en-us/windows/win32/directcomp/basic-concepts)、
[ConPTY 句柄说明](https://learn.microsoft.com/en-us/windows/console/getconsolewindow)。

新版 composition swapchain 有进一步的呈现时间控制能力，但不等于当前 DXGI
composition swap chain，也不是本次跨平台抽象的前置条件。先保留已验证的路径，
通过统一时间戳测量后再决定是否需要另做呈现器。
[Microsoft 说明](https://learn.microsoft.com/en-us/windows/win32/comp_swapchain/comp-swapchain-portal)。

### macOS：AppKit/AX 跟随，Metal 合成，输入须设验证门槛

宿主适配器计划使用进程/窗口身份与 AX 文本对象绑定。AXObserver 可以监听目标
可访问对象；使用前检查 Accessibility 客户端信任状态。宿主拒绝访问或不提供文本
区域时，明确报告不可用或接受用户指定的 viewport，不假设整个窗口就是终端网格。
[AX 信任检查](https://developer.apple.com/documentation/applicationservices/1459186-axisprocesstrustedwithoptions?language=objc)、
[AXObserver](https://developer.apple.com/documentation/applicationservices/1462089-axobserveraddnotification?language=objc)。

**移动跟随不能只依靠 AX 通知。** Apple 对 kAXWindowMovedNotification 的说明是
在窗口移动结束时发送，而非整个拖动过程中持续发送。工程上需要前台活动时受控
查询窗口几何、缓存文本区相对偏移，空闲降频；不能把每帧网页渲染绑在 AX 查询上。
查询频率、拖动偏差与 CPU 开销需实测，不能预先承诺与 Windows 同等跟随流畅度。
[通知语义](https://developer.apple.com/documentation/applicationservices/kaxwindowmovednotification)。

AppKit 有按窗口编号进行相对排序的 order(_:relativeTo:)；addChildWindow 则要求
可用的 NSWindow 对象。远端进程的窗口编号不是本进程可用的 NSWindow/NSView 指针，
不能通过把 CGWindowID 强转指针获得跨应用父子关系。Qt 文档中的 macOS WId 是
NSView*，其 foreign-window 示例也不能证明任意第三方终端可直接 reparent。
因此独立终端模式采用跟随和排序的原型，不照搬 Win32 owner 的语义。需处理终端
和本层之间的焦点切换、最小化、切换 Space、全屏、同一终端进程的多个窗口；若
不能可靠识别当前宿主，隐藏本层而不是浮在其他应用之上。
[AppKit 排序](https://developer.apple.com/documentation/appkit/nswindow/1419672-orderwindow)、
[子窗口](https://developer.apple.com/documentation/appkit/nswindow/addchildwindow(_:ordered:))、
[Qt 原生句柄类型](https://doc.qt.io/qt-6/mixing-qt-with-other-ui-toolkits.html)。

视觉上采用透明 NSWindow + CAMetalLayer，读取自有 IOSurface-backed Metal 纹理。
跨进程用 Mach port/XPC 传递有效资源权利，使用共享 Metal 事件或可靠完成通知控制
复用；不能只传 IOSurface 指针或 Mach port 数值。Mach port 也具有释放义务。
[IOSurface Mach port](https://developer.apple.com/documentation/iosurface/iosurfacecreatemachport(_:))、
[Metal 跨进程同步](https://developer.apple.com/documentation/metal/synchronizing-events-across-multiple-devices-or-processes)、
[CAMetalLayer](https://developer.apple.com/documentation/quartzcore/cametallayer)。

部分 Apple HTML 页面只返回标题，故通过官方 DocC JSON 核对：
[IOSurface 文档数据](https://developer.apple.com/tutorials/data/documentation/iosurface/iosurfacecreatemachport(_:).json)、
[CAMetalLayer 文档数据](https://developer.apple.com/tutorials/data/documentation/quartzcore/cametallayer.json)。

**局部鼠标穿透是单窗口方案的另一关键门槛。** ignoresMouseEvents 是整个窗口级
属性；不能仅在 NSView::hitTest 返回 nil 就认定事件一定交给下层其他应用。
Qt 6.12 Cocoa 源码也提醒：该属性的初始透明命中状态与显式设置 false 不等价；
setMask 的实现是 CALayer mask，不足以单独证明任意 Metal 场景的跨进程输入洞。
因此应验证不相邻画布、透明空白、滚轮、拖动捕获及终端文本选择，才声明 native
input holes 支持。该结论是对现有实现证据的限制，不是断言 macOS 不可能局部穿透。
[Apple 属性](https://developer.apple.com/tutorials/data/documentation/appkit/nswindow/ignoresmouseevents.json)、
[Qt 6.12 Cocoa 实现](https://code.qt.io/cgit/qt/qtbase.git/tree/src/plugins/platforms/cocoa/qcocoawindow.mm?h=6.12)。

若原生洞验证不通过，可提供显式的 app_forwarded 输入模式：整个 TUI viewport
接收输入，画布外事件转成本库的 TUI 事件。它保留单视觉窗口，但不能冒充系统级
穿透，也不保证终端自身的鼠标选择。要求原生洞的应用应返回能力不满足；另一选择
是自有窗口宿主。默认不使用全局事件 tap、合成系统点击或恢复每画布一个输入窗口。
本方案不读取其他应用图像；录屏不是正常绘制的前置权限，若将来增加屏幕采集需
单独设计权限。窗口元数据在不同 macOS 版本的可访问范围仍需逐版本验证。

### X11：EWMH 跟踪 + ARGB 窗口 + 输入区域

在原生 X11 会话中，计划以 XID 标识宿主，监听 ConfigureNotify、Map/Unmap、
Destroy 和属性变化，坐标换算包含窗口装饰及客户区偏移。EWMH 的
_NET_CLIENT_LIST_STACKING、_NET_ACTIVE_WINDOW 和桌面状态用于辅助可见性判断。
WM_TRANSIENT_FOR 声明相对关系，具体层级仍受窗口管理器策略影响；不用
_NET_WM_STATE_ABOVE 让层永久压住无关应用。初版优先窗口管理器管理的辅助窗口，
override-redirect 仅在明确验证的环境使用，因为它会把更多可见性责任交给本库。
[EWMH 根窗口属性](https://specifications.freedesktop.org/wm/latest/ar01s03.html)、
[窗口属性](https://specifications.freedesktop.org/wm/latest/ar01s05.html)、
[transient 扩展](https://specifications.freedesktop.org/wm/latest/ar01s08.html)、
[Xlib 接口](https://xorg.freedesktop.org/releases/current/doc/libX11/libX11/libX11.html)。

透明呈现需要支持 alpha 的 visual 和合成管理器。用 SHAPE 1.1/XFixes 的 Input
region 描述可见捕获画布的并集，空白处由服务器正常交给底下窗口，不能只调整
alpha。输入 region 与渲染裁剪独立维护。
[X.Org SHAPE](https://x.org/releases/current/doc/xextproto/shape.html)、
[XFixes](https://xorg.freedesktop.org/archive/current/doc/fixesproto/fixesproto.txt)。

终端内部标签页、字体、padding 和分屏不能由 XID 推出。计划提供特定终端适配、
AT-SPI 和显式 viewport 三条路径。AT-SPI 的字符矩形只有 visible/showing 状态下
才有意义，且需验证具体终端实现、坐标系与缩放；不能把整个应用无障碍矩形当网格。
WezTerm CLI 能返回 window/tab/pane 关系和行列数，但其 window_id 不能无条件当
操作系统 XID，CLI 也不自动提供终端 pane 的全局矩形。
[AT-SPI](https://gnome.pages.gitlab.gnome.org/at-spi2-core/libatspi/method.Text.get_character_extents.html)、
[WezTerm CLI](https://wezterm.org/cli/cli/list.html)。

Linux GPU 路径优先评估 DMA-BUF + EGL/OpenGL 导入，保留 Vulkan 的适配空间；
默认不同时实现两套 Linux 呈现器。需要协商 DRM format/modifier、plane stride/
offset、设备与同步方式，不能假设任何 DMA-BUF 都能导入另一块 GPU。DMA-BUF
能共享内存不等于读取时已经就绪，DMA_BUF_IOCTL_SYNC 只解决 CPU 缓存一致性，
不提供独占访问。GPU acquire/release fence 与槽租约仍然必须设计。
[Linux 缓冲区交换规范](https://www.kernel.org/doc/html/latest/userspace-api/dma-buf-alloc-exchange.html)、
[同步规范](https://docs.kernel.org/driver-api/dma-buf.html)、
[EGL modifier 导入](https://registry.khronos.org/EGL/extensions/EXT/EGL_EXT_image_dma_buf_import_modifiers.txt)。

### Wayland：通用附加层不成立，采用明确的替代部署

标准 xdg-shell 没有任意设置顶层窗口绝对位置的接口，Qt 也明确说明 Wayland 不支持
setPosition。知道一个外部窗口存在、获得其可访问对象或换用 Vulkan，都不能得到
将本窗口贴到该终端任意 pane 的通用权限。
[Qt QWindow 限制](https://doc.qt.io/qt-6/qwindow.html)、
[上游 xdg-shell](https://raw.githubusercontent.com/wayland-mirror/wayland-protocols/main/stable/xdg-shell/xdg-shell.xml)。

常见替代机制的边界如下：

| 机制 | 能得到什么 | 不能据此承诺什么 |
| --- | --- | --- |
| xdg-foreign | 由另一客户端主动 export handle 后建立 transient parent 关系 | 任意坐标、pane 定位、外部 surface 的子区域合成 |
| wlr-layer-shell | output 上的层、锚点、边距、输入 region 和键盘策略 | 自动随某个终端移动、与宿主一致的相对层级、所有合成器均支持 |
| Sway IPC | GET_TREE、窗口/工作区订阅、部分几何 | 所有 WM/桌面通用或已证明无延迟原生洞 |
| KWin 脚本 / GNOME Shell 扩展 | 合成器内部可获取部分窗口几何和状态 | 普通 Wayland 客户端拥有同等能力；不同桌面版本无需适配 |
| Xwayland | X11 客户端之间的部分 X11 机制 | 覆盖任意原生 Wayland 终端；跨两种窗口体系一定有一致层级 |

依据：[xdg-foreign v2](https://raw.githubusercontent.com/wayland-mirror/wayland-protocols/main/unstable/xdg-foreign/xdg-foreign-unstable-v2.xml)、
[wlr-layer-shell 原始协议](https://raw.githubusercontent.com/swaywm/wlr-protocols/master/unstable/wlr-layer-shell-unstable-v1.xml)、
[Sway IPC](https://github.com/swaywm/sway/blob/master/sway/sway-ipc.7.scd)、
[KWin API](https://develop.kde.org/docs/plasma/kwin/api/)、
[Mutter 窗口几何](https://mutter.gnome.org/meta/method.Window.get_frame_rect.html)。
Sway/KWin/GNOME 适配均为待验证候选，不是本库已支持列表；仅存在 API 不证明
能够完成同步层级、透明命中和拖动跟随的全部要求。

layer-shell 的 top/overlay 是 output 层级，不是终端 owner；若另写协作插件，必须
根据宿主焦点、工作区、最小化和遮挡状态控制显示，避免压住无关应用。不把随时
抢键盘的 exclusive 模式当浏览器输入方案。通用协议存在 input region 并不等于
运行中的桌面一定发布 layer-shell global，应运行时协商再启用。

### 终端图像协议：解决位置兼容，不承诺 GPU 与原生窗口事件等价

Kitty graphics 将图像 placement 放在终端网格内，因此终端自行处理窗口移动、
分屏及部分滚动语义，不需要外部屏幕坐标。在满足协议能力的终端中，这也是
Wayland/远端部署的候选通路；需实际探测，不能只凭终端名称判断所有子功能都存在。

它传输 RGB/RGBA/PNG 像素，可以使用本机共享内存或文件；远端一般用分块编码。
共享内存是 CPU 像素传输，并非把 CEF 的 D3D/IOSurface/DMA-BUF 直接交给终端。
因此 GPU 网页源通常需要回读/转换，且要考虑上传、协议解析和终端绘制成本。
协议有动画、placement 和局部帧机制，但不能由此承诺 4K120 或屏幕低延迟。
[Kitty graphics 规范](https://sw.kovidgoyal.net/kitty/graphics-protocol/)。

该后端仍可把编码、传输和帧通知放在 C++，Python 不搬运像素。终端输出必须经
同一个写入器协调 ANSI 与图像消息，维护图像/placement 生命周期和裁剪；不能让
浏览器线程直接与 TUI 线程竞争写 stdout。tmux、SSH、共享内存命名空间和每终端
实现差异需单独探测。初版只支持明确通过测试的组合。

交互使用终端上报的键盘和鼠标协议，不能保证可得到像素级坐标、完整 key-up、
IME preedit、原生拖放或触控笔事件。中文输入通常只能消费终端提交的文本，不能
伪造一个完整的 OS IME 会话。报告为 terminal_events 能力；需要标准窗口完整
事件的应用应选择原生后端或自有窗口后端。Sixel/字符渲染可以是更低能力的兼容
方向，本轮不将其列为高性能交互 WebView 的首版主通路。

### 自有窗口宿主：最一致的事件与合成范围

本程序拥有窗口时，可以在同一窗口合成现有 TUI 单元格和所有浏览器纹理，不依赖
外部终端坐标、跨应用 owner 或辅助功能权限。窗口只需常规的原生输入和 IME。
这是基于各平台窗口模型的工程选择，不是已经实现的后端。

它需要新增单元格文本绘制、字体回退/整形、选区及无障碍支持；复用 Widget 树和
布局，不另起一套控件系统。若只是显示本 TUI 程序，不必实现通用 shell/PTY；若
产品还要求任意终端程序运行，则另需 PTY 与终端仿真，不能把两项混为同一工作量。
该模式明确命名 window_host，由应用选择，不在 native_overlay 不可用时静默打开。

## 显示基础架构、资源协议与能力协商

```text
Python Widget 树 / 拟议 WebView 控件
       │ 控制、布局、低频业务通知
       ▼
WebView 服务的 BrowserProvider / 普通 C++ 提供者
       │ 自有帧资源、原生同步（不经 Python 逐帧回调）
       ▼
FrameTransport ───► SceneCompositor（变换、裁剪、popup、可见性）
                           │
       ┌───────────────────┼──────────────────────┐
       ▼                   ▼                      ▼
NativeOverlayHost    TerminalGraphicsHost      WindowHost
 Win/macOS/X11       Kitty / 受限输入           自有窗口
       ▲
HostBinding：窗口身份、pane、坐标、可见性、权限和能力
```

宿主绑定与渲染设备分离：调整窗口位置不会重新创建浏览器，AX/UIA/AT-SPI 查询
不会阻塞 GPU worker。一个终端宿主一份场景；各画布保留完整矩形、裁剪矩形、
z 顺序、节点 token 和缩放变换。所有平台沿用 attach/retire/detached 的回收确认。

宿主识别应优先采用明确会话/pane 身份或终端协作，再考虑可访问文本标记与用户
校准。窗口标题、PID、环境变量中的逻辑 window_id 单独使用均不足以证明目标
就是当前输出流。终端插件须提供 viewport、字体/DPI、切页/分屏和关闭通知；
“有远程控制 API”不表示已提供这些数据，也不表示终端支持导入本库 GPU 帧。

### C++ SDK v2（拟议）

现有 public header 直接包含 d3d11_1.h，GpuFrameInfo 限定 Windows 名称和 LUID。
新公共门面应隐藏平台头文件，分别提供 native resource adapter，保留 Windows
v1 作为兼容适配器。进程内 Python 封装只持有 session/描述符；跨进程控制与
资源传递采用可版本化的本机协议，不把 C++ 对象内存布局直接当稳定 wire ABI。

| 资源 | 正确的跨进程机制 | 同步及回收 |
| --- | --- | --- |
| D3D11 | NT 共享纹理，沿用受验证的句柄/名称机制 | keyed mutex/原生完成机制及槽租约 |
| IOSurface/Metal | Mach port/XPC 资源权利，导入本进程对象 | 共享事件或完成确认，正确释放 port 和引用 |
| DMA-BUF | Unix socket 的 fd 传递，并带 plane/format/modifier 元数据 | acquire/release fence 与租约，正确 dup/close |
| CPU BGRA/RGBA | 有明确寿命的共享映射/槽 | 原生就绪通知、读租约、回收确认 |

不能将 fd、Mach port 或指针的数值经 JSON 发送后就认定另一进程拥有有效资源。
跨设备共享需探测导出/导入和同步能力，没有同一纹理跨所有 GPU 的保证；Vulkan
external memory 和 external synchronization 也是不同能力集合。
[Vulkan 官方指南](https://docs.vulkan.org/guide/latest/extensions/external.html)。

描述符需表达 version、backend、尺寸/stride、格式、alpha、色彩空间、设备身份、
generation、slot/sequence 语义和资源寿命。分辨率切换使用新 generation，忽略旧
token 事件；元数据验界后才导入资源。保持最新帧策略，不为网页积压未显示帧。

保留平台原生 D3D11、Metal、EGL 后端比本轮立刻全部换 Vulkan 风险更可控。
Qt Quick/QRhi 可以减少绘制抽象工作，但不解决外部窗口定位；直接使用 QRhi 还
需 Qt::GuiPrivate，官方不提供跨小版本的源码/二进制兼容保证。因此先评估一个
小型同场景原型，再选择是否引入；不把 Qt 私有 API 变成公共 SDK 依赖。
[QRhi 兼容性说明](https://doc.qt.io/qt-6/qrhi.html)。

### 明确的能力契约（拟议，尚无对应 API）

显示会话至少报告：host_mode、frame_backend、
gpu_shared、native_input_holes、input_delivery、ime_composition、pixel_pointer、
viewport_tracking、pane_visibility、device_sync、requires_accessibility，以及
unavailable_reason。使用方能要求 native_overlay/GPU/完整 IME，能力不满足时
返回具体错误；只有调用方允许时才选兼容模式。

这些是显示能力。应用身份、profile、Cookie 策略、JS origin 与主题权限由上层
WebView/消息服务独立协商，不加入图像描述符或 Frame SDK 的 capability 字段。

input_delivery 区分 native、app_forwarded、terminal_events。画布未消费事件只沿
本 TUI 父链传递，不伪造另一应用的点击；不以全局键盘钩子弥补平台事件缺失。
GUI 线程负责 AppKit/Qt 主线程要求，原生渲染线程处理图像；C++ 浏览器 UI 线程
按 CEF 平台约束运行，不能把当前任意 worker 的例子当成 macOS 主线程替代品。

Wayland 原生/自有窗口输入可通过 Qt 平台支持或 text-input 协议对接 IME，需处理
preedit、commit、删除周边文字和光标矩形；协议的 UTF-8 字节索引不能直接当
Python 字符索引。具体 toolkit/合成器协议版本要在实现阶段固定。
[text-input 上游规范](https://raw.githubusercontent.com/wayland-mirror/wayland-protocols/main/unstable/text-input/text-input-unstable-v3.xml)。

## Qt、Ultralight 和 Wry 的取舍

Qt WebEngine 的 Chromium 图像可以进入 Qt Quick 的 GPU 管线，QQuickRenderControl
支持离屏目标，也能借用外部 D3D/Metal/Vulkan 设备。但“Quick 能输出纹理”与
“本项目选择的 WebEngineView 在各平台、无可见窗口下稳定更新”是两个检查项。
本轮没有运行该组合，应先制作小型原型验证隐藏状态、动画、WebGL、popup、IME
和目标纹理，不使用 grabWindow 定时截图作为高性能架构。
[Quick API](https://doc.qt.io/qt-6/qquickrendercontrol.html)、
[WebEngine GPU 管线](https://doc.qt.io/qt-6/qtwebengine-features.html)。
现有 PySide6-Essentials 不等于已带 WebEngine；Qt WebEngine 源码构建要求也不能
套用当前 C++17 小型 SDK 的要求。发行时包含 Qt 与 Chromium 两部分许可和资源。
[平台构建说明](https://doc.qt.io/qt-6/qtwebengine-platform-notes.html)、
[官方许可说明](https://doc.qt.io/qt-6/qtwebengine-licensing.html)。

Ultralight 的 GPUDriver 很适合把 HTML UI 画进引擎纹理，但 2.0 文档列出 WebGL、
WebGPU、WebRTC、Service Worker 等未提供；媒体、JIT 和其他能力还有版本/授权
等级差异。其基础 WebKit 版本为 Safari 16.4 对应代际，不能当作完整 Chromium。
旧 wiki 与 2.0 文档在媒体、Canvas 等能力上有冲突，本轮以 2.0 专门文档为准。
若用途是自有受控 HTML 页面，可以保留它；若希望随意加载第三方现代网页，优先
CEF。授权对 SDK/库再分发是否适用需按具体版本条款核实。
[2.0 功能表](https://ultralig.ht/docs/2.0/supported-web-features)、
[版本差异](https://ultralig.ht/docs/2.0/editions-and-feature-macros)、
[授权入口](https://ultralig.ht/pricing/)。

Wry 的通用接口面向原生窗口：Windows 使用 WebView2，macOS 使用 WebKit，Linux
使用 WebKitGTK。能创建子 WebView 不等于能获得统一的离屏 GPU 帧。若把它直接
作为原生子窗口使用，就需要重新处理原生表面与列表裁剪、滚动和弹窗的组合问题。
现有单窗口多画布架构更适合消费纹理，因此本轮不推荐将 Wry 放在帧适配层。
[官方 API](https://docs.rs/wry/latest/wry/)。

## 消息循环、进程与性能

CEF 是多进程浏览器。即使主 browser 对象通过 pybind11 嵌入 Python 进程，网页
renderer/GPU 等辅助进程仍然存在；不能承诺整个浏览器单进程。macOS 的主线程和
原生应用消息循环也不能直接套用当前任意 C++ worker 线程示例。通用默认方案建议
采用原生浏览器宿主及控制 IPC，进程内模式作为有平台生命周期约束的可选部署。
[CEF 进程与消息循环](https://chromiumembedded.github.io/cef/general_usage.html)。

固定 CEF 版本的运行时交付需要处理资源、辅助程序和沙箱。尤其 M138 起 Windows
沙箱引导方式已有变化，不能照搬旧版 cef_sandbox 静态库教程，也不能默认通过关闭
沙箱解决打包。这里属于浏览器运行时设计，不影响图像走 C++ 数据平面的要求。
[CEF 官方沙箱集成](https://chromiumembedded.github.io/cef/sandbox_setup.html)。

本轮没有测试各引擎的 FPS、内存或端到端延迟。已有纯色 GPU producer 的测量不能
代表网页渲染性能。建议原型记录：网页 JS/rAF 时间、CEF 回调时刻、原生复制完成、
publish、Present 返回，以及 CPU/GPU 占用；用外部手段另测输入到屏幕响应。

完整 BGRA 帧的数据量按 width*height*4*fps 计算，3840×2160×4×60 约为
1.99 GB/s，每一次完整内存遍历都会增加成本。这是理论数据量而非本机带宽实测。
GPU 路径也要计入复制、颜色转换、popup 合成、同步和最终绘制的成本。静态网页应
事件驱动更新，隐藏网页应节流；先验证 720p/1080p、60/120Hz，再评估 4K。

## 合并实施计划

本节是统一实施计划，覆盖浏览器、帧传输和附加层。阶段不等于本轮全部承诺完成；
已有 Windows 基础作为复用条件。报告不在未知硬件验证成本时
给出固定工期。不同宿主有明确能力范围，不将终端协议或自有窗口包装成同一种附加层。

### 里程碑与完成条件

2026-10-17 当前进度：M1/M2 已有独立 CEF 服务、portable CPU 图像接口、WebView
输入适配和隔离/消息测试；M6 的 Linux window_host 在 WSLg X11/Wayland 分别跑通。
初版 Windows 完整桌面测试曾在前台/层级/恢复检查失败。焦点与后台显示修复后，
Windows Terminal 的所有权、层级、移动缩放、输入、透明命中、Tabs/modal 和恢复
检查现已通过。默认 WebView 导航与交互分开，Tab 保留 TUI 焦点链；后台显示保留
相对 owner 的窗口层级，不强制隐藏或全局置顶。M0/M3/M4 的 X11 外部终端附加层、
Linux GPU 导入和 M5/M7 均未完成；原生 OS 输入法候选窗与更广泛桌面验证仍待完成。
下表 macOS 行仅保留历史方案，本轮全部排除，不作为待交付支持。

| 阶段 | 依赖与具体工作 | 可评审产物 / 完成条件 |
| --- | --- | --- |
| M0：风险原型 | 最先验证 macOS 跟随、窗口排序、透明命中、Space/全屏；X11 合成/input region；Wayland globals/终端协议探测；固定 CEF 版本并验证 context/目录单例 | 各模式能力 JSON、最小原型、硬件/系统记录；应用 A/B 存储隔离原型。macOS 原生洞失败则明确限制模式；Wayland 不具备协作则不启动伪附加层 |
| M1：分层协议 | 显示侧冻结 FrameSource/Transport、HostBinding、SceneCompositor 和能力契约；另行冻结 BrowserService/ProfileRegistry/AppMessageBus 的服务协议，互不添加对方字段 | 平台无关图像头文件与 v1 适配、独立服务控制协议；普通图像无需 CEF/总线，profile/消息测试无需加载图像 SDK |
| M2：CEF 上层服务与显示闭环 | 服务侧实现每应用根、独立/共享/临时 profile、主题授权与消息生命周期；再通过 BrowserProvider 接现有 Windows BGRA/GPU 画布、输入/IME/popup/导航 | 独立服务测试通过；拟议 WebView 控件可用，HTML/URL/内嵌示例，应用隔离与双页联动示例；图像性能另行验收 |
| M3：跨平台资源与合成 | 依赖 M1 和对应 M0 验证；新增 macOS IOSurface/Metal、Linux DMA-BUF/EGL 和 CPU 共享内存基线 | 本机双进程图案/alpha/同步/设备不匹配测试；帧生成和呈现独立于 GIL；分辨率换代无资源越界 |
| M4：原生宿主集成 | 依赖 M2/M3；先 X11，再按 M0 结果接 macOS；补文本区校准、分屏/标签可见性和焦点 | 每个明确终端/WM 组合的真实拖动、缩放、遮挡、输入及退出验证。保留一个视觉层窗口，数量不随画布数增长 |
| M5：终端协议兼容 | 依赖 M1/M2；C++ Kitty 输出器、placement/裁剪/回收、与 ANSI 写入协调、终端输入映射 | 支持的终端/复用器组合清单；报告 terminal_events 和 GPU 回读成本；不把完整 IME/拖放列为已支持 |
| M6：自有窗口宿主 | 依赖 M1/M3；复用场景与 Widget 树，新增单元格文本呈现和正常窗口输入 | 在 Windows/macOS/Linux（含 Wayland）拥有窗口内的 TUI+WebView；验证字体/中文/选区/输入。明确它不是外部终端嵌入 |
| M7：Wayland 专用协作 | 依赖 M0；只有明确要求外部终端附加时再进入；先选一个合成器/终端组合，设计可安装插件及控制通道 | 固定合成器版本、协议探测和宿主权限，逐项验证层级/位置/命中/工作区；不通过则该组合 unavailable，不扩大为所有 Wayland |
| M8：交付与性能 | 对已通过的 M2–M7 模式执行打包、CI 和真实桌面测试；处理浏览器资源、辅助程序、macOS 签名及版本对应关系 | 安装包/可复现构建、示例、SDK/API 文档、能力矩阵、原始性能数据和已知限制；每种模式有明确维护范围 |

M0 应在大规模改造前完成；M2 可先交付 Windows 浏览器闭环。M3/M4 不能仅靠
Windows CI 判断完成；M5 提供终端内兼容范围，M6 提供自有窗口内完整事件范围。
WebView 的存储/消息可先在不加载图像 SDK 的独立 CEF 测试宿主内验收，显示后端
可用无浏览器图案源独立验收；只有组合验收才同时需要两者。
M7 是桌面环境专用扩展，不阻塞其他已验证后端交付；它也不会被列为通用 Wayland
原生附加层。要求“任意现有终端、不改终端、完整原生事件、始终贴合、三平台相同”
的组合目前没有本轮资料证明可行，不把该组合作为可保证的验收承诺。

### 仓库工作包（拟议路径，不是现有实现）

| 路径/模块 | 责任 |
| --- | --- |
| native/canvas/include/comfortable/* | 平台无关帧/能力门面，平台资源 API 分头文件，Windows v1 适配 |
| native/canvas/platform/{win32,cocoa,x11,wayland} | 呈现/输入/宿主适配；Wayland 下区分自有窗口和专用协作 |
| native/browser/* | 上层 BrowserService/ProfileRegistry、CEF 生命周期、独立服务 IPC、OSR 到普通帧源的适配 |
| native/services/*（可选） | AppMessageBus/通用控件业务消息；不放入 native/canvas 公共 SDK |
| src/comfortable_tui/webview.py | 上层 URL/profile/业务事件/状态，保持 Python 不逐帧处理图像 |
| src/comfortable_tui/*host* | 宿主身份、能力选择、viewport 与错误状态；慢查询和帧时钟分离 |
| examples/webview*.py | 导航、中文输入、内嵌、应用 A/B 隔离、同应用双页消息、分 profile 联动及进程内/独立宿主示例 |
| examples/verify_*、tests/* | 协议/生命周期自动化和真实桌面原型测试，失败时输出能力原因 |
| docs/*、CI | 同一报告持续更新，补用户指南、构建/打包、模式差异和测试证据 |

不把 CEF/Qt 运行时强塞进基础字符 TUI 依赖；保持可选安装和本机能力检查。
默认原生 browser host 可复用多个 browser/session，不能每一行都启动一套宿主。
这里的复用以同一应用的数据边界为前提，不能默认混用不同应用的根目录和消息空间。
进程内例子额外记录平台主线程、初始化/关闭与 GIL 约束，CEF 的辅助进程仍可存在。

### 统一验收矩阵

先独立验收上层服务：

| 用例 | 必须观察到的行为 |
| --- | --- |
| 应用 A/B 打开同一个 origin | Cookie（含服务器侧验证的 HttpOnly）、localStorage、IndexedDB、站点缓存/权限按应用隔离；不能只靠不同 URL 规避检查 |
| 同应用两个 profile | 不共享账号/站点存储；相同 profile 的多个页面按 Chromium 规则共享，不假设 sessionStorage 自动共享 |
| 临时/持久化与重启 | 临时 profile 不恢复登录；持久化只恢复对应应用/profile 的数据；会话 Cookie 遵循独立选项 |
| 同应用多实例 | 复用该应用服务或明确隔离，不并发写同一个 CEF root，不因锁冲突退回公共目录 |
| 消息独立于存储 | 隔离 profile 可通过授权主题联动；不同应用同名主题不互通；不同 origin/子 frame 不自动获得桥接权限 |
| 刷新/导航/销毁/崩溃 | 旧 document_token 的订阅与回调失效，待处理请求结束或超时，ID 复用不误投递 |
| 业务消息压力 | 有界队列、背压/取消及 ACK 语义，不占图像元数据或阻塞 GPU 呈现 |
| 模块独立性 | 普通图像示例不依赖 CEF/profile/bus；CEF 存储与业务消息测试不依赖帧传输 SDK |

组合显示验收：

| 验收项 | Windows 原生 / X11 原生 | macOS 原生 | 终端协议 | 自有窗口 |
| --- | --- | --- | --- | --- |
| 滚动、Tabs、DataTable、VirtualList、Text 裁剪 | 必测 | 必测 | 必测 placement 回收 | 必测 |
| 透明洞与其他应用点击 | 原生服务器命中测试 | 关键门槛；app_forwarded 必须另标 | 不适用，无外部层 | 不适用，窗口内部命中 |
| 窗口拖动、缩放、DPI/多屏、最小化 | 必测 | 另测 AX 时延、Retina、Space/全屏 | 终端处理位置，本库测更新和网格变化 | 自有窗口的常规事件 |
| 中文 IME、焦点、网页 select、popup | 必测 | 必测候选窗坐标与焦点返回 | 测提交文本，preedit 不保证 | 必测原生 IME |
| 遮挡/应用切换，不压住其他程序 | 必测 | 关键门槛 | 无外部层 | 常规窗口行为 |
| 同源多视图与独立 browser 会话 | 必测共享焦点语义和资源去重 | 同左 | 同左，另测 image/placement ID | 同左 |
| 消费者慢/崩溃、生产者退出、换尺寸 | 必测超时/重连与 detached | 必测 port/引用回收 | 必测未完成传输和图像删除 | 必测 GPU 资源回收 |
| Python 持有 GIL | 原生帧路径应继续 | 原生帧路径应继续；主循环不能由 Python 逐帧驱动 | C++ 传输继续，布局事件仍需 Python | 同原生路径 |

Wayland 专用协作模式逐项沿用原生验收，另测未发布协议、插件禁用/崩溃、工作区
切换、输出热插拔和桌面升级。Xwayland 组合单列；不能把其结果移植为原生 Wayland
结果。无障碍权限拒绝/撤销、宿主关闭、同 PID 多窗口和不同分屏都要覆盖。

性能用相同页面、源尺寸和显示面积对比静态页、长文滚动、Canvas/WebGL 动画、
1/4/16 个视图；固定 browser 会话数，另外测独立会话成本。记录 p50/p95/p99 的
生产/复制/就绪/提交帧龄、CPU/GPU/显存/映射数、丢弃帧、几何跟随误差与采样间隔。
就绪通知用事件/等待对象，几何跟踪按活动状态调频；不把全局高频轮询作为默认。
没有显示端时间戳/外部设备时，不把 Present 返回、swap 返回或协议 ACK 称为
input-to-photon，也不把 rAF 次数称为屏幕 FPS。

### 决策与风险退出条件

| 风险 | 验证/退出条件 | 保留的可用路径 |
| --- | --- | --- |
| macOS 实时跟随或原生洞不稳定 | M0 原型不能满足指定交互和遮挡用例 | 显式 app_forwarded 或 window_host；native_holes 要求返回不满足 |
| 某 GPU 无法共享/导入 | 格式/设备/同步探测失败，禁止读无效句柄 | 仅在允许时 CPU 后备，并报告实际后端 |
| Wayland 无宿主协作 | 无相应 globals/插件、无法获得 pane 位置与可见性 | terminal_graphics 或 window_host；不创建全局置顶伪附加层 |
| 浏览器打包/主线程冲突 | 固定版本下进程内启动或关闭不能可靠完成 | 原生独立 browser host；Python 仍不接触像素 |
| CEF 资源回调寿命与异步复制冲突 | 不能证明复制/同步在资源回池前安全 | 改为可靠同步/自有中转资源；不能用缓存外部句柄换性能 |
| profile 根冲突或隔离测试失败 | 存储泄漏、同根多 runtime、共享 context 错配 | 服务创建失败或显式临时模式；不回落公共 profile，图像 SDK 不承担修复 |
| 消息跨应用或过期文档误投递 | 权限/生命周期测试失败 | 撤销该桥接能力，保留图像显示；不靠共享 Cookie 实现联动 |
| 终端协议吞吐不足 | 同页面分辨率下 p95 或 CPU/传输量无法满足目标 | 明确调低频率/分辨率或选择原生/自有窗口，不伪报 GPU 模式 |

后续实现以此文档为同一基线更新：能力、完成阶段、验证环境和数据同时变更。
当前已交付原生 CPU 基线与可复现实测；剩余后端不能因本基线通过而宣称支持。
macOS 按用户要求排除。未来实现应继续在同一能力矩阵标明后端、完成状态和实测证据。
