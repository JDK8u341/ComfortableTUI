# Comfortable Top 本机验证 · 2026-10-17

环境沿用 WebView 验证：Windows x64、Python 3.12.14、VS 2022；WSL2 Ubuntu 22.04、
Python 3.10.12、GCC 11.4、WSLg。psutil 7.2.2、pybind11 3.1.0；CEF 152.0.14。

| 记录 | 实际验证范围 |
| --- | --- |
| [windows-cpu.json](windows-cpu.json) | 独立 Windows Terminal，C++ CPU 图表＋CEF，七项组合检查通过 |
| [windows-gpu.json](windows-gpu.json) | 独立 Windows Terminal，D3D11 共享图表＋CEF CPU 帧，同七项通过 |
| [linux-x11.json](linux-x11.json) | Qt 实际 platform=xcb，C++ CPU 图表＋CEF，七项通过 |
| [linux-wayland.json](linux-wayland.json) | Qt 实际 platform=wayland，同七项单独通过 |
| [producer-windows-cpu.json](producer-windows-cpu.json) | Python 持 GIL 期间继续发布、静止不重发、公共 CPU SDK 读帧、拒绝 NaN |
| [producer-windows-gpu.json](producer-windows-gpu.json) | GIL 独立 GPU 发布、静止不重发、拒绝 NaN |
| [producer-linux-cpu.json](producer-linux-cpu.json) | Linux 本例原生模块实际编译、CPU SDK 读帧和 GIL 独立性 |

七项组合检查：有效真实采样、筛选／固定身份、暂停、独立页面及内嵌 Enter/Esc、
发布指标、设置与重复帮助弹窗，以及所有分析页收样／网页选择回到 TUI 的往返。
操作由本例自测直接发给自己的控件和消息桥，不注入全局键鼠，不访问外部网站。

初版自测曾用 page.evaluate() 读取页面状态，被严格 CSP 拒绝。已改为正常的
monitor.probe/proof 主题验证，没有开放 unsafe-eval 或修改 CEF 全局策略。这里保存的
记录均为修复后通过的结果。旧失败报告保留于未纳入版本控制的 .research/。

这些计数不是屏幕 FPS 或端到端延迟测量。Windows 图表生命周期事件来自原生附加层；
Linux 来自自有 WindowHost，不能推断已经支持任意外部终端或所有 compositor。
报告不保存进程名称、用户名、命令行、Cookie 或调查笔记。

运行方式与参数见 [示例报告](../../comfortable-top.md) 及 examples/comfortable_top/README.md。
