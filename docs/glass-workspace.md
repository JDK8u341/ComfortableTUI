# 终端周边玻璃工作区（研发功能）

`GlassWorkspace` 在真实终端的周围创建原生卡片。它与字符控件、终端内的
`OverlayCanvas` 是不同的上层 UI：终端继续承载主要内容，卡片承担临时工具、笔记、
状态、快捷动作和外部渲染画布。默认不遮住整个桌面，不启动 WebView 或 CEF。

Card 可以通过 [GlassCanvas](glass-canvas.md) 承载现有 OverlayCanvas（包括 WebView）、
CPU 或 GPU 来源；浏览器不是卡片的实现依赖。混合示例为 `python -m examples.glass_workspace.mixed`。

本功能位于 `codex/liquid-glass-cards`，尚未发布。支持本机 Windows 桌面，当前实测为
Windows 11。光学材质使用 D3D11 feature level 11 和 Desktop Duplication；Mica
另需系统支持。采样不可用、排除自身窗口失败或显卡不匹配时回退实色。
设计来源、技术选型和实施计划见[光学与事件调研](glass-optics-research.md)，实际检查见
[验证记录](validation/glass/README.md)。

每控件独立效果器、自定义控件/Card 类型及用户动画钩子的后续设计见
[扩展调研与方案](glass-extensibility-plan.md)。第一批[原生控件SDK和按需动画钩子](glass-controls-sdk.md)
已实现；完整效果器、变换及原生帧订阅仍待实现。

## 安装和启动

使用与 Python 架构匹配的 Visual Studio 2022 C++ Build Tools、Windows SDK、
Python 3.10+ 和 pybind11。以下命令在仓库根目录的 PowerShell 中运行：

```powershell
python -m pip install -e ".[dev,overlay]"
$env:COMFORTABLE_TUI_BUILD_GLASS = '1'
python -m pip install --no-build-isolation -e .
python -c "from comfortable_tui import _native_glass; assert _native_glass.glass_protocol_version == 5"
python -m examples.glass_workspace
```

`.[overlay]` 用于绑定真实终端所需的现有宿主发现能力，也用于可选 Qt 测试夹具。
玻璃卡片本身由 Win32、D3D11 shader、Direct2D、DirectWrite 和 DirectComposition
绘制，不由 Qt 绘制。此构建开关同时构建底层 `_native_gpu`，但卡片模型、窗口和光学
实现属于独立 `_native_glass`；仅构建 GPU 帧扩展不会提供玻璃工作区。
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
| `"deck"` | 固定错位槽位，选中只置前，不带动其他卡片换位 |
| `"ribbon"` | 侧带，按当前卡纵向换位 |
| `"focus"` | 当前容器进入焦点位，仅旧/新焦点容器换位，其他侧翼位置不变 |

orbit 模式在终端接近全屏、两侧空间不足时，自动改用工作区内的固定紧凑叠牌。
显式选择 ribbon/focus 时保留其转场，不被紧凑模式替换；位置仍约束到工作区。
普通 orbit/deck 点击只提升当前卡片/容器，其他兄弟节点的相对层级和位置不变。
CardGroup 内选中卡置于本组最前，整组置前，但组内不因选择自动换位。
`draggable=False` 限制手动拖动，不阻止显式更换布局、宿主移动或缩放时的布局调整。
窗口和字体使用 DPI
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
| `liquid` / `glass` | 自有 SDF 光学着色器：通透中心、边缘位移折射、小幅色散和指针高光；glass 为兼容别名 |
| `frosted` | GPU 桌面采样与两遍高斯模糊，默认模糊强度更高 |
| `mica` | 系统 Mica，以壁纸为主要输入，不是实时桌面模糊 |
| `solid` | 实色，不依赖系统磨砂 |
| `transparent` | 不绘制背景材质；保留控件与文字自身的可读性层 |

这是自有光学效果，借鉴通透材质，并非 Apple 的 Liquid Glass API 或逐像素复刻。
采样失败、高对比模式、`reduced_transparency=True` 时回退实色。
`metrics()["windows"][i]["material"]` 给出每卡实际材质；混合时工作区 material 为 mixed。
该主题选项作用于卡片；如需同时避免聚焦背景磨砂，可将 `backdrop` 设为 `dim` 或 `none`。

每张卡可独立配置；`background=None` 才继承工作区主题：

```python
from comfortable_tui import GlassBackground, GlassCard, GlassCardGroup

clear = GlassCard("tools", "Tools", background=GlassBackground(
    "liquid", opacity=.025, blur=0, refraction=20, dispersion=.45), draggable=True)
frost = GlassCard("status", "Status", background=GlassBackground(
    "frosted", blur=20, opacity=.06), draggable=False)
group = GlassCardGroup("tools_group", [clear, frost], layout="row", gap=16)
workspace.set_cards([group])
```

`GlassBackground(kind="liquid", tint=0xffffff, opacity=.04, blur=1.2,
refraction=20, dispersion=.45, corner_radius=24)`。tint 为 RGB 整数；opacity 为 0..1，
blur 为 0..40，refraction 为 0..48，dispersion 为 0..3，corner_radius 为 0..64。
距离按逻辑像素缩放；并非物理光学单位。Mica 的底色主要由系统控制。

`backdrop` 可为 `none`（默认）、`dim`、`frosted`、`mica`。背景窗口位于终端下方，
`dim` 仅压暗，`frosted` 使用真实桌面高斯模糊，`mica` 是壁纸材质。
只在终端/卡片焦点组活动时显示；切换到其他应用立即隐藏，卡片本身仍属于终端，
不会因为终端失焦而统一消失。背景空白处的第一下点击仅关闭聚焦背景，不转发给其他应用。
背景不会永久置顶或修改其他应用的窗口样式。超过 8 兆像素的显示器不创建聚焦背景，
发送 `backdrop_unavailable`，卡片继续可用。

动画使用 C++ 原生解析欠阻尼弹簧，切换目标保留速度，入场和退场可打断。
`reduced_motion=True` 直接定位。启动时也读取系统客户端动画与高对比偏好；运行中
切换系统偏好应重建工作区。首版高对比适配仅保证不透明，不声称完整替换系统颜色表。

## 控件模型

`GlassCard(id, title, controls=[], subtitle="", eyebrow="", width=320,
background=None, draggable=True, on_event=None)`。
宽度为逻辑像素，高度依据控件自动计算。`id` 用于稳定匹配，不用显示文案作为身份。

`GlassControl(id, kind, label="", text="", value=0, enabled=True, series=(), options=(),
source=None, fit="contain", on_event=None, canvas_height=96, capture_escape=False)`：

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

`canvas_height` 是图像区逻辑高度16..1024，默认96；`capture_escape=True` 时允许画布
通过 key_down 接收 Escape，自行处理退出层级。普通画布默认仍由 Escape 返回终端。
可选 [GlassWebView](glass-webview.md) 是消费 BrowserPage 的上层控件，复用这种图像视图。

更新模型后调用 `workspace.update()`；或者使用 `set_cards(cards)` 整体替换卡片。
原生交互先更新自身状态，`poll()` 再更新 Python 模型并触发回调。场景带编辑确认序号，
防止业务定时器提交的旧状态抹掉刚刚输入的文本或拖动结果。

## Card 事件与 CardGroup

`GlassCardGroup(id, children=[], layout="row", gap=16, padding=0, visible=True,
enabled=True, on_preview_event=None, on_event=None)` 是轻量容器，不是另一个窗口。
children 接受 Card 或嵌套 Group；row 按可用宽度换行，column 纵向排列，stack 重叠。
超出当前工作区时组内降为紧凑堆叠。工作区的 orbit/deck/ribbon/focus 负责根节点位置，
组负责内部排列。visible/enabled 向子树继承，禁用还会取消进行中的拖动。

事件顺序为祖先 Group preview（外到内）→ Control 回调（如有）→ Card.handle_event
→ 祖先 Group.handle_event（内到外）→ Workspace 回调 → 未消费时 Card.default_event。
任一回调返回 True 会停止后续路由。工作区级 ready/activation 等事件没有 Card 路由。
原生控件 change/click 是已执行动作的通知；消费通知不能撤销已改变的控件值。

标题区 pointer_down 的移动默认行为则由 **Card** 决定：回调不消费且 draggable=True，
Card 才授权原生线程继续跟踪这一指针。窗口消息处理器不自行决定拖动。可覆写
handle_event/default_event；处理器应及时返回，Python 忙碌时拖动开始会延迟，开始后
移动由原生线程推进。控件区域与关闭按钮不作为拖动区。

```python
group.on_preview_event = lambda event: event.kind == "pointer_down"  # 拦截组内默认拖动
clear.draggable = False
workspace.update()
```

拖动后位置相对终端保存，普通数据更新不重排；拖动叶子可暂时离开组的自动排列。
组可见性/禁用仍然继承。`command("reflow")` 或更换工作区布局恢复排列。
Escape 或捕获丢失取消拖动并还原位置；隐藏/禁用/撤销拖动能力也会取消。

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

`GlassEvent` 字段：`kind, card, control, text, value, position, sequence`，另有
`modifiers`（ctrl/shift/alt）、`scan_code`、`repeat`、`button`、`buttons`、`clicks`、`inside_source`。
画布输入的 `source_name` 标识当时的来源，适配器据此丢弃已替换来源的排队输入。
`position` 是 `(x, y)`；`sequence` 用于诊断，不需要业务手动确认。

| kind | 有效字段 |
| --- | --- |
| `click` | card/control |
| `change` | card/control、text（输入/选项）、value（数字控件） |
| `submit` | card/control、text |
| `mouse_down`, `mouse_move`, `mouse_up` | 画布 card/control、position 为源像素坐标 |
| `wheel`, `wheel_horizontal` | 画布 card/control、value 为滚轮步数、position 为源坐标 |
| `key_down`, `key_up` | 画布 card/control、text 为 Windows 虚拟键码的十进制字符串 |
| `text` | 画布 card/control、text 为 Unicode 提交文字 |
| `control_focus_in`, `control_focus_out`, `mouse_cancel` | 控件焦点/捕获生命周期通知 |
| `transform` | 画布 card/control、value 为 DPI 比例、position 为显示逻辑宽高 |
| `pointer_down`, `pointer_move`, `pointer_up` | card，position 为卡片逻辑坐标，text 为 header 或空字符串 |
| `pointer_cancel` | card，捕获丢失通知；正在拖动时另发 drag_cancel |
| `drag_start`, `drag`, `drag_end`, `drag_cancel` | card，position 为相对终端的逻辑坐标 |
| `selected`, `focus`, `escape` | card |
| `activation` | text 为 active/inactive |
| `ready` | card、text 为 HWND 的十进制字符串 |
| `scene_applied` | text 为场景序号 |
| `dismissed`, `backdrop_dismissed`, `close_request` | 工作区行为 |
| `card_close_request` | 当前卡片的 × / WM_CLOSE 请求；可经路由消费，默认移除该卡 |
| `backdrop_unavailable` | text 为原因 |

此扩展的画布事件是独立 API，首版提供上述鼠标/滚轮/键盘/变换事件；尚未复制旧
OverlayCanvas 的完整 IME、触摸、拖放或文本编辑事件。不要假定两者的事件结构相同。
回调均在调用 `poll()` 的 Python/App 线程执行，不在渲染线程；回调应及时返回。

## 焦点、召回和生命周期

Tab/Shift+Tab 遍历卡片标题和可交互控件，越界进入相邻卡片；反向从标题进入上一卡的
最后一个可交互控件。隐藏/禁用卡片跳过，纯状态卡可停靠标题。Ctrl+Tab/Shift+Ctrl+Tab
直接换卡并转移真实键盘焦点。标题上的方向键循环换卡，非编辑控件的上/下遍历控件，
左/右换卡；输入框保留编辑键，滑块保留左右调值。Escape 返回终端，F6 始终返回。
GlassWebView 的 Enter/点击进入页面，Escape 先离开页面；具体见[嵌入说明](glass-webview.md)。
Ctrl+Q 发出 `close_request`，绑定 App 时请求退出。卡片右上角 × 和该 HWND 的 WM_CLOSE
发出 `card_close_request`，经原有 preview/bubble 路由，未消费时由 Card 默认行为调用
`workspace.remove_card(id)`，只移除该卡。关闭按钮在按下并于按钮内松开时生效，移出
后松开或丢失捕获会取消。关闭最后一张卡后停止背景/捕获，工作区仍可添加新卡。
单卡删除保留其他卡的布局空位，包括嵌套容器和自动焦点回退；内容更新不压缩空位。
显式 reflow、添加节点、尺寸/容器布局更改和宿主 resize/DPI 变化重新计算布局；
focus/ribbon 中主动选择卡片仍可触发其布局动画。保留空位不会把卡片改成拖动 pinned 状态。
隐藏整个工作区使用 dismiss/hide，销毁整个工作区使用 close；见[关闭测试](glass-close-testing.md)。
提供 MSAA 的名称、角色、值、状态、边界和基础操作，仍需进一步屏幕阅读器兼容测试。

| 方法 | 合约 |
| --- | --- |
| `bind(app)` | 加入 App 事件源并延迟发现真实终端；只能绑定一次 |
| `start(hwnd)` | 显式启动；不得与 bind 重复使用 |
| `poll()` | 处理事件、提交最新场景；外部宿主必须轮询 |
| `watch_events(callback)` | 注册非阻塞服务轮询源，返回幂等取消函数；浏览器适配器自动使用 |
| `update()` | 验证当前模型，下一次 poll 提交 |
| `set_cards(cards)` | 验证后替换场景，失败保留旧模型 |
| `configure(layout=..., theme=..., backdrop=...)` | 修改配置，失败保留旧配置 |
| `show()` / `hide()` | 召回/退回卡片；hide 不等于释放 |
| `command(action, value="")` | next / previous（换卡）/ next_control / previous_control（控件焦点环）/ select（卡 id）/ focus_card / focus_terminal / dismiss / reflow |
| `metrics()` | 有界原生诊断快照 |
| `close()` | 幂等关闭、等待原生线程退出；释放来源引用 |
| `remove_card(id)` | 移除单张卡，保留嵌套容器与兄弟实例；等待原生解除后再通知消费者，来源仍由调用方拥有 |

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
桌面 DDA ──GPU dirty/move 区域──► 私有 SDF 光学/高斯表面 ──┘
```

动画与宿主移动在原生线程推进，Python 不逐帧移动窗口或传递像素。WinEvent 仅针对
宿主进程的位置变化，移动走平移快速路径；DPI/内容变化才重配表面。
UI 脏标记和有界文字布局缓存避免停稳重绘，外部画布保持自己的帧节奏。
桌面采样在 C++ 层：不向 Python 传桌面像素，不保存截图。自身窗口通过 DDA 专用属性
排除，不设置会让其他截图工具也看不见 UI 的全局捕获保护。DDA 的 dirty 元数据可能
包含自身呈现的回声，因此先用 GPU 比较区域，只异步回读16字节变化包围盒；相同
像素不更新镜像/光学表面。变化时 GPU 返回实际变化包围盒，避免宽泛的桌面脏区域
让远离变化的卡片一起重绘。此检测引入一个原生 tick 左右的背景延迟，不承诺零延迟。
隐藏所有卡片且无聚焦背景时释放采样会话。
UI 调度目标 60 Hz；不承诺144 Hz、零 DWM 延迟或多窗口严格同一时刻呈现。

`metrics()` 包含 applied、frames（UI 绘制次数）、presents（卡合成呈现）、canvas_frames、
tick_ms、dropped_events、active、compact、backdrop_visible、material、windows、error、closed。
Python 快照另附 pane_available、pane_error，便于区分终端标签页隐藏与绘制错误。
tick_ms 是最近一次原生控制 tick 的 CPU 时间，不是屏幕延迟；统计约每 0.5 秒刷新，
退出后 HWND 列表清空。另有 capture_active、capture_frames（提交到镜像的变化帧）、
optical_frames、copied_pixels（GPU 区域复制的像素量）、unchanged_capture_frames、
optics_error、backdrop_material，以及 windows 每卡 material/pinned/draggable。
每卡另报告 visible、enabled、focused_control，便于验证可见性和真实控件焦点。
close() 后保留 close_hide_ms、close_join_ms、close_surfaces_ms、close_windows_ms、
close_resources_ms 诊断；工作区先批量隐藏，再等待资源释放，详见[关闭耗时基准](glass-close-testing.md)。
请在退出前采样性能。

限制为最多12卡、24组、8层容器，节点 id 全局唯一，不允许环/共享子树。
每卡32控件、宽220..1600逻辑像素、高最多1200且必须放入当前
显示器工作区。UI 纹理预算256MiB，不包括提供者持有的外部纹理。
场景最多保留8批待确认来源，事件队列1024项，连续画布移动事件合并；溢出计数可观测。
卡片是普通窗口层级，不使用全局 topmost。光学材质会采样当前显示器桌面，包括其他
可见应用，但仅在 GPU 使用；库不执行脚本、请求网络或永久修改宿主样式。
Python 和原生提供者是可信宿主代码，玻璃 UI 本身不是沙箱。

当前未支持 macOS/Linux、任意卡片旋转/透视、复杂文本编辑和完整无障碍认证。
旋转显示器不启用光学采样；HDR、跨显卡、多显示器跨 DPI 和远程会话尚未验证。
桌面采样上限16兆像素，聚焦背景上限8兆像素；显卡/采样不可用时实色回退。
Windows 10 尚无真实视觉验证。
请使用[验证命令](validation/glass/README.md)在目标显示器、缩放和终端上检查实际表现。

## C++ 扩展入口

这是与 BrowserService 同级的上层服务。独立 CMake 包 `ComfortableGlass` 导出
`Comfortable::comfortable_glass`，依赖底层 `ComfortableFrames`，安装头文件
`comfortable/glass.hpp`。原 Canvas 帧提供者不需要链接它，帧协议仍是 v1。

```cmake
find_package(ComfortableGlass CONFIG REQUIRED)
target_link_libraries(my_host PRIVATE Comfortable::comfortable_glass)
```

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
原生宿主也须自行路由事件，决定是否在有效 header pointer_down 后调用
`host.begin_drag(card_id, event.sequence)`；token 在鼠标松开或捕获取消后失效。
原生 API 不内置 Python 的 Group 回调。
