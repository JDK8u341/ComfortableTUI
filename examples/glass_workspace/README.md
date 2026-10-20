# Glass Workspace

自有 C++ UI 库的周边卡片示例：终端内记录笔记，周围放置专注计时、外部实时画布、
原生 tick 曲线、材质与空间布局控制。无需浏览器，不执行 shell 任务；笔记只在本次
运行的内存中保存，计时与原生性能数据是真实采样。

完整安装/API/限制见[玻璃工作区文档](../../docs/glass-workspace.md)，
设计和计划见[光学与事件调研](../../docs/glass-optics-research.md)。本功能尚未发布。

先按完整文档设置 `COMFORTABLE_TUI_BUILD_GLASS=1` 构建独立玻璃扩展，再在仓库根目录运行：

```powershell
python -m examples.glass_workspace
python -m examples.glass_workspace --layout deck --material frosted --backdrop dim
python -m examples.glass_workspace --reduced-motion --material solid
python -m examples.glass_workspace --groups --material liquid
```

推荐让终端保持中等大小以查看环绕；最大化会改为屏幕内紧凑叠牌。
环绕/叠牌点击只改变前后层级，其他卡片保持位置和相对顺序。`--groups` 时03与04
同属 tools 容器，整组可置前，但不会跟着无关选择换位。focus 只交换旧/新焦点，
ribbon 是显式选择的整体轮播转场。

- F6 从终端进入卡片；Escape 返回终端，Ctrl+Q 退出。
- Tab/Shift+Tab 遍历标题及控件，越界换卡；Ctrl+Tab/Shift+Ctrl+Tab 直接换卡并转移
  键盘焦点。标题上的方向键换卡；编辑控件保留自己的按键。点击外观卡上的选项循环切换。
- 信号幅度滑块控制提供者，CPU 信号画布支持点击放置标记。
- 暂停开关暂停专注计时，保存按钮把笔记放入终端日志。
- 聚焦背景仅在终端/卡片焦点组活动时出现；点背景空白处解除。
- 标题区事件交给 Card，未消费才执行默认拖动；健康卡默认不可拖，其余卡可独立切换。
- 外观卡先选目标卡，再选该卡材质与拖动开关，互不影响。恢复位置按钮解除手动位置。
- `--groups` 演示 CardGroup 内部排列；组支持 preview/bubble、继承隐藏与禁用。
- liquid 为通透边缘折射，frosted 为 GPU 高斯模糊；dim 只压暗，Mica 是壁纸材质。

共享 GPU 版本：

```powershell
cmake -S . -B .build/glass-native -A x64
cmake --build .build/glass-native --config Release --target ctui_glass_signal
python -m examples.glass_workspace --gpu-provider .build/glass-native/Release/ctui_glass_signal.exe
```

`provider.cpp` 用 C++/D3D11 绘制共享纹理；Python 仅握手和发送幅度值。
GPU 提供者没有实现点击标记回传，事件仍会送给 Python，不能把 CPU 示例的标记效果
误认为所有提供者自动支持。

自动退出与报告：

```powershell
python -m examples.glass_workspace --smoke-seconds 15 --report .build/glass-run.json
python examples/verify_glass_windows.py --report .build/glass-cpu.json
python examples/verify_glass_windows.py --gpu-provider .build/glass-native/Release/ctui_glass_signal.exe --report .build/glass-gpu.json
python examples/verify_glass_optics.py --report .build/glass-optics.json --hold 30
python examples/verify_glass_focus.py --report .build/glass-focus.json
python examples/verify_glass_webview.py --render-mode gpu --report .build/glass-browser.json
python -m examples.glass_workspace.closing
python -m examples.glass_workspace.closing --grouped --self-test --report .build/glass-terminal-close.json
python examples/verify_glass_close.py --with-browser --report .build/glass-close.json
python examples/verify_glass_close_layout.py --report .build/glass-close-layout.json
```

需要桌面交互。`--preview` 另建一个明确标注的 Qt **视觉测试宿主**，用于电脑插件检查
卡片，并提供彩色网格背景以观察透色/模糊/边缘；它不是终端，普通入口不会使用它。
`--smoke-seconds` 默认为0，不自动退出。
预览默认也使用正式 owned-window 模式，卡片不单独占任务栏。
仅电脑插件需要分别选中卡片时加 `--inspectable-windows`；该检查模式会把卡片拆成
独立任务栏窗口，不用于验收正式模式的任务栏联动。
本目录中的 `__main__.py` 是 `python -m examples.glass_workspace` 入口。

closing 入口附着当前真实终端，可分别点击单卡 ×、Close entire workspace 和终端内的
Recreate cards；不会创建 Qt 替代宿主。verify_glass_close*.py 是独立自动化夹具。
单卡删除保留其他卡的空位；显式 reflow 或布局/尺寸更改可以重新排布。
Glass 控制协议4起，×仅请求关闭当前卡，可由事件处理器拦截。整体隐藏使用 Hide cards/
dismiss，整体退出使用 Quit/Ctrl+Q；独立测试中的 Close entire workspace 按钮验证
workspace.close()。测试命令与资源寿命见[关闭测试](../../docs/glass-close-testing.md)。

关闭先批量隐藏卡片，再同步释放消费者；closing 示例会显示关闭毫秒数。
用 `python examples/benchmark_glass_close.py` 在真实终端测试3/8/12卡的关闭耗时，
默认各测3次；只有显式 --fixture 才使用独立 Qt 基准宿主。

## 内嵌网页

准备好已有 CEF 浏览器运行时后，可运行独立的双网页卡片示例：

```powershell
python -m examples.glass_workspace.webview
python -m examples.glass_workspace.webview --render-mode cpu
python -m examples.glass_workspace.webview --preview
```

可通过 `--browser PATH` 指定运行时，否则沿用 BrowserService 的发现规则。每张卡拥有
独立 BrowserPage；在 Notes 中输入、点击 Share note，Inbox 通过受限消息桥显示内容，
其输入框不会同步。Enter/点击进入网页，Escape 离开；Tab 始终用于卡片导航。
原生控件、液态玻璃材质并不使用 WebView 实现。
API、缩放、生命周期和输入边界见[卡片 WebView 文档](../../docs/glass-webview.md)。

## 普通画布与网页混排

```powershell
python -m examples.glass_workspace.mixed
python -m examples.glass_workspace.mixed --render-mode cpu
```

在双网页示例基础上增加一个普通 OverlayCanvas 图像卡，点击波形可暂停/恢复；
这不是网页。也可通过 `--gpu-source CHANNEL_NAME` 接入正在运行的标准 GPU 提供者。
`GlassCanvas` 接受现有 OverlayCanvas（包括 WebView）或直接接受 CPU/GPU 来源。
接口和事件约定见[通用画布文档](../../docs/glass-canvas.md)。
