# 终端周边玻璃工作区（研发功能）

`GlassWorkspace` 在真实终端的周围创建原生卡片。它与字符控件、终端内的
`OverlayCanvas` 是不同的上层 UI：终端继续承载主要内容，卡片承担临时工具、笔记、
状态、快捷动作和外部渲染画布。默认不遮住整个桌面，不启动 WebView 或 CEF。

本功能位于 `codex/liquid-glass-cards`，尚未发布。支持本机 Windows 桌面；系统磨砂
需要 Windows 11 22H2 / build 22621 及以上。不支持的材质回退为实色。
设计来源、技术选型和实施计划见[Exa 调研报告](glass-research-plan.md)，实际检查见
[验证记录](validation/glass/README.md)。

## 安装和启动

使用与 Python 架构匹配的 Visual Studio 2022 C++ Build Tools、Windows SDK、
Python 3.10+ 和 pybind11。以下命令在仓库根目录的 PowerShell 中运行：

```powershell
python -m pip install -e ".[dev,overlay]"
$env:COMFORTABLE_TUI_BUILD_GPU = '1'
python -m pip install --no-build-isolation -e .
python -c "from comfortable_tui import _native_gpu; assert _native_gpu.glass_protocol_version == 1"
python -m examples.glass_workspace
```

`.[overlay]` 用于绑定真实终端所需的现有宿主发现能力，也用于可选 Qt 测试夹具。
玻璃卡片本身由 Win32、Direct2D、DirectWrite 和 DirectComposition 绘制，不由 Qt 绘制。
更新 `.pyd` 前应正常退出使用该模块的程序，Windows 会锁定已加载的扩展。

普通示例的画布由一个小型 Python CPU 源以 10 Hz 生成。要测试原生 GPU 帧路径：

```powershell
cmake -S . -B .build/glass-native -A x64
cmake --build .build/glass-native --config Release --target ctui_glass_signal
python -m examples.glass_workspace --gpu-provider .build/glass-native/Release/ctui_glass_signal.exe
```

`provider.cpp` 是独立 C++ 提供者，使用现有 SDK 创建 D3D11 BGRA 共享纹理，约 60 Hz
绘制信号；标准输出只给出 `CTUI_GPU_V1` 握手，标准输入接收幅度值。Python 不读取像素，
关闭标准输入即可停止提供者。这不是浏览器，也不需要加载外部网页。

## 最小集成

```python
from comfortable_tui import App, Label, GlassCard, GlassControl, GlassWorkspace

def changed(event):
    if event.kind == "click":
        message.set_text("Card action received")

message = Label("Terminal content")
card = GlassCard("tools", "Your next step", [
    GlassControl("action", "button", "Continue", on_event=changed),
    GlassControl("paused", "toggle", "Pause"),
], subtitle="A quiet companion to the terminal")

with GlassWorkspace([card]) as workspace, App(message) as app:
    workspace.bind(app)
    app.bind("f6", lambda: workspace.command("focus_card"))
    app.run()
```

`bind(app)` 延迟到 App 的真实交互终端初始化后发现宿主，使用已有的输出流和标签页
校准，不依赖“当前前台窗口就是终端”的猜测。终端标签页不可用或 App 显示模态框时，
卡片隐藏。终端最小化/销毁时同步隐藏/清理。已绑定工作区由 App 关闭，同时推荐使用
上下文管理器确保初始化失败和业务异常时也能释放资源。

周边卡片只需要已绑定标签页的可见性，因此使用 HostTracker 的轻量可见性分支；
不逐次测量字符边界或读取文本范围。原内嵌画布仍使用默认的完整视口校准路径。

已有原生窗口的集成可使用 `workspace.start(hwnd)`，随后从宿主 UI 线程周期调用
`workspace.poll()`；退出时调用 `close()`。这里的 HWND 必须是调用方有权绑定的有效窗口。
该 API 不会创建或替代终端模拟器。

## 布局与材质

| 选项 | 行为 |
| --- | --- |
| `layout="orbit"` | 左右错落环绕，保留终端中央空间 |
| `"deck"` | 叠牌，当前卡置前，其余露出错位边缘 |
| `"ribbon"` | 侧带，按当前卡纵向换位 |
| `"focus"` | 当前卡进入终端附近，其余卡退为侧翼 |

终端接近全屏、两侧空间不足时，自动改用工作区内的紧凑叠牌。窗口和字体使用 DPI
缩放，位置约束到当前显示器工作区；卡片内容过高会报错，首版没有卡内滚动容器。
显示器/DPI 改变可能重建表面，不能假定 HWND 永久不变。

```python
from comfortable_tui import GlassTheme

workspace.configure(layout="deck", backdrop="frosted",
    theme=GlassTheme(material="glass", accent=0x85E5D1,
                     reduced_motion=False, reduced_transparency=False))
```

| `material` | 实际效果 |
| --- | --- |
| `glass` | 系统 Desktop Acrylic、自绘明暗层、柔和边缘与悬停高光 |
| `frosted` | Acrylic 上更高不透明度的着色层，便于阅读 |
| `mica` | 系统 Mica，以壁纸为主要输入，不是实时桌面模糊 |
| `solid` | 实色，不依赖系统磨砂 |

这是一套自有玻璃风格 UI，不是 Apple 的 Liquid Glass API，也没有承诺对任意桌面
内容进行光学折射。DWM 材质调用失败、高对比模式、`reduced_transparency=True` 时
回退实色，`metrics()["material"]` 给出实际卡片材质。
该主题选项作用于卡片；如需同时避免聚焦背景磨砂，可将 `backdrop` 设为 `dim` 或 `none`。

`backdrop` 可为 `none`（默认）、`dim`、`frosted`、`mica`。背景窗口位于终端下方，
只在终端/卡片焦点组活动时显示；切换到其他应用立即隐藏，卡片本身仍属于终端，
不会因为终端失焦而统一消失。背景空白处的第一下点击仅关闭聚焦背景，不转发给其他应用。
背景不会永久置顶或修改其他应用的窗口样式。超过 8 兆像素的显示器不创建聚焦背景，
发送 `backdrop_unavailable`，卡片继续可用。

动画使用 C++ 原生解析欠阻尼弹簧，切换目标保留速度，入场和退场可打断。
`reduced_motion=True` 直接定位。启动时也读取系统客户端动画与高对比偏好；运行中
切换系统偏好应重建工作区。首版高对比适配仅保证不透明，不声称完整替换系统颜色表。

## 控件模型

`GlassCard(id, title, controls=[], subtitle="", eyebrow="", width=320)`。
宽度为逻辑像素，高度依据控件自动计算。`id` 用于稳定匹配，不用显示文案作为身份。

`GlassControl(id, kind, label="", text="", value=0, enabled=True, series=(), options=(),
source=None, fit="contain", on_event=None)`：

| kind | 数据与交互 |
| --- | --- |
| `text` | 次级 `label`、主要 `text` |
| `button` | 点击或 Enter/Space，发送 `click` |
| `toggle` | `value` 0..1，>=0.5 为开；点击切换，发送 `change` |
| `slider` | `value` 0..1；拖动或左右键，发送 `change` |
| `input` | `text` 单行输入；Enter 发送 `submit`，编辑发送 `change` |
| `choice` | `options` 与整数索引 `value`；点击/Enter/Space循环选项，发送 `change` |
| `progress` | `value` 0..1，固定绝对比例 |
| `sparkline` | `series` 为 0..1 的样本，最多 256 个 |
| `canvas` | `source` 为 FrameBuffer / GpuFrameSource，见下节 |

更新模型后调用 `workspace.update()`；或者使用 `set_cards(cards)` 整体替换卡片。
原生交互先更新自身状态，`poll()` 再更新 Python 模型并触发回调。场景带编辑确认序号，
防止业务定时器提交的旧状态抹掉刚刚输入的文本或拖动结果。

输入框首版支持 Unicode 提交文字、末尾输入、退格、Ctrl+A 全选、Delete 删除全选、
用户触发的 Ctrl+V 粘贴。暂不支持任意光标定位、局部选择、撤销、密码模式或 IME 内联
预编辑显示；不要把它替代成熟的文档编辑器。没有现成列表/表格/树控件的自动皮肤转换，
原终端控件继续使用自己的事件和布局。

## 画布和事件

```python
from comfortable_tui import FrameBuffer, GlassCard, GlassControl, GlassWorkspace

with FrameBuffer(320, 96, pixel_format="bgra8_premultiplied") as frames:
    canvas = GlassControl("plot", "canvas", "Live plot", source=frames, fit="contain")
    with GlassWorkspace([GlassCard("signal", "Live signal", [canvas])]) as workspace:
        # 绑定 App 后运行，或 start(hwnd) 后由宿主轮询。
        pass
```

`fit` 为 `contain` / `cover` / `stretch` / `pixel`，和现有原生画布相同。
CPU 缓冲区走现有 C++ 上传适配器；GPU 来源走现有共享纹理与同步租约。
每张卡首版支持一个画布，同一通道不能同时用于多张卡或旧叠加层；底层 GPU 协议
仍只支持一个活动消费者。需要两处显示相同数据时由提供者输出独立通道。

工作区会保留已提交来源直到原生确认替换/移除，退出按合成器、UI 表面、外部提供者
顺序关闭。应用方仍应在工作区关闭后才关闭自身 FrameBuffer、GpuFrameSource 或提供者。

`GlassEvent` 字段：`kind, card, control, text, value, position, sequence`。
`position` 是 `(x, y)`；`sequence` 用于诊断，不需要业务手动确认。

| kind | 有效字段 |
| --- | --- |
| `click` | card/control |
| `change` | card/control、text（输入/选项）、value（数字控件） |
| `submit` | card/control、text |
| `mouse_down`, `mouse_move`, `mouse_up` | 画布 card/control、position 为源像素坐标 |
| `wheel` | 画布 card/control、value 为滚轮步数 |
| `key_down`, `key_up` | 画布 card/control、text 为 Windows 虚拟键码的十进制字符串 |
| `transform` | 画布 card/control、value 为 DPI 比例、position 为显示逻辑宽高 |
| `selected`, `focus`, `escape` | card |
| `activation` | text 为 active/inactive |
| `ready` | card、text 为 HWND 的十进制字符串 |
| `scene_applied` | text 为场景序号 |
| `dismissed`, `backdrop_dismissed`, `close_request` | 工作区行为 |
| `backdrop_unavailable` | text 为原因 |

此扩展的画布事件是独立 API，首版提供上述鼠标/滚轮/键盘/变换事件；尚未复制旧
OverlayCanvas 的完整 IME、触摸、拖放或文本编辑事件。不要假定两者的事件结构相同。
回调均在调用 `poll()` 的 Python/App 线程执行，不在渲染线程；回调应及时返回。

## 焦点、召回和生命周期

卡片中 Tab/Shift+Tab 遍历可交互控件，Ctrl+Tab/Shift+Ctrl+Tab 换卡，非编辑控件上的
左右键或空白处滚轮换卡。Escape 返回终端，非编辑控件上的 F6 也可返回。
Ctrl+Q 发出 `close_request`，绑定 App 时请求退出。卡片右上角关闭键隐藏整个工作区。
提供 MSAA 的名称、角色、值、状态、边界和基础操作，仍需进一步屏幕阅读器兼容测试。

| 方法 | 合约 |
| --- | --- |
| `bind(app)` | 加入 App 事件源并延迟发现真实终端；只能绑定一次 |
| `start(hwnd)` | 显式启动；不得与 bind 重复使用 |
| `poll()` | 处理事件、提交最新场景；外部宿主必须轮询 |
| `update()` | 验证当前模型，下一次 poll 提交 |
| `set_cards(cards)` | 验证后替换场景，失败保留旧模型 |
| `configure(layout=..., theme=..., backdrop=...)` | 修改配置，失败保留旧配置 |
| `show()` / `hide()` | 召回/退回卡片；hide 不等于释放 |
| `command(action, value="")` | next / previous / select（卡 id）/ focus_card / focus_terminal / dismiss |
| `metrics()` | 有界原生诊断快照 |
| `close()` | 幂等关闭、等待原生线程退出；释放来源引用 |

程序可以自行绑定召回快捷键；库不安装全局热键。系统前台窗口限制仍有效，命令不会
绕过 Windows 的前台权限。`start(..., inspectable_windows=True)` 仅供自有窗口测试：
创建独立可检查卡片，便于电脑插件正确识别其坐标。生产默认始终使用终端 owned popup。

## 架构、性能和限制

```text
Python 声明 / 业务回调 ──有界场景、命令、事件── C++ GlassHost
  App.watch_events                         Win32 / 原生弹簧 / 命中 / MSAA
                                            │ Direct2D + DirectWrite
                                            ▼
                              FrameProvider BGRA UI 纹理
外部 CPU / D3D11 帧源 ──既有帧协议──► Compositor ──► DirectComposition / DWM
```

动画与宿主移动在原生线程推进，Python 不逐帧移动窗口或传递像素。WinEvent 仅针对
宿主进程的位置变化，移动走平移快速路径；DPI/内容变化才重配表面。
UI 脏标记和有界文字布局缓存避免停稳重绘，外部画布保持自己的帧节奏。
UI 调度目标 60 Hz；不承诺144 Hz、零 DWM 延迟或多窗口严格同一时刻呈现。

`metrics()` 包含 applied、frames（UI 绘制次数）、presents（卡合成呈现）、canvas_frames、
tick_ms、dropped_events、active、compact、backdrop_visible、material、windows、error、closed。
Python 快照另附 pane_available、pane_error，便于区分终端标签页隐藏与绘制错误。
tick_ms 是最近一次原生控制 tick 的 CPU 时间，不是屏幕延迟；统计约每 0.5 秒刷新，
退出后 HWND 列表清空。请在退出前采样性能。

限制为最多12卡、每卡32控件、宽220..1600逻辑像素、高最多1200且必须放入当前
显示器工作区。UI 纹理预算256MiB，不包括提供者持有的外部纹理。
场景最多保留8批待确认来源，事件队列1024项，连续画布移动事件合并；溢出计数可观测。
卡片是普通窗口层级，不使用全局 topmost。不会截取桌面、执行脚本、请求网络或修改
宿主样式。Python 和原生提供者是可信宿主代码，玻璃 UI 本身不是沙箱。

当前未支持 macOS/Linux、任意卡片旋转/透视、桌面真实折射、复杂文本编辑和完整
无障碍认证。Windows 10 实色回退按失败分支设计，尚无此版本的真实视觉验证。
请使用[验证命令](validation/glass/README.md)在目标显示器、缩放和终端上检查实际表现。

## C++ 扩展入口

CMake SDK 额外导出 `Comfortable::comfortable_glass`，安装头文件
`comfortable/glass.hpp`。原帧提供者不需要链接它；只有直接创建工作区的原生宿主需要。

```cpp
#include <comfortable/glass.hpp>

void attach_workspace(std::uintptr_t terminal_hwnd) {
    comfortable::GlassHost host(terminal_hwnd);
    comfortable::GlassScene scene;
    comfortable::GlassControl action;
    action.id = "continue"; action.kind = "button"; action.label = "Continue";
    comfortable::GlassCard card;
    card.id = "tools"; card.title = "Your next step"; card.controls = {action};
    scene.cards = {card}; scene.selected = "tools";
    auto serial = host.configure(scene);
    // 在宿主循环中读取 host.events()/metrics()；保存 host 到真实 UI 生命周期末尾。
    // serial <= metrics.applied 后，才可释放被替换的外部帧来源。
    (void)serial;
    host.close(); // 示例仅演示握手；实际应用不应马上关闭。
}
```

原生宿主修改可编辑控件后，应在下次场景中将该控件的 `acknowledged` 更新为已处理
的 change 事件 sequence；Python API 自动执行此步骤。`edit_serial` 是原生内部状态。
