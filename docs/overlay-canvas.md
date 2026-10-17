# 原生缓冲区画布（Windows）

`OverlayCanvas` 是一个真正的 TUI 控件：在 VBox/HBox/Grid/Panel 中参与布局、裁剪和
焦点切换，但像素由另一个透明、无边框的原生窗口显示。Windows Terminal 不需要
支持 Kitty 或 Sixel。渲染方只提供图像缓冲区，库不要求它使用某种绘图 API。

首版支持本机 Windows 10/11 桌面。纯 TUI 和 headless 渲染不加载 Qt。

## 安装与运行

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[overlay]"
.\.venv\Scripts\python.exe examples/overlay_canvas.py
```

在目标终端分屏内启动。示例使用独立进程绘制动态图像，点击图像出现绿色圆环，
`Scale mode` 循环切换缩放模式，`Pause / resume` 控制渲染方，`Modal` 验证遮挡。
`Tab` / `Shift+Tab` 进出画布焦点；`Ctrl+Q` 退出整个应用。

示例的渲染器使用 Qt 的 QPainter，但与 TUI 渲染器无关。可替换为视频解码器、
游戏、NumPy/OpenCV、Pillow 或通过 pybind11 接收可写 buffer 的 C++ 函数。

## 最小接口

```python
from comfortable_tui import App, FrameBuffer, Label, OverlayCanvas, Panel, VBox

status = Label("Waiting for native canvas", height=1)

def handle(event):
    if event.kind == "transform":
        transform = event.data["transform"]
        status.set_text(f"Scale: {transform.scale}; DPI: {transform.dpi_scale}")
    elif event.kind == "mouse_down":
        status.set_text(f"Click: {event.data['source_position']}")
    elif event.kind == "close_request":
        app.exit()
    return False

if __name__ == "__main__":
    with FrameBuffer(640, 360, pixel_format="rgba8") as frames:
        # 这里只演示上传一张图。实时源应在自己的线程/进程中持续提交。
        frames.submit(bytes([255, 124, 184, 255]) * (640 * 360))
        view = OverlayCanvas(frames, on_event=handle, fit="contain")
        with App(VBox(status, Panel(view, "Video"))) as app:
            app.run()
```

不要在 TUI 运行期间直接 `print()` 到终端。回调在 TUI 线程执行，不要在其中解码
视频或等待耗时工作。
Windows 的 spawn 要求创建/运行应用的代码放在 `if __name__ == "__main__"` 中；
打包为可执行文件时调用 `multiprocessing.freeze_support()`。

## 缓冲区与内存所有权

`FrameBuffer(width, height, pixel_format="rgba8", stride=None, max_bytes=512*1024*1024)`
创建三槽共享内存。尺寸固定，默认 stride 向上对齐四字节，显式 stride 必须为四字节
倍数并容纳完整一行。数据是从上到下的原始像素，不是 PNG/JPEG 编码数据。

| 格式 | 字节顺序 | alpha |
| --- | --- | --- |
| `rgba8` | R G B A | 非预乘 |
| `bgra8` | B G R A | 非预乘 |
| `bgra8_premultiplied` | B G R A | RGB 已乘以 alpha/255；推荐 Qt 渲染器使用 |
| `rgb8` | R G B | 不透明；每行可能包含 padding |

`frames.info` 是可 pickle 的 `FrameBufferInfo(name, width, height, stride, pixel_format)`。
在另一个本机进程中使用 `FrameBuffer.attach(info)`，无需复制像素；附着方关闭只释放
自己的句柄，创建方负责最终关闭。Windows 共享内存在最后一个句柄关闭后释放。

最易用的上传方式：

```python
frames.submit(contiguous_pixels)  # 恰好 stride*height 字节；返回后源数据可立即复用
```

`submit` 接受连续的 Python buffer protocol 对象，包括 bytes、bytearray、memoryview
及形状/类型合适的连续 NumPy 数组。它执行一次完整复制。非连续切片和长度错误会报错。

避免这次复制，让渲染器直接写入共享槽：

```python
with frames.write() as pixels:
    renderer.render_into(pixels, frames.info.width, frames.info.height, frames.info.stride)
```

`pixels` 为可写 memoryview；退出 `with` 才原子发布完整帧，渲染抛异常则不发布。
渲染器必须写好完整帧，不能假定当前槽保存上一帧。memoryview、NumPy 视图、
QImage 或 C++ 指针都不能超出租约寿命；异步 GPU 工作必须在退出前完成写入。

Qt 渲染方可以像示例一样用可写 memoryview 构造 QImage，然后 QPainter 绘制；
在退出写租约前结束 painter 并删除所有包装该内存的 QImage。NumPy 可以通过
`numpy.ndarray(..., buffer=pixels, strides=(info.stride, 4, 1))` 包装 RGBA/BGRA，
使用后立即删除视图。

读取方的 `with frames.read() as frame` 返回 `FrameLease(pixels, sequence, timestamp)`，
没有帧则返回 None。pixels 只读且仅在租约内有效。序号单调增加；`timestamp` 使用
`time.perf_counter()` 的秒数，也可以在提交时显式提供同一时钟域的时间戳。

三槽只保留最新完成帧。慢消费者会跳过中间帧，生产者不会等待它显示每一帧；正在
读取的槽不能被覆盖。锁实现支持独立 Windows 进程附着，不要求双方属于同一次 spawn。
每个缓冲区按单生产者、单显示消费者使用；不要在绘制回调内嵌套读租约。

停止生产者，关闭 App/画布窗口，再关闭创建方 FrameBuffer。不要让导出的视图活到
close 之后。`close()` 幂等；仍有导出视图时会拒绝关闭，而不是使指针失效。

## 尺寸与缩放

`OverlayCanvas` 的 `width` / `height` 是终端单元格尺寸。源图像的像素尺寸来自
FrameBuffer，二者独立；不指定控件尺寸时按布局分配空间。

| fit | 行为 |
| --- | --- |
| `contain`（默认） | 保持比例完整显示，空白区域透明 |
| `cover` | 保持比例铺满，多余像素裁掉 |
| `stretch` | 分别缩放宽高，可能变形 |
| `pixel` | 一个源像素对应一个物理屏幕像素，居中并裁剪 |

`canvas.configure(fit="cover", smooth=False, input_mode="capture")` 动态修改配置。
`smooth=False` 适合像素风；True 使用 Qt 平滑缩放。窗口移动、布局变化、终端字体
缩放、源尺寸变化或屏幕 DPI 变化后，提供 `transform` 事件。

`CanvasTransform` 包含：

- `source_size`：源像素尺寸。
- `viewport_size`：整个控件对应的物理像素尺寸，包含被父容器裁掉的部分。
- `destination`：源图像在完整 viewport 中的目标矩形，cover 可以为负坐标。
- `scale`：分别对应 x/y 的物理像素/源像素比例。
- `dpi_scale`：Qt 逻辑像素到物理像素的比例。
- `source_at(x,y)` / `contains_source(x,y)`：完整 viewport 坐标的反向映射与命中测试。

事件同时附带 `screen_rect`（完整控件）和 `clip_rect`（实际窗口可见区域）。
坐标均为屏幕物理像素，可为负数；Qt 内部自行转换逻辑坐标。由于分数 DPI 下的
整数窗口尺寸取整，原生窗口边缘允许一物理像素差异。

渲染方不必重建源缓冲区即可自动缩放。若希望按目标尺寸重新渲染，可依据 transform
分配新的 FrameBuffer，在 TUI 线程调用 `canvas.set_buffer(new_buffer)`。收到匹配
新描述符的 `buffer_attached` 事件后再释放旧缓冲区，避免辅助进程仍在附着/读取旧帧。

## 事件

回调形式为 `on_event(CanvasEvent(kind, data, timestamp))`，均在 TUI 线程执行。
返回 True 表示渲染方消费方向键；未消费的方向键可按空间位置移动到邻近控件。
App 的全局绑定（Ctrl+Q、Ctrl+C、Tab、Shift+Tab、F6）保持生效。

| kind | data 主要内容 |
| --- | --- |
| `ready` | 辅助窗口 hwnd、pid、host_hwnd、host_class |
| `buffer_attached` | 已安全附着的 FrameBufferInfo，适合切换缓冲区后的释放确认 |
| `transform` | transform、previous、screen_rect、clip_rect |
| `mouse_down`, `mouse_up`, `mouse_move`, `mouse_double_click` | position、source_position、screen_position、inside_source、button、buttons、modifiers |
| `mouse_enter`, `mouse_leave` | 进入含坐标；离开无位置要求 |
| `wheel` | 坐标、angle_delta（通常每格120）、pixel_delta、inverted、phase、modifiers |
| `key_down`, `key_up` | name、text、repeat、modifiers、Qt key、原生 scan_code/virtual_key |
| `ime` | preedit、commit、replacement_start、replacement_length、attributes |
| `focus_in`, `focus_out` | reason；窗口焦点与 TUI 逻辑焦点区分 |
| `activate`, `deactivate`, `window_state` | 原生窗口激活及状态变化，state/old_state 为 Qt 位掩码 |
| `show`, `hide`, `move`, `resize` | 显示状态或 screen_rect、logical_size |
| `context_menu` | 坐标与 reason |
| `drag_enter`, `drag_move`, `drag_leave`, `drop` | text、urls、formats、proposed_action；仅复制元数据，不移动文件 |
| `touch` | phase、points（id、state、pressure、映射后的坐标） |
| `tablet` | phase、pressure、倾角、rotation、buttons、坐标 |
| `gesture` | 系统原生手势 type、value、坐标 |
| `close_request` | 由应用决定调用 canvas.close() 或 app.exit()，默认忽略窗口关闭 |
| `metrics` | 每秒的 Qt painted_fps、skipped_frames、sequence、draw_ms、frame_age_ms |
| `host_status`, `error` | 定位状态或故障；致命辅助进程故障会终止 run 并清理窗口/终端 |
| `events_dropped` | count；TUI 长时间阻塞导致有界输入队列溢出 |

鼠标 `position` 是完整画布物理像素坐标，不会因裁剪重新归零。source_position 是
浮点源像素坐标，留白/拖动出界时可超出图像范围，先检查 inside_source。按住鼠标
拖出窗口后仍由 Qt 的原生捕获机制提供移动/释放事件。button/buttons 使用 Qt 位掩码：
1 左键、2 右键、4 中键、8 后退、16 前进；modifiers 为 ctrl/alt/shift/meta 元组。

键盘事件支持 press/repeat/release，文本和按键分开保留。在辅助窗口尚未显示或使用
穿透模式时，TUI 收到的 Key/Paste 也会提供 `native=False` 的 key/text 事件；终端
协议未提供的信息不会伪造成原生事件。`terminal_mouse` 是终端整数单元格鼠标事件。

移动、几何变化和指标合并为最新值，关键按键/按下/释放按顺序排队；这些合并事件
与输入事件之间不保证逐帧全序。队列有限，不适合把 UI 回调当作视频处理线程。
拖放限制文本64Ki字符、URLs128条、MIME类型64种，不把文件内容当事件传输。

### 输入法

`canvas.set_ime(cursor=(x,y,w,h), text="周边文本", position=..., anchor=...)` 配置 IME 查询。
cursor 是源像素坐标，库按缩放/裁剪转换候选框位置；文本位置使用 Qt/Windows 的
UTF-16 偏移（emoji 可能占两个单元）。渲染方负责根据 ime 事件绘制预编辑文字、
光标和选择区，应用 replacement 范围。库不替渲染方实现文本编辑器。

### 捕获与穿透

`input_mode="capture"`（默认）接受窗口输入。Windows 分层窗口对 alpha=0 的像素
可能穿透，因此完全透明背景保留 alpha=1/255 的最小黑色，以支持整个矩形命中。
`input_mode="passthrough"` 使用真正透明背景及 WS_EX_TRANSPARENT，鼠标到达下面的
终端，覆盖窗口不主动获取焦点。可通过 configure 动态切换。

## 宿主定位与窗口层级

默认先向当前 TUI 输出流写入一段与背景同色的随机标记，通过 UIA FindText 绑定
实际接收它的终端窗口和分屏，随后完整重绘清除标记，并在 App 内缓存绑定。这样
不会因为辅助窗口/其他应用获得前台焦点而选错终端；不修改终端标题。对明确指定
viewport 的非 UIA 终端，才允许使用前台终端/root owner 或可见经典控制台后备识别。
明确忽略 PseudoConsoleWindow，也不会因为 root owner 隐藏而否认可见终端本身。
Windows Terminal 使用 UI Automation 的 TermControl 文本范围测量字体与文本原点，
不会把标签标题当作终端。UIA 在后台线程以约10Hz校准，窗口移动使用缓存的客户区
偏移更频繁同步；终端调整字体/分屏时可能存在一次校准间隔的滞后。

固定首次选中的文本元素。离开标签页/分屏不可见时隐藏；普通 App 标签切换、
ScrollView 裁剪、TUI 弹窗和通知栏也会更新窗口可见区域。UIA 失败时隐藏并报告
host_status，启动定位持续失败会给出明确错误，不猜测客户区。

```python
from comfortable_tui import TerminalHost, PixelRect

host = TerminalHost(hwnd=your_terminal_hwnd, pane_index=0)
# 无 UIA 的终端：明确给出文本区（相对于客户区的物理像素）。
host = TerminalHost(hwnd=your_terminal_hwnd, viewport=PixelRect(8, 48, 1200, 700))
canvas = OverlayCanvas(frames, host=host)
```

显式 viewport 默认将它解释为初始客户区中的文本区域，宿主调整大小时保留四边
inset；`follow_resize=False` 则保持固定像素区域。该模式无法辨认终端内部的标签页，
调用方需通过 widget.visible 等控制隐藏。pane_index 只用于自动 UIA 模式，按可见
终端文本控件顺序选择。第三方终端 UIA 实现不同，优先用显式 viewport 校准。

覆盖窗口使用 Qt transient parent 和 Win32 owner 双重绑定；终端在前台时检查层级，
终端重新激活、移动或调整大小后仍在终端上方。画布自身激活时一起抬升所属终端组，
避免单独浮在其他应用上；不使用持续全局 topmost。切到其他应用或宿主最小化时隐藏。
不会改造或注入 Windows Terminal，不会绘制到远端机器、SSH 客户端或独占全屏表面。

## 性能与验证

Qt 的图像缩放、混合和原生窗口是 C++ 实现。控制/事件走 IPC，像素走共享内存：
无 PNG 编码、base64、逐像素终端输出，也无每帧 pickle。QImage 包装源内存；
这仍是 CPU 栅格显示，Qt backing store、格式转换与 DWM 合成可能复制数据，
不是 DXGI 共享纹理零复制。Qt timer fps 是刷新目标，不能保证显示器帧率。

```powershell
python examples/benchmark_overlay.py --qt --frames 120
python examples/verify_overlay_windows.py --report .research/overlay-smoke.json
```

第一项分别测提交复制、写/读租约管理与 Qt 离屏绘制；写租约管理耗时**不包含生成
图像**。结果在 [benchmark-overlay.json](benchmark-overlay.json)，不等于屏幕显示延迟。
第二项创建专用 Windows Terminal 窗口，验证真实 owner、抬升终端后的 z-order、
移动/缩放/DPI 坐标与鼠标/键盘回传，结束后自动关闭。

已有验证：三槽并发与 spawn 附着、异常写入、裁剪逆变换、纯 headless、标签/弹窗、
Qt 原生图像绘制与更新、Tab、IME、关闭请求，以及本机 Windows Terminal 的真实桌面
测试（包含125% DPI）。触摸/数位笔与真实中文 IME 候选窗需要相应设备/输入法另测；
事件单元测试不能替代这些设备验证。

高分辨率源应优先使用预乘 BGRA，减少 Python 逐像素循环，以原生渲染器直接写槽。
首版每个 OverlayCanvas 使用一个 GUI 辅助进程，适合少量视频/图形画布；大量画布
会增加 Qt 进程、窗口与三槽内存成本。后台渲染方可依据 hide/show 事件暂停/恢复。
完整决策和官方来源见 [调研记录](overlay-research.md)。
