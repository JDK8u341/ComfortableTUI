# Examples

浏览器上层服务示例：`python examples/webview.py`（Windows 终端附加层）。
需要可选 CEF/原生宿主，详见 [WebView 指南](../docs/webview.md)。
Linux WindowHost 和 WebView 图形运行路径已经暂停；旧 Linux GUI 验证脚本仅为历史资料。
普通纯 TUI、PixelCanvas 仍按终端能力运行，见[支持决策](../docs/linux-support-decision.md)。

原生安全负面测试用 `verify_browser_security.py --browser PATH --report NEW.json`，
覆盖原始 RPC、跨域、文件选择/下载拒绝、消息洪泛和 CSP。需要匹配安全协议的 CEF helper。

先在仓库根目录执行 `python -m pip install -e .`。随后：

| 命令 | 内容 |
| --- | --- |
| `python examples/hello.py` | 最小表单、中文输入、按钮回调 |
| `python examples/gallery.py` | 全部控件族、表格、目录树、编辑器、图形 |
| `python examples/motion.py` | Expressive/Subtle 动效切换、按压回弹、弹窗、通知和演示数据插值 |
| `python examples/animated_menu.py` | 惯性滚动、页面切换；Ctrl+N 插入、Ctrl+D 删除 |
| `python examples/dashboard.py` | 定时器、曲线、进度条、后台任务 |
| `python examples/pixel_oled.py` | OLED 风格的 128×64 像素动画；自动降级 braille |
| `python examples/pixel_oled.py --backend braille` | 强制通用字符画布 |
| `python examples/embedded_canvas.py` | 一个 HWND 中的列表行、表格单元格、Text 混排、标签切换与弹窗画布；安装 `.[overlay]` |
| `python examples/benchmark_overlay_layer.py` | 1/4/16 画布局部/全部更新的 Qt CPU 合成采样 |
| `python examples/overlay_canvas.py` | Windows 透明原生画布、独立渲染进程、缓冲区、缩放与输入；先安装 `.[overlay]` |
| `python -m examples.glass_workspace` | Windows 终端周围的自有原生玻璃卡片，四种空间布局、笔记与 CPU/GPU 画布；先构建 GPU 扩展，见[专用指南](glass_workspace/README.md) |
| `python examples/verify_glass_windows.py --report .build/glass.json` | 自有原生窗口夹具，验证玻璃交互、帧呈现、移动、焦点和清理；需要真实桌面 |
| `python examples/benchmark_overlay.py --qt` | 共享缓冲区与 Qt 离屏性能；不代表屏幕 FPS |
| `python examples/verify_overlay_windows.py` | 创建专用 Windows Terminal，验证原生定位/层级/输入并退出 |
| `python examples/benchmark.py --frames 300` | CPU 基准；不包含真实终端显示耗时 |
| `python examples/record_motion.py --output motion.gif` | 可选 Pillow 生成离线动画预览，非终端屏幕录制 |
| `python -m comfortable_tui --snapshot` | 无交互终端也可输出一帧，用于自动化检查 |

公共操作：Tab / Shift+Tab 切换焦点，方向键编辑/选择，Enter 确认，Escape 关闭弹窗/返回，
Ctrl+Q 或 Ctrl+C 退出。画廊中先聚焦顶部 Tabs，再按左右键切换页面。
按 F6 可直接聚焦菜单栏，再按 F6 返回内容；Enter / ↓ 也可从菜单进入当前页。
建议窗口至少 100×32；小窗口会裁剪内容，表单可滚动。
Gallery 默认目标 120fps，CLI 可用 `python -m comfortable_tui --fps 60` 调低。

`python examples/verify_overlay_windows.py --embedded` 在自己创建的 Windows Terminal 中验证多画布共享窗口、原生输入、标签页隐藏与恢复。

- `python examples/gpu_canvas.py`：独立C++提供者、共享GPU纹理、原生呈现与内嵌视图。
- `python examples/gpu_canvas_inprocess.py`：在Python进程内加载pybind11模块，C++线程绘制移动方块；先构建`examples/native`，步骤见SDK文档。
- `python examples/benchmark_gpu.py`：同一原生CPU源对比Qt/C++呈现，再测共享GPU源。
- `python examples/verify_gpu_windows.py`：真实DWM图案、GIL独立性、输入和释放握手。

这些示例需先编译原生GPU扩展与CMake SDK，步骤见 [构建文档](../docs/native-gpu.md)。

综合系统监视器已迁移到独立项目 [ComfortableTop](https://github.com/JDK8u341/ComfortableTop)，不再随本库 examples 分发。

- `python examples/webview_gpu.py`：Windows CEF GPU 纹理输出，含列表/表格/Text 镜像与标签隐藏；可用 `--render-mode cpu` 对照。
- `python examples/webview.py --render-mode gpu`：GPU 模式下的多页面、独立账号与消息示例。
- `python examples/verify_webview_gpu_windows.py --report gpu-report.json --benchmark-seconds 2`：真实 GPU/DWM 验证与同网页 CPU/GPU 采样。

这些 WebView 示例需要匹配的新 CEF helper；构建、预算与同步说明见 [WebView GPU 文档](../docs/webview-gpu.md)。
