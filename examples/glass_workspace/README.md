# Glass Workspace

自有 C++ UI 库的周边卡片示例：终端内记录笔记，周围放置专注计时、外部实时画布、
原生 tick 曲线、材质与空间布局控制。无需浏览器，不执行 shell 任务；笔记只在本次
运行的内存中保存，计时与原生性能数据是真实采样。

完整安装/API/限制见[玻璃工作区文档](../../docs/glass-workspace.md)，
设计和计划见[调研报告](../../docs/glass-research-plan.md)。本功能尚未发布。

在已构建 GPU 扩展的仓库根目录运行：

```powershell
python -m examples.glass_workspace
python -m examples.glass_workspace --layout deck --material frosted --backdrop dim
python -m examples.glass_workspace --reduced-motion --material solid
```

推荐让终端保持中等大小以查看环绕；最大化会改为屏幕内紧凑叠牌。

- F6 从终端进入卡片；Escape 返回终端，Ctrl+Q 退出。
- 卡片中 Tab/Shift+Tab 换控件，Ctrl+Tab 换卡；点击外观卡上的选项循环切换。
- 信号幅度滑块控制提供者，CPU 信号画布支持点击放置标记。
- 暂停开关暂停专注计时，保存按钮把笔记放入终端日志。
- 聚焦背景仅在终端/卡片焦点组活动时出现；点背景空白处解除。

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
```

需要桌面交互。`--preview` 另建一个明确标注的 Qt **视觉测试宿主**，用于电脑插件检查
卡片；它不是终端，普通入口不会使用它。`--smoke-seconds` 默认为0，不自动退出。
本目录中的 `__main__.py` 是 `python -m examples.glass_workspace` 入口。
