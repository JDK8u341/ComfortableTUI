# 浏览器实现简化与验证

> 2026-10-18 状态更新：本文涉及的 Linux/WSLg 图形测试是历史自有窗口结果。Linux 图像混合支持现已暂停，不能据此宣称外部终端兼容；[当前决策](linux-support-decision.md)。

2026-10-18。基于安全分支 `997904c` 简化，使用未修改的 ComfortableTop 0.1.2
（`52c6be9`）验证。此次目标是减少重复职责和修复资源生命周期，而不是扩展权限系统。
基础 TUI、CPU/GPU 传输协议和现有控件保持原有分工。

## 调研依据

使用 Exa 做 4 次搜索，三个方向共请求 20 个候选，筛选官方文档，并对照本机 CEF
152 SDK 核实 API。检索记录在 `exa-results/simplification-2026-10-18/sources.json`。

| 官方来源 | 得到的约束 | 对实现的影响 |
| --- | --- | --- |
| [CEF RequestContext](https://github.com/chromiumembedded/cef/blob/master/include/cef_request_context.h) | 存储和 content settings 属于 context；同 context 的页面共享这些状态 | 初始化和完成记录放回 profile，不在每页重复设置 |
| [CEF RequestContextHandler](https://github.com/chromiumembedded/cef/blob/master/include/cef_request_context_handler.h) | 有明确的 context 初始化阶段和 UI 线程要求 | 继续在第一个安全空白页创建后、加载真实文档前执行；无需增加异步初始化框架 |
| [Chromium origin 与 URL](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/security/origin-vs-url.md) | 安全判断使用准确的 origin，网页自报身份不能代替原生判断 | URL/origin 只由 CEF 解释，移除 Python 的 IDNA/IP/端口解析；网络主 frame 的既有精确比较继续保留 |
| [Qt QImage](https://doc.qt.io/qt-6/qimage.html) | 包装外部内存不会接管所有权，底层缓冲区必须覆盖读取寿命 | 明确按最新附加确认回收，不能把切换控件字段视作读端已释放 |

Chromium 文档并不意味着可以从任意 URL 恢复一个 frame 的真实安全 origin。
当前桥接仍只支持获准的网络主 frame，不扩展到 about/srcdoc/blob 或不透明 origin。
本次也没有把 JS 求值结果升级为可信执行结果。

## 实际修改

1. **一份最终页面配置。** `BrowserService(policy=...)` 仅提供默认值；
   `open(policy=...)` 整体替换，不进行父子权限继承。配置来自可信宿主 Python，
   网页不能 open 页面或改变配置。服务级页面数和总缓冲预算继续有效。
2. **CEF 是唯一的 URL 解析器。** Python 只检查类型、长度和集合形状；原生同时规范化
   初始 URL、导航/资源/桥接 origin，检查桥接集合包含关系，再进行精确比较。
   删除 `BrowserPolicy.bind()` 和 Python `web_origin()`。不增加额外的预校验 RPC。
3. **profile 初始化复用。** 每个 profile 设置并回读一次打印/密码偏好，按已完成的
   requesting/top-level origin 组合去重权限设置；只清理第一次遇到的 origin 的旧
   选择数据。仍在真实页面加载前完成，失败就拒绝加载。
4. **音频是应用行为。** 去掉创建页面时强制静音，提供 `page.set_muted(bool)`。
   Chromium 自身的播放规则继续生效。打印/文件/下载和非编辑 Chrome UI 命令限制保留；
   当前 OSR 宿主没有承载完整 Chrome UI 的接口，本次没有为每个命令添加权限开关。
5. **每 App 每服务一次轮询。** WebView mount 注册事件源，unmount 解除；
   `WebView.tick()` 不再重复 poll。隐藏页仍可接收消息。
6. **通过确认事件维护缓冲寿命。** BrowserPage 只记录消费者 token 和已确认描述符；
   不读取 Widget 的 `_session` 或浏览器附加字段。WebView 负责把附加事件转为记录。
   多视图、退场控件和连续 resize 都等待最新 attach 或 detach 确认。

连续 resize 的具体缺口：旧实现只保护“最后确认的源”和“当前源”。如果 B 正在附加，
应用已 resize 到 C，B 可能被过早回收。现在任何替换待确认期间都保留相关旧缓冲；
迟到的 B 确认不能解除对 C 的等待，多视图中任一消费者未确认也不能回收。
既有总缓冲预算仍计入这些等待释放的帧。

没有新增角色、授权树、全局资源管理器、每控件策略或逐帧 Python 权限检查。
Python 配置文件由 123 行减少到 76 行；新增的原生缓存是 profile 上的完成记录。

## 行为与迁移

```python
from comfortable_tui import BrowserPolicy, BrowserService

with BrowserService(policy=BrowserPolicy()) as browser:
    # 单页完整配置；宿主显式开启诊断，不再要求服务先授予同一个开关。
    page = browser.open("https://example.com", origins=("https://example.com",),
                        policy=BrowserPolicy(allow_evaluate=True))
    page.set_muted(True)
```

- `navigation_origins` 为空由 CEF 绑定初始网络 origin；资源列表为空继承导航集合。
  桥接仍默认关闭，必须声明精确 origin/topic。`file:`、凭据、通配符、路径授权继续拒绝。
- Unicode 主机、默认端口、大小写和 Chromium 接受的 IP 拼写由同一解析器规范化，
  例如 `faß.de` 对应 `xn--fa-hia.de`，`127.1` 对应 `127.0.0.1`；授权与请求使用相同
  语义，不依赖 Python IDNA2003。非 loopback HTTP 仍需显式开启。
- URL 语义错误由原生 RPC 报 `RuntimeError`；Python 的形状/预算错误继续在分配前检查。
- 需要安全协议 **2** 的匹配 CEF helper，协议 1 或旧发布运行时会被拒绝。通用帧 SDK
  的协议没有改变。不覆盖 ComfortableTop 已安装的普通运行时，测试显式选择新 helper。
- 画布新增 `attaching(buffer)` 通知。连续替换时，释放旧源应等待匹配最新描述符的
  `buffer_attached`，或最终 `detached`；不能收到任意一条旧确认就全部释放。

## 验证记录

原生专项测试从 14 项增加到 **17 项**，Windows、WSL2 X11、WSL2 Wayland 均通过。
保留导航/资源/消息/文件/下载/CSP/旧授权等原有反向检查，新增 profile 去重、CEF
规范化和显式音频控制。规范化用真实 CEF 验证；不再用 Python 解析器测试证明原生行为。

同一 profile 的组合完成数实测为 **1 → 1 → 4 → 4**：第二页相同 origin 不重配；
添加第二个 origin 后配置缺失组合；再次创建相同配置不增加。偏好初始化次数始终为 1。
这是完成记录和真实行为检查；单次 cached-open 耗时不是可比较的启动性能基准。

| 原始报告 | Windows | X11 | Wayland |
| --- | --- | --- | --- |
| 原生专项，17 项 | [通过](validation/simplification/adversarial-windows.json) | [通过](validation/simplification/adversarial-x11.json) | [通过](validation/simplification/adversarial-wayland.json) |
| CEF 功能，14 项 | [通过](validation/simplification/feature-windows.json) | [通过](validation/simplification/feature-x11.json) | [通过](validation/simplification/feature-wayland.json) |
| ComfortableTop 混合，10 项 | [GPU](validation/simplification/top-windows.json) | [CPU](validation/simplification/top-x11.json) | [CPU](validation/simplification/top-wayland.json) |

单元测试 Windows 256 passed / 1 skipped，WSL2 240 passed / 6 skipped；数量变化包括
移除 Python URL 解释测试，改由原生专项覆盖。新增检查每次 App 推进只 poll 一次、
连续两次 resize、旧确认迟到、多视图、重复设置相同源不等待新确认，以及关闭后等待
detach。Ruff 和 MkDocs strict 通过。

集成过程中，WSL 的固定 4 秒采样断言出现过失败；未放宽断言，最终结果单独保存。
Windows 首次临时启动脚本缺少 multiprocessing main guard 导致 helper 退出，修正了
测试启动脚本；应用正常模块入口已有 guard。失败尝试不计为通过。

### 帧节拍和输出

ComfortableTop 自带 probe，Windows Terminal 120×33，GPU/CEF 混合，每次 12 秒、
排除前三秒预热；运行时没有并发构建/测试。与 `997904c` 已保存结果对照：

| 指标 | 前 60 | 后 60 | 前 120 | 后 120 |
| --- | ---: | ---: | ---: | ---: |
| 循环 FPS | 59.91 | 59.69 | 118.52 | 118.42 |
| 间隔 P95 ms | 17.122 | 17.132 | 8.785 | 8.878 |
| render P95 ms | 3.766 | 4.773 | 3.768 | 3.832 |

[修改后 60](validation/simplification/after-60.json)、
[修改后 120](validation/simplification/after-120.json)；基线在
[首轮安全验证目录](validation/security/README.md)。两次修改后运行均无超过 50ms
间隔；60 目标有 2 次、120 目标有 10 次超过 1.5 倍预算。60 目标的 CPU render 尾部
有所变差，不能据此声称所有指标都优化了。前后不是同批随机交错实验，单次结果存在
环境噪声；首轮循环 FPS 接近目标，仍不能据此排除性能回退，也没有证明屏幕呈现或
端到端延迟提升。

因为 CPU 尾部偏高，另外保留了全部补测，没有挑选最好的一次：

| 补测 | 循环 FPS | render P95 ms | 超过 50ms 间隔 |
| --- | ---: | ---: | ---: |
| [当前实现 60 再测](validation/simplification/after-60-repeat.json) | 59.76 | 6.389 | 0 |
| [旧 Python / 同 helper](validation/simplification/matched-before-60.json) | 59.78 | 4.797 | 0 |
| [新 Python / 同 helper](validation/simplification/matched-after-60.json) | 58.82 | 5.566 | 2 |

后两个使用 `997904c` 的 Python 副本与当前代码，共用同一 CEF helper、GPU provider、
Top 和依赖。旧副本仅把协议常量从 1 调到 2，以连接同一新 helper；其余 Python 实现
不变，编译扩展相同。这只能比较 Python 部分，不能当成两个完整原生版本的 A/B。
该对照中仍出现尾延时变差，当前证据不足以排除实时性能回退。

另以相同 420 帧固定 fixture 检查计算开销：
[旧 Python](validation/simplification/cpu-before.json)与
[新 Python](validation/simplification/cpu-after.json)使用线程 CPU 时间测整批，平均
3.088 → 2.790ms/帧，输出一致。Windows 线程 CPU 时钟的单帧粒度不足，因此不报告
这项时钟的单帧分位数。该 fixture 没有 CEF/GPU 画面，不能用它消除实时测试中的疑问。
本轮可确认减少重复轮询/配置、修复回收行为；不声称整体显著提速或零性能回退。

[固定输入报告](validation/simplification/output.json)：420 帧依旧 233729 字节，
SHA-256 与前轮相同，静止后 90 帧仍为零输出。此项验证行为，没有把测试脚本执行时的
CPU 数字当作可复现的性能保证。

## 保留的限制

同进程插件、原生生产者和浏览器 broker 仍可信。共享内存没有应用身份认证，eval
仍可被页面干预。单源错误影响整个合成层的问题、统一普通协议握手校验尚未在本轮
完成；没有因为整理边界就声称具备强故障隔离或原生插件沙箱。

后续只应根据具体失败补齐 native 行为，并验证正常负载的代价；不预先增加通用
授权对象。构建和专项复现命令见[WebView 文档](webview.md)和
[安全验证记录](validation/security/README.md)，Linux 分别选择 x11/wayland。
