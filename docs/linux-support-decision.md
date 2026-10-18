# 暂停 Linux 图像混合模式：调研与决策

日期：2026-10-18。使用 Exa 检索 15 个候选结果，按 Wayland 权限模型、X11 窗口管理、
终端原生图像协议三个方向核对主要来源，并检查本项目实现。

## 决策与范围

**暂时撤下 Linux 的图形画布／WebView 展示支持是合理的。** 产品目标是在用户现有
终端中混合显示 TUI 与图像；旧 `WindowHost` 把文字和图像绘制进另一个 Qt 窗口，
没有达到这个目标。它不是完整终端模拟器，WSLg 中可运行也不能证明终端兼容性。

| 能力 | 当前范围 |
| --- | --- |
| Linux 字符控件、布局、动画、输入与 `App.run()` | 保留 |
| PixelCanvas 的终端协议图像／字符回退 | 保留；取决于终端实际能力 |
| 独立的 CPU 帧 SDK | 保留；可移植传输不等于已有显示后端 |
| Linux 外部终端附加层 | 未实现，不对外宣称支持 |
| Linux WindowHost、自有 Qt 图像混合窗口 | 撤下；旧入口只给明确错误 |
| Linux CEF 图形运行时构建、Top native/hybrid | 暂停；不再发布为支持路径 |
| Windows 外部终端附加层、CEF CPU/GPU | 保留；Windows Terminal 是实际验证宿主 |
| macOS 图形后端 | 不支持 |

本次不是删除所有 POSIX 代码，也不将纯文本 TUI 限制为 Windows。历史原生源码和
验证报告保留作研究记录；构建入口、文档操作路径和当前支持声明必须一致。

## 协议事实与工程判断

### Wayland：普通应用不能自行复制 Windows 的宿主追踪方式

Wayland 官方文档说明，客户端不知道自身 surface 的全局位置，也不能访问其他
客户端的 surface。故普通客户端不能仅凭终端进程身份就获得终端文本区的屏幕坐标。
[Wayland 协议与运行模型](https://wayland.freedesktop.org/docs/book/Protocol.html)

`xdg-foreign` 允许跨进程建立父子关系，但目标客户端必须先导出句柄并将其交给
请求方。协议解决的是窗口关系，不提供终端单元格、分屏、当前标签页的语义。
因此它不能充当“任意终端无需协作”的附加层方案。
[上游 xdg-foreign 协议](https://gitlab.freedesktop.org/wayland/wayland-protocols/-/blob/main/unstable/xdg-foreign/xdg-foreign-unstable-v2.xml)，
[同一协议的可读展示](https://wayland.app/protocols/xdg-foreign-unstable-v2)

工程判断：特定终端或合成器协作仍有实现空间，不能把 Wayland 概括为永远不可能。
但目前没有这样的适配和验收，以另开窗口交付会改变使用方式。

### X11：可实现窗口叠加，但不是已经完成的终端集成

EWMH 定义窗口状态和堆叠建议；transient 窗口应在其父窗口上方，窗口管理器仍可
采用不同堆叠策略。`_NET_WM_STATE_ABOVE` 表示高于多数窗口，不能直接用作只覆盖
某个终端的替代品。[EWMH 窗口状态与堆叠](https://specifications.freedesktop.org/wm/latest-single/)

工程判断：X11 是独立适配项目。窗口移动、输入区域、DPI、遮挡、后台显示、最小化、
终端标签页和分屏都需实现并实测；窗口管理协议没有提供终端内部布局信息。
旧 Qt 自有窗口的测试不能替这些缺失能力作证。

### 终端原生协议：值得作为未来独立方案，而非本次自动替代

Kitty 图像协议允许图像与文字一起定位、缩放和滚动，提供像素／文件／共享内存
传输形式。终端自己合成图像可以避免额外窗口追踪，但需终端支持协议。
[Kitty 图像协议](https://sw.kovidgoyal.net/kitty/graphics-protocol/)

工程判断：它不等于已有 D3D11 共享纹理或 CEF 输入桥。未来如选此路线，要单独
实现浏览器帧适配、输入坐标、焦点、裁剪、生命周期和吞吐验证，不能据此宣布现已
支持 Linux WebView。现有 PixelCanvas 的 Kitty 输出仍保持原能力范围。

## 代码与交付收缩

- `WindowHost` 兼容名字保留，仅抛出说明性的 `NotImplementedError`，不启动进程。
- CMake 拒绝启用 Linux host；非 Windows 平台拒绝构建受支持的 CEF 运行时。
- CI 保留 Linux 纯 TUI 和可移植 CPU SDK，撤掉 Qt Linux host 构建任务。
- Top 0.1.4 起整体只支持 Windows：安装、运行和构建入口拒绝 Linux，不发布 Linux
  安装包或运行时。ComfortableTUI 库的 Linux 纯 TUI 独立保留。
- 当前安装指南撤下 Linux GUI 命令。旧报告明确标记为历史自有窗口结果。
- 从 ComfortableTUI 0.1.3 起生效，旧版归档保留原样。ComfortableTop 0.1.4 起整体
  仅提供 Windows 版本；库的 Linux 纯 TUI 与可移植 CPU SDK 不受影响。

## 恢复支持的条件

先明确支持的终端和桌面环境，提供真实外部终端集成或明确约定的终端原生图像路径。
验收至少包括标签页／分屏切换、字体与 DPI、移动缩放、遮挡与最小化、后台显示、
嵌入列表／表格、焦点返回、IME、输入与裁剪，以及帧传输的实际开销。
X11 与原生 Wayland 分别验证；Xwayland、自有窗口和 WSLg 测试都应注明具体边界。
CPU 与 GPU 作为独立能力报告，不以设备存在、帧计数或浏览器 rAF 代替端到端验证。
