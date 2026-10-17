# Comfortable Top：实用混合示例

源码位于独立目录 `examples/comfortable_top/`。真实系统采样驱动终端控件、动画、
虚拟化、内嵌画布、终端图像协议、C++ 图像生产者、Windows 共享 GPU 纹理，以及
独立 CEF 分析页和消息联动。

## 直接运行

```powershell
python -m pip install -e ".[monitor]"
python -m examples.comfortable_top
```

默认不需编译。`--mode native` 启用原生图表，`--mode hybrid` 加入 CEF；Windows
另可选 `--canvas gpu`，Linux 选择 `--platform x11` 或 `--platform wayland`。
本轮不支持 macOS。参数、快捷键、构建、指标含义和模块说明见
[示例完整文档](https://github.com/JDK8u341/ComfortableTUI/tree/codex/comfortable-top/examples/comfortable_top)。

## 调研与计划落地

使用 Exa 搜索五项结果，并查阅以下原始资料。主要设计关系：

| 来源 | 本例采用的设计 |
| --- | --- |
| [htop 官方项目](https://github.com/htop-dev/htop) | 键盘／鼠标、筛选排序、展开进程关系 |
| [btop 官方功能](https://github.com/aristocratos/btop#features) | 资源趋势、进程详情、暂停采样、逐网卡／磁盘速率 |
| [psutil API](https://psutil.readthedocs.io/en/stable/) | 首次 CPU 读数无效、进程 CPU 可超过 100%、访问权限与进程退出竞争 |
| [psutil 性能说明](https://psutil.io/performance/) | 只采必要属性，详情按需，不遍历全部 memory_maps 等高开销信息 |
| [Microsoft Toolhelp](https://learn.microsoft.com/en-us/windows/win32/toolhelp/taking-a-snapshot-and-viewing-processes) | 一次快照取得父 PID／线程数 |
| [GetProcessTimes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes) | 可访问进程的创建时间／CPU 时间，稳定身份为 PID＋创建时间 |
| [NtQuerySystemInformation](https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntquerysysteminformation) | 一次读取公开 WorkingSetSize，不解读保留字段，失败时为 n/a |

没有复制 htop/btop 的实现。这是可排查实际进程的综合示例，不声称拥有它们的所有
功能；GPU 硬件占用、传感器和进程结束操作未实现。共享 GPU 选项指图表呈现和传输，
不指 GPU 使用率采集。

## 功能组合

Processes 的虚拟表格嵌入 Sparkline，Text 混排选中进程的原生／协议图表。
Resources 同时提供 CPU、内存、逐核、网卡、磁盘与网页曲线。Compare 的虚拟列表
嵌入画布，表格嵌入每个固定项自己的 WebView，并有真实进程树。Workspace 用文件树、
笔记、日志和后台导出形成排查流程；Settings 演示表单、滚动、展开、弹窗与原有焦点链。

基础 PixelCanvas 使用 Kitty 协议或 braille。native/hybrid 图表在 C++ 线程生成
并发布帧，Python 只提交低频数值、描述符和布局。Windows 可经 D3D11 共享纹理，
Linux 经 CPU 共享帧与 WindowHost。浏览器为独立上层服务，帧 SDK 不含 URL/profile。

每个固定进程调用一次 open()，独立 DOM、滚动、图表范围，通过明确主题交换业务消息。
最多四个固定项、五个浏览器页、六个原生图表线程，采样刷新不重建网页。
网页保留 CSP，自动验证通过正常消息桥确认收样及选择往返，不开放 unsafe-eval。

## 性能与覆盖边界

一次采集在单后台线程进行，与 UI／C++ 绘图分离，任务不重叠。CPU 和 I/O 取实际
采样差值，不用动画帧间隔；首次、重置和未知不填假零。每进程最多 180 历史点，
进程退出仅保留四个固定项的历史。图表纵轴为 0..100%，超限在表格保留精确值。

Windows 实机发现 psutil 对受保护进程读取的全系统回退开销，改为批量关系／RSS
加可访问进程时间。不能读取创建时间的进程计入 unavailable，不参与稳定身份行；
Windows 状态仅为 alive，交换区为 n/a。这是明确的覆盖取舍，顶栏区分可访问数量
和不可用记录数。Linux 保留 psutil 的平台状态语义。

CPU 共享帧仍有复制和显示上传成本，GPU 仍有驱动与合成成本；不宣称零开销。
CEF 继续使用 CPU 共享帧；UI 60fps 为调度目标，GIL 检查帧数不是屏幕 FPS。
WSLg 验证不能外推到所有原生 Linux compositor。笔记不发送网页，导出只在用户显式
操作时写新 JSON 文件。

## 验证

单元测试覆盖计数器预热／重置、PID 重用、过滤排序、有界历史、排他导出、单采集任务、
三种窗口尺寸、重复弹窗、异常进程关系与 Windows 原生字段。原生检查验证 GIL 独立
绘图、静止不重发、非法样本拒绝，以及 CPU 公共 SDK 租约读取。

窗口组合测试在 Windows Terminal、WSL2 X11、WSL2 Wayland 独立运行，检查真实采样、
筛选固定、暂停、独立网页、表格 Enter/Esc、帮助弹窗、网页消息接收与选择往返。
记录见 [本机验证](validation/comfortable-top/README.md)，不包含进程名称、用户名或笔记。
