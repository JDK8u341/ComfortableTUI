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

仍需要本机 Windows 桌面和已有 overlay 依赖；测试宿主使用 Qt，卡片绘制本身仍为原生。

## 手动点击

```powershell
.\.venv\Scripts\python.exe examples/verify_glass_close.py --interactive
```

测试创建自己的宿主和三张卡，不操作已有终端或其他应用：

1. 点击 image/extra 卡的 ×，仅该卡消失，其他卡保持显示。
2. 点击 Workspace controls 卡内的 **Close entire workspace** 按钮，关闭整个工作区。
3. 若先关掉了 Workspace controls 卡，可关闭测试宿主窗口结束测试。

可选择与原混合示例一致的 GPU 图像和 WebView 路径：

```powershell
.\.venv\Scripts\python.exe examples/verify_glass_close.py --interactive --with-browser --gpu-provider .build/glass-native/Release/ctui_glass_signal.exe
```

`--with-browser` 使用已有浏览器运行时发现规则，可用 `--browser PATH` 明确指定。
默认网页 render-mode 为 gpu；用 `--render-mode cpu` 验证共享内存网页来源。
手动模式记录结果，但不能代替下面覆盖拦截、资源寿命和撤销操作的自动验证。

## 自动验证

```powershell
# CPU 图像：12项
.\.venv\Scripts\python.exe examples/verify_glass_close.py --report .build/glass-close-cpu.json
# 原生 GPU 图像：12项
.\.venv\Scripts\python.exe examples/verify_glass_close.py --gpu-provider .build/glass-native/Release/ctui_glass_signal.exe --report .build/glass-close-gpu.json
# GPU 图像 + GPU 网页：14项
.\.venv\Scripts\python.exe examples/verify_glass_close.py --with-browser --gpu-provider .build/glass-native/Release/ctui_glass_signal.exe --report .build/glass-close-web-gpu.json
# CPU 图像 + CPU 网页：14项
.\.venv\Scripts\python.exe examples/verify_glass_close.py --with-browser --render-mode cpu --report .build/glass-close-web-cpu-mode.json
```

默认材质 liquid、背景 frosted；可用 `--material solid --backdrop none` 单独隔离关闭逻辑。
自动测试只向本夹具自己的 HWND 发送输入，检查真实窗口状态、模型事件和资源确认，
不把修改 Python 对象就当作窗口已经消失，也不声称自动化了物理鼠标/系统任务栏点击。

检查内容包括：移出关闭按钮后松开会取消、Card/Group 可拦截、×只产生一次单卡关闭请求、
其他卡在整个关闭过程中保持可见、WM_CLOSE 单卡语义、单卡消费者解除、隐藏/恢复、
最后一张卡关闭后停止背景/捕获、重新创建，以及工作区整体关闭和幂等关闭。
浏览器选项额外验证单卡关闭和整体关闭均解除页面消费者，再由调用方关闭页面释放来源。

`remove_card(id)` 只移除指定卡，保留容器和兄弟实例；空容器保留以便动态添加。
已关闭的卡不会因 show() 自动重建；使用 set_cards() 添加后才重新出现。
最后一张卡关闭后，工作区可继续使用，背景/捕获停止。

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
