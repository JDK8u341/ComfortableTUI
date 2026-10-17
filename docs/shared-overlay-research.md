# 共享附加层调研与实现

> WebView、macOS/X11/Wayland 附加层及替代呈现模式的后续选型与唯一实施计划，
> 已合并到 [跨平台完整报告](webview-research.md)。本页仍记录 Windows 共享层的历史实现依据。

> 共享层的控件/输入架构仍然使用。当前可选的
> [C++ GPU 数据平面](native-gpu.md) 已替换其逐帧传输与呈现路径；
> 本页 Qt backing-store 基准作为原路径记录保留。

2026-10-17，通过 Exa 先搜索三个方向：Windows 透明命中/合成、Qt 脏区性能、场景事件
路由。共请求 15 条搜索候选，再读取 Microsoft 和 Qt 的一手文档及 Qt Windows 后端源码。
以下区分资料结论、工程选择与实测，避免将一个窗口误认为自动获得 GPU 零复制。

## 可行性与选择

可行。采用一个附加到终端文本区的透明 HWND 和一个 GUI 辅助进程，内部使用非原生
QWidget 子控件显示各 FrameBuffer。Qt 的 [alien widgets 文档](https://doc.qt.io/qt-6/qwidget.html#native-widgets-vs-alien-widgets)
明确说明这些控件不具有原生窗口句柄；绘制、移动、裁剪使用同一 backing store。
因此不是将多个原生窗口藏在一个容器中，也不需要对终端进程注入代码。

[Graphics View 架构](https://doc.qt.io/qt-6/graphicsview.html) 提供场景、命中、焦点和
事件路由的参考。现有库已经维护 Widget 树和终端布局，故保留这套布局，映射到 Qt
子控件；不再引入第二套 QGraphicsScene 布局模型。Qt 原生处理子控件的命中顺序、
鼠标捕获、键盘、IME、触摸、笔和拖放；父层负责透明空白及 TUI 输入转换。

## 透明与事件

[Microsoft 分层窗口文档](https://learn.microsoft.com/en-us/windows/win32/winmsg/window-features#layered-windows)
说明 alpha=0 的区域允许鼠标到达底下窗口。共享层背景因此保持 alpha=0，只有捕获型
画布矩形填充 alpha=1/255，透明图像和 contain 留白也可以接收点击。

不能依赖 [HTTRANSPARENT](https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-nchittest)：
它描述的继续命中范围是同一线程的底层窗口，而终端与 GUI 辅助进程属于不同进程。
也不能为单个画布设置 WS_EX_TRANSPARENT，它会影响整个 HWND。

实现中，单个 passthrough 画布使用 Qt 的透明鼠标属性：输入到达更低层画布，或者
由共享层将点击、释放、移动、滚轮转换为 App 的终端单元格事件。画布之外的空白
直接由 Windows 穿透。此转发目标是本库 App，不伪造其他应用的通用原生鼠标消息。

按键先交给对应画布回调；未消费的按键/滚轮沿 TUI 父链进入列表、表格，再执行
空间焦点回退。全局 Tab/F6/退出绑定仍生效。隐藏时取消未完成的指针操作。
事件附带节点标识及实例 token，避免快速回收虚拟行后，旧输入误发到复用标识的新行。
高频状态按节点合并，关键事件有界排队。

## 生命周期与裁剪

每次 TUI 合成生成完整、有顺序的逻辑画布清单：完整矩形负责缩放，可见矩形负责
裁剪，顺序决定叠放。普通嵌套容器、表格表头/水平滚动、列表部分行、Text 混排、
标签页滑动和通知栏均在 TUI 坐标中完成裁剪后交给共享层。

标签切换结束隐藏旧页；modal 清空背景画布清单。普通后来绘制的文字不自动生成
逐字符遮挡蒙版；自定义重叠布局仍需明确隐藏/裁剪底层画布。

ListView 只为可见行实例化工厂控件。离屏行卸载后，辅助层先关闭共享内存引用，
再确认 detached；生产者据此停止并释放自己的源。隐藏页仍保留控件状态/缓冲区，
暂停生产应按可见订阅数管理，不能因一个共享视图隐藏就停止所有视图的共同帧源。

一个 App 对应一个终端文本元素；多个终端窗口/分屏不混在同一个层中。终端前台、
最小化和 UIA 定位规则沿用初版；Qt transient parent 与 Win32 owner 保持附加层层级。
库不会抢占其他应用的前台焦点。真实桌面验证脚本只激活自己创建的测试终端。

## 性能依据与边界

[QWidget.update](https://doc.qt.io/qt-6/qwidget.html#update) 会合并更新请求；
[QPainter](https://doc.qt.io/qt-6/qpainter.html) 的裁剪、缩放和混合在 Qt C++ 中执行。
实现按源序号与各画布 fps 标记局部更新，布局/配置未变时不重复移动或改变叠放。
源图像仍直接映射共享内存，不通过控制队列复制像素。

[UpdateLayeredWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-updatelayeredwindow)
的整窗成本提醒我们避免将共享窗口数减少直接等同于更快。
读取的 [Qt Windows backing-store 源码](https://code.qt.io/cgit/qt/qtbase.git/tree/src/plugins/platforms/windows/qwindowsbackingstore.cpp)
在透明窗口路径调用 UpdateLayeredWindowIndirect，并传入 dirty rectangle。
这是采用局部 update 的依据，但多个离散区域会取包围矩形；系统、Qt 版本和 DWM
仍可引入大面积复制。这里不是 DXGI 共享纹理接口，暂不增加自制 pybind11 渲染层。

1280×720 共享层、每源160×90、Qt6.12、Windows11、120次离屏采样的中位耗时：

| 画布数量 | 只更新一个 | 全部更新 | 原生窗口/原生子控件 |
| --- | --- | --- | --- |
| 1 | 0.837 ms | 0.840 ms | 1 / 0 |
| 4 | 0.389 ms | 1.757 ms | 1 / 0 |
| 16 | 0.191 ms | 2.825 ms | 1 / 0 |

画布数量增加时单个视图面积缩小，单更新耗时因此不能当作同尺寸性能比较。
数据包括 Qt update/processEvents 的 CPU 工作，不含生产者、DWM、显示延迟和 GUI
辅助进程 IPC。完整环境和 p95 见 [原始结果](benchmark-overlay-layer.json)，复现见
[性能文档](performance.md)。这组实测证明局部更新有效，不能推导屏幕固定 FPS。

## 验证

离屏测试检查图像更新、透明洞、移动/隐藏清除旧像素、局部裁剪逆映射、叠放命中、
独立键盘/IME、虚拟行数量有界、表头裁剪和 Text 换行。真实 Windows Terminal
脚本检查 owner/z-order、125% DPI 移动/缩放、原生输入回传、多节点同 HWND、
滚动移除、标签页与 modal 隐藏/恢复。真实中文输入法候选窗口、触摸屏和数位笔
仍需对应设备实测；这些事件的自动化测试不能代替硬件验证。

接口、迁移及示例见 [原生画布文档](overlay-canvas.md)。
