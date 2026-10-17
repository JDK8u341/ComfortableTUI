# 帧调度与卡顿排查

日期：2026-10-18。通过 Exa 搜索 Windows 终端、帧调度和 CPython 时钟四个方向，
共请求 20 条候选结果；原始记录在 `exa-results/frame-pacing-2026-10-18/`。

## 确认的主因

本机 Windows 的 Python 3.12.14 返回：

```text
time.get_clock_info('monotonic'):
  implementation='GetTickCount64()', resolution=0.015625
time.get_clock_info('perf_counter'):
  implementation='QueryPerformanceCounter()', resolution=1e-07
```

此前 App 和 Windows Terminal.read 都使用 monotonic 判断帧截止时间。60 FPS 的
16.67ms 刚好大于一个约 15.625ms 的计时粒度，等到条件成立已经过去两个粒度，
循环约 32 FPS；120 FPS 的 8.33ms 会被推到约 15.625ms，循环约 64 FPS。
固定 dt=1/60 的离屏渲染测试不经过这个时钟和等待过程，因此无法发现问题。

[CPython #116781](https://github.com/python/cpython/pull/116781) 与
[文档修正 #126745](https://github.com/python/cpython/issues/126745) 确认 Windows
monotonic 到 Python 3.13 才改用 QPC。应核实实际 Python 版本的 clock_info，不能
把最新版文档直接套在 3.12 上。[Python time 文档](https://docs.python.org/3/library/time.html)
还说明 sleep 可能受调度影响；高精度 sleep 不能补救低分辨率的截止时间判断。

## 实现

- App、Windows 输入等待、Escape 超时、退出输入清理、Linux WindowHost 全部使用
  单调且高分辨率的 perf_counter，不修改系统 timer resolution，也不忙等。
- FramePacer 保持连续截止时间，微小唤醒误差不逐帧累积；超出预算时从当前时间
  继续，只绘制最新状态，不补发过期动画帧。
- cProfile 发现静态文字的逐字裁剪与写入是主要 CPU 热点。短文本和样式使用有界
  缓存，复用不可变单元格并按行复制，保留裁剪、宽字所有权、控制字符清理和返回
  逻辑宽度的语义。长文本走非缓存路径；缓存不保留 Widget/App。
- 保留已有同步更新协商和差分输出，默认 60 FPS 不变，120 FPS 由应用选择。
  [Ghostty 官方建议](https://ghostty.org/docs/help/synchronized-output)同样强调减少
  更新量和原子呈现；同步更新不能修正错误时钟。

原始窗口输出仍同步执行，保持差分帧顺序。个别终端 write 会阻塞，不能随意把
依赖前帧的 ANSI 差分丢进一个丢帧队列。
[Windows Terminal 官方设置](https://learn.microsoft.com/en-us/windows/terminal/customize-settings/rendering)
说明软件渲染和强制全屏重绘也会影响成本；本次没有更改用户的终端配置。
[Bubble Tea 的按需调度提案](https://github.com/charmbracelet/bubbletea/pull/1776)
可作研究参考，该链接是提案，不把其中数字当成本库成绩。

## 实际 ComfortableTop + Windows Terminal 验证

使用独立应用安装的普通 wheel、Python 3.12.14、120×33、GPU 图表和 CEF。每次
单独启动专用 Terminal，连续换焦点、切页和开关弹窗，采样持续运行，剔除前 3 秒。
观测点位于实际 App.run 循环，分别记录 tick、render、encode、write、overlay。

| 目标 | 粗时钟循环 FPS | 修正后循环 FPS | 帧间隔 P95 前 / 后 |
| --- | --- | --- | --- |
| 60 | 32.00 | 59.67 | 32.744 / 17.259ms |
| 120 | 64.01 | 117.77 | 17.492 / 8.984ms |

为隔离时钟问题，表中的粗时钟对照也已安装文字缓存优化，只保留原来的低精度
截止时间；因此可以看到，仅减少绘制 CPU 仍然不能解除 32/64 FPS 上限。
60 FPS 修正后 538 个采样中，超过 1.5 倍帧预算的间隔为 1 个，修正前 290 个
采样全部超过。120 FPS 修正后 1060 帧中有 13 个超过 1.5 倍预算。两种最终测试
都没有超过 50ms 的间隔。这是生产循环节奏，不是显示器扫描或端到端输入延迟。

同一实际 Monitor 的 cProfile 复查中，函数调用量约 54.6M→21.5M；Canvas.text 的
累计剖析时间约 11.9→1.36 秒。剖析器有额外开销，不能把该数字直接称为实际 FPS。

原始数据：[60 FPS 对照](validation/pacing/coarse-60.json)、
[60 FPS 修正后](validation/pacing/after-60.json)、
[120 FPS 对照](validation/pacing/coarse-120.json)、
[120 FPS 修正后](validation/pacing/after-120.json)。独立应用也保存自己的报告与复现工具。
完整功能另通过 [Windows GPU](validation/pacing/windows-gpu.json)、
[WSL2 X11](validation/pacing/linux-x11.json) 和
[Wayland](validation/pacing/linux-wayland.json) 各十项混合模式自测。
库测试 Windows 225 项通过，WSL2 208 项通过、6 项跳过；应用分别为 27 项通过、
26 项通过及 1 项跳过。减少动效、无色、文字宽度和焦点语义保持原有行为。

## 诊断接口

```python
from collections import deque

samples = deque(maxlen=600)
app.on_frame = samples.append
app.run()
# 每项为不可变 FrameTiming，所有时间字段单位为秒
```

字段包括 started、interval、fps、rendered、tick、render、encode、write、overlay、
host。只在实际终端循环或 WindowHost.step/run 调用，App.render 的离屏模拟不生成
运行时帧率。回调应轻量，停用可设为 None；库本身不保存无界历史。

终端路径 render 仅含 compose，tick 单独记录。WindowHost 的 render 含 App.render
（包含业务 advance），overlay 含场景序列化与 RPC，encode/write 为零，host="window"。
interval 是实际循环间隔，不能把空闲无帧时的循环频率当作呈现帧率。

在 ComfortableTop 中复现 Windows 测试：

```powershell
.\.venv\Scripts\python.exe -m comfortable_top.verify_pacing --fps 60 --report .build/pacing-new-60.json
.\.venv\Scripts\python.exe -m comfortable_top.verify_pacing --fps 120 --report .build/pacing-new-120.json
```

报告需使用新路径。正常体验可运行 `comfortable-top --mode hybrid --canvas gpu --fps 120`。
目标超过终端/显示器刷新率时，实际视觉收益取决于宿主；默认 60 适合通常情况。
