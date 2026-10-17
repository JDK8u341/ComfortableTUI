# 跨平台 WebView 与画布接入调研

调研日期：2026-10-17。使用 Exa 完成 8 组检索，共返回 40 条候选结果，URL 去重后
37 条，并取得 19 个不同官方文档/上游头文件页面的正文。论坛、旧提交和未返回正文
的示例文件未用作决定性依据。检索清单保存在仓库
`exa-results/webview-2026-10-17/sources.json`。

**结论：可基于现有画布接入 WebView，优先选择 CEF 的离屏渲染接口。**
CEF 提供网页图像和输入接口；现有 SDK 负责帧传输，OverlayCanvas 负责布局与裁剪。
但是当前 SDK、GPU 合成器和终端宿主绑定都是 Windows 实现，接入跨平台浏览器
不会自动让终端附加层跨平台。本轮只调研，没有新增 WebView API 或运行浏览器基准。

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

## 跨平台需要拆成三个层次

| 层次 | Windows | macOS | Linux |
| --- | --- | --- | --- |
| 浏览器离屏接口 | CEF 有像素/GPU API | CEF 有像素/IOSurface API | CEF 有像素/原生缓冲区 API |
| 本项目帧 SDK 与 GPU 合成 | 已有 D3D11/DComp | 尚需 IOSurface/Metal 后端 | 尚需 DMA-BUF/EGL 或 Vulkan 后端 |
| 贴合外部终端窗口 | 已有宿主识别与定位 | 尚需宿主绑定、可见性与窗口跟踪 | X11 尚需适配；Wayland 需要其他呈现/协作机制 |

现有 public header 直接包含 d3d11_1.h，GpuFrameInfo 也限定 Windows 通道名和 LUID。
真正跨平台需新增带后端标签的描述符/资源接口，保留当前 Windows v1 协议的兼容性。
Windows 资源用 NT HANDLE，macOS 用可跨进程传递的 IOSurface 机制，Linux 传
DMA-BUF fd、plane 信息、modifier 和同步信息。fd/指针不能作为整数经 JSON 发送就
视为在另一进程有效；需要操作系统级句柄传递和明确的所有权、释放确认。

Wayland 是最大的宿主限制。标准 xdg-shell 由合成器决定顶层窗口位置，客户端没有
任意设置绝对几何、追踪并覆盖另一终端窗口的通用能力。不能把 CEF 支持 Wayland
解释为本库附加层可以通用于所有 Wayland 终端。候选方向是终端自身协议/插件、
合成器特定集成，或自有窗口中的终端与网页共同合成。图像协议后端还要另外解决
输入与性能，并不提供现有共享 GPU 纹理的等价承诺。
[Canonical/Mir 官方解释](https://canonical.com/mir/docs/latest/explanation/window-positions-under-wayland/)。

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

## 建议实施顺序与验收

1. 固定一套 CEF 二进制和源码版本，先验证三平台离屏 API、运行时启动与关闭。
2. Windows 做 BrowserProvider + BGRA 兼容通路，验证导航、输入、IME、popup 和标签隐藏。
3. 接入 Windows GPU 回调到自有共享纹理，核验颜色、alpha、资源寿命和同 GPU 约束；CPU 模式显式报告。
4. 提供拟议 WebView 控件与控制事件接口，分别给出独立宿主和受约束的进程内使用方式。
5. 抽象帧描述符与合成后端，实现 macOS、Linux 图像通路；独立验证其终端宿主可用范围。
6. Wayland 通过独立的终端/合成器协作或自有窗口方案验收，不承诺任意终端透明覆盖。

最低验收覆盖中文输入、网页选择框、缩放后点击命中、快速切页、列表/表格裁剪、
同一源多视图、浏览器崩溃、分辨率替换、解除订阅与退出回收，并包含受控的 Canvas/
WebGL 动画及滚动页面。最终公开能力应区分浏览器引擎支持、帧后端支持、终端宿主
支持和实测结果。
