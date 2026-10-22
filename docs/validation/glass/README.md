# 玻璃工作区验证

最新扩展改造、真实终端和CPU/GPU压力结果见[最终验收](completion.md)。

## 效果性能与自动压力（2026-10-21）

方法、Exa核对的原始资料及复现命令见[性能验收](../../glass-effects-performance.md)。
[性能数据](effects-performance.json)含同机缓存前后对照及48组64帧硬件采样：
内置编译288次降为6次，第2至12设备首次效果提交中位数41.408ms降为1.274ms。
不以这个倍数声称FPS提升。基准不包含最终呈现或输入延迟。

[集成压力数据](effects-stress.json)包含CPU/GPU各1/4/12卡、每组12次替换/增删/resize，
及单独12路GPU来源8秒更新和48周期寿命压力。参数稳态无新效果纹理分配或编译，
CPU静态画面无额外效果pass，GIL持有期间原生GPU继续；每次关闭后效果纹理计数为0。
真实CEF CPU/GPU回归资源创建/销毁均为7、跨线程释放0。

新增WARP离屏稳定性CTest并接入Windows CI；本机3项CTest通过，原有像素测试增加48次
合成缩放/局部尺寸循环和无效更新不改变状态检查。全库正常及强制纯Python均为
319 passed、1 skipped；Ruff及严格文档构建通过。GitHub托管CI尚未因本地提交自动运行，
不将本机结果说成远端CI成功。

早期GPU夹具有一次首次帧就绪等待超时，当时未留下足够指标，原因未确定；
现在超时会写出完整场景指标和已完成组。随后单卡、完整矩阵及12卡48周期复跑通过，
没有复现，未将该次超时计为通过，也不声称修复了未经定位的驱动问题。

本批使用测试拥有的Qt宿主和API驱动，未使用电脑插件或输入模拟。其结果不替代外部
终端附加、真实跨显示器DPI、实际驱动重置或新的视觉审美检查。

## 原生图像效果链（2026-10-21）

实现与限制见[效果SDK](../../glass-effects-sdk.md)。本轮正常/强制纯Python均为
319 passed、1 skipped；两项CTest、Ruff和严格文档构建通过。
[像素结果](effects-pixels.json)使用D3D11 WARP和调试层，验证真实Shader像素、
链顺序、alpha、背景边缘、ABI拒绝、资源销毁和设备替换；没有D3D11 warning/error。
[真实合成结果](effects-integration.json)含CPU画布和CEF CPU/GPU WebView，
记录400ms静止期0次额外扩展绘制；同ID替换后只保留一个实例，
扩展资源创建/释放均为7，跨线程释放计数0。

电脑使用插件另行检查了持续自有窗口演示和独立滑块，
并修正演示成组紧凑布局遮挡宿主按钮的问题。此项不是外部终端附加测试，
也不能据此承诺所有背景的视觉观感、某个FPS或GPU时间开销。
生产路径没有新增像素读回，像素读回仅存在于测试可执行程序。


## 控件 SDK 与基础帧钩子（2026-10-20）

第一批实现范围见[控件SDK](../../glass-controls-sdk.md)，原始结果见
[control-sdk.json](control-sdk.json)。完整效果器/布局动画方案尚未全部实现。

- 全库正常及强制纯Python均为314 passed、1 skipped，包含独立扩展的ABI不匹配、
  禁止覆盖、批次原子性检查；CI新增独立模块编译与无需GPU的注册测试。
- 原生旋钮9项检查通过：实例独立、圆形命中、Tab/方向键、真实MSAA角色/值/动作、
  持有GIL时继续绘制、带代号的指针钩子、无需configure的增量、静止不重绘、同ID替换。
- 原有焦点8项、CPU画布17项通过，画布静止300ms记录0次UI重绘。
  首次焦点夹具意外最小化超时；重跑通过。直接启动的画布夹具完成13项后因Windows
  前台激活限制超时，不能计为通过；从自己的Windows Terminal重启后17项全部通过。
- 真实Windows Terminal运行新控件示例，两个卡片均实际由同一
  CASCADIA_HOSTING_WINDOW_CLASS宿主拥有，非WindowHost替代窗口。
- 原生构建/CTest、Ruff和严格文档构建通过。没有调用电脑插件或做本轮完整视觉验收。
  没有新增逐控件离屏纹理，但未测绘制吞吐前后对照，不能宣称GPU加速比例。

## 批量关闭性能（2026-10-20）

见 [close-performance.json](close-performance.json) 和[关闭基准说明](../../glass-close-testing.md)。
原实现逐卡停止合成器后才销毁该卡，UI 设备也逐卡独立创建/销毁。现在先批量隐藏整个
工作区、统一请求合成线程停止，再等待消费者和资源释放；同一工作区按 GPU 复用 UI
生产设备，UIA 跟踪也提前停止。没有修改帧协议、来源所有权或 close() 的同步完成语义。

- 同一本机 Qt 夹具、liquid/frosted、各3次中位数：12卡 API close 从428.8降至190.8毫秒，
  卡片全部不可见从399.8降至1.9毫秒。3卡/8卡 API close 分别为78.8/155.7毫秒。
- 真实 Windows Terminal 各测3次，3/8/12卡 close 中位数73.4/114.8/157.4毫秒；
  12卡全部不可见10.4毫秒。真实终端嵌套关闭测试还验证了单卡位置、内容更新和整体关闭。
- CPU/GPU 图像各13项、CPU/GPU 网页组合各15项关闭检查通过；消费者先解除，来源仍由
  调用方关闭。32组布局及关闭后焦点/尺寸/新增节点回归通过。
- 全库与强制纯 Python 均为300 passed、1 skipped；新增检查确认跟踪器先收到停止信号，
  消费者停止前来源仍保留，随后才发 detached，重复 close 不重复清理。
- 本轮测量观察 Win32 可见性和同步 API 完成时间，没有调用电脑插件或测量显示器出光。

## 单卡与工作区关闭（2026-10-19）

见 [close-results.json](close-results.json) 和[测试指南](../../glass-close-testing.md)。
关闭按钮此前直接调用工作区 dismiss，语义与单卡关闭不符；本次升级 Glass 控制协议4，
×/WM_CLOSE 发出可拦截的 card_close_request，由 Card 的默认行为只移除自身。

- CPU/GPU 图像各13项；CPU图像+CPU网页、GPU图像+GPU网页各15项实际窗口检查通过。
- 新增位置断言先在旧实现上失败，确认删除会压缩容器/重排根卡。修复后保留兄弟布局空位，
  不将其转成拖动 pinned；显式 reflow、尺寸变化及布局编辑仍可重新布局。
- 32组位置回归通过：4布局 × 根卡/行/列/嵌套 × 普通/最大化宿主，开启弹簧；覆盖删除
  选中卡、连续删除、内容更新、显式 reflow，以及额外的 resize/添加节点后解除保留。
- 关闭后继续切换 focus 只释放前后两个根容器的保留位置；第三个容器保持原位，避免
  延迟到下一次焦点切换才压缩旧槽位。
- 真实 Windows Terminal 中，根卡和嵌套容器各4项通过；实际 GW_OWNER 指向终端，
  关闭单卡后兄弟矩形完全一致，原生整体关闭按钮关闭卡片而保留终端/来源。
- 检查按钮移出取消、Card/Group 拦截、兄弟 HWND 全程可见且矩形不变、单卡解除消费者、WM_CLOSE、
  整体隐藏/恢复、末卡关闭停止背景/捕获、动态重建、整体关闭及幂等关闭。
- 页面单独关闭、工作区关闭时的活跃页面均验证消费者解除及调用方后续关闭页面；GPU
  网页为 native_gpu_d3d11、cpu_paints=0，并要求已有发布帧；CPU网页要求发生实际 paint。
- 全库和强制纯 Python：299 passed、1 skipped；Ruff、严格 MkDocs、原生构建及 CTest、
  源码包和原生 wheel 构建通过。测试只向本夹具 HWND 发消息；没有调用电脑插件或宣称物理点击已验证。
- 推荐 `python -m examples.glass_workspace.closing` 手动模式，直接附着当前真实终端，
  用单卡 × 与明确标注 Close entire workspace 的原生按钮分别验证；--self-test 可自动检查。
  verify_glass_close*.py 保留为独立 Qt 自动回归夹具；本轮未使用电脑插件或物理鼠标自动化。

## 跨卡键盘、通用画布与内嵌浏览器（2026-10-19）

见 [webview-results.json](webview-results.json) 和 [GlassWebView 指南](../../glass-webview.md)。
GlassCanvas 承载原有 OverlayCanvas 及其 WebView 子类，GlassWebView 仅作为便捷入口；
来源协议不变，玻璃控制扩展协议升级为3，帧协议仍为1。

- 全库及强制纯 Python：291 passed、1 skipped；Ruff、严格 MkDocs、原生构建/CTest、
  安装后的 ComfortableGlass 客户端链接通过。
- 真实 CEF 的 CPU/GPU 各13项通过：独立页面呈现、Tab 跨卡与禁用跳过、反向 Tab
  路径、实际原生键盘焦点、方向键、Enter/Escape 交互、鼠标/中文、退格/表单提交、
  受限主题消息、快速 resize 退休确认、容器隐藏、实际帧后端和已显示页面关闭后的延迟释放。
- 正向 Tab/方向键通过自有 HWND 的按键消息运行；反向路径使用与 Shift+Tab 共用
  原生函数的 previous_control 命令。未声称本轮使用电脑插件实按过 Shift/Ctrl 组合键。
- GPU 检查为 native_gpu_d3d11，cpu_paints 为0；CPU 是原有共享缓冲区/原生上传路径。
  DOM 状态通过已授权的消息桥回传，不启用 evaluate，也不放宽页面 CSP 的 unsafe-eval。
- 原有 CPU/GPU 图像路径各16项、焦点/层级8项回归通过。失焦通知虽可能在禁用后到达，
  禁用输入检查仍明确要求不向提供者发送鼠标/按键/文字事件。
- 通用 GlassCanvas 的真实 CPU/GPU 路径各17项通过，额外确认原 CanvasEvent 回调和
  物理像素 transform。换源、单宿主约束、App/卡片挂载转换、回调内换源及延迟 detached
  由单元测试覆盖。卡片仍限制每卡一个画布，不宣称支持旧 Qt 宿主所有高级输入功能。
- 空闲测试曾在启动0.6秒时统计到3次 UI 绘制而失败；已改为最多等待8秒，直到 UI 绘制
  计数和窗口几何/焦点连续350毫秒稳定，再测量300毫秒内绘制量。持续重绘会在等待阶段
  超时，断言上限仍为2；CPU/GPU 复测均记录0次。这是夹具稳定条件修正，没有禁用动画。
- 真实 Windows Terminal 运行双网页卡片示例20秒并正常关闭，两个页面均使用 GPU
  绘制，工作区正常解除消费者；没有把只创建隐藏窗口当作呈现成功。
- 真实 Windows Terminal 运行 mixed 示例20秒：原生控件、CPU 波形和两个 GPU 网页混排，
  图像消费者均有呈现，退出后工作区及捕获正常关闭。

验证时后台启动器曾被 Windows 拒绝前台请求；从普通可见终端入口启动后完成。
这是测试启动条件的差异，没有添加前台权限绕过或全局输入钩子。本轮未调用电脑插件。

```powershell
python examples/verify_glass_webview.py --render-mode cpu --report .build/glass-browser-cpu.json
python examples/verify_glass_webview.py --render-mode gpu --report .build/glass-browser-gpu.json
python -m examples.glass_workspace.webview --smoke-seconds 20 --report .build/glass-browser-terminal.json
```

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
