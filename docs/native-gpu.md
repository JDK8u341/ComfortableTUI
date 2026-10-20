# 原生 C++ 帧提供者与 GPU 纹理

Windows 10/11、本机桌面、D3D11.1。一个透明附加层仍显示所有画布，原有标签页、
列表、表格、Text、裁剪和事件接口继续使用。新增 C++ 数据平面，移除每帧 Python
序号检查、租约和 QPainter CPU呈现；Python负责连接描述符、布局、可见状态和输入。
Qt仅维护窗口及非原生输入节点，原生呈现在线程内独立运行，不取GIL。

## 构建与运行

需要 Visual Studio 2022 Build Tools 的 C++ 工具、Windows SDK、CMake，以及匹配
Python 架构的编译器。以下使用仓库虚拟环境，所有构建均为本地，不发布软件包。

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,overlay]"
$env:COMFORTABLE_TUI_BUILD_GPU = "1"
.\.venv\Scripts\python.exe -m pip install --no-build-isolation -e .
Remove-Item Env:COMFORTABLE_TUI_BUILD_GPU

cmake -S . -B .research/gpu-build -A x64
cmake --build .research/gpu-build --config Release
.\.venv\Scripts\python.exe examples/gpu_canvas.py
```

`gpu_canvas.py` 启动独立 C++ 生产者，读取一行握手描述符，再交给 GpuFrameSource。
Python不生成、复制或发布帧。示例第二页包含虚拟列表和 Text 内嵌，共用同一源。
`--provider path/to/ctui_gpu_producer.exe` 指定构建产物；`--source 3840x2160 --fps 240`
改变提供者的源尺寸和生成频率。

检查可用性：

```python
from comfortable_tui import gpu_available
print(gpu_available())  # 扩展可导入；不保证当前桌面/驱动支持所有GPU功能
from comfortable_tui import _native_gpu
print(_native_gpu.adapters())  # name / low / high / software
```

未编译扩展时，普通 FrameBuffer 仍用原 Qt 路径。编译后，本机Windows默认选择
C++ D3D11呈现，旧CPU缓冲区也由C++直接附着、读取和上传，不经Python帧循环。
CPU兼容输入通过动态纹理WRITE_DISCARD映射直接复制，仍有CPU→GPU带宽成本，
大图不保证快于Qt下采样；高分辨率源优先使用共享GPU纹理。
`COMFORTABLE_TUI_OVERLAY_BACKEND=qt` 强制旧路径用于对比；`native` 强制原生路径，
不可用就明确报错；默认 `auto` 只在扩展存在且 Qt 使用 windows 平台时选择 native。
GPU源不支持静默降级为CPU，offscreen Qt后端也不能显示GPU源。

## Python 握手

### 进程内提供者示例

`examples/gpu_canvas_inprocess.py` 将提供者作为 pybind11 模块加载到运行 TUI 的
Python 进程中，不启动 producer.exe。C++ 线程拥有 FrameProvider、D3D11 context
和绘制循环，每帧绘制一个移动方块；Python 只创建对象、读取一次通道名称、处理
布局/输入和关闭。附加层显示进程仍然存在，与其他画布共用，并非把 Qt 窗口放进
TUI 主进程。图像继续通过共享 GPU 纹理交给原生合成器。

先按上文编译 GPU 扩展和 SDK，再编译单独的示例模块（无需修改或重编 TUI 核心）：

```powershell
cmake --install .research/gpu-build --config Release --prefix .research/gpu-sdk
$sdkPath = (Resolve-Path .research/gpu-sdk).Path
$pythonPath = (Resolve-Path .venv/Scripts/python.exe).Path
$pybindPath = & $pythonPath -m pybind11 --cmakedir
cmake -S examples/native -B .research/inprocess-build -A x64 "-DCMAKE_PREFIX_PATH=$sdkPath" "-Dpybind11_DIR=$pybindPath" "-DPython_EXECUTABLE=$pythonPath"
cmake --build .research/inprocess-build --config Release
.\.venv\Scripts\python.exe examples/gpu_canvas_inprocess.py
```

核心用法：

```python
from _ctui_inprocess_example import InprocessProvider
from comfortable_tui import App, GpuFrameSource, OverlayCanvas, Panel

provider = InprocessProvider(1280, 720, 120)  # C++ 后台线程，无 Python 绘图回调
source = None
try:
    source = GpuFrameSource.attach(provider.channel_name)
    with App(Panel(OverlayCanvas(source), "Embedded C++ renderer")) as app:
        app.run()
finally:
    if source is not None:
        source.close()
    provider.close()  # App 必须先关闭，释放显示端的读租约
```

模块搜索路径需包含 `.research/inprocess-build/Release`；完整示例自动添加它，
也可用 `--module-dir` 指定其他目录。`--fps` 设置生产率；`--check` 无需终端显示，
在 Python 持有 GIL 时检查 C++ 线程仍在发布帧。这只验证生产线程，不验证屏幕显示。

源码 `examples/native/inprocess_provider.cpp` 展示线程启动握手、异常传回、可中断
的节拍等待和幂等 close/join。`frames`、`error` 是显式查询的诊断属性，不会触发
Python 每帧回调。运行中错误可查询 error；构造时设备初始化失败会立即抛出。
示例使用 condition_variable 定时等待，不承诺严格帧间隔。替换线程里的 ClearView
即可接入自己的渲染逻辑；已有引擎也可以在自己的渲染线程上直接调用 SDK，无需
套用这个线程类。当前示例不将鼠标/缩放事件反向传入 C++，仍由 OverlayCanvas
Python 回调处理；需要交互的引擎可另建控制队列，避免每帧图像进入 Python。

### 独立进程握手

```python
from comfortable_tui import App, GpuFrameSource, OverlayCanvas, Panel

# channel_name 由你的 C++ 程序通过自己的启动/握手通道传来。
source = GpuFrameSource.attach(channel_name)
with App(Panel(OverlayCanvas(source, fit="contain"), "GPU")) as app:
    app.run()
# App.close() 完成后再停止/关闭 C++ 提供者。
source.close()
```

`GpuFrameInfo(name, width, height, adapter_low, adapter_high, version=1,
pixel_format="bgra8_premultiplied")` 可pickle，只保存连接信息。
`GpuFrameSource(info)` 用已有描述符建立本地引用，`attach(name)` 通过C++验证控制区。
GpuFrameSource没有read/write/sequence接口；每帧不能回到Python。
close仅关闭本地描述符引用，不停止生产者，也不使其他画布的共享源失效。

OverlayCanvas的缩放、smooth、input_mode、set_buffer、set_ime和事件用法不变。
native模式按源事件和DXGI呈现容量运行，**不经过OverlayCanvas.fps的Qt轮询限速**；
需要限制生产率时在原生提供者设定帧率。应用的App.fps仍控制TUI布局和事件处理。

## C++ SDK

公开头文件 `native/canvas/include/comfortable/frame_provider.hpp`，静态库
`comfortable_frames` 无Python/Qt依赖；`comfortable_compositor` 提供可复用呈现器。

```cpp
#include <comfortable/frame_provider.hpp>

comfortable::FrameProvider frames(1280, 720);
const auto descriptor = frames.descriptor(); // 用你的握手通道传给 TUI
while (running) {
    auto frame = frames.begin_frame(1); // 最多等待1ms；空租约表示本帧可跳过
    if (!frame) continue;
    const auto source_time = comfortable::clock_ticks();
    renderer.draw(frames.device(), frames.context(), frame.texture(), frame.target());
    frame.publish(source_time);
} // 先让TUI解除订阅，再销毁frames
```

device/context/texture/target为借用的D3D11接口指针，不能手动Release。写租约存续时
才可写该texture；publish后不可继续修改，所有图像必须完整渲染。租约未publish就
销毁会放弃写入。发布会提交命令、释放GPU同步锁、更新元数据并通知原生消费者。
源时间戳必须来自QueryPerformanceCounter，clock_ticks/frequency提供相同时钟域。

已有D3D11渲染器可 `FrameProvider(w,h,existing_device)`，在该device的immediate
context内绘制目标。该context所有生产者访问需自行串行化，不能在另一线程同时
修改D3D状态。可对租约纹理创建自己的RTV等视图，使用后必须在租约寿命内结束访问。
同一设备上的外部纹理可通过context.CopyResource/CopySubresourceRegion复制到
frame.texture；这是GPU复制，需匹配格式、尺寸及D3D限制。当前不接受D3D12/Vulkan/
CUDA裸指针，相关引擎应通过其D3D11互通机制提供纹理。
NV12/P010等解码输出应先在GPU转换为预乘BGRA8，本协议不直接接受YUV/HDR格式。

CPU渲染器可 `frames.submit_bgra(data, stride, source_ticks)`：输入为完整的预乘BGRA，
原生上传一次，后续跨进程仍用GPU共享纹理。data在该调用返回后可复用。帧大小固定；
更改分辨率需创建新FrameProvider，传新描述符并等待buffer_attached，再停止旧源。

读取方 `FrameConsumer(descriptor, device)` 使用相同适配器，`ready_event()` 提供
原生帧通知，`acquire_latest(after_sequence, timeout_ms)` 返回ReadFrame。读租约可
持续保存并多次绘制；销毁前完成该设备的采样命令。一个通道只允许一个消费者（含
其尚未释放的读租约），多个画布由同一消费者复用。构造、读取、销毁在同一线程完成。

### 在其他 CMake 项目中使用

```powershell
cmake --install .research/gpu-build --config Release --prefix .research/gpu-sdk
```

```cmake
find_package(ComfortableFrames 1 CONFIG REQUIRED)
target_link_libraries(your_renderer PRIVATE Comfortable::comfortable_frames)
```

配置调用方时将安装路径加入CMAKE_PREFIX_PATH；也可add_subdirectory引入源码，
链接comfortable_frames。SDK使用C++17和Win32系统库，公开接口无Python对象。

## 规范与所有权

协议版本1：固定三槽、BGRA8预乘、单Mip、单样本2D纹理、D3D11_USAGE_DEFAULT、
SHADER_RESOURCE|RENDER_TARGET以及SHARED_NTHANDLE|SHARED_KEYEDMUTEX。
随机Local命名的NT资源、短元数据mutex和auto-reset event构成通道。
元数据映射为144字节，magic为CTUIGPU1，长度/版本/尺寸/LUID均验证；槽状态与序号
受跨进程mutex保护。请使用SDK而不是依赖内部C++结构直接改写元数据。

生产者选择Free或最旧Ready槽，不能覆盖Reading；消费者只取最新Ready。GPU key=0
配合槽分配，不要求严格轮流取每帧。锁失败/超时不把不完整图像当成完整帧。
显示端直接从持有租约的共享纹理采样，没有GPU→CPU下载，也没有额外私有中转纹理；
最终绘制到DComp交换链仍消耗GPU和显存带宽。

创建方和显示端必须同一adapter LUID。混合CPU/GPU输入会选GPU源指定的适配器；
同层多个GPU源必须一致。跨GPU不作隐式复制。每层最多60个独立注册源、4096个逻辑
视图；同源多个视图不增加消费者。每个源纹理约 `width*height*4*3` 字节，另有交换链。

buffer_attached在原生线程成功打开源之后提供；detached在原生线程释放引用之后
提供（包括虚拟行移除）。收到detached后，可停止专用生产者并释放其资源。隐藏页
保留连接但不取新帧；共用源时应按可见订阅数决定是否暂停。App关闭先停止原生线程。
本地用户会话的默认对象权限保护通道；这不是面向不可信跨用户客户端的远程服务协议。

C++ 的多个 Compositor 可先统一 request_close()，再逐个 close()，使停止过程重叠：

```cpp
for (auto *renderer : renderers) renderer->request_close();
for (auto *renderer : renderers) renderer->close();
```

request_close() 仅发出停止请求，后续 configure/visible 被拒绝；close() 仍须等待线程退出，
返回后才能释放来源和 HWND。Glass 工作区使用此方式关闭多个消费者；帧提供者接口和共享内存
协议没有改变。此新增 C++ SDK 方法需要重新构建 SDK/宿主，原有独立帧提供者不受影响。

设备丢失、弃用keyed mutex、旧消费者带着读租约崩溃、LUID不匹配会报错，要求新建
FrameProvider并重新握手。不会复用不一致纹理，也不承诺自动TDR恢复。

## 透明层、输入与指标

使用原有的一个Qt窗口、非原生子控件、owner层级和UIA定位。native模式禁用Qt绘制，
去除WS_EX_LAYERED，启用WS_EX_NOREDIRECTIONBITMAP，C++挂接DirectComposition。
DXGI使用双缓冲、预乘alpha、flip sequential、最大帧队列1及frame latency等待对象；
先等待容量，再读取最新帧。没有Python timer驱动的帧同步。

Win32输入region为可见画布矩形并集，矩形外直接到终端；矩形内透明像素仍属于该
capture画布。passthrough继续通过Qt命中/转成TUI事件。原有鼠标、键盘、IME等仍为
控制平面，不需要原生提供者每帧回调Python。数据到达与布局刷新独立。

metrics新增：renderer="d3d11"、gpu_shared、source_fps、ready_age_ms、acquire_ms、presents、closed。
frame_age_ms为源时间戳到Present返回；ready_age_ms从GPU发布时刻计算，旧CPU协议
没有独立发布时间戳时为None；draw_ms含本轮
提交/Present调用。painted_fps统计取到的新源帧，不是显示器实际FPS；presents也是
提交计数。ready与layer_status中的renderer区分旧Qt路径与D3D11路径。

这些数值不包含DWM合成、显示扫描或输入到像素响应。没有外部测量设备时，不把
亚毫秒提交帧龄称为“亚毫秒显示延迟”。

## 验证与性能复现

```powershell
.research/gpu-build/Release/ctui_frames_test.exe
.\.venv\Scripts\python.exe examples/verify_gpu_windows.py --report .research/verify-gpu.json
.\.venv\Scripts\python.exe examples/benchmark_gpu.py --seconds 3 --output docs/benchmark-gpu.json
```

真实GPU验证脚本暂时显示自己的小测试层，只读取其不透明测试图案像素，不注入全局
输入、不采集其他应用画面。它验证真实DWM图像、输入透明洞、多视图、隐藏恢复、
GIL独立性和解除订阅。SDK测试的GPU readback只用于校验，不在生产呈现路径中。
原终端定位/层级集成仍用verify_overlay_windows.py；需要测试终端能进入前台。

本机实测与官方依据见 [调研](native-gpu-research.md)、[720p数据](benchmark-gpu.json)、
[4K数据](benchmark-gpu-4k.json)。CI验证SDK和pybind编译、安装及无硬件控制API；
托管runner不能替代真实GPU/驱动/桌面测试。
