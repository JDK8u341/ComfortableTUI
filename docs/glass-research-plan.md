# 终端周边液态玻璃：调研与研发计划

> 本文保留首轮方案的决策过程。后续已改为独立上层 SDK、GPU 桌面光学采样、Card
> 默认事件与 CardGroup 容器；本文的“不采样桌面”、Acrylic 及圆角决策已被取代。
> 当前方案见[光学与事件调研](glass-optics-research.md)和[使用指南](glass-workspace.md)。

2026-10-18，研发分支 `codex/liquid-glass-cards`。本计划在实现之前制定。
这是可选的 Windows 原生 UI 扩展；现有内嵌 OverlayCanvas、TUI 布局和帧协议继续独立使用。

## 调研结论

通过 Exa 分别调查项目识别、材质设计、Windows 合成、Qt 兼容和空间交互，第一轮共请求
27 条搜索候选，继续读取一手资料验证。不是把网页效果或游戏截图当作桌面性能证据。

- 用户给出的 JacklClinet 最接近 [JackalClient](https://github.com/JackalClient/JackalClient)。
  官方介绍包含动态 HUD、Island、悬浮信息和 WindowHighlight；可参考其周边信息组织。
  该项目为部分开源、AGPL-3.0，本项目保持 Apache-2.0，自行实现，不复制其代码或素材。
- Apple 的 [Meet Liquid Glass](https://developer.apple.com/videos/play/wwdc2025/219/)
  将材质和交互作为一个整体：曲面边缘、高光、空间关系、从触发源展开，以及随背景调整
  读写层级。透明白框不是完整的液态玻璃；动效也不能掩盖内容。
  [新设计系统说明](https://developer.apple.com/videos/play/wwdc2025/356/)
  强调让主要内容保持中心地位，短暂操作从其来源展开，长期并行内容保持稳定位置。
- [Windows system backdrops](https://learn.microsoft.com/en-us/windows/apps/develop/ui/system-backdrops)
  区分真正磨砂的 Desktop Acrylic 与低成本、采样壁纸的 Mica。Mica 不是实时桌面模糊。
  [DWM_SYSTEMBACKDROP_TYPE](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwm_systembackdrop_type)
  的公开 Win32 接口要求 Windows 11 build 22621，必须检查 HRESULT 并提供实色回退。
- [Qt Windows 源码](https://github.com/qt/qtbase/blob/dev/src/plugins/platforms/windows/qwindowswindow.cpp)
  对无边框透明 QWidget 启用 WS_EX_LAYERED。不能假定现有 Qt 栅格透明窗天然支持同一套
  DWM 系统背景。因此新扩展使用自有 Win32/D3D11/Direct2D 控件层，不勉强改造旧窗口。
- [Direct2D/Direct3D 互操作](https://learn.microsoft.com/en-us/windows/win32/direct2d/direct2d-and-direct3d-interoperation-overview)
  允许 Direct2D 在 DXGI GPU 表面绘制。现有 FrameProvider 的 BGRA 纹理可以承载原生
  文字和控件，再由现有 Compositor 合成外部 CPU/GPU 画布，像素不经过 Python。
- [DWM 性能建议](https://learn.microsoft.com/en-us/windows/win32/dwm/bestpractices-ovw)
  提醒大面积动态模糊有成本，不应每帧读回桌面。我们使用系统背景，避免桌面抓屏循环。
  背景磨砂是可选的聚焦效果，默认采用低干扰方式，不常驻覆盖全部桌面。
- [Windows Terminal 的召回交互](https://github.com/microsoft/terminal/issues/8888)
  说明“召回、滑入、回到来源”比固定平铺更适合短暂工具。借鉴交互关系，不接管终端设置。

## 视觉与行为方案

终端是舞台中心，周边最多 12 张卡片。每张卡片有明确用途、标题、低对比副说明、内容和
最多一个主动作。间距、字号、圆角、前景和强调色由统一 token 定义。

1. **Orbit 环绕**：卡片以不同高度分布于终端两侧，主卡稍大；保留中央终端负空间。
2. **Deck 叠牌**：主卡完整显示，其他卡片错位露出边缘；切换时沿同一轨迹换入、退后。
3. **Ribbon 侧带**：短暂工具沿一侧纵向错开，滚轮或方向键换卡，避免网格仪表盘。
4. **Focus 聚焦**：当前卡片贴近终端展开，其余卡片退为小型侧翼。窄屏或终端全屏时
   自动把周边空间转换为边缘叠牌，而不是让卡片跑到屏幕外。

入场从终端边缘滑出，退场回到该来源；切换保留速度的弹簧，支持反向打断。
已停稳的卡片只响应实际内容、悬停或状态变化，不运行无意义的闪烁。鼠标高光只改变
边缘和少量光泽，不让正文随鼠标移动。支持 reduced motion 与高对比/降低透明度。

材质提供 `glass`、`frosted`、`mica`、`solid`。玻璃风格采用系统 Acrylic + 自绘边缘、
柔和明暗层和高光；不会把它宣传成 Apple 原生 Liquid Glass 或任意桌面的真实折射。
Windows 10/不支持背景/高对比时回退到清晰实色，并上报实际材质。

焦点组包括终端及其卡片。背景提供 `none`、`dim`、`frosted`、`mica`：后两者在支持的
系统上使用独立背景窗，放在终端下方、普通窗口层级内。失焦立即隐藏，不能成为全局
置顶遮罩。聚焦背景的空白处允许点击关闭；第一下点击只解除聚焦，不伪造输入给其他应用。

## 架构和边界

- **Python API**：`GlassWorkspace`、`GlassCard`、`GlassControl`、`GlassTheme` 与事件。
  只负责有界声明、握手、业务回调和状态更新。可与 App 绑定，也可显式传入宿主 HWND。
  不改变纯 TUI 导入行为，不把玻璃控件伪装成终端字符控件。
- **C++ GlassHost**：拥有 Win32 消息循环、窗口、Direct2D/DirectWrite 绘制、动画、命中、
  键盘焦点、DPI/工作区适配、材质和背景生命周期。Python 停顿时，移动和已有动画仍继续。
  所有 API 操作通过一次最新场景快照或命令提交，不在 Python 逐帧布局。
- **Canvas 复用**：原生控件层作为 FrameProvider 输出；每张卡片使用现有 Compositor，
  外部 CPU FrameBuffer/GpuFrameSource 继续作为 NativeView 合成。保留标准画布输入
  源坐标、transform 事件和来源释放确认。扩展不调用浏览器服务，不创建浏览器进程。
- 原生帧协议仍为版本 1；只新增控制层能力。单个 GPU 通道仍只允许一个活动消费者，
  首版会严格拒绝把同一通道分配到多个独立卡片/旧叠加层，避免隐含多消费者冲突。
- 最多 12 卡片、每卡 32 控件、单卡最大 1600×1200 逻辑像素，文字和路径均有界。
  数据处理和外部提供者属于可信宿主，不增加插件执行/任意 shell/网络能力。
- 只处理明确绑定的宿主；无桌面全局输入钩子、终端注入、窗口样式永久修改、屏幕抓取、
  系统设置修改或新的浏览器权限。退出恢复自身窗口，宿主销毁则自行清理。

## 实施顺序及验收

1. 落实模型、验证、公共 API、headless 场景测试；定义场景快照及有界事件。
2. 添加可选 C++/pybind11 GlassHost，复用 FrameProvider/Compositor；先完成单卡实际
   GPU 绘制与真实 DWM 背景，再完成原生控件、转场、空间布局和焦点背景。
3. 接入 App 的事件轮询和显式上下文关闭；保持内嵌画布回归不变；验证 CPU 和 GPU
   来源、键盘/鼠标源坐标、场景更新确认后释放资源。
4. 在 `examples/glass_workspace/` 提供独立入口、实用开发工作台示例、参数和 README；
   提供不依赖真实进程执行的任务清单、笔记、实时信号/外部画布、材质与布局切换。
5. 自动化：模型边界、交互、停稳空闲、反向动画、屏幕边缘、宿主最小化/销毁、
   背景关闭/失焦、资源释放；全部原库测试和文档检查。
6. 真实桌面：用电脑插件选择并操作新卡片窗口，实际点击、键盘、截图检查。
   真实终端通过正常命令入口启动和绑定；不通过电脑插件自动操作终端或修改其设置。
   系统效应不足时如实记录 fallback，不把设置成功的 HRESULT 等同于视觉验证。
7. 记录 CPU/GPU/动画统计、原生线程独立性和清理结果；整理 API、原理、限制、
   快速上手、开发扩展及验证文档。提交研发分支供审阅，不自动合并或发布此新功能。

只有外部权限/桌面锁屏等真正需要人工介入时才停止依赖工作，并尝试用系统提示音提醒。
没有人工阻塞时持续实现与验证。

## 实施中的补充调研

使用 Exa 继续核对了 MSAA simple child 的 ID、物理像素边界，以及同进程 UI 宿主的
跨线程关闭。微软的 [Creating Windows in Threads](https://learn.microsoft.com/en-us/windows/win32/procthread/creating-windows-in-threads)
明确要求创建窗口的线程在等待时继续处理消息，普通 join/无限等待可能与同步窗口
通知互相等待。关闭等待因此使用消息感知等待，仅处理 sent messages，不重入已投递
的 Python 业务工作。[SetForegroundWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow)
仍受系统前台限制，本扩展不通过输入线程附加或模拟全局输入绕过限制。

无障碍实现依据 [child IDs](https://learn.microsoft.com/en-us/windows/win32/winauto/how-child-ids-are-used-in-parameters)
和 [accLocation](https://learn.microsoft.com/en-us/windows/win32/api/oleacc/nf-oleacc-iaccessible-acclocation)，
在原生 STA 内提供简单子控件、物理像素边界及生命周期失效保护。

视觉验证还检查了材质外沿：[Windows 圆角政策](https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/ui/apply-rounded-corners)
不保证对自定义区域或逐像素分层窗口提供系统圆角。卡片因此遵循系统圆角，自绘着色
与边框使用相同的常规圆角尺度，避免大圆角内容外露出未着色的 Acrylic 楔形区域；
不通过额外窗口区域破坏系统阴影和背景路径。
