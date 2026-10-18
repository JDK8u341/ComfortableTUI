# 玻璃工作区验证

## 焦点联动修复回归（2026-10-19）

见 [focus-results.json](focus-results.json)。旧实现的选中索引轮转会改变所有叠牌槽位，
且窗口插入顺序反向，选中卡可能位于其他卡后方。新增自有原生窗口回归8项通过：
自动紧凑叠牌、显式 deck、容器置前、仅旧/新焦点换位、显式 ribbon、还原后的布局、
启用弹簧时其他卡不动，以及关闭释放。检查实际 HWND 矩形和前后顺序，不只检查模型。
CPU/GPU 原有路径各16项、全库280 passed / 1 skipped、Ruff、MkDocs 和原生 CTest
也再次通过；本轮未调用电脑操作插件。

```powershell
python examples/verify_glass_focus.py --report .build/glass-focus.json
```

## 当前回归（2026-10-19，光学与 CardGroup）

第二轮摘要见 [optics-results.json](optics-results.json)。下方的 [results.json](results.json)
为首轮 Acrylic 方案的历史记录，“未实现折射”等旧说明已被新方案取代。

- 完整测试与强制纯 Python 模式均为280 passed、1 skipped；Ruff、严格 MkDocs 通过。
- `_native_glass` / `_native_gpu` 分别为玻璃协议2、帧协议1；底层模块不再导出 GlassHost。
  CMake 安装、外部 ComfortableGlass 客户端链接、CTest、源码包/原生 wheel 构建通过。
- CPU FrameBuffer 和共享 D3D11 路径各通过16项真实桌面检查，覆盖原有输入、旧场景、
  来源替换、四种布局、GIL 阻塞时运动、移动/最大化、焦点、隐藏/恢复和销毁。
  还确认正式卡片的 GW_OWNER 为宿主，最小化/恢复及焦点组行为正常。
- 新增14项光学/CardGroup 检查通过：每卡材质、容器布局、无持续光学自反馈、拖动
  策略、Group preview/Card 消费、自由位置/reflow、子树隐藏/禁用、拖动中取消、独立
  材质修改、真实 frosted 与 dim 区分。具体名称见第二轮 JSON。
- 电脑插件观察了自有彩色网格后的透色/边缘及独立模糊，实际拖动卡片，聚焦输入框，
  输入“中文回归 Glass”，MSAA 值与画面一致。未使用电脑插件操作终端。
- 真实 Windows Terminal 的20秒运行使用 CardGroup 和共享 GPU 来源，退出前记录156次
  UI 绘制、1436次合成呈现、1095个外部 GPU 帧；正常退出，采样会话已关闭，
  不只检查隐藏窗口创建。

光学700毫秒样本中 UI 和光学重绘均为0，同时有25次桌面镜像更新、17次无变化检测。
实际变化包围盒使远处背景变化不再带动无关卡片重绘；GPU 只异步回读16字节边界，
不回读背景像素。这是短样本，不是屏幕 FPS、GPU 耗时或显示到光子的延迟保证。
背景比较增加约一个原生 tick 的更新延迟。

任务栏说明：`inspectable_windows=True` 是电脑检查用的独立窗口模式，卡片可能单独
占任务栏，不用于验收正式窗口组。`--preview` 现默认使用正式 owned-window，仅显式
加 `--inspectable-windows` 才拆分。归属、最小化/恢复及焦点由自有桌面夹具验证，未声称
自动点击过系统任务栏的所有恢复路径。

新增检查命令（先按指南设置 `COMFORTABLE_TUI_BUILD_GLASS=1` 构建）：

```powershell
python examples/verify_glass_optics.py --manual-start --focus-timeout 180 --report .build/glass-optics.json
python -m examples.glass_workspace --groups --gpu-provider .build/glass-native/Release/ctui_glass_signal.exe --smoke-seconds 20 --report .build/glass-terminal.json
```

光学检查先激活测试自己的彩色背景，再激活 Routing verification owner，点击 Start
checks。卡片后方应无其他动画，采样中不操作鼠标；其他区域变化可以继续。
Windows 拒绝前台请求时，FOCUS 日志表示等待激活自有夹具。`--hold 30` 可保留成功
场景供目视检查。测试按钮避免桌面准备与采样竞争，不绕过 Windows 前台限制。

桌面采样在 GPU 内使用，不生成背景截图文件，不向 Python 或网络传像素。验证摘要
不保存桌面截图、临时 HWND 或用户路径。旋转显示器不启用采样；Windows 10、HDR、
跨显卡、其他终端、远程会话、多显示器跨 DPI 和屏幕阅读器仍未完成真实兼容性验证。

## 首轮历史基线（Acrylic 方案）

2026-10-18，本机 Windows 11、125% DPI、Windows Terminal、Python 3.12、MSVC 2022。
完整结果摘要见 [results.json](results.json)。这是桌面原生呈现与窗口行为验证，不是
离屏模拟。研发功能尚未发布；Windows 10、其他终端、屏幕阅读器和多显示器跨 DPI
迁移未完成真实兼容性验证。

## 自动化与桌面检查

- 全库：277 passed、1 skipped；Ruff 通过。跳过项为原有可选环境检查。
- 可选 pybind11 GPU 扩展、CMake SDK、原生 UI 静态库与信号提供者构建成功。
- CTest 的原生帧协议检查通过。GUI 检查需要交互桌面，未伪装成 CI 离屏验证。
- CPU FrameBuffer 与 D3D11 共享 GPU 两条路径分别通过16项检查，见 JSON 的 checks。
- 电脑插件实际操作了原生选择控件、布局、材质、聚焦背景、中文输入与笔记保存，
  验证 Ctrl+Q 退出。为避免插件把 owned popup 归一到宿主坐标，点击检查采用明确
  标注的独立卡片测试模式；默认模式仍使用终端 owned popup。
- 真实 Windows Terminal 使用普通 `App + workspace.bind(app)` 入口，实际出现控件绘制
  和 GPU 帧消费，正常自动退出。不能用仅创建窗口但全程隐藏的运行证明显示成功。
- 视觉检查发现并修复自绘大圆角外露出未着色 Acrylic 的边缘，改为与系统圆角一致。

16项检查覆盖：实际 UI/外部帧呈现、停稳无持续重绘、按下与松开间更新场景仍只产生
一次点击、控件值回传、旧场景不覆盖新编辑、画布源坐标、GIL 阻塞期间原生动画、
四种可打断布局、降低动态/透明度、来源替换确认且保留 HWND、移动跟随、最大化
紧凑布局、焦点背景与 Escape 返回、隐藏/召回、最小化/恢复、销毁清理。
画布输入检查同时验证禁用状态不会继续向提供者发送鼠标和按键松开事件。

测试还发现并修复同进程宿主关闭时的消息等待死锁，以及宿主销毁与 DPI 重配竞态。
关闭仅泵送同步 sent messages，不重新执行已投递的 Python 业务事件。

## 可重现命令

先按[玻璃工作区指南](../../glass-workspace.md)构建原生模块和信号提供者：

```powershell
python -m pytest -q
python -m ruff check .
python -m mkdocs build --strict
cmake --build .build/glass-native --config Release
ctest --test-dir .build/glass-native -C Release --output-on-failure
python examples/verify_glass_windows.py --focus-timeout 90 --report .build/glass-cpu.json
python examples/verify_glass_windows.py --focus-timeout 90 --gpu-provider .build/glass-native/Release/ctui_glass_signal.exe --report .build/glass-gpu.json
python -m examples.glass_workspace --gpu-provider .build/glass-native/Release/ctui_glass_signal.exe --smoke-seconds 20 --report .build/glass-terminal.json
```

Windows 可以拒绝后台进程的前台请求；验证器在日志出现 FOCUS 后留出等待时间。
本次由电脑插件激活**验证器自己的图形夹具**，没有让库绕过系统前台权限。
实际使用应从正常可见的交互终端启动。无人值守启动器若继承隐藏/最小化状态，
请使用普通显示状态启动新进程；库不会把隐藏标签页强行显示。

## 性能证据的范围

两个来源的静态控件在300毫秒停稳采样内无 UI 重绘。Python 主线程连续持有 GIL
450毫秒时，原生卡片位置仍变化608物理像素。CPU/GPU 采样都报告移动平移误差0像素。
这些是本次短样本，不是最坏情况保证；位置测量不等于显示到光子的延迟。

真实终端20秒运行的退出前采样记录了152次 UI 绘制、1252次合成呈现、1099个外部
GPU 帧；示例每500毫秒主动更新计时及性能曲线。它验证共享纹理在真实终端宿主上持续
消费，不是受控帧率基准。tick_ms 只表示最近一次原生控制循环 CPU 时间，不能当成
屏幕 FPS、GPU耗时或输入延迟。

系统 Acrylic/Mica 的视觉取决于 Windows 设置、主题、远程会话及硬件；成功 HRESULT
不代表所有机器上都有同样效果。没有实现桌面截图后模糊或任意桌面的真实折射。
验证摘要移除了临时 HWND、用户路径和背景截图，只保存与本功能有关的结果。
