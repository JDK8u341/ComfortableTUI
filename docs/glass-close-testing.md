# 单卡关闭与工作区关闭测试

本测试验证三个不同操作：

| 操作 | 预期 |
| --- | --- |
| 卡片右上角 × / 该卡的 WM_CLOSE | 发出 card_close_request；允许拦截，默认只移除这一卡 |
| dismiss / hide | 隐藏整个工作区，保留卡片、来源和可恢复的窗口 |
| workspace.close() | 关闭所有原生卡片、背景和消费者，停止捕获；调用方的宿主和图像来源仍归调用方拥有 |

这是 Glass 控制协议4的行为；旧版右上角 × 会隐藏整个工作区。需重新构建扩展后测试，
不能只更新 Python 文件。底层帧协议仍为1，原 GPU 提供者无需为此改接口。

## 构建

在仓库根目录 PowerShell 执行；先正常退出占用该扩展的示例：

```powershell
$env:COMFORTABLE_TUI_BUILD_GLASS = '1'
.\.venv\Scripts\python.exe -m pip install --no-build-isolation -e .
.\.venv\Scripts\python.exe -c "from comfortable_tui import _native_glass; assert _native_glass.glass_protocol_version == 4"
```

仍需要本机 Windows 桌面和已有 overlay 依赖；卡片绘制为原生。下面的推荐入口直接附着
当前终端；只有后面的自动回归夹具使用 Qt 宿主。

## 手动点击

```powershell
.\.venv\Scripts\python.exe -m examples.glass_workspace.closing
# 嵌套 CardGroup：
.\.venv\Scripts\python.exe -m examples.glass_workspace.closing --grouped
```

在实际 Windows 终端中运行，TUI 留在该终端，三张卡通过 workspace.bind(app) 附着它；
不会创建替代终端的 Qt 窗口。

1. 点击 image/extra 卡的 ×，仅该卡消失，其他卡保持原位置和可见性。
2. 点击 Workspace controls 卡内的 **Close entire workspace** 按钮，关闭整个工作区。
3. 终端 TUI 继续运行，可用终端中的 **Recreate cards** 创建新工作区，或 **Quit** / Ctrl+Q 退出。
4. 即使先关掉 Workspace controls 卡，也可用终端中的 **Close workspace** 完成整体关闭。

真实终端自动检查模式（完成后退出 TUI）：

```powershell
.\.venv\Scripts\python.exe -m examples.glass_workspace.closing --self-test --report .build/glass-terminal-close.json
.\.venv\Scripts\python.exe -m examples.glass_workspace.closing --self-test --grouped --report .build/glass-terminal-close-grouped.json
```

检查实际 GW_OWNER 为终端 HWND、单卡关闭全程的兄弟窗口矩形、关闭后的内容更新，
以及原生按钮关闭整个工作区后终端和来源仍有效。默认开启弹簧动画，不关闭动画来掩盖位移。
可用 --layout 选择 orbit/deck/focus/ribbon，--material solid --backdrop none 隔离关闭逻辑。

只删除卡片时保留空位，不重新编号、压缩或居中。普通内容更新和隐藏/恢复也保留该位置。
添加卡片、改变尺寸/容器结构，或终端尺寸、DPI、显示器工作区变化及显式 reflow 会重新布局；focus/ribbon
中主动切换焦点仍执行其原定过渡。删除当前选中卡造成的自动焦点回退不会重排兄弟。
关闭后继续切换 focus 时也只移动前后两个根容器，其他容器仍保留位置。
这不把兄弟卡变成手动拖动的 pinned 状态；如需主动收拢空位，调用 workspace.command("reflow")。

## 自动验证

```powershell
# CPU 图像：13项
.\.venv\Scripts\python.exe examples/verify_glass_close.py --report .build/glass-close-cpu.json
# 原生 GPU 图像：13项
.\.venv\Scripts\python.exe examples/verify_glass_close.py --gpu-provider .build/glass-native/Release/ctui_glass_signal.exe --report .build/glass-close-gpu.json
# GPU 图像 + GPU 网页：15项
.\.venv\Scripts\python.exe examples/verify_glass_close.py --with-browser --gpu-provider .build/glass-native/Release/ctui_glass_signal.exe --report .build/glass-close-web-gpu.json
# CPU 图像 + CPU 网页：15项
.\.venv\Scripts\python.exe examples/verify_glass_close.py --with-browser --render-mode cpu --report .build/glass-close-web-cpu-mode.json
# 32种位置组合：4布局 × 根卡/行/列/嵌套容器 × 普通/最大化宿主，开启弹簧
.\.venv\Scripts\python.exe examples/verify_glass_close_layout.py --report .build/glass-close-layout.json
```

本节命令使用明确标注的独立 Qt **自动化夹具**，不用于代替真实终端附着测试。
verify_glass_close.py 默认材质 liquid、背景 frosted；加 --animated 开启弹簧。可用
`--material solid --backdrop none` 单独隔离关闭逻辑。
`--with-browser` 使用已有浏览器运行时发现规则，可用 --browser PATH 指定；默认 GPU，
--render-mode cpu 验证共享内存网页来源。另有 --interactive 供调试夹具自身。
自动测试只向本夹具自己的 HWND 发送输入，检查真实窗口状态、模型事件和资源确认，
不把修改 Python 对象就当作窗口已经消失，也不声称自动化了物理鼠标/系统任务栏点击。

检查内容包括：移出关闭按钮后松开会取消、Card/Group 可拦截、×只产生一次单卡关闭请求、
其他卡在整个关闭过程中保持可见且窗口矩形不变、WM_CLOSE 单卡语义、单卡消费者解除、隐藏/恢复、
最后一张卡关闭后停止背景/捕获、重新创建，以及工作区整体关闭和幂等关闭。
浏览器选项额外验证单卡关闭和整体关闭均解除页面消费者，再由调用方关闭页面释放来源。

`remove_card(id)` 只移除指定卡，保留容器和兄弟实例；空容器保留以便动态添加。
已关闭的卡不会因 show() 自动重建；使用 set_cards() 添加后才重新出现。
最后一张卡关闭后，工作区可继续使用，背景/捕获停止。

## 关闭耗时

工作区关闭先用 Win32 批量隐藏全部卡片和背景，再通知所有合成线程停止，最后等待消费者
释放并销毁资源。UI 绘图设备在同一工作区内按 GPU 复用，绘制仍由原来的原生 UI 线程负责。
UIA 跟踪器也提前收到停止信号，与原生清理重叠。`close()` 返回时，消费者已释放；
这没有把资源清理改成调用方不可见的后台任务，来源仍由调用方管理。

真实终端基准，默认每种卡片数测3次，完成后自动退出 TUI：

```powershell
.\.venv\Scripts\python.exe examples/benchmark_glass_close.py --report .build/glass-close-benchmark.json
# 明确使用独立 Qt 夹具进行对照：
.\.venv\Scripts\python.exe examples/benchmark_glass_close.py --fixture --cards 3 8 12 --repeat 3
```

`glass_workspace.closing` 也会在终端日志和报告中记录 workspace_close_ms。
基准分别记录卡片 HWND 全部不可见、全部销毁、原生 close、跟踪器 close 和整个 Python
close 的耗时；不可见时间是 Win32 状态采样，不是显示器实际出光延迟。

2026-10-20 同一本机 Qt 夹具的前后对比，liquid/frosted，各3次的中位数（毫秒）：

| 卡片数 | 优化前 close | 优化后 close | 优化后卡片全部不可见 |
| --- | ---: | ---: | ---: |
| 3 | 116.9 | 78.8 | 2.0 |
| 8 | 272.2 | 155.7 | 2.0 |
| 12 | 428.8 | 190.8 | 1.9 |

真实 Windows Terminal 的独立验证中，12卡 close 中位数157.4毫秒，卡片全部不可见10.4毫秒。
这是本机样本，驱动和桌面负载会影响结果；完整清理仍需要时间。
原始样本与验证结果见 [close-performance.json](validation/glass/close-performance.json)。

关闭后 metrics 增加 close_hide_ms、close_join_ms（各卡合成线程等待之和）、
close_surfaces_ms、close_windows_ms、close_resources_ms（隐藏之后整个原生清理阶段）。
resources 包含 join/surfaces/windows，不能将这些值重复相加；完整 API 耗时以基准的 close_ms 为准。
需按上面的构建步骤重编原生扩展；Glass 控制协议仍为4，底层帧协议仍为1。

## 单元测试与资源边界

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_glass.py -q
```

单元测试覆盖 Card/Group preview/Group bubble/Workspace 消费关闭请求、嵌套容器移除、
失败回滚、回调已移除/替换节点时的默认行为，以及场景确认之前不得发送 detached。

调用方仍拥有 Canvas、FrameBuffer、GpuFrameSource、BrowserPage 和提供者进程。
关闭卡片或工作区只关闭显示消费者；自行渲染的提供者应等 detached 再释放来源，
或者在整个工作区 close() 返回后释放。关闭 BrowserPage 的旧帧保留沿用原确认机制。
不能把关闭显示控件等同于强制杀掉所有使用该提供者的业务。
