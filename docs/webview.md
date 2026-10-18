# WebView 服务与 Linux 窗口宿主

本模块是可选的上层服务。CEF 管理网页、Cookie 和 JS；普通图像 SDK 只认识帧。
基础字符 TUI 不依赖 CEF 或 Qt。默认使用 Windows 与 Linux 的原生 CPU 共享帧闭环，
Windows 可通过 `open(..., render_mode="gpu")` 选择 [GPU 纹理模式](webview-gpu.md)。
不支持 macOS；Linux 的 DMA-BUF 导入未实现，X11/Wayland 仍使用 CPU WindowHost。

## 构建

固定 CEF：`152.0.14+g8a0b5b9+chromium-152.0.7977.167`。下载脚本从官方构建索引
取得 minimal 包并核对索引内的大小和 SHA-1，拒绝归档链接和越界路径。
CEF 的发行包体积数百 MB，完整运行目录必须与辅助程序一起保留。

Windows 需要 x64、VS 2022 C++ 工具链、CMake、Python 3.10+：
CEF 152 宿主/wrapper 使用 C++20；通用 CPU 帧 SDK 与 Linux Qt 宿主保持 C++17。

```powershell
python -m pip install -e ".[dev,overlay]"
$env:COMFORTABLE_TUI_BUILD_GPU = '1'
python -m pip install --no-build-isolation -e .
$cefRoot = python tools/fetch_cef.py --platform windows64
cmake -S . -B .research/browser-build -A x64 "-DCEF_ROOT=$cefRoot" -DCOMFORTABLE_TUI_BUILD_BROWSER=ON
cmake --build .research/browser-build --config Release --target ctui_browser
python examples/webview.py
```

Windows 启动程序使用 CEF 的 `bootstrapc.exe` 与同名 `ctui_browser.dll`，保持该版本
沙箱入口。不要仅复制 exe 或 libcef.dll。交付目录还包括 CEF 的资源、locale、
快照、依赖 DLL 和许可证；构建会复制官方 Release/Resources 的完整内容。

Ubuntu 22.04 / WSL2（CEF 用 GCC 11+；Qt 宿主用 Qt 6.2+）：

```bash
sudo apt-get update
sudo apt-get install -y build-essential cmake ninja-build python3-venv python3-dev \
  libgtk-3-dev libnss3 libgbm1 libasound2 libatk-bridge2.0-0 libxkbcommon-x11-0 \
  qt6-base-dev qt6-wayland
python3 -m venv .venv-linux
.venv-linux/bin/python -m pip install -e '.[dev]'
CEF_ROOT=$(.venv-linux/bin/python tools/fetch_cef.py --platform linux64)
cmake -S . -B build/linux -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCEF_ROOT="$CEF_ROOT" -DCOMFORTABLE_TUI_BUILD_BROWSER=ON \
  -DCOMFORTABLE_TUI_BUILD_LINUX_HOST=ON
cmake --build build/linux --target ctui_browser ctui_linux_canvas -j 4
export COMFORTABLE_TUI_BROWSER_EXECUTABLE="$PWD/build/linux/native/browser/ctui_browser"
export COMFORTABLE_TUI_LINUX_HOST_EXECUTABLE="$PWD/build/linux/native/host/ctui_linux_canvas"
.venv-linux/bin/python examples/webview.py --platform x11
.venv-linux/bin/python examples/webview.py --platform wayland
```

在 WSL 中建议把构建输出和 venv 放在 Linux 文件系统，源码可位于 `/mnt/c`。
WSLg 下若默认的 `wayland-0` 链接缺失，辅助进程会在 WSL 环境中检查现存的
`/mnt/wslg/runtime-dir/wayland-0` 并仅为该进程使用绝对 WAYLAND_DISPLAY；不修改
XDG_RUNTIME_DIR、用户 shell 或 systemd，不替换用户指定的其他 compositor/socket。
没有可用 socket 时仍报错，不静默改用 X11。
运行需要真实的 X11/Wayland 会话；Qt 的 offscreen/minimal 不会被接受为验证通过。
同一套源码支持 Python 3.10；部分较早的 3.10 发行版需更新到带 tarfile 安全过滤器的
维护版本，或用较新的 Python 执行 SDK 下载脚本。

沙箱要求保持启用。安全分支中 `BrowserService(allow_no_sandbox=True)` 会拒绝启动，
主 helper 仅接受固定启动参数，旧 helper 缺少安全协议时也拒绝使用。
权限与原生校验边界详见[安全模型](security-model.md)。图像和浏览器是各自的独立进程，Python 不持有
逐帧像素租约；Linux Qt 宿主也在 C++ 中读取、缩放与绘制共享缓冲区。

## 最小使用

```python
from comfortable_tui import App, BrowserService, Panel, WebView, WindowHost

with BrowserService("com.example.notes") as browser:
    account = browser.profile("account-a", persistent=True)
    page = browser.open("https://example.com", profile=account,
                        origins=["https://example.com"])
    with App(Panel(WebView(page), "Notes")) as app:
        # Windows terminal:
        app.run()
        # Linux, instead of app.run():
        # with WindowHost(app, display_platform="wayland") as host:
        #     host.run()
```

运行本机完整示例 `examples/webview.py` 可看到两个独立 profile、消息联动，以及
VirtualList、DataTable 和 Text 内嵌视图。所有示例网页由本机 HTTP 服务提供，
验证不登录外部账号。Tab/Shift+Tab 始终沿原有 TUI 焦点链切换；F6 切换到菜单并可
返回原控件。WebView 默认是导航态，方向键和滚轮可交给外层列表/表格或相邻控件。
点击网页或在 WebView 上按 Enter 进入交互态，此时键盘、IME 和滚轮交给网页；
焦点在 ListView/DataTable 本身时，Enter 也会进入当前选中行中第一个可见且可用的
WebView，包括通过 HBox 等容器间接内嵌的视图。进入时的这次 Enter 不发送给网页；
没有可进入的视图时仍执行原有行确认回调。
Esc 返回导航态，若嵌在列表/表格中则直接把焦点交回该控件，方向键导航同样优先
交回外层选择控件。离开控件、隐藏画布或退出原生焦点也会结束交互态。
Ctrl+Q/Ctrl+C 退出，应用注册的全局快捷键仍优先。

关闭顺序必须是显示宿主 → App 控件 → BrowserService；示例中的 context 嵌套遵循
此顺序。一个页面可以有多个 WebView 镜像，它们共享同一个 DOM、焦点、滚动、源
分辨率和 Cookie。需要独立页面就调用两次 `open()`。

## BrowserService

`BrowserService(application_id=None, *, executable=None, data_root=None,
display_platform=None, allow_no_sandbox=False, timeout=30, policy=None, limits=None)`：

| 参数 | 行为 |
| --- | --- |
| application_id | 由应用指定的稳定身份；不根据 python.exe 推导。省略时创建临时私有根 |
| data_root | 自定义应用根的父目录，实际目录再加 application_id 的哈希；要求 application_id |
| executable | CEF 原生宿主完整路径；其次读取 COMFORTABLE_TUI_BROWSER_EXECUTABLE，最后尝试仓库默认构建目录 |
| display_platform | Linux 的 x11 / wayland，传给 Chromium Ozone；Windows 省略 |
| timeout | 进程首次握手超时，单位秒 |
| policy | 页面默认 BrowserPolicy；open(policy=...) 整体覆盖；默认固定初始导航/资源 origin，JS 求值关闭 |
| limits | BrowserLimits；页面、单缓冲及总缓冲预算，均在创建/调整尺寸时检查 |

默认根位于 `%LOCALAPPDATA%/ComfortableTUI/browser/<app-hash>` 或
`$XDG_DATA_HOME/ComfortableTUI/browser/<app-hash>`（未设置时为 ~/.local/share）。
根目录使用独占锁；同应用重复启动必须复用服务或选择不同 data_root，不能并发写同根。
同一 OS 用户下的目录分区是存储隔离，不是对恶意本机进程的 OS 安全边界。

`profile(name="main", persistent=False, persist_session_cookies=False)` 创建/复用应用内
profile。设置不能在同一服务中改变；profile 不能传给另一个服务。持久化要求稳定
application_id；会话 Cookie 只有显式开启 persist_session_cookies 才可恢复。
相同 profile 共享 Chromium 持久存储，临时 profile 不恢复数据；sessionStorage
遵守 Chromium 页面/标签语义，不保证在不同页面之间共享。

`open(url, *, profile=None, width=960, height=540, fps=60, origins=(),
publish_topics=(), subscribe_topics=(), message_group=None, policy=None)` 返回 BrowserPage。
源尺寸是浏览器布局像素，与画布显示尺寸分开；fps 为 1..240 的目标上限。
默认无网页桥接权限；origins 必须是精确 origin（scheme/host/port，不能带路径）。
默认最多 16 个页面（含未脱离的退场页面），可在硬上限 64 内设置；profile 最多 64 个。
文件、设备、下载和外部协议没有允许路径；额外资源 origin 需要显式授权。
`poll()` 在调用线程处理事件；WebView 在 mount 时为 App 去重注册一个事件源，
每个服务在同一 App 每次推进仅轮询一次，控件 tick 不再重复调用。标签页隐藏后仍
轮询业务事件；空轮询不会强制重绘。
无控件时应自行定期调用。`on_event(callback)` 返回取消监听函数。
`capabilities` 返回固定版本、实际帧后端和启动策略；`close()` 幂等关闭进程和源。

## BrowserPage 与 WebView

| API | 语义 |
| --- | --- |
| source | 普通 FrameBuffer；Python 创建映射和描述符，CEF C++ 写入像素 |
| navigate(url), reload() | 导航/刷新；旧文档订阅和未完成评估失效 |
| evaluate(code, timeout=15) | 需服务/页面显式允许 allow_evaluate 且主 frame origin 已授权；遵守 CSP，可返回 Promise；超时报 TimeoutError |
| evaluate_async(code) | 返回 Future，结果字典含 value/error；调用者应管理自己的等待期限 |
| resize(width, height) | 显式更换源分辨率；原生写端确认后替换控件 buffer，消费者附加确认后回收旧缓冲区 |
| set_visible(bool) | 通知 Chromium 节流；WebView/宿主也根据可见布局协调 |
| set_muted(bool) | 宿主显式静音/取消静音；默认不强制静音，仍遵守 Chromium 播放规则 |
| input(kind, **data) | 低层异步输入适配；常规应用使用 WebView 自动转发 |
| on_event(callback) | 监听 navigating、loaded、load_error、title、new_window、message、ime、crashed、closed |
| loaded, url, title, info | 本线程 poll 后更新的状态；loaded 表示主 frame 完成，不代表所有异步请求完成 |
| close() | 关闭浏览器；已附加显示仍需先解除，源由服务最终回收 |

`evaluate_async()` 的 Future 取消会从 Python 等待队列移除；使用同步 `evaluate()`
可同时通知原生端取消未完成的评估。网页内容与返回值均不应视为可信代码/数据。
消息和评估不提供任意 Python 代码执行入口。

URL/origin 由原生 CEF 统一规范化与校验；Python 不另行解释 Unicode 域名或 IP。
服务和页面没有权限继承：单页传入 policy 就完整覆盖服务默认值。消息桥接仍必须
显式声明 origin/topic，不能由网页修改。[简化与迁移说明](browser-simplification.md)。

`WebView(page, on_event=None, on_browser_event=None, **overlay_options)` 继承
OverlayCanvas。`interacting` 表示当前交互状态；`enter_interaction()` / `leave_interaction()`
供应用显式进入/退出，`native_focus` 只有控件已聚焦且处于交互态时为 true。
进入所用的 Enter 不作为网页按键重复发送。

其余选项支持 contain/cover/stretch/pixel、smooth、布局宽高、裁剪以及
已有控件工厂。布局会自动缩放图像；不会每次调整窗口都重建网页源分辨率。
鼠标坐标转为源坐标；中文提交/预编辑和光标位置进入 CEF 输入接口；select popup
合成到同一帧。`window.open` 产生 new_window 事件并拒绝默认新窗口，应用自行决定
是否用自己的 profile 创建新页面。默认不显示系统上下文菜单。
当前 IME 提供提交和基本预编辑；复杂属性、相对 replacement、原生候选窗口交互、
剪贴板、拖放、下载、摄像头/麦克风权限等仍需专门策略与桌面验收，不能按全功能
桌面浏览器宣称支持。

## Cookie 隔离与消息联动

默认消息范围为同服务、同 profile。跨 profile 联动需要应用在 open() 中明确指定
相同 message_group；它不会改变任何 Cookie/存储分区。跨应用同名 group/topic 不互通。
每个网页操作均由原生端检查真实 frame、origin 和主题权限；子 frame 不获得授权。

```python
page = browser.open(url, origins=["https://example.com"],
                    publish_topics=["selection.changed"],
                    subscribe_topics=["selection.changed"], message_group="linked-editors")
browser.publish("selection.changed", {"item_id": 42}, message_group="linked-editors")
```

授权网页可调用：

```javascript
const unsubscribe = comfortable.subscribe("selection.changed", message => {
  console.log(message.payload, message.sender, message.document);
});
await comfortable.publish("selection.changed", {item_id: 42});
unsubscribe();
```

publish 返回 Promise，ACK 的 delivered 是当时成功路由的订阅数，不表示业务处理
完成。subscribe 返回取消函数；导航、刷新、关闭与 renderer 退出使旧订阅失效。
target 可指定该服务中一个页面 ID，省略时广播到范围内获准订阅者。
Python host 的 publish 是可信应用控制接口；网页不能自行指定 app/profile/scope。
桥接请求最多 64 KiB、每页面最多 256 订阅、最多 256 待完成评估。控制 IPC 每条
1 MiB、256 个 Python 待回复、1024 条事件（满时丢弃最旧事件）。不提供持久化可靠
队列或业务 ACK 重试；高频消息应合并/限流，图像字节不进入消息 IPC。

## WindowHost 的能力边界

`WindowHost(app, executable=None, display_platform=None, test_controls=False)` 创建
一个拥有完整文本区的 Linux 窗口。`run()` 驱动 App；`step(dt)` 便于嵌入自己的控制
循环；`metrics()` 返回原生绘制计数/源数/视图数；`close()` 先停止原生读取再解除绑定。
共享源的多个视图在一个 C++ 画布窗口中绘制，按控件布局裁剪和命中；没有逐画布
原生窗口。`test_controls=True` 仅开放向自己控件发送测试事件和读取自己 backing
store 的检查操作，默认关闭。

| 模式 | 本轮状态 |
| --- | --- |
| Windows 原生显示与输入闭环 | 受控真实 DComp 层通过 8 项测试，含 DWM 测试色、中文输入和 GIL 独立性；frame_backend 为 native_cpu |
| Windows 外部终端附加层 | Windows Terminal 实测通过所有权、层级、移动缩放、输入、透明洞、Tabs/modal 隐藏恢复；失去前台焦点时保留附加层，置于终端上方和其他前台应用下方 |
| Linux X11 window_host | WSLg Qt xcb 窗口实测通过，见验证脚本和记录 |
| Linux Wayland window_host | WSLg Qt wayland 窗口单独实测通过；没有使用 Xwayland 替代 |
| Linux 外部终端附加层 | 尚未实现；App.run() 继续明确报不支持，使用 WindowHost |
| 任意 Wayland 终端透明附加层 | 无通用宿主定位/层级权限；需要具体终端/合成器协作，不做伪置顶覆盖 |
| Kitty 图像输出 / DMA-BUF / CEF GPU adapter | 后续工作，本次浏览器后端不会报告 GPU zero-copy |
| macOS | 按用户要求本轮不支持 |

WSL2/WSLg 通过证明这组系统版本可运行，不替代每个 Linux 桌面/WM/GPU 的验收。
原生 Qt paint_calls/source_paints、CEF rAF 和 DComp Present 计数均不代表屏幕实际 FPS，
frame_age 也不等于输入到屏幕的延迟。

## 首次运行不再反复弹条款窗口

固定 Chromium 版本启用了 Linux 的首次启动条款流程；测试的每个新私有根都可能
重新触发。宿主与 Python 启动入口现在都设置 `--no-first-run` 和
`--no-default-browser-check`，使用浏览器提供的开关取消这套独立浏览器初始化 UI。
不伪造条款接受文件、不合并应用目录，不屏蔽网页自己的服务条款。
这是嵌入式宿主的启动策略，不免除发行者保留 CEF/Chromium 与第三方许可证的责任。
原因、上游源码链接与 Exa 清单见[调研更新](webview-research.md)。

## 验证与诊断

```bash
python examples/verify_webview.py --platform x11 --report build/service-x11.json
python examples/verify_webview.py --platform wayland --report build/service-wayland.json
python examples/verify_window_host.py --platform x11 --report build/window-x11.json
python examples/verify_window_host.py --platform wayland --report build/window-wayland.json
```

Windows 服务测试省略 --platform；完整终端组合测试为
`python examples/verify_overlay_windows.py --webview --report build/windows-overlay.json`。
受控原生层测试为 `python examples/verify_webview_windows_native.py --report build/windows-native.json`，
额外需要 Pillow；只短暂显示自己的小测试层并抽样该层测试色，不依赖终端前台策略。
它只创建/操作自己的 Windows Terminal；附加层可在终端处于后台时继续显示。
服务测试可加 `--data-root <测试目录>` 保留 profile 与 cef.log/service-stderr.log，
以定位启动失败；普通临时根在关闭后清理。

服务集成覆盖隔离/共享 profile、跨应用隔离、持久化重启、授权/越权主题、子 frame
拒绝、消息长度、评估结果绑定、评估超时恢复、中文、首帧、导航与全新目录无人值守
启动。窗口测试覆盖两视图共一源、真实窗口绘制、原生输入路径、GIL 暂停期间继续
绘制、Tabs、缩放、源替换与释放顺序。IME 用例是向自己的 Qt 控件发送提交事件，
不宣称已验证外部输入法候选窗。实测原始记录见仓库 docs/validation/webview/。
