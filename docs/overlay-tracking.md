# Windows 叠加层移动同步

2026-10-18：使用 Exa 搜索 8 条候选，并读取 Microsoft 的 WinEvent、窗口定位及
UI Automation 一手文档；随后在 Windows Terminal 和 ComfortableTop 0.1.5 中对比验证。

## 延迟来自哪里

旧实现把终端的任何屏幕位置变化都当成完整场景变化。调用链是
`tick → apply → 各子控件 place → setMask → Compositor.configure → C++ 确认`。
即使只是拖动窗口、内部画布的相对位置完全没变，也会重复这条路径。基准中完整更新
通常花费数毫秒到十几毫秒；GUI 线程等待时，下一次位置检查也被延后。

另一个风险是 UIA 读坐标：文本矩形、字符尺寸、宿主原点来自不同时间点。若一边读取
一边拖动，把旧屏幕矩形减去新窗口原点，会污染原本应该稳定的客户区偏移。
Microsoft 说明 [UIA 逐个属性访问涉及跨进程调用](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-cachingforclients)，
因此它适合校准文本区，不适合作为连续拖动的位置源。

## 修复

- 注册仅针对宿主进程的 `EVENT_OBJECT_LOCATIONCHANGE`，再过滤宿主 HWND、
  `OBJID_WINDOW` 和 `CHILDID_SELF`。在 Qt 消息循环收到通知后立即读取最新客户区原点。
  回调保持存活，退出时注销；注册不可用时仍由原有 4 ms 定时器补偿。
- 场景、尺寸和 DPI 不变时，只移动共享层并更新子画布事件的屏幕坐标。
  不重设子控件布局、窗口区域、纹理或 swap chain，不调用原生场景确认。
  大小、DPI 或 TUI 场景改变仍走完整配置，继续保证裁剪、命中和生命周期确认。
- 校准前后检查窗口位置、客户区大小、DPI 和网格。几何变化后须静止 150 ms 才重新
  读取 UIA 几何；拖动时持续沿用已经校准的偏移，移动本身不等待这 150 ms。
  页面离屏检查仍保留，切换终端页不会靠旧几何保持画布显示。

[SetWinEventHook 文档](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwineventhook)
说明进程外回调是异步、按顺序投递的，且注册线程必须有消息循环。因此这里复用 Qt
GUI 线程，回调只进行位置同步，不执行 COM 或渲染握手；没有注入终端进程。
仍使用 Qt 的物理/逻辑坐标转换，避免混用原生尺寸破坏分数 DPI backing store。
[SetWindowPos 的异步标志](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowpos)
本身只是跨输入队列投递请求，不能消除场景握手，也不是显示延迟的保证。

## 验证与限制

同一台本机 Windows、125% DPI、Windows Terminal、Top GPU hybrid 模式，依次移动
150 次。每次移动后读取 7 组客户区原点和叠加层 HWND 坐标，采样间隔约 2 ms，
允许一物理像素取整误差。结果和原始采样见
[验证记录](validation/tracking/results.json)，原始 Top 数据分别在
[修改前 CSV](validation/tracking/top_before.csv) 和 [修改后 CSV](validation/tracking/top_after.csv)。

| Top 对比 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 采样窗口内对齐次数 | 103 / 150 | 150 / 150 |
| 未在采样窗口内对齐 | 47 | 0 |
| 修改后全部步骤首次对齐 P95 | — | 7.77 ms |

旧测试未对齐的步骤被截断，不能把旧报告约 16.8 ms 的截断百分位当成真实尾延迟，
也不能据此计算精确的提速倍数。这是窗口坐标对齐测试，不是高速摄影或显示到光子的
测量；Windows 跨进程消息和 DWM 调度仍可能造成短暂的相对移动，不能承诺绝对零延迟。

额外的真实桌面检查覆盖 D3D11、Qt 栅格回退、缩放、透明输入区域、键盘鼠标、
后台遮挡及焦点。多画布场景创建 41 个逻辑画布，验证虚拟行释放、标签页隐藏、模态框
遮挡和恢复。单元回归检查纯位移不触发原生配置、事件源坐标不变、屏幕坐标更新、
尺寸/DPI 变化退出快速路径，以及 WinEvent 的进程范围、过滤和注销。

```powershell
.\.venv\Scripts\python.exe examples/verify_overlay_windows.py --tracking --report .build/tracking-native.json
.\.venv\Scripts\python.exe examples/verify_overlay_windows.py --embedded --tracking --report .build/tracking-embedded.json
$env:COMFORTABLE_TUI_OVERLAY_BACKEND = 'qt'
.\.venv\Scripts\python.exe examples/verify_overlay_windows.py --tracking --report .build/tracking-qt.json
Remove-Item Env:COMFORTABLE_TUI_OVERLAY_BACKEND
```

这些命令只移动各自创建的测试终端，报告保留原始采样。性能阈值依赖桌面负载和机器；
不是硬实时服务等级。多画布测试在定时隐藏/恢复结束后再测连续移动，避免把按设计隐藏
误判为跟踪延迟。示例报告合并高频状态并定时写入；验证失败时保留报告目录，避免子进程
尚未退出就删除其输出目录。

本次不修改帧传输、C++ SDK 或浏览器协议，也不需要重建 CEF/提供者运行时。
