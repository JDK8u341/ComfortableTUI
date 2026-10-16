# 调研：从 OLED 到终端

调研日期：2026-10-17。osu-mc 源码固定在提交
[`9b931b5de183b7e1f8f4cb08ff0b7d701e4ab470`](https://github.com/chhongzh/osu-mc/tree/9b931b5de183b7e1f8f4cb08ff0b7d701e4ab470)。
以下区分源码事实与 ComfortableTUI 的设计推导；没有在 ESP32 实机上测量刷新率。

## 硬件没有替代动画设计

osu-mc 使用 ESP32-S3、128×64 SH1106、TinyGo。页面是可选中列表，嵌套行也遵循相同
交互模型；作者没有用复杂容器层级组织小屏界面。见项目
[README](https://github.com/chhongzh/osu-mc/blob/9b931b5de183b7e1f8f4cb08ff0b7d701e4ab470/README.md)。

核心机制来自下列独立源码模块：

| 模块 | 源码中的机制 | 可迁移的原则 |
| --- | --- | --- |
| [animation.go](https://github.com/chhongzh/osu-mc/blob/9b931b5de183b7e1f8f4cb08ff0b7d701e4ab470/internal/animation/animation.go) | 每个 Value 分离 Current/Target；指数趋近；弹簧保存速度，用最长 1/240s 的半隐式 Euler 子步积分 | 保持浮点状态，输入只改目标，帧时间参与计算 |
| [manager.go](https://github.com/chhongzh/osu-mc/blob/9b931b5de183b7e1f8f4cb08ff0b7d701e4ab470/internal/animation/manager.go) | 统一推进动画，报告是否发生改变，显式注册/释放值 | 每帧只推进一次；静止时不用重画 |
| [list.go](https://github.com/chhongzh/osu-mc/blob/9b931b5de183b7e1f8f4cb08ff0b7d701e4ab470/internal/ui/list.go) | 从目标区域计算条目位置；滚动与视觉追随解耦；目标列表几何不依赖中间位置 | 不让动画位置反过来扰动逻辑布局 |
| [cursor.go](https://github.com/chhongzh/osu-mc/blob/9b931b5de183b7e1f8f4cb08ff0b7d701e4ab470/internal/ui/cursor.go) | 共享光标的位置、大小、倾斜追踪焦点 | 焦点变化也作为连续运动 |
| [viewer.go](https://github.com/chhongzh/osu-mc/blob/9b931b5de183b7e1f8f4cb08ff0b7d701e4ab470/internal/ui/viewer.go) | 页面栈保存进入、挂起、退出状态，通过偏移切页，退出后再释放 | 保留页面状态，等过渡完成才销毁 |
| [event.go](https://github.com/chhongzh/osu-mc/blob/9b931b5de183b7e1f8f4cb08ff0b7d701e4ab470/internal/ui/event.go) | Press / Repeat / Release | 连续导航与一次性确认分开处理 |
| [osu-mc.go](https://github.com/chhongzh/osu-mc/blob/9b931b5de183b7e1f8f4cb08ff0b7d701e4ab470/cmd/osu-mc/osu-mc.go) | 上限 120fps，按真实 dt 更新；只在变化时发送画面；SPI 配置 10MHz | 有帧预算，静止不传输，不用固定每帧位移 |
| [sh1106.go](https://github.com/chhongzh/osu-mc/blob/9b931b5de183b7e1f8f4cb08ff0b7d701e4ab470/driver/sh1106/sh1106.go) | 内存帧缓冲、dirty page 位图、合批页寻址命令 | 局部刷新与减少通信调用同样关键 |

128×64 单色帧缓冲只有 1024 字节；按 10MHz 原始位率估算，纯像素数据理论传输时间约
0.8192ms。该推算不包括寻址命令、SPI 调用、绘制、系统调度和 OLED 面板扫描，因此不能
等同于实际帧率。终端的字符、样式与转义序列通常比同尺寸单色位图复杂得多。

## 动画模型

指数趋近：

```text
x(t + dt) = target + (x(t) - target) * exp(-speed * dt)
```

弹簧：

```text
x'' + 2 * damping * speed * x' + speed² * (x - target) = 0
```

目标改变时保留速度，连续按方向键就有惯性。临界阻尼 `damping=1` 平滑收敛；小于 1 可
越过目标；大于 1 更迟缓。ComfortableTUI 对固定目标的每一时间段使用解析解，避免
Euler 积分的步长误差。`damping=0` 与原项目一样表示指数趋近模式，而非无阻尼振荡。

## 现有通用 TUI 库的控件范围

调研使用官方控件与 API 文档：

- [Textual widget gallery](https://textual.textualize.io/widget_gallery/)：表单、列表、树、
  表格、页面容器和反馈控件覆盖较广。因此本库同时实现单行输入、多行输入、选择、数据
  展示和组合布局，而不局限于 OLED 的列表行。
- [prompt_toolkit full-screen applications](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/full_screen_apps.html)：
  输入、布局、焦点管理与键绑定分离。本库采用独立事件路由和显式焦点域。
- [Ratatui widgets](https://docs.rs/ratatui/latest/ratatui/widgets/index.html)：基础绘图和数据
  视图可组合。本库提供 Canvas、Surface、Sparkline、BarChart、ProgressBar 和虚拟化列表。

本版实现的精确范围见[控件手册](widgets.md)。Markdown、语法高亮、CSS、富文本编辑、
日期选择器等不属于 0.1 API；不能把“参考控件目录”理解为完整复刻任意现有库。

## 终端带来的变化

终端是有状态的远程显示接口：仅重画完整屏幕会增加字节、解析和绘制成本。
ComfortableTUI 将 SH1106 的“脏页”思想迁移为**双帧缓冲 + 行内变化区间 + 样式合并**。
布局和业务留在 Python，可选 C++/pybind11 只扫描精确差分。

[DEC mode 2026 同步更新](https://github.com/contour-terminal/vt-extensions/blob/master/synchronized-output.md)
用于减少终端在收到半帧时显示中间状态。它减少撕裂，不会压缩通信或保证帧率。

[Kitty graphics protocol](https://sw.kovidgoyal.net/kitty/graphics-protocol/)
允许在终端中显示 RGB 图像。本库为 PixelCanvas 使用直接 RGB、zlib、base64 分块；
普通文字继续使用可选中、可编辑的字符单元格，不把整套 UI 转成图片。
图形协议并非天然比文本快：高熵大图可能比 ANSI 差分昂贵。

[Kitty keyboard protocol](https://sw.kovidgoyal.net/kitty/keyboard-protocol/)
允许上报按下、重复和松开。只有协商成功才启用松开确认；传统终端不会伪造并不存在的松开事件。

## 实现边界与结论

视觉流畅度来自连续状态、稳定布局、合理调度与局部刷新协作。硬件加速或 C++ 不能消除
字符网格的量化。字符后端用混色高亮、braille 和块字符表达亚单元格变化，图形后端只
针对显式像素画布。帧耗时与原生扩展收益以[可复现基准](performance.md)为准。

本库为独立实现，没有复制 osu-mc 的代码、字体或图像资源；上游保留其 MIT 许可。
