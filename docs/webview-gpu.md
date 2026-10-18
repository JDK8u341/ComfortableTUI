# WebView 的 Windows GPU 纹理模式

这是 BrowserService 上层服务的一种输出模式，普通图像 SDK 不依赖 CEF。
`service.open(..., render_mode="gpu")` 使用 CEF 加速离屏输出和现有 D3D11 帧提供者。
默认仍是 `render_mode="cpu"`；Linux 图像混合支持已暂停，见 [支持决策](linux-support-decision.md)。
这项功能没有实现 Linux 外部终端附加层，也不改变 Windows 普通图表提供者接口。

## 使用

先按 [WebView 构建文档](webview.md)重新编译 CEF helper，并安装 Windows GPU 扩展与
overlay 依赖。旧 helper 即使支持安全协议 2，也不代表支持 GPU 输出；需要握手能力
`service.capabilities["gpu_texture_transport"] == 1`。该能力表示接口可用，实际设备、驱动和导入
仍可能失败。默认原生合成器可消费 GPU 源，显式 Qt CPU 后端不能消费 GPU 源。

```python
from comfortable_tui import App, BrowserService, Panel, WebView

with BrowserService("org.example.myapp") as service:
    page = service.open("https://example.com", render_mode="gpu", fps=60)
    with App(Panel(WebView(page), "Web")) as app:
        app.run()
```

窗口大小、焦点、Enter/Esc/F6、鼠标、IME、隐藏和内嵌规则与 CPU WebView 相同。
`WebView(page)` 可以放进 Panel、Tabs、VirtualList、DataTable 和 Text。
多个 WebView 引用同一 page 会镜像同一文档；独立页面需要分别调用 open。
页面的 Cookie/profile 隔离、消息 topic 授权和 BrowserPolicy 不受输出模式影响。

完整交互示例：

```powershell
python examples/webview_gpu.py
# 明确选择匹配的新 helper，避免命中旧运行时：
python examples/webview_gpu.py --browser C:\path\to\ctui_browser.exe
# 同一界面的 CPU 帧路径：
python examples/webview_gpu.py --render-mode cpu
# 既有账号隔离、多页面与内嵌示例也可选择 GPU：
python examples/webview.py --render-mode gpu
```

GPU 模式是明确要求，不能满足时会报错，不会静默改用 CPU。页面模式在创建时选定；
切换模式应创建新 page。CEF 异步绘制错误会通过页面的 `error` 事件报告，并保存在
`page.frame_stats()["error"]`；应关闭并重建该页面。GPU 导入遇到显卡不匹配时，也会报错；
当前没有跨显卡纹理迁移或自动设备恢复，不能宣称任意多 GPU 配置都可用。

## 数据路径与同步

```text
CEF GPU 离屏帧（临时借用）
    → 在 OnAcceleratedPaint 内 OpenSharedResource1
    → GPU 复制到服务自有 view / popup 纹理
    → 确认借用纹理的复制完成，才返回 CEF 回调
    → GPU 合成到 SDK 三槽共享纹理
    → 原生 D3D11 消费者读取最新槽并完成 DirectComposition 呈现
```

这里的“GPU 直通”指应用帧路径不回读 CPU、不经 Python 搬运或序列化网页像素。
它**不是零 GPU 复制**：CEF 会在回调返回后回收自己的池化纹理，不能缓存句柄交给
显示进程。服务先保存到自有纹理，再绘制到 SDK 输出槽；有下拉菜单时一并进行
预乘 alpha 合成。没有菜单时也保留底图，以便菜单消失后正确恢复。

CEF 的导入纹理不带 keyed mutex，不对它猜测锁值。代码在 GPU 复制后插入
`D3D11_QUERY_EVENT`，Flush 提交命令，再观察查询完成；Flush 本身不是完成屏障。
库自有三槽纹理继续使用现有 SDK 的 keyed mutex、帧事件和最新帧租约。
如果 GPU 复制五秒仍未完成，helper 以退出码 3 结束，避免把仍被读取的纹理交还给 CEF；
宿主会报告 helper 退出。这个策略不会重启显卡或修改系统 TDR 设置。

同一服务在 CEF UI 线程复用 D3D11 device/context，shader 字节码每进程只编译一次。
尺寸改变会分配新通道；旧通道保持有效，直到所有显示消费者确认换源或 detach。
页面关闭后仍显示的最后一帧，也保持到 detach。应先关闭 App，再关闭 BrowserService，
不要手动关闭 `page.source`。

## 预算与诊断

CPU 通道按三个 BGRA 帧计费，GPU 通道按五张 BGRA 表面保守计费：三个共享槽、
一张底图和至多一张可见区域 popup。菜单超出页面的部分会裁掉，不分配超大菜单纹理。
在途旧通道也计入总预算；原生 helper 同样检查 GPU 分配预算。
该预算不覆盖 Chromium 自己的 GPU 进程、驱动池、着色器缓存等全部显存开销。

默认 `BrowserLimits.max_frame_bytes=64 MiB`，GPU 1920×1080 在预算内，4K 需要显式增大。
例如 `BrowserLimits(max_frame_bytes=256*1024**2, max_total_frame_bytes=768*1024**2)`；
必须评估页面数量和缩放时新旧通道同时存在的开销。

`page.source` 在 GPU 模式为 `GpuFrameSource`，没有 CPU `read/submit/sequence` 接口。
使用 `page.frame_stats()` 可获得低频诊断数据：

| 字段 | 含义 |
| --- | --- |
| frame_backend | GPU 为 native_gpu_d3d11，CPU 为 native_cpu |
| cpu_paints | 本页面收到的 CPU OnPaint 次数；严格 GPU 模式应为 0 |
| accelerated_paints | 当前通道完成 GPU 导入复制的回调数 |
| published_frames / dropped_frames | 当前通道成功发布或因槽忙丢弃的帧数 |
| popup_paints / popup | 菜单加速绘制次数及菜单矩形/可见性 |
| copy_wait_ms | 借用 CEF 纹理复制完成等待的累计毫秒数 |
| error | 最近 GPU 路径错误，空字符串代表尚未报告错误 |

缩放创建新通道时，通道计数重新开始；cpu_paints 属于页面生命周期。
这些字段不是网页可信业务消息，不需要每帧请求；也不代表显示器呈现帧率或端到端延迟。

## 验证与复现

安装 Pillow、PySide6 和 GPU 扩展，在真实 Windows 桌面运行：

```powershell
$env:QT_QPA_PLATFORM = 'windows'
python examples/verify_webview_gpu_windows.py --browser C:\path\to\ctui_browser.exe `
  --report gpu-report.json --benchmark-seconds 2
```

测试使用本地 HTTP 页面和自己创建的小窗口，只读取已知测试图案。检查 CEF 加速回调、
实际 DWM 像素、GIL 独立呈现、原生下拉菜单图像及输入、镜像、隐藏恢复、连续换源和
关闭后的 detach。对照采样使用同一动画网页、相同目标 60fps，源尺寸分别为
640×360 和 1920×1080，显示区域保持 640×360。

原生 `submit_ms` 是合成/Present 提交耗时，不包含完整 Chromium 渲染及显示器扫描。
复制等待也可能阻塞 CEF UI 线程，因此不保证 GPU 模式在所有小画布上更快。
调研依据与实际结果见 [GPU 调研记录](webview-gpu-research.md)。
