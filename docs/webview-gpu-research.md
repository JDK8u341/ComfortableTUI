# WebView GPU 输出调研与实现记录

2026-10-18 使用 Exa 做两组搜索、检查十条候选结果，再读取 CEF 152 文档、上游
D3D11 示例和 Microsoft 同步文档；实现时以本地固定的 CEF 152.0.14 头文件为准。
范围是 Windows 浏览器服务输出；Linux GPU 按用户要求暂缓。

## 已确认的约束

| 问题 | 依据与决定 |
| --- | --- |
| CEF 如何交出 GPU 帧？ | shared_texture_enabled 启用 OnAcceleratedPaint；Windows 通过 OpenSharedResource1 打开共享纹理。使用现有通用 SDK 发布自有纹理。 |
| 能否长期保留 CEF 句柄？ | 不可。纹理来自 CEF 池，回调后可复用；每次重新打开，在回调内复制。 |
| 是否对 CEF 纹理 AcquireSync？ | 固定版本头文件明确它不带 keyed mutex；只在自有 SDK 槽上使用互斥协议。 |
| Flush 是否代表复制完成？ | 不代表。使用 EVENT 查询观察复制完成，再结束借用。不能依赖论坛中“Flush 就同步”的说法。 |
| popup 怎么办？ | view 和 popup 分别接收加速帧，缓存到自有 GPU 纹理并合成；隐藏菜单时重发干净底图。 |
| Python 是否参与逐帧传输？ | 不参与。它只创建页面、接受通道描述符、发布局/输入和确认资源退役。 |
| 是否自动回退？ | 不。默认 CPU 保持兼容；显式 GPU 必须协商支持，CPU OnPaint 被视为错误。 |

主要来源：

- [CEF 152 CefRenderHandler](https://cef-builds.spotifycdn.com/docs/152.0/classCefRenderHandler.html)
- [CEF 152 加速帧结构](https://cef-builds.spotifycdn.com/docs/152.0/structcef__accelerated__paint__info__t.html)
- [CEF 上游 D3D11 OSR 示例](https://github.com/chromiumembedded/cef/blob/master/tests/cefclient/browser/osr_d3d11_win.cc)
- [CEF OpenSharedResource1 文档修正](https://github.com/chromiumembedded/cef/issues/4027)
- [Microsoft D3D11 Flush 的异步语义](https://learn.microsoft.com/en-us/windows/win32/api/d3d11/nf-d3d11-id3d11devicecontext-flush)
- [CEF 上游卡顿与资源生命周期讨论](https://github.com/chromiumembedded/cef/issues/3755)：用于识别风险，不把评论中的同步建议当规范。

## 验证范围

验收要求包括真实 CEF 加速回调、原生消费者 gpu_shared、屏幕颜色、菜单图像与选择、
Python 持有 GIL 时的序号推进、隐藏恢复、尺寸变化与延迟退役。
CPU 原路径和 Linux CPU WindowHost 必须继续通过回归。

本机 Windows 使用 NVIDIA GeForce RTX 4060 Laptop GPU、D3D11 与固定 CEF 152.0.14；
Linux 回归为 WSL2 Ubuntu 22.04 的 CPU 路径。默认 CPU 未改变。

| 验证 | 结果与报告 |
| --- | --- |
| Windows 单元测试 | 261 项通过，1 项跳过 |
| WSL 单元测试 | 245 项通过，6 项平台跳过 |
| 真实 GPU / DWM | [九项通过](validation/webview-gpu/windows-gpu.json)，含实际颜色、菜单图像、GIL、镜像、隐藏、连续缩放与释放 |
| GPU 浏览器安全 | [十七项通过](validation/webview-gpu/windows-gpu-security.json)；畸形 CPU 通道检查仍检查通用 CPU 协议 |
| 原有 CPU 功能 | [Windows](validation/webview-gpu/windows-cpu-regression.json)、[X11](validation/webview-gpu/linux-x11-cpu-regression.json)、[Wayland](validation/webview-gpu/linux-wayland-cpu-regression.json) 各十四项通过 |
| 真实终端内嵌 | [报告](validation/webview-gpu/embedded-gpu.json)：嵌入列表、表格、Text 时有九个画布，隐藏页与弹窗时为零，两个页面的 cpu_paints 均为零 |
| ComfortableTop 0.1.3 | [十项混合检查通过](validation/webview-gpu/comfortable-top-gpu.json)；测试驱动显式将 BrowserService.open 默认参数设为 GPU，Top 源码与普通环境默认值未改 |
| 打包与文档 | sdist 包含 browser_gpu.hpp、示例页面和手册；Ruff、MkDocs strict 通过 |

GPU 专项测试确认渲染器报告 gpu_shared，测试色为 `(255,64,0)` 和 `(17,34,51)`，
菜单不仅收到回调，还比较了真实窗口对应区域的像素变化。所有旧通道 detach 后原生
GPU 通道注册数为零。第一次菜单测试因测试驱动将浮点鼠标位置直接传给整数输入协议而
超时；改为取整坐标后通过，没有通过跳过菜单检查掩盖问题。

## CPU / GPU 对照采样

同一 CSS 动画网页，目标 60fps，固定 640×360 的显示区域，源图像分别为两种尺寸。
预热后每组约三秒，源码和完整数据见上方 Windows GPU 报告。

| 源尺寸 | CPU 提交 P95 | GPU 提交 P95 | CPU / GPU Present 次数 | GPU 借用复制平均等待 |
| --- | --- | --- | --- | --- |
| 640×360 | 0.338ms | 0.183ms | 156 / 180 | 0.581ms |
| 1920×1080 | 0.396ms | 0.204ms | 174 / 180 | 0.949ms |

这里提交耗时来自原生合成器的 submit_ms 采样，Present 次数是 API 调用计数，
不等于显示器实际扫描帧率。GPU 复制等待发生在 CEF UI 线程，不包含在合成器提交值里；
不能用提交值下降推断端到端延迟按相同比例下降，也不能将缩小显示的 1080p 源结果
宣传为全屏 1080p/4K 性能。后台负载、驱动调度和显卡频率仍会影响单机短样本。

汇总边界见 [summary.json](validation/webview-gpu/summary.json)。
