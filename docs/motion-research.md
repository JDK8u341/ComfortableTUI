# 焦点残影与运动优化：Exa 调研及验证

日期：2026-10-17。针对旧焦点黑边、移动不连续和菜单缺少动画进行修正。
使用 Exa 分三个方向检索：终端背景/同步绘制、可打断弹簧、上游光标实现；四次检索共请求
18 条候选结果。过滤无关游戏光标和二手教程，进一步读取并核对以下一手资料。

| 来源 | 核实的机制 | 对本次实现的影响 |
| --- | --- | --- |
| [osu-mc cursor.go](https://github.com/chhongzh/osu-mc/blob/9b931b5de183b7e1f8f4cb08ff0b7d701e4ab470/internal/ui/cursor.go) | 共享光标，前后沿分开追踪目标 | 跨表单控件移动的共享高亮，四条边独立推进 |
| [osu-mc animation.go](https://github.com/chhongzh/osu-mc/blob/9b931b5de183b7e1f8f4cb08ff0b7d701e4ab470/internal/ui/animation.go) | 不同属性采用不同速度和阻尼 | 分别调整滚动、光标、页面，避免统一使用默认临界弹簧 |
| [Android Compose](https://developer.android.com/develop/ui/compose/animation/customize) | 弹簧目标改变时保持速度连续 | Tabs 在途页面只改变目标，不清零位置或速度 |
| [XTerm control sequences](https://invisible-island.net/xterm/ctlseqs/ctlseqs.html) | 背景色、反显、默认属性是有状态的协议操作 | 颜色级屏幕重建测试；完整刷新先设置新背景 |
| [DEC 2026](https://github.com/contour-terminal/vt-extensions/blob/master/synchronized-output.md) | 限制半帧呈现，无法修复应用写错的背景 | 保留协商和单次写出，同时修正应用合成逻辑 |

## 可复现的问题

1. Panel 填充 panel 背景，但边框使用 muted 自带的根背景。
2. Checkbox、Switch、placeholder、进度文字等用语义样式背景覆盖父容器。
3. 焦点直接切换 selection / 非 selection 样式，使底色错配尤其明显。
4. Tabs 同帧直接切换标题样式和 visible 页面，没有过渡状态。
5. 多个运动属性沿用 speed=18 / damping=1，响应较慢；字符取整进一步降低连续感。

这些是从源码与帧缓冲可验证的背景错配和交互缺失。用户具体终端是否还叠加半帧撕裂，
仅凭现象无法确认；支持 DEC 2026 时继续使用同步事务。

## 修正

- 无显式 style 的控件文字继承已绘制的父背景；Panel 边框采用同一底色。
- 表单共享高亮的四条边独立追踪目标，前沿 28/s、后沿 16/s，先伸展再收拢。
- 使用单元格面积覆盖率混色，保留字符和宽字所有权；不是连续光栅移动文字。
- 列表指示器不再写入孤立深色格；滚动采用 22 rad/s、阻尼 0.85，光标采用 28/s 趋近。
- Tabs 选中条位置/宽度平滑过渡，内容页采用 20 rad/s、阻尼 0.9 的滑动弹簧。
- 反向切页保留在途页面速度；只有当前页接收焦点/鼠标；标题溢出时平滑滚动。
- Gallery/CLI 默认目标 120fps，可用 `--fps 60` 调低；App 默认仍为 60。
- reduced_motion 一次完成变化；无色模式用反显保留可见焦点。

文字仍受终端网格限制，高亮通过覆盖率表现亚格运动；不宣称等同于 OLED 实机效果。

## 性能与验证

先对新增合成做 profile，再增加有界不可变 Cell 复用，避免重复清理/分段已处理的 grapheme。
保持 Unicode 与精确差分检查。本机 300 帧，Python 布局+动画+绘制+ANSI 编码：

| 尺寸 | 项数 | 中位 ms | P95 ms | 平均字节/帧 |
| --- | --- | --- | --- | --- |
| 80×24 | 1,000 | 1.057 | 1.708 | 492 |
| 120×40 | 10,000 | 1.718 | 2.972 | 717 |
| 200×60 | 10,000 | 2.881 | 5.798 | 961 |

原始记录：[benchmark-motion.json](benchmark-motion.json)。不含终端 I/O、合成和实际显示，
不能据此宣称实际 120fps。动画产生更多变化字节，收敛后仍为零字节。

新增测试：旧焦点/边框背景恢复、连续几何、反向切页速度、标题滚动、宽字合成、
无色模式、reduced motion，以及 pyte 逐帧逐格比较背景色。Windows ConPTY 进行交互烟雾
检查；跨平台 CI 以对应提交状态为准。

![Actual cell-frame motion preview](images/motion.gif)

上图由实际 Canvas 帧生成，为 50fps 确定性离线示意，不是终端屏幕录制。
`pip install Pillow` 后运行 `python examples/record_motion.py --output motion.gif` 可重现。
