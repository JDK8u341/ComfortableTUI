# Glass 效果器实现接续记录

更新时间：2026-10-20
分支：`codex/liquid-glass-cards`

## 当前状态

第二批设计改造已完成第一个可恢复阶段。效果器 API、实例快照、原生注册入口、构建接入和参数增量通道已经接入并验证；视觉 GPU pass 尚未实现，因此当前不能把 blur/liquid/glow 当成已经可用的视觉效果宣传。此次没有推送、合并或发布。

本次改动文件：

- `native/glass/include/comfortable/glass_effect.hpp`：效果类型、参数 schema、D3D11 绘制上下文和原生扩展描述。
- `native/glass/effect.cpp`：线程安全的类型注册表草稿，列出 `tint`、`blur`、`liquid`、`glow` 四个内置类型。目前内置回调是 no-op，占位而非真实渲染。
- `native/glass/include/comfortable/glass.hpp`：给 Card/Control 加入效果绑定列表，给 metrics 增加效果绘制计数，并声明参数更新接口；该接口尚无实现。
- `src/comfortable_tui/glass.py`、`src/comfortable_tui/__init__.py`：新增 `GlassEffect`、Card/Control 效果快照、注册入口和参数更新 API。
- `native/glass/bindings.hpp`、`native/glass/glass.cpp`：解析/校验效果绑定，暴露注册与参数更新通道。
- `CMakeLists.txt`、`setup.py`：将 `native/glass/effect.cpp` 纳入 Windows 原生构建。
- `tests/test_glass.py`、`docs/glass-controls-sdk.md`：增加实例隔离回归测试和 SDK 说明。

此前已有最新提交为 `299054a docs(glass): add control SDK guide to navigation`。本接续记录与当前未完成代码将作为新的本地检查点提交。

## 设计目标与约束

- 效果器定义（类型、参数 schema、设备级 shader/资源）与效果器实例（每个 Card/Control 的参数、生命周期和输出）分离。
- 每个卡片可独立挂载效果器；实例的参数、状态及输出纹理必须独立。shader/program 与可安全共享的设备资源可复用。
- 一个 Card 可以包含 Canvas 或 WebView 等现有控件；这里是 Glass/Card 上层能力，不应改变 Canvas/WebView 的 FrameProvider 协议，也不要重复消费帧。
- 局部效果应先绘制到独立的输入/输出纹理，不能同一纹理同时读写，避免 D3D11 自采样反馈；采样半径需要扩展脏矩形/输出区域。
- Python 层只负责构造、校验和提交效果描述；渲染、GPU 资源、效果 pass 与生命周期放在 C++。
- 先做 Windows D3D11 原生路径，不在本批次重新引入 Linux、WebView 或新的渲染框架。
- 原生扩展注册 ABI 必须验证版本、结构大小和构建工具链兼容性。扩展注册后所引用的名称、参数 schema 与回调所需资源必须有明确且稳定的生命周期；当前草稿还没有把这一生命周期契约落实到 SDK 文档。

## 已有调研依据

Microsoft 文档已用于前序设计核对：

- Direct2D custom effects: https://learn.microsoft.com/en-us/windows/win32/direct2d/custom-effects
- D3D11 shader resource binding: https://learn.microsoft.com/en-us/windows/win32/api/d3d11/nf-d3d11-id3d11devicecontext-pssetshaderresources
- DXGI ResizeBuffers: https://learn.microsoft.com/en-us/windows/win32/api/dxgi/nf-dxgi-idxgiswapchain-resizebuffers
- Direct2D effect invalidation: https://learn.microsoft.com/en-us/windows/win32/api/d2d1_1/nf-d2d1_1-id2d1devicecontext-geteffectinvalidrectangles
- Device-lost recovery: https://learn.microsoft.com/en-us/windows/uwp/gaming/handling-device-lost-scenarios
- D3D11 context threading: https://learn.microsoft.com/en-us/windows/win32/direct3d11/overviews-direct3d-11-render-multi-thread-intro
- Composition effect definition/instance model: https://learn.microsoft.com/en-us/uwp/api/windows.ui.composition.compositioneffectbrush

## 接续步骤

1. 重新阅读 `docs/glass-extensibility-plan.md`、`docs/glass-controls-sdk.md`、`native/glass/include/comfortable/glass_effect.hpp` 和 `native/glass/effect.cpp`，核对此前约定及 API 草稿。
2. 先审查 C++ ABI：内置效果不应以成功的 no-op 暴露为已支持功能；明确扩展描述内指针/回调的生命周期、注册线程和设备重建规则，并在注册时校验效果名及参数 schema。
3. 已完成构建接入、pybind11 绑定、Python 效果描述/验证，以及 Card/Control 快照更新。不要在 Python 重复维护一份可漂移的效果定义。
4. 接入 C++ D3D11 compositor：以 card/control 局部纹理为输入，串联效果 pass；每个实例拥有独立参数缓冲、必要的中间纹理和缓存失效状态，设备丢失/resize 后可重建。
5. 按效果逐个实现并记录真实能力；优先一个可测的 `tint` pass，再实现 blur/liquid/glow。效果注册与可运行效果要区分。
6. 加一个示例展示两个 Card 独立设置参数及自定义效果注册；覆盖 Canvas/WebView 子控件不被二次取帧的情形。
7. 补 SDK 文档和必要测试；验证原生构建、Python API、效果参数校验、多个实例隔离、窗口 resize/device recreation、无效果的基准性能和 idle draw。
8. 汇总验证结果后再决定是否提交后续实现。当前阶段已通过 34 个 Glass 纯 Python 测试，并用当前 Windows MSVC/pybind11 成功构建原生扩展。当前分支尚未合并到 `main`；除非用户明确要求，不要推送、合并或发布。

## 恢复工作

在仓库根目录运行：

```powershell
git status --short --branch
git log -5 --oneline
```

从 `codex/liquid-glass-cards` 分支继续即可；本记录说明当前代码边界和下一步，不要把 no-op 占位效果当成完成实现。
