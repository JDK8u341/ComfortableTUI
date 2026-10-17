# C++ 数据平面与 GPU 共享纹理调研

2026-10-17 使用 Exa。首次三路检索（18条候选请求）及网页读取连接失败，重连后完成
共享纹理、原生合成和同步/延迟三个方向的检索（16条成功候选请求），再阅读 Microsoft
一手文档。失败请求不算已经审阅的资料；成功候选去重后仅将官方技术资料用于以下决策。

## 原路径的问题与修改

源码中原 Qt 路径每4 ms由 Python 检查源序号，又按 `now + 1/fps` 决定是否更新。
到达事件不能直接唤醒呈现，周期取整和调度会增加等待。QImage/QPainter 虽调用 Qt
C++，帧序号、租约、绘制触发和 CPU backing store 仍在 Python/Qt 路径中。

新路径为：原生提供者 → C++ 三槽协议 → D3D11 共享纹理 → C++ DXGI/DirectComposition。
Python 只做连接握手、布局/可见状态和输入控制；没有每帧像素、序号或纹理租约调用，
也不使用 Python 回调来驱动 C++ 绘制。呈现线程不取 GIL。

## 一手依据

| 问题 | 资料与决定 |
| --- | --- |
| 跨进程共享 | [CreateSharedHandle](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_2/nf-dxgi1_2-idxgiresource1-createsharedhandle) 与 [OpenSharedResourceByName](https://learn.microsoft.com/en-us/windows/win32/api/d3d11_1/nf-d3d11_1-id3d11device1-opensharedresourcebyname)：D3D11.1 NT shared handle，通过随机 Local 命名握手；句柄/资源用 RAII 关闭。 |
| 纹理约束 | [D3D11_RESOURCE_MISC_FLAG](https://learn.microsoft.com/en-us/windows/win32/api/d3d11/ne-d3d11-d3d11_resource_misc_flag)：SHARED_NTHANDLE 与 SHARED_KEYEDMUTEX，单层非 MSAA 的2D BGRA8预乘纹理；双方使用同一 adapter LUID。 |
| 同步 | [AcquireSync](https://learn.microsoft.com/en-us/windows/win32/api/dxgi/nf-dxgi-idxgikeyedmutex-acquiresync)：明确检查 WAIT_TIMEOUT、WAIT_ABANDONED，不能只用 SUCCEEDED。用短 metadata mutex 分配槽，再取得 GPU keyed mutex，锁期间渲染/采样。双方始终使用 key=0，槽状态分配所有权，避免丢弃旧帧后 key交替死锁。 |
| 命令提交 | [OpenSharedResource](https://learn.microsoft.com/en-us/windows/win32/api/d3d11/nf-d3d11-id3d11device-opensharedresource) 提醒共享表面更新需要 Flush。Flush 只提交命令，不能当作 GPU 完成；可见性与所有权由 keyed mutex 保证。 |
| 原生透明合成 | [Windows Composition Engine](https://learn.microsoft.com/en-us/archive/msdn-magazine/2014/june/windows-with-c-high-performance-window-layering-using-the-windows-composition-engine) 说明传统 WS_EX_LAYERED 为命中保留 CPU 表面。新 HWND 使用 WS_EX_NOREDIRECTIONBITMAP，避免在共享 GPU 后又下载为 Qt CPU 分层窗口。 |
| Swap chain | [CreateSwapChainForComposition](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_2/nf-dxgi1_2-idxgifactory2-createswapchainforcomposition)：FLIP_SEQUENTIAL、SCALING_STRETCH、双缓冲、预乘 alpha。 |
| 队列延迟 | [DXGI 1.3 低延迟交换链](https://learn.microsoft.com/en-us/windows/uwp/gaming/reduce-latency-with-dxgi-1-3-swap-chains) 与 [SetMaximumFrameLatency](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_3/nf-dxgi1_3-idxgiswapchain2-setmaximumframelatency)：frame-latency waitable object，上限1；先等呈现容量，再获取最新源帧，不先锁住旧帧再等队列。 |
| CPU兼容上传 | [动态资源](https://learn.microsoft.com/en-us/windows/win32/direct3d11/how-to--use-dynamic-resources)：动态纹理与WRITE_DISCARD映射。C++直接把固定槽复制到映射行，不做额外CPU缩放；GPU源不走这个路径。 |

这些资料支持本实现的选择，并不证明本实现的屏幕端到端延迟；后者需要单独测量。

## 协议与取舍

三张共享 GPU 纹理，每槽有状态、序号、源 QPC 时间戳和发布 QPC 时间戳。消费者只选
最新完整帧。当前帧保持 GPU 读租约，可供多个逻辑画布重复采样；替换时释放旧租约。
生产者不覆盖 Reading 槽，空闲不足时复用较旧 Ready 槽，无视频 FIFO 积压。

数据平面不进行 GPU→CPU readback，也不为正常呈现复制到私有 GPU 中转纹理；
像素着色器直接采样共享资源，最终仍需绘制到合成 swap chain，不能把它称为完全无
GPU 工作/无显存带宽。C++ CPU 源可通过 submit_bgra 上传一次；旧 FrameBuffer 兼容
适配器在 C++ 读取三槽并上传，RGB转换也在 C++ 完成。

新 GPU 源用原生 event 唤醒。原生 CPU 对比工具也发 event；旧 Python FrameBuffer
没有此通知时，只有兼容适配器在 C++ 以1 ms检查，不再经 Python GUI 帧循环。
多个画布共享同一源时只开一个消费者，最多60个独立注册源；每层最多4096个逻辑视图。

原 QWidget 子控件仅作输入/几何节点，禁用其绘制。窗口 region 为可见矩形并集，让
画布之外的 TUI 区域继续命中终端；DComp 的 alpha透明不等于 Win32 的输入穿透。
矩形内的 capture/passthrough 保持原有事件路由语义。

配置是带确认的控制握手：C++ 附着新源、释放旧源后才返回，随后发送 buffer_attached
和 detached，避免 Python 释放一个辅助端尚未打开/仍在使用的缓冲区。同步等待期间
释放 GIL。场景切换、隐藏、按键和 IME 仍属于 Python 控制平面。

设备丢失、keyed mutex放弃、原消费者带着租约崩溃、适配器不匹配均明确报错，要求
重建提供者并重新握手；不盲目复用不一致纹理，也不静默把 GPU 源降级为 CPU 复制。

## 实测与边界

使用同一个独立 C++ CPU 生产者比较 Qt 与 C++ CPU 呈现；另测原生 GPU 生产者。
源码、频率、源尺寸、预热和采样方法见 `examples/benchmark_gpu.py`。
720p/240fps、显示区960×540、本机 Windows11 的3秒采样：

| 路径 | 帧龄中位数 | p95 | 终点 |
| --- | --- | --- | --- |
| Qt CPU | 2.710 ms | 4.574 ms | Qt paint完成 |
| C++ CPU | 0.425 ms | 0.570 ms | Present返回 |
| C++ 共享 GPU | 0.441 ms | 0.554 ms | Present返回 |

这里 GPU 路径没有比 CPU 上传路径更低的中位数；它的价值还包括省去大图 CPU复制/
上传带宽及与原生 GPU 渲染器互通。不能从这组数据推导输入到屏幕的延迟或显示FPS。
原始记录为 [720p](benchmark-gpu.json) 和 [4K](benchmark-gpu-4k.json)。

4K输入、同样960×540显示区的中位数分别为Qt 3.550 ms、C++ CPU 4.054 ms、
共享GPU 0.436 ms。CPU兼容路径的大图上传仍可能比Qt CPU下采样慢，因此不承诺
所有CPU源都加速；高分辨率提供者应直接输出共享GPU纹理。源为原生填色/清屏测试，
GPU和CPU源生成方式不同；不是视频解码/游戏负载的同工作量比较。采样只覆盖
读到的新序号，不一定捕获每一原生提交帧；不等同于完整逐帧延迟分布。

已验证独立进程像素正确、abort/最新帧/固定槽防覆盖/重连/单消费者/适配器拒绝；
真实 DWM 图案、125%DPI输入矩形、透明洞、无原生子窗口、隐藏恢复、共享视图及
detached后可重新开消费者。Python忙循环持有GIL时，原生序号及Present计数继续增长。

实际显示扫描延迟、真实解码器/游戏输入响应、设备拔插/TDR故障及不同驱动/跨GPU复制
未在这里证明。跨GPU纹理共享不在本协议支持范围；需要生产者选择同一适配器。

构建、API和迁移见 [C++/GPU 使用文档](native-gpu.md)。
