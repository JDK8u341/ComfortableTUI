# WebView 与跨平台附加层：完整调研与实施计划

调研日期：2026-10-17。WebView 调研 8 组、附加层补充调研 17 组，共使用 Exa
检索 125 条候选结果，按完整 URL 去重为 121 条。候选数包含旧资料及重复内容的
不同 URL，不等于 121 个独立验证结论。浏览器部分取得 19 个官方页面正文；附加层
部分核对 Apple 文档及 DocC JSON、Qt 上游源码、X.Org、Wayland、Linux/Khronos
规范、终端和桌面环境官方接口。论坛和未取得正文的示例文件未作为决定性依据。
检索清单及抓取限制保存在仓库
`exa-results/webview-2026-10-17/sources.json`。

**结论：采用 CEF 离屏渲染、平台化帧 SDK、共享场景和可替换宿主后端。**
Windows 延续现有 D3D11/DComp；X11 可设计原生附加层；macOS 采用 AppKit/Metal
并先验证跨应用跟随和透明命中。Wayland 不能承诺任意终端原生附加层，需提供终端
图像协议、自有窗口或明确限定桌面环境的协作后端。浏览器跨平台、图像传输跨平台、
外部终端嵌入跨平台是三个不同交付项。本报告合并选型、架构、能力矩阵、风险与
同一套实施计划；新后端和 WebView 均尚未实现，没有新增跨平台性能实测。

## 范围与现状

| 能力 | 仓库现状 | 本计划目标 |
| --- | --- | --- |
| TUI 布局及内嵌画布 | 已有列表、表格、Text、Tabs、裁剪与事件 | 复用布局语义，浏览器与普通图像统一作为源 |
| C++ 图像数据平面 | Windows D3D11 共享纹理及 CPU 兼容输入 | 抽象资源、同步和传输，保留 Windows v1 兼容 |
| 共享附加层 | Windows，一个 App 一个辅助层窗口 | 同宿主一份场景，新增经过验证的宿主后端 |
| 原生输入 | Windows 画布事件及 IME 数据已有 | 补浏览器输入适配，按模式报告完整或受限能力 |
| WebView | 只有研究，无浏览器实现 | CEF provider、导航/JS 控制接口、可运行示例 |
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

## CEF 怎样接入现有数据平面

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
让 producer.cpp 执行网页截图。普通页面事件和 URL/标题变化可以按需通知 Python。

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

## 统一架构、资源协议与能力协商

```text
Python Widget 树 / 拟议 WebView 控件
       │ 控制、布局、低频业务通知
       ▼
BrowserRuntime / 普通 C++ 提供者
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

不提供单个 supports_webview 布尔值。会话至少报告：host_mode、frame_backend、
gpu_shared、native_input_holes、input_delivery、ime_composition、pixel_pointer、
viewport_tracking、pane_visibility、device_sync、requires_accessibility，以及
unavailable_reason。使用方能要求 native_overlay/GPU/完整 IME，能力不满足时
返回具体错误；只有调用方允许时才选兼容模式。

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

本节是唯一实施计划，覆盖浏览器、帧传输和附加层。各阶段均尚未执行；已有 Windows
基础只作为复用条件。报告本身不承诺新的后端已支持，也不在未知硬件验证成本时
给出固定工期。不同宿主有明确能力范围，不将终端协议或自有窗口包装成同一种附加层。

### 里程碑与完成条件

| 阶段 | 依赖与具体工作 | 可评审产物 / 完成条件 |
| --- | --- | --- |
| M0：风险原型 | 最先验证 macOS 跟随、窗口排序、透明命中、Space/全屏；X11 合成/input region；Wayland globals/终端协议探测；固定 CEF 版本 | 各模式能力 JSON、最小原型、硬件/系统记录。macOS 原生洞失败则明确限制模式；Wayland 不具备协作则不启动伪附加层 |
| M1：公共协议 | 根据 M0 冻结 FrameSource/Transport、HostBinding、SceneCompositor 与 capability 契约；隔离 Win32 头文件，保留 v1 适配 | 平台无关头文件、控制 ABI/wire 说明、异常/超时/生命周期测试；旧 gpu_canvas/inprocess 示例仍通过 |
| M2：CEF 首个完整闭环 | 依赖 M1；先在 Windows 接 BrowserRuntime、BGRA 路径、鼠标/键盘/IME、popup、导航，再接共享 GPU 适配 | 拟议 WebView 控件实际可用；HTML/远端 URL、列表/表格/Text/Tabs 示例；GPU 和显式 CPU 模式分别验收 |
| M3：跨平台资源与合成 | 依赖 M1 和对应 M0 验证；新增 macOS IOSurface/Metal、Linux DMA-BUF/EGL 和 CPU 共享内存基线 | 本机双进程图案/alpha/同步/设备不匹配测试；帧生成和呈现独立于 GIL；分辨率换代无资源越界 |
| M4：原生宿主集成 | 依赖 M2/M3；先 X11，再按 M0 结果接 macOS；补文本区校准、分屏/标签可见性和焦点 | 每个明确终端/WM 组合的真实拖动、缩放、遮挡、输入及退出验证。保留一个视觉层窗口，数量不随画布数增长 |
| M5：终端协议兼容 | 依赖 M1/M2；C++ Kitty 输出器、placement/裁剪/回收、与 ANSI 写入协调、终端输入映射 | 支持的终端/复用器组合清单；报告 terminal_events 和 GPU 回读成本；不把完整 IME/拖放列为已支持 |
| M6：自有窗口宿主 | 依赖 M1/M3；复用场景与 Widget 树，新增单元格文本呈现和正常窗口输入 | 在 Windows/macOS/Linux（含 Wayland）拥有窗口内的 TUI+WebView；验证字体/中文/选区/输入。明确它不是外部终端嵌入 |
| M7：Wayland 专用协作 | 依赖 M0；只有明确要求外部终端附加时再进入；先选一个合成器/终端组合，设计可安装插件及控制通道 | 固定合成器版本、协议探测和宿主权限，逐项验证层级/位置/命中/工作区；不通过则该组合 unavailable，不扩大为所有 Wayland |
| M8：交付与性能 | 对已通过的 M2–M7 模式执行打包、CI 和真实桌面测试；处理浏览器资源、辅助程序、macOS 签名及版本对应关系 | 安装包/可复现构建、示例、SDK/API 文档、能力矩阵、原始性能数据和已知限制；每种模式有明确维护范围 |

M0 应在大规模改造前完成；M2 可先交付 Windows 浏览器闭环。M3/M4 不能仅靠
Windows CI 判断完成；M5 提供终端内兼容范围，M6 提供自有窗口内完整事件范围。
M7 是桌面环境专用扩展，不阻塞其他已验证后端交付；它也不会被列为通用 Wayland
原生附加层。要求“任意现有终端、不改终端、完整原生事件、始终贴合、三平台相同”
的组合目前没有本轮资料证明可行，不把该组合作为可保证的验收承诺。

### 仓库工作包（拟议路径，不是现有实现）

| 路径/模块 | 责任 |
| --- | --- |
| native/canvas/include/comfortable/* | 平台无关帧/能力门面，平台资源 API 分头文件，Windows v1 适配 |
| native/canvas/platform/{win32,cocoa,x11,wayland} | 呈现/输入/宿主适配；Wayland 下区分自有窗口和专用协作 |
| native/browser/* | CEF 生命周期、OSR、输入/IME/popup、控制 IPC、资源转换 |
| src/comfortable_tui/webview.py | URL/会话/业务事件/状态，保持 Python 不逐帧处理图像 |
| src/comfortable_tui/*host* | 宿主身份、能力选择、viewport 与错误状态；慢查询和帧时钟分离 |
| examples/webview*.py | 导航、中文输入、内嵌列表/表格/Text、切页、独立/受约束进程内示例 |
| examples/verify_*、tests/* | 协议/生命周期自动化和真实桌面原型测试，失败时输出能力原因 |
| docs/*、CI | 同一报告持续更新，补用户指南、构建/打包、模式差异和测试证据 |

不把 CEF/Qt 运行时强塞进基础字符 TUI 依赖；保持可选安装和本机能力检查。
默认原生 browser host 可复用多个 browser/session，不能每一行都启动一套宿主。
进程内例子额外记录平台主线程、初始化/关闭与 GIL 约束，CEF 的辅助进程仍可存在。

### 统一验收矩阵

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
| 终端协议吞吐不足 | 同页面分辨率下 p95 或 CPU/传输量无法满足目标 | 明确调低频率/分辨率或选择原生/自有窗口，不伪报 GPU 模式 |

后续实现以此文档为同一基线更新：能力、完成阶段、验证环境和数据同时变更。
现阶段只有调研和计划已完成，macOS/X11/Wayland 新后端及 WebView 都仍待实施。
