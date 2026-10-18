# 玻璃工作区验证

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
