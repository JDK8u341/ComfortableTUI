# Windows 覆盖画布调研

> 本页保留初版的技术依据。每画布一个窗口/进程的架构已经被
> [共享附加层方案](shared-overlay-research.md) 取代；当前使用说明以 [画布文档](overlay-canvas.md) 为准。

调研日期：2026-10-17。先通过 Exa 进行三个方向的检索（窗口合成、终端坐标、
Qt 缓冲区与事件，共请求 24 条候选），随后阅读 Microsoft、Qt、Python 一手资料。
本功能面向本机 Windows 桌面，使用独立透明窗口覆盖终端中的控件区域。

## 结论与取舍

| 问题 | 证据与决定 |
| --- | --- |
| 窗口句柄 | [GetConsoleWindow](https://learn.microsoft.com/en-us/windows/console/getconsolewindow) 在 ConPTY 下只提供消息窗口；不能据此定位 Windows Terminal。使用明确 HWND 或 UIA 流标记绑定；前台识别仅用于显式 viewport 的后备。 |
| 文本区域 | [Windows Terminal UIA 实现](https://github.com/microsoft/terminal/pull/2423) 对文本范围提供经过字体、padding 和屏幕坐标转换的矩形；[DPI 修复](https://github.com/microsoft/terminal/pull/4497) 说明这些坐标需要考虑缩放。固定首次选中的文本元素，避免跟随到其他分屏。 |
| 前台焦点竞争 | [UIA FindText](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextrange-findtext) 不区分隐藏/可见文本；向当前 VT 流临时写入背景同色随机标记，以文字范围匹配实际窗口/分屏，随后重绘。比单独依赖前台 HWND 更能确定连接关系。 |
| 空文本与定位失败 | [GetVisibleRanges](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtextpattern-getvisibleranges) 在空控件中可以返回空范围。不能假定整个客户区就是文本区；提供客户区相对物理像素 viewport 校准。 |
| 透明合成 | [QWidget](https://doc.qt.io/qt-6/qwidget.html#creating-translucent-windows) 支持半透明顶层窗口；Windows 要求 FramelessWindowHint。使用 Qt 原生 C++ 实现与 PySide6 绑定。 |
| 层叠和输入 | [Win32 Window Features](https://learn.microsoft.com/en-us/windows/win32/winmsg/window-features) 解释 owned window、最小化、透明像素命中行为。覆盖窗口属于终端宿主，不设置全局置顶；捕获输入模式以最低非零 alpha 保留透明区域命中，另有完全穿透模式。 |
| 帧传输 | [SharedMemory](https://docs.python.org/3/library/multiprocessing.shared_memory.html) 允许多个进程直接访问相同内存。使用三槽、单生产者/单消费者租约；控制消息和输入事件独立传递，像素不经过 pickle/base64/管道。 |
| 缓冲区寿命 | [QImage 外部内存构造](https://doc.qt.io/qt-6/qimage.html) 要求像素内存覆盖 QImage 的寿命。消费者持有读租约直到 QPainter 完成；生产者不能覆盖正在读取的槽。 |
| CPU 与 GPU | [UpdateLayeredWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-updatelayeredwindow) 更新整个窗口，建议窗口尽可能小。[QOpenGLWidget](https://doc.qt.io/qt-6/qopenglwidget.html) 存在额外合成、上下文和透明限制。首版采用 QImage/QPainter 原生栅格路径，避免未经测量就增加 DXGI/共享纹理复杂度。 |
| 输入法 | [QInputMethodEvent](https://doc.qt.io/qt-6/qinputmethodevent.html) 区分 preedit、commit、replacement；原样提供这些数据，并提供光标和周边文本查询配置。 |

## 性能边界

默认格式 RGBA8，支持带 stride 的 RGB8、BGRA8、预乘 BGRA8。直接写入槽可以省去
提交时的像素复制；`submit()` 为易用性保留一次 memcpy。Qt 包装共享内存中的 QImage，
缩放和混合在 Qt 中完成，但系统合成、栅格缩放、像素格式转换仍可能产生复制。
这不是 GPU 纹理零复制接口，也不承诺固定帧率。

三槽空间约为 `stride × height × 3 + header`，1080p RGBA 约 23.7 MiB。
每帧完整复制 1080p RGBA、60 fps 的理论单方向字节量约 475 MiB/s；4K60 约
1.85 GiB/s。优先减少复制、使用最新帧而非排队、只创建控件面积大小的窗口，
不要依赖 Python 逐像素循环。基准程序应分别测提交、读取、Qt 绘制和实际呈现反馈，
不能把 memcpy 耗时当成屏幕帧率。

## 范围

首版支持本机 Windows、TUI 控件区域裁剪、原生鼠标/键盘/IME/拖放事件、DPI 与缩放
变换通知、失焦和显示状态。跨机器 SSH、终端复用器、系统权限隔离、GPU 共享纹理、
自定义窗口非客户区操作不属于这一后端。显式 viewport 模式不能自动识别终端标签页，
调用方需要管理可见性。
