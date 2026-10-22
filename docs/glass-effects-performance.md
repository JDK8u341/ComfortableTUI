# 效果器性能与自动化验收

更新：2026-10-21。Windows / D3D11；Glass 6、Effect ABI 2 不变。

## 调研与测试方法

本轮通过 Exa 核对 Microsoft 和 Qt 原始文档，采用三层验证：

| 测试 | 覆盖 | 执行方式 |
| --- | --- | --- |
| 像素及寿命 | 输出像素、alpha、实例隔离、设备替换、异常、释放 | WARP + 可选D3D11调试层，仅测试程序读回像素 |
| 硬件基准 | CPU提交、GPU执行、纹理和编译计数 | 原生离屏effect chain，不依赖窗口或输入 |
| 集成压力 | 多卡片、CPU/GPU画布、参数动画、增删、resize | 自有QWidget宿主 + 真实HWND/DComp，通过API驱动 |

基准使用8槽异步timestamp/disjoint查询，单次有界轮询，`DONOTFLUSH`不隐式提交。
环满时让出CPU，超过60秒失败；disjoint、零频率、时间反序的样本丢弃，没有有效样本
则失败，不能伪报0ms。依据：[查询类型](https://learn.microsoft.com/en-us/windows/win32/api/d3d11/ne-d3d11-d3d11_query)、
[DONOTFLUSH限制](https://learn.microsoft.com/en-us/windows/win32/api/d3d11/ne-d3d11-d3d11_async_getdata_flag)、
[timestamp可靠性](https://learn.microsoft.com/en-us/windows/win32/api/d3d11/ns-d3d11-d3d11_query_data_timestamp_disjoint)。

离屏测试没有Present，因此在计时区外显式Flush提交批次，环满时暂停提交并记录
`throttle_polls`。生产渲染路径没有增加查询、Flush或同步等待。GPU样本是一批所有
节点的效果命令时间；CPU样本只计draw提交。两者排除冷启动、查询及节流，
不包含最终屏幕呈现、输入延迟或Python业务逻辑。

窗口测试按有界条件等待attach/detach和场景确认；固定时间只用于采样区间和
500ms原生汇总指标的稳定期，超时输出现场指标。
依据：[Qt测试最佳实践](https://doc.qt.io/qt-6/qttest-best-practices.html)、
[QTest等待接口](https://doc.qt.io/qt-6/qtest.html)。Qt只是测试夹具，产品未新增Qt依赖。
需要桌面的DComp测试不以 `QT_QPA_PLATFORM=offscreen` 的结果冒充；
CI新增的是不需要桌面的WARP稳定性检查。

## 发现与优化

旧实现每次设备缓存失效都重新D3DCompile，同进程不同Card的设备反复编译同一组
内置Shader，而且编译持有设备缓存锁。12个冷设备加48组8帧短测，实际编译288次。

现在将6份不可变字节码保留至SDK模块寿命结束，编译移出设备缓存锁。
每个设备仍创建自己的Shader对象/常量缓冲，每个节点的参数和输入输出纹理独立。
自定义扩展Shader不受此缓存接管。根据
[CreatePixelShader字节码输入契约](https://learn.microsoft.com/en-us/windows/win32/api/d3d11/nf-d3d11-id3d11device-createpixelshader)
作此实现选择，不跨设备共享ID3D11PixelShader。

同时把参数默认值补齐和类型查找移到创建/更新边界。绘制直接读取已验证的参数向量及
不可变类型，不再每个pass重新获取全局注册锁、验证schema及分配临时参数向量。
无效更新仍先拒绝，不改变已有绘制状态。

| 同机相同短测 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 内置Shader编译 | 288次 | 6次 |
| 第2至12个设备首次效果提交，中位数 | 41.408ms | 1.274ms |

第一个设备仍需真实编译；没有将成本藏到其他启动阶段。此对照不等于FPS提升倍数，
也不是所有硬件保证。字节码保留占少量CPU内存，不保留已销毁设备的纹理。

## 本机硬件结果

64帧完整基准：1/4/12节点 × 100%/125%/150%/200%合成尺寸 × 4种配置，共48组。
下表为12节点，每节点逻辑尺寸256×144；本机硬件D3D11设备。

| 配置 | 100% GPU批次p95 | 200% GPU批次p95 | 200%局部效果纹理 |
| --- | ---: | ---: | ---: |
| CopyResource基线 | 0.018ms | 0.024ms | 0 |
| tint | 0.077ms | 0.095ms | 27.0MiB |
| blur + glow | 1.662ms | 3.687ms | 82.7MiB |
| background blur + liquid | 0.831ms | 1.716ms | 72.3MiB |

CopyResource是原始复制成本对照，**不是完整无效果UI耗时**。表中不包含Canvas传输、
桌面捕获、D2D墨水及最终DComp合成；其他扩展Shader另测。不隔离其他桌面GPU负载，
不设置通用毫秒通过阈值。完整JSON保留CPU/GPU的p50/p95、有效/丢弃样本、pass、
局部纹理计数和驱动内存快照。

`surface_bytes` 只计算框架持有的局部效果纹理，不含SwapChain、帧源、桌面捕获、
驱动缓存和扩展自行创建的纹理。DXGI `CurrentUsage` 是**进程在适配器上的用量**，
不能逐卡相加；释放后驱动可延迟回收，不断言驱动用量立即归零。
依据：[QueryVideoMemoryInfo](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_4/nf-dxgi1_4-idxgiadapter3-queryvideomemoryinfo)。

## 验收项目

- 稳态绘制无新纹理分配/Shader编译，参数更新不提交完整场景。
- CPU静态画面停稳后无额外效果pass；GPU来源持续发布时继续消费。
- Python持有GIL、不泵送Qt时原生GPU继续。先丢弃一次延迟汇总，再取两次快照，
  避免把持有GIL之前的旧帧作为证据。
- CPU/GPU各测1/4/12卡，每组12次同ID替换、移除重加及宿主移动/resize。
- 每次关闭后效果纹理计数归零，源仍属于调用方。12卡本次局部效果纹理约15.7MiB。
- WARP额外48次不同缩放的局部区域/尺寸循环；设备替换不重编字节码。
- 原有像素/ABI/异常、全库正常/纯Python、Ruff、严格文档及真实CEF CPU/GPU回归。

原生指标每500ms汇总，测试还有稳定期；报告中presents/frames是进展证据，
**不可直接除以 `measured_seconds` 得到精确FPS**。`parent_cpu_seconds` 包含父进程
原生线程，但不含独立GPU提供者进程。GIL快照也不是精确帧率计。

## 复现

先按[效果SDK](glass-effects-sdk.md)构建原生模块和扩展，采用仓库 `.venv` 的Python：

```powershell
cmake -S . -B .build/glass-native -A x64
cmake --build .build/glass-native --config Release
ctest --test-dir .build/glass-native -C Release --output-on-failure
.build/glass-native/Release/ctui_glass_effects_benchmark.exe --frames 64 > .build/glass-effects-benchmark.json
python -m examples.benchmark_glass_workspace --source cpu --report .build/glass-workspace-cpu.json
python -m examples.benchmark_glass_workspace --source gpu --report .build/glass-workspace-gpu.json
python -m examples.benchmark_glass_workspace --source gpu --counts 12 --seconds 8 --churn 48 --report .build/glass-workspace-soak.json
python -m examples.verify_glass_effects --webview --browser .build/browser-gpu/Release/ctui_browser.exe
```

GPU提供者默认为 `ctui_gpu_producer.exe`，可用 `--producer` 指定；性能比较应串行运行。
`--warp --smoke --frames 8` 只作为功能/稳定性CI，不代表硬件性能。
集成测试短暂显示自己创建的窗口并自动关闭，无需电脑插件，宿主不是外部终端。

本轮不改变系统DPI、不跨物理显示器移动、不重置显卡驱动。合成缩放和第二设备替换
分别验证尺寸计算和重建路径，不替代这些硬件场景。真实终端附加、鼠标键盘命中及
视觉美观仍使用原有独立入口，不以API压力结果宣称重做了人工验收。
结果见[验证记录](validation/glass/README.md)。未增加Python渲染抽象或公开像素读回接口。
