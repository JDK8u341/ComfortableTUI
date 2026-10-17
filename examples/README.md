# Examples

先在仓库根目录执行 `python -m pip install -e .`。随后：

| 命令 | 内容 |
| --- | --- |
| `python examples/hello.py` | 最小表单、中文输入、按钮回调 |
| `python examples/gallery.py` | 全部控件族、表格、目录树、编辑器、图形 |
| `python examples/animated_menu.py` | 惯性滚动、页面切换；Ctrl+N 插入、Ctrl+D 删除 |
| `python examples/dashboard.py` | 定时器、曲线、进度条、后台任务 |
| `python examples/pixel_oled.py` | OLED 风格的 128×64 像素动画；自动降级 braille |
| `python examples/pixel_oled.py --backend braille` | 强制通用字符画布 |
| `python examples/embedded_canvas.py` | 一个 HWND 中的列表行、表格单元格、Text 混排、标签切换与弹窗画布；安装 `.[overlay]` |
| `python examples/benchmark_overlay_layer.py` | 1/4/16 画布局部/全部更新的 Qt CPU 合成采样 |
| `python examples/overlay_canvas.py` | Windows 透明原生画布、独立渲染进程、缓冲区、缩放与输入；先安装 `.[overlay]` |
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
