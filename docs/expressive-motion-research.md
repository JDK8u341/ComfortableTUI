# 弹性动效：Exa 调研与 ComfortableTop 验证

日期：2026-10-17。使用 Exa 从弹簧交互、终端渲染和可访问性三个方向检索，七次
搜索共请求 35 条候选结果，按 URL 去重为 32 条；排除二手教程、无关游戏仓库及
镜像文档的性能数字。原始记录位于 `exa-results/expressive-motion-2026-10-17/`。

| 一手来源 | 核实的机制 | 实现决定 |
| --- | --- | --- |
| [Apple Designing Fluid Interfaces](https://developer.apple.com/videos/play/wwdc2018/803/) | 即时响应、持续重定向；弹簧不必处处回弹 | 逻辑状态不等待动画；保留速度，区分空间/颜色/数据 |
| [Apple Animate with springs](https://developer.apple.com/videos/play/wwdc2023/10158/) | 反向时保持速度；感知时长与数学停止时间不同 | 解析求解保持，提供周期和阻尼参数，不使用每帧固定比例 |
| [Material motion theming](https://m3.material.io/blog/m3-expressive-motion-theming) | 统一语义 tokens；spatial 可以过冲，effect 不过冲 | Expressive/Subtle、focus/selection/control 与 effects/data 分开配置 |
| [Material transitions](https://m3.material.io/styles/motion/transitions/applying-transitions) | 大面积切换要克制，避免漫长混杂的透明交叠 | 页面小幅回弹，文字保持可读，不使用逐项无限错峰 |
| [osu-mc cursor.go](https://github.com/chhongzh/osu-mc/blob/9b931b5de183b7e1f8f4cb08ff0b7d701e4ab470/internal/ui/cursor.go) | 独立前后沿、按压挤压、释放弹回 | 保留四边追踪，增加焦点背景挤压；不移动按钮文字 |
| [Ghostty synchronized output](https://ghostty.org/docs/help/synchronized-output) | 同步更新控制原子显示，不消除生成帧的 CPU 开销 | 保留协商和差分，优化实际 CPU 路径 |
| [W3C animation from interactions](https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions) | 非必要运动应能关闭 | 环境变量/构造参数关闭动效，停止装饰性 spinner 驱动 |

这是针对终端的设计推导，不是声称遵循所有网页 WCAG 条款，也不把其他项目的
benchmark 当作本库性能。更弹不等于全屏震动或让 CPU 数字上下过冲。

## 原有问题

共享焦点和选择位置使用 damping=0 的指数缓动，没有回弹且重定向时没有速度可保留。
多数控件的动画参数散落，复选框和柱状图缺少可见过渡，弹窗和通知直接出现/消失。
按钮反馈还会被最后绘制的共享焦点覆盖。静态像素画布在其他控件运动时重复做
braille 采样，扩大了整页动画的 CPU 成本。

## 实现边界

使用现有 Python 解析弹簧，无新 C++ ABI。语义参数、图像缓存、整格合成快路径
减少 Python 工作；GPU/共享缓冲区/CEF 仍由原生层管理。Canvas.blit 只复制单元格，
宽字裁剪不拆半，原生图像与输入所有权不被视觉快照保留。

验证命令、独立安装路径、CPU 耗时与实际显示检查见下一节；CPU render+encode
不等于终端端到端延迟或屏幕 FPS。文档中的结果仅覆盖本机 Windows 与 WSLg。

## ComfortableTop 实测

先重新阅读独立项目的 README、安装、开发与使用指南；使用
`ChatGPT/ComfortableTop/.venv` 安装本分支普通 wheel，保留可选 C++/GPU 扩展，
确认 comfortable_tui 来自其 site-packages。没有把库复制进应用或改 sys.path。
WSL 使用 `/home/lenovo/comfortabletop-dev/venv` 安装 Linux 纯 Python wheel，
原生宿主、CEF 和图表继续使用该项目 `.runtime/linux-x64`。

| 检查 | 结果 |
| --- | --- |
| Windows 库测试 | 220 项通过，包含 30/60/120fps 一致性、真实反向动量、过冲、文字/宽字/焦点/无色/减少动效和缓存失效 |
| WSL2 库测试 | 204 项通过，5 项跳过；Windows 特定或缺少可选 Qt Python 绑定的测试未冒充已执行 |
| ComfortableTop | Windows 27 项通过；WSL2 26 项通过、1 项 Windows 测试跳过 |
| [真实 Windows GPU + CEF](validation/motion/windows-gpu.json) | 十项组合检查通过，原生页面显隐、网页 Enter/Esc、语言与消息往返保持正常 |
| [真实 X11](validation/motion/linux-x11.json) / [Wayland](validation/motion/linux-wayland.json) | 从 /tmp 启动，各十项检查通过，分别确认实际 Qt xcb / wayland 平台 |
| [Expressive](validation/motion/expressive.json)、[Subtle](validation/motion/subtle.json)、[Reduced](validation/motion/reduced.json) | 真实采样两次后暂停，连续快速切页/换焦点、通知、模态和 PageStack 检查通过；最后 90 帧差分全部为空 |

以下是本机 Windows 140×42、60Hz 模拟步长、约 290 个可读进程的 CPU render+encode
P95（毫秒）。[原基线](validation/motion/baseline.json)使用之前安装的 208c823 依赖；
新结果采用最后一次单独执行的 Expressive 验证。两次读取的是各自当时的真实系统
快照，负载/进程数量不完全相同，属于性能采样，不是隔离实验或帧率保证。

| 场景 | 基线 | 新版 |
| --- | --- | --- |
| 资源页切换 | 33.857 | 11.611 |
| 弹窗进入 | 28.886 | 14.170 |
| 弹窗退出 | 27.904（直接切走） | 13.900（带退场动画） |
| 通知出现 | 25.841（直接出现） | 8.243（带展开动画） |
| 快速切页 / 快速换焦点 | 原工具未测 | 14.136 / 15.400 |

退出快照第一版 alpha 合成曾测得约 36ms P95，因此增加整格染色和按不可变单元格
复用的合成快路径；不通过删掉检查或调低终端尺寸让结果通过。仍可能存在 16.7ms
以上的尾部帧；更高分辨率、远程连接及终端自身绘制耗时需要独立测量。

复现（均在 ComfortableTop 仓库，报告使用新路径）：

```powershell
.\.venv\Scripts\python.exe -m comfortable_top.verify_motion --report .build/motion-new.json
.\.venv\Scripts\python.exe -m comfortable_top.verify_motion --motion subtle --report .build/motion-subtle-new.json
.\.venv\Scripts\python.exe -m comfortable_top.verify_motion --reduced-motion --report .build/motion-reduced-new.json
.\.venv\Scripts\python.exe -m comfortable_top.verify_windows --mode hybrid --canvas gpu --report .build/motion-display-new.json
```

Linux 使用当前独立项目文档的 `--mode hybrid --platform x11/wayland --self-test
--smoke-seconds 15 --report PATH`，不是旧的 examples 入口。WSL 第一次库测试因该
应用 venv 没有测试工具 pyte 而无法收集；安装库声明的开发依赖后，完整复查通过。
