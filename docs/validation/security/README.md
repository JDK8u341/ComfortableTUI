# 原生安全边界与性能验证

> 2026-10-18 状态更新：本文涉及的 Linux/WSLg 图形测试是历史自有窗口结果。Linux 图像混合支持现已暂停，不能据此宣称外部终端兼容；[当前决策](../../linux-support-decision.md)。

2026-10-18，本轮安全特性分支。完整威胁模型、覆盖矩阵和未实现项见
[安全模型](../../security-model.md)。本目录记录实际结果，不把编译成功、默认配置或
helper 自报 `sandbox_requested` 当作 OS 隔离验证。

## 环境与版本

- 基线 ComfortableTUI：`b7d88454007f67552c667f68c3fda2f32c727bc2`（0.1.1）。
- 测试应用 ComfortableTop：`52c6be9c803d6ba70a47464ef4ebd3d83bb5a5b7`（0.1.2），
  应用代码和已安装的普通运行时未修改；通过显式 executable / 模块目录选择测试运行时。
- Windows：Python 3.12.14、C++ 差分扩展、共享 GPU 图表、Windows Terminal。
  前后两环境均为 wcwidth 0.9.2、regex 2026.9.29、PySide6-Essentials 6.12.0、psutil 7.2.2。
- Linux：WSL2/WSLg，Python 3.10；实际 X11 和 Wayland 图形会话、Qt 原生窗口宿主、
  CPU 三缓冲图表。这里验证的是 WSLg，不能外推为所有 Linux 桌面/驱动，也不是外部
  Wayland 终端窗口附加的支持声明。
- 两平台 CEF 均为 `152.0.14+g8a0b5b9+chromium-152.0.7977.167`，从修改后的原生代码
  重新编译；Windows GPU 扩展及 Linux 窗口宿主也已重新编译。

## 功能与反向验证

| 验证 | Windows | WSL2 X11 | WSL2 Wayland |
| --- | --- | --- | --- |
| 原生安全入口，14 项 | [通过](adversarial-windows.json) | [通过](adversarial-x11.json) | [通过](adversarial-wayland.json) |
| CEF 既有功能，14 项 | [通过](feature-windows.json) | [通过](feature-x11.json) | [通过](feature-wayland.json) |
| ComfortableTop 混合模式，10 项 | [通过，GPU](hybrid-windows.json) | [通过，CPU](hybrid-x11.json) | [通过，CPU](hybrid-wayland.json) |

安全检查直接通过原始 native RPC 绕过 Python 前置检查，覆盖未授权求值/导航、
网页自行导航、未授权资源和重定向（核对第二个 HTTP 服务没有收到请求）、显式资源
授权、主 frame/topic 限制、嵌套/洪泛/未知桥接操作、CSP、帧尺寸/步幅/损坏帧头、
非法启动参数、重启撤销持久化旧授权、空白页导航后订阅失效。真实浏览器输入还会触发
文件选择及下载回调并核对拒绝计数。测试只使用临时 profile 和本地 fixture 页面。

已有功能检查包含应用间 Cookie/localStorage 隔离、显式共享 profile、授权消息、
子 frame 拒绝、超时恢复、中文输入、实际像素输出、订阅重置、持久化重启及全新数据
目录的无人值守启动。Top 检查包含采样、暂停、筛选、固定进程、内嵌画布 Enter/Escape、
页面独立性、消息往返、中英文切换及浏览器 viewport 重排。

单元测试：Windows **272 passed, 1 skipped**；WSL2 **256 passed, 6 skipped**。
跳过项依平台/可选原生后端决定，不能将其算作沙箱通过。Ruff 与 MkDocs strict 通过。

验证期间纠正了既有中文输入检查的时序：先确认控件获焦，再提交输入并等待结果。
Linux 混合验证首次未通过首个采样断言，最后使用显式匹配的源码/原生模块和 0.5 秒
采样间隔复测通过；未把失败尝试算作通过。没有为通过测试放宽 CSP 或沙箱。

## ComfortableTop 性能对照

同一台 Windows 机器、120×33、GPU/CEF 混合模式，每次运行 12 秒，排除前 3 秒预热，
持续真实采样并移动焦点/切换页签/打开弹窗。测量期间不并发运行构建和测试。
基线使用 Top 原来的环境，修改后使用独立测试环境；依赖版本匹配。

| 指标 | 修改前 60 | 修改后 60 | 修改前 120 | 修改后 120 |
| --- | ---: | ---: | ---: | ---: |
| 循环 FPS | 59.88 | 59.91 | 118.64 | 118.52 |
| 间隔 P95，ms | 17.115 | 17.122 | 8.811 | 8.785 |
| CPU render P95，ms | 3.688 | 3.766 | 3.653 | 3.768 |
| overlay P95，ms | 0.062 | 0.063 | 0.061 | 0.062 |

原始数据：[前 60](before-60.json)、[后 60](after-60.json)、
[前 120](before-120.json)、[后 120](after-120.json)。修改后 60 目标测量 541 帧，
120 目标测量 1068 帧；两者均没有超过 50ms 的间隔。120 目标前后均有 10 次间隔
超过帧预算的 1.5 倍。CPU 阶段存在小幅变化，不能据此宣称零开销。

结果支持“此次单机对照未观察到明显帧率下降”。这是应用循环节拍，**不是**显示器
呈现 FPS、输入到光子的延迟或统计显著性结论，也没有用这些数字证明抗洪泛能力。

### 固定输入的终端输出等价

[前](output-before.json)与[后](output-after.json)均运行相同 420 帧 fixture：
固定 24 条记录、停止采样、显式 braille 后端、固定时间步，并依次移动焦点、切页、
显示/关闭弹窗。累计 **233729 字节**，SHA-256 完全相同：

```text
de9b4925fb874970941284eba9946c3320ba3b79bb7ef1580bf34892b59c67d4
```

静止后的最后 90 帧均为 **0 字节**。此项验证输出和空闲行为，没有视频/CEF 画面
逐像素比较的含义。该 fixture 的 CPU P50 为 2.263→2.245ms，P95 为 3.879→4.034ms，
后者约增加 4%，保留原始数据而不隐藏这个变化。

## 复现

先按 [WebView 构建文档](../../webview.md)编译匹配的 helper；旧 helper 会被拒绝。
以下路径由本机环境替换，报告写入新文件，不覆盖随版本保存的记录。

```powershell
python -m pytest -q
python examples/verify_browser_security.py --browser PATH/ctui_browser.exe --report NEW-security.json
python examples/verify_webview.py --browser PATH/ctui_browser.exe --report NEW-feature.json

# 安装对应的 ComfortableTop 0.1.2；保持基线和修改后的 Python 环境独立。
$env:COMFORTABLE_TUI_BROWSER_EXECUTABLE = 'PATH/ctui_browser.exe'
python -m comfortable_top --mode hybrid --canvas gpu --native-module PATH/native --interval .5 --self-test --smoke-seconds 16 --report NEW-top.json
$env:COMFORTABLE_TOP_NATIVE_DIR = 'PATH/native'
python -m comfortable_top.verify_pacing --mode hybrid --fps 60 --seconds 12 --report NEW-60.json
python -m comfortable_top.verify_pacing --mode hybrid --fps 120 --seconds 12 --report NEW-120.json

# 前后两环境分别运行。需要 Top 的 tests/test_monitor.py 和 pytest。
python docs/validation/security/output_probe.py --top ../ComfortableTop --output NEW-output.json
```

实际 pacing 对照使用 Top 自带 `comfortable_top.verify_pacing`，目标分别为 60/120，
其余参数按上面的窗口尺寸、模式和运行时配置保持一致。固定输出脚本
[output_probe.py](output_probe.py)包含全部 fixture 与事件时间点。

Linux 两套 CEF 检查分别添加 `--platform x11` / `--platform wayland`；Top 使用
`--canvas cpu --platform ... --native-module PATH/native`，并通过
`COMFORTABLE_TUI_LINUX_HOST_EXECUTABLE` 选择同次构建的 Qt 窗口宿主。不能用 Qt
offscreen 结果代替这两个图形会话。

## 测试不能证明的事情

同进程 Python/C++、浏览器 broker、帧生产者仍可信；没有实现不可信原生插件的 OS
沙箱。URL-loader 策略不是覆盖 WebSocket/WebRTC/DNS 的网络防火墙。ContentSetting
回读不是实际 USB/HID/WebAuthn 硬件攻击测试。标准净化剪贴板写入遵循 Chromium 手势
规则，没有被虚假标记为全部禁止。CEF 内部路由分配、JS 堆、持久 profile 总容量和
进程总 RSS 也不属于当前帧/RPC 预算。具体后续隔离方案与授权 UI 约束见安全模型。
