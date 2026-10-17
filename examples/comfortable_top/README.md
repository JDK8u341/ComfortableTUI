# Comfortable Top

独立目录中的实用混合系统监视器。真实采样、虚拟控件、动画、C++ 画布、共享 GPU
纹理与独立 CEF 页面在同一应用中协作。默认模式不需要原生编译。

## 运行

从仓库根目录：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[monitor]"
.\.venv\Scripts\python.exe -m examples.comfortable_top
```

也支持 `python examples/comfortable_top`。建议至少 120×38，140×42 更适合观察。
窄窗口隐藏进程详情侧栏，表格可左右滚动；小于 80×24 时会裁剪内容。

| 模式 | 参数 | 显示和依赖 |
| --- | --- | --- |
| 纯终端 | 默认 / `--mode terminal` | psutil；PixelCanvas 使用 Kitty 协议或 braille，不加载 Qt/CEF/原生模块 |
| 原生画布 | `--mode native` | 本目录 C++ 模块与原有显示宿主；Windows 附加层，Linux 自有窗口 |
| 完整混合 | `--mode hybrid` | 再加原有 CEF 服务，独立分析页与消息联动 |

Windows 后两种模式可加 `--canvas gpu`，将趋势图改为 D3D11 共享纹理。
CEF 仍使用现有 CPU 共享帧，不会随此参数变为 GPU 输出。
Linux 后两种模式须指定 `--platform x11` 或 `--platform wayland`，这是 WindowHost
自有窗口，非外部终端附加层。macOS 不支持。
WSL 中采样的是 Linux 子系统的进程和资源，不是 Windows 宿主的进程列表。

## 页面和操作

| 页面 | 用途和组合功能 |
| --- | --- |
| Processes | 实时虚拟 DataTable、内嵌 Sparkline、筛选排序、用户过滤、身份跟踪、后台详情弹窗；Text 混排趋势画布 |
| Resources | CPU 曲线、逐核 BarChart、内存 ProgressBar、网卡／磁盘吞吐表，以及交互式网页分析 |
| Compare | 四个固定进程；Virtual List 内嵌画布、DataTable 内嵌独立 WebView；可展开的真实进程树 |
| Workspace | Unicode TextArea 笔记、Log、DirectoryTree、SelectionList 导出字段、后台 JSON 导出、PageStack 预览 |
| Settings | Slider 采样频率、RadioGroup 排序方向、Collapsible 指标说明、ScrollView 和帮助弹窗 |

顶栏还使用 Input、Select、Switch、Checkbox、Spinner、Rule；Tabs、焦点和进度条
沿用库的动画。只读取进程，不提供结束系统进程等操作。

| 按键 | 行为 |
| --- | --- |
| F1 / F2 | 帮助 / 搜索；匹配 PID、名称、用户和状态，空格分隔的词须全部匹配 |
| F3 / F4 | 循环 CPU／内存／PID／名称排序；固定／取消固定选中进程 |
| F7 / F8 | 暂停采样／恢复；后台读取当前进程详情 |
| Ctrl+1…5 | 切换主要页面 |
| Ctrl+E | 导出当前快照和笔记到界面指定目录，生成新 JSON 文件 |
| F6 / Tab / Shift+Tab | 菜单与内容／原有焦点链 |
| Enter / Esc | 进程行打开详情；Compare 表格进入当前行网页／退回表格；关闭弹窗或返回预览 |
| Ctrl+Q / Ctrl+C | 退出并释放消费者、生产者、服务和共享缓冲区 |

进程身份为 **PID + 创建时间**。刷新排序继续跟踪身份；固定项退出后显示 exited，
不会把重用 PID 的新进程当成原进程。在 Compare 列表／表格上按 F4 可移除已退出项。
普通进程表中原选择退出／被过滤时会选择邻近行。暂停冻结整个采样快照，不暂停系统
进程，丢弃一份已经在后台执行的采样。

每个固定项的网页单独调用 `BrowserService.open()`，DOM、滚动位置、图表范围独立。
这些页属于本应用私有的临时 `local-analytics` profile。联动使用明确授权的
`monitor.sample/select` 主题；`monitor.probe/proof` 用于消息桥验证。
网页保留 CSP，验证不使用动态 eval。最多五页，取消固定后关闭对应页面。
非混合模式下，同一 PID 的两个小图可镜像同一图像源；不同固定项的源独立。

## 构建本例 C++ 提供者

先完成库的可选显示宿主构建，参见 [WebView 指南](../../docs/webview.md) 和
[GPU SDK](../../docs/native-gpu.md)。本例模块独立编译，复用通用 SDK。

Windows x64、VS 2022 C++、CMake、匹配架构的 Python：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[monitor,dev,overlay]"
$topPython = (Resolve-Path .\.venv\Scripts\python.exe).Path
$topPybind = & $topPython -m pybind11 --cmakedir
cmake -S examples/comfortable_top/native -B .research/top-native -A x64 "-DPython_EXECUTABLE=$topPython" "-Dpybind11_DIR=$topPybind"
cmake --build .research/top-native --config Release --target _ctui_top_native
.\.venv\Scripts\python.exe -m examples.comfortable_top --mode native
.\.venv\Scripts\python.exe -m examples.comfortable_top --mode hybrid
.\.venv\Scripts\python.exe -m examples.comfortable_top --mode hybrid --canvas gpu
```

hybrid 另需 `ctui_browser` 完整构建目录。GPU 呈现需要原有
`COMFORTABLE_TUI_BUILD_GPU=1` 构建的库扩展。基础模式无需这些步骤。

Linux / WSL2，在已有 GCC 11+、Qt 6、CEF 与 Python venv 环境中：

```bash
.venv-linux/bin/python -m pip install -e '.[monitor,dev]'
cmake -S examples/comfortable_top/native -B build/top-native -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DPython_EXECUTABLE="$PWD/.venv-linux/bin/python" \
  -Dpybind11_DIR="$(.venv-linux/bin/python -m pybind11 --cmakedir)"
cmake --build build/top-native --target _ctui_top_native -j 4
export COMFORTABLE_TUI_BROWSER_EXECUTABLE="$PWD/build/linux/native/browser/ctui_browser"
export COMFORTABLE_TUI_LINUX_HOST_EXECUTABLE="$PWD/build/linux/native/host/ctui_linux_canvas"
.venv-linux/bin/python -m examples.comfortable_top --mode hybrid --platform x11 --native-module build/top-native
.venv-linux/bin/python -m examples.comfortable_top --mode hybrid --platform wayland --native-module build/top-native
```

WSL 建议把 venv／构建输出放在 Linux 文件系统，替换命令中的实际路径。
`--native-module` 指向包含 `_ctui_top_native.*.so/.pyd` 的目录；默认查找仓库
`.research/top-native` 及其 `Release` 子目录。

## 参数和架构

`--interval .25..5` 控制采样秒数，默认 1；`--fps 1..240` 控制 UI 目标，默认 60。
`--output DIR` 预填导出目录；`--snapshot` 真实采样两次后输出终端帧。
`--smoke-seconds N` 自动退出，`--report PATH` 保存验证数量和检查项。
`--self-test` 自动验证交互，需至少 14 秒；不用它即可正常交互使用。

```text
后台采样 → 不可变 Sample → 有界 Model → 终端控件
                                  ├→ 低频数值 → C++图表线程 → CPU共享帧 / Win GPU纹理
                                  └→ 业务消息 → 独立CEF页面 → 现有图像显示层
```

基础模式在 Python 绘制小尺寸 PixelCanvas。原生模式中 Python **不生成、复制或逐帧
传递像素**，仅分配缓冲区、传递描述符、每次采样提交最多 180 个数值及处理标准布局／
缩放／生命周期事件。C++ 插值并发布帧，静止后停止重复发布。最多六个图表生产者，
固定项复用四个槽，不按系统进程数创建线程或网页。帧 SDK 不包含 URL/profile。
图表是只读显示，鼠标穿透给容器；网页通过原有焦点规则交互。

进程 CPU = 相邻 user+system 时间差 / 实际单调时钟间隔，100% 为一个核心；可超过
100%。系统 CPU 为各核平均。曲线固定 0..100% 并裁剪，表格保留精确值；横轴为有效
采样点，缺失值不画成零。Sparkline、逐核条形图相对缩放，旁边数值为实际百分比。
第一次 CPU/I/O、新进程、重置和不可读字段为 warmup/n/a，不伪造零值。
RSS 为驻留内存，非独占内存；系统内存显示 total-available。网络包含回环；磁盘
总量是 OS 设备计数之和，分层设备可能重复，应结合逐设备表解读。

Linux 通过 psutil.process_iter(attrs=...) 采集。Windows 一次 Toolhelp 获取关系／
线程数，一次 NtQuerySystemInformation 读取公开 RSS 字段，再用 GetProcessTimes
取得可访问进程的身份和 CPU；reserved 字段保持不透明。取不到创建时间的受保护进程
计入 unavailable，不显示为稳定身份行；状态 alive 仅表示存在，交换区为 n/a。
用户名按身份缓存。一次采集未完成时不提交下一份，实际频率受系统读取开销约束。

每个当前进程最多保留 180 个历史点；退出后只保留四个固定项的历史。笔记不发送给
网页，只有显式导出才写文件，生成新名称并以排他方式创建，不覆盖现有文件。

## 验证与文件

```powershell
python -m pytest -q tests/test_comfortable_top.py
python -m examples.comfortable_top --snapshot --interval .25
python -m examples.comfortable_top.verify_native --canvas cpu
python -m examples.comfortable_top.verify_native --canvas gpu
python -m examples.comfortable_top.verify_windows --mode hybrid --canvas cpu --report .research/top-own-check.json
```

Windows 验证创建专用 Terminal，自动操作本示例并退出；报告须为新路径。
Linux 分别运行 `--mode hybrid --platform x11/wayland --native-module MODULE_DIR
--self-test --smoke-seconds 14 --report PATH`。验证不操作其他应用。
来源与实测记录见 [完整报告](../../docs/comfortable-top.md)。

| 文件 | 职责 |
| --- | --- |
| main.py | 模式、命令行、资源关闭顺序 |
| collector.py / windows.py | 后台采集、平台优化和差值 |
| model.py | 身份、筛选排序、有界历史、显式导出 |
| ui.py | 页面、控件与交互 |
| rendering.py / native/provider.cpp | 握手和 C++ CPU/GPU 图表线程 |
| web.py / web/ | 应用 profile、本机服务、独立网页与消息主题 |
| verification.py / verify_native.py / verify_windows.py | 自动验证入口 |
