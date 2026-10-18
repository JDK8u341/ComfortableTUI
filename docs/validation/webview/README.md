# 2026-10-17 本机验证

> 2026-10-18 状态更新：本文涉及的 Linux/WSLg 图形测试是历史自有窗口结果。Linux 图像混合支持现已暂停，不能据此宣称外部终端兼容；[当前决策](../../linux-support-decision.md)。

固定 CEF 152.0.14 / Chromium 152.0.7977.167；Windows x64 VS 2022，Linux 为
Ubuntu 22.04 WSL2、GCC 11.4、Qt 6.2.4、Python 3.10.12。
WSLg 1.0.66，DISPLAY=:0、WAYLAND_DISPLAY=wayland-0。
Mesa 23.2.1 glxinfo 报告 D3D12 AMD Radeon 780M；这不是 DMA-BUF 导入能力证明。

| 记录 | 结果与含义 |
| --- | --- |
| service-windows.json | Windows CEF 服务 14 项检查通过，默认沙箱 |
| service-x11.json | CEF 显式 Ozone x11，同组服务检查通过，默认沙箱 |
| service-wayland.json | CEF 显式 Ozone wayland，同组服务检查通过，默认沙箱 |
| window-x11.json | Qt 实际 platform=xcb，10 项 CEF+原生窗口组合检查通过，含 Tab 返回 TUI |
| window-wayland.json | Qt 实际 platform=wayland，10 项组合检查独立通过，含 Tab 返回 TUI |
| windows-controlled-layer.json | 真实 DComp 层通过，包括 DWM 测试色、原生输入、Tab/Esc 退出与延迟焦点事件、GIL 独立帧、暂停恢复及源更换 |
| windows-terminal.json | 焦点/后台显示修复后，真实 Windows Terminal 的所有权、层级、移动缩放、输入、透明洞、Tabs/modal 隐藏恢复通过 |

首轮去除弹窗的 X11 服务回归额外在 Xvfb 中通过；后续 service-x11.json 在 WSLg
显示服务器重新运行。匿名服务每次都生成全新目录；测试未共享应用 A/B 的目录，
未模拟点击条款窗口，未关闭沙箱。测试请求只访问本机 HTTP fixture。

GIL 暂停用例暂停 Python 调度约 0.4 秒，native source_paints 仍增长。该数为两视图
绘制次数，不能直接换算为屏幕 FPS。快照检查只读取自己的 Qt backing store，
不验证桌面合成后的最终像素；IME 用例验证原生 Qt 提交事件到浏览器字段，未测试
实体输入法候选窗。窗口关闭、源更换和 Tabs 均有独立检查。

期间出现过 WSL user session 初始化失败、/run/user/1000 为空的情况，CEF 明确报
Failed to connect to Wayland display。复查发现 WSLg socket 仍在 /mnt/wslg/runtime-dir。
当前辅助进程为这个特定缺失链接情形使用绝对 WAYLAND_DISPLAY，保留用户目录与
其他 compositor 选择；修复后 window-wayland.json 重新通过。依据是
[Wayland 官方客户端 API](https://wayland.freedesktop.org/docs/html/apb.html) 的绝对路径语义；
[Microsoft WSL 问题记录](https://github.com/microsoft/WSL/issues/8842)提供运行目录与
用户 session 交互背景，不能据此推断所有 WSL 启动错误都相同。

初版 Windows 完整终端测试是 incomplete。随后修复了“失去前台直接隐藏”、
后台层级跟随、原生焦点异步回抢和 WebView 吞掉 TUI 导航的问题，新增终端验证
现已通过；不将这一特定 Windows Terminal 结果外推到任意终端。

后台策略保留 owner 之上的正常窗口层级，用 SWP_NOACTIVATE/SWP_NOOWNERZORDER
调整位置，不使用全局置顶，最小化/隐藏/DWM cloak 仍隐藏画布。接口依据：
[Microsoft SetWindowPos](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowpos)。
逻辑焦点为 App 的焦点链，延迟 focus_in 不能重新选中已离开的画布；没有原生子表面
持有键盘时，辅助层将按键和提交文本转回 TUI。

可复现命令与局限见 [WebView 指南](../../webview.md)。这里的通过范围仅对应该环境；
原生 Linux 桌面、不同 compositor、真实中文输入法、GPU 浏览器 adapter、X11 外部
终端附加层、Wayland 专用协作均未在这些记录中获得支持结论。macOS 本轮排除。
