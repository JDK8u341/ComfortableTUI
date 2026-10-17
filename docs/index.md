# ComfortableTUI 文档

ComfortableTUI 是基于 Python 的终端 UI 库。它把嵌入式 OLED 界面中的目标值、弹簧、
局部刷新机制迁移到终端，同时提供表单、集合、布局和像素画布。

本手册对应 **0.1.0 alpha**。API 仍可能调整；已实现行为和边界均在下列页面说明。

1. [安装、运行与第一个应用](getting-started.md)
2. [osu-mc 原理与现有 TUI 库调研](research.md)
3. [架构、动画和渲染生命周期](architecture.md)
4. [控件目录与交互](widgets.md)
5. [公共 API 参考](api.md)
6. [终端协议与平台兼容](terminal-protocols.md)
7. [性能、基准与 pybind11 扩展](performance.md)
8. [自定义控件、测试与贡献](development.md)
9. [示例程序](https://github.com/JDK8u341/ComfortableTUI/tree/main/examples)
10. [Exa 调研：焦点与菜单运动优化](motion-research.md)
11. [Exa 调研：跨平台 WebView 与画布接入](webview-research.md)（方案调研，尚未实现）

项目结构：

```text
src/comfortable_tui/   应用、动画、画布、协议、布局、控件
native/diff.cpp       可选的 C++17 差分扫描器
examples/             可直接运行的示例与基准
tests/                动画、协议、屏幕重建、控件和终端生命周期测试
docs/                 本手册与可复现基准数据
```

普通字符终端中的文字只能在单元格边界移动。库保留连续的动画状态，并用背景混合、
braille 指示器、八分之一块进度条改善观感。`PixelCanvas` 在 Kitty graphics 可用时可以
直接传输 RGB 帧，实现像素内容的运动；普通表单文字仍走字符渲染后端。
