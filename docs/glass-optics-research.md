# Liquid Glass 第二轮调研与修改计划

2026-10-18，继续 `codex/liquid-glass-cards`。第一版使用深色 Acrylic，加上近乎不透明
的自绘卡片底色，没有达到用户要求的 iOS 26 通透质感。dim 是压暗，Mica 主要取壁纸，
二者不是磨砂；不能以系统 API 调用成功代替实际视觉验收。

## Exa 调研

本轮通过 Exa 请求30条候选，按视觉原理、原生 GPU 实现、公开 API、采样反馈及事件容器
方向筛选，并读取微软 API 文档核对边界。参考实现仅用于理解方案，不引入 Electron、
WebView、网页 UI 或第三方着色器。

| 来源 | 结论及本次应用 |
| --- | --- |
| [Apple Meet Liquid Glass](https://developer.apple.com/videos/play/wwdc2025/219/) | 以 lensing、反射、环境透色和交互共同表现材质；Clear 更透明，需要局部对比保护，Regular 更注重适应性。不能把模糊、染色等同于液态玻璃。 |
| [Apple 自定义玻璃视图](https://developer.apple.com/documentation/SwiftUI/Applying-Liquid-Glass-to-custom-views) | 形状和交互属于材质系统；借鉴较大圆角、可响应的边缘，不复制苹果私有实现。 |
| [Microsoft Composition effects](https://learn.microsoft.com/en-us/windows/apps/develop/composition/composition-effects) | 公共合成效果不是任意像素着色器；HostBackdropBrush 无法直接读回为自定义 D3D 采样输入，不把有限的材质接口当成完整折射管线。 |
| [CreateHostBackdropBrush](https://learn.microsoft.com/en-us/uwp/api/windows.ui.composition.compositor.createhostbackdropbrush) | 取样宿主之后的背景，但行为受系统策略影响；不能从该 Brush 提取像素给自定义 shader。 |
| [Desktop Duplication](https://learn.microsoft.com/en-us/windows/win32/direct3ddxgi/desktop-dup-api) | 提供 GPU 桌面纹理及 dirty/move 元数据。复用最近 GPU 图像，局部复制变化，避免 CPU 截屏与每帧完整复制。 |
| [AcquireNextFrame](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_2/nf-dxgi1_2-idxgioutputduplication-acquirenextframe) | timeout=0 不阻塞 UI；指针更新不等于图像更新，ACCESS_LOST 要释放并重新建立。 |
| [WCA_EXCLUDED_FROM_DDA](https://learn.microsoft.com/en-us/windows/win32/dwm/windowcompositionattrib) | 在自身窗口上排除 DDA 自采样，避免玻璃递归采集自己。仅用于该用途，检查返回值；无法可靠排除则禁用实时光学采样。 |
| [SetWindowCompositionAttribute](https://learn.microsoft.com/en-us/windows/win32/dwm/setwindowcompositionattribute) | 该入口需动态获取，微软总体不推荐替代 DWM 常规属性。这里仅使用已文档化、DWM 未提供等价入口的 DDA 排除属性，材质本身由自有 shader 生成。 |
| [SetWindowDisplayAffinity](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowdisplayaffinity) | 广义排除会让其他截图工具也无法看到卡片，本次不自动将整个 UI 设置为全局截图保护。 |
| [原生 Electron Liquid Glass 参考](https://github.com/hicccc77/electron-liquid-glass) | MIT 项目证明 DDA→D3D11→DComp 的独立原生光学层路径可行；其性能数字不作为本项目实测结果。 |

## 架构与 API 约束

这是与 `BrowserService` / WebView 同级的**上层 UI 服务**，不属于 Canvas Widget，
不修改 FrameProvider 帧协议，也不往 Canvas SDK 添加卡片语义。

```text
BrowserService / WebView          GlassWorkspace / 自有原生控件、卡片、光学材质
          │                                  │
          └──────── 图像来源 / 私有适配 ────────┘
                         │
              FrameProvider / Compositor 图像基础设施
```

原生玻璃头文件及 CMake 导出迁到独立 ComfortableGlass 上层 SDK。Python 只提供模型、
状态、握手和事件；已有外部图像视图是可选输入，不是该服务的定义。

- `GlassBackground`：每张卡独立选择 liquid / frosted / mica / solid / transparent，
  并设定 tint、opacity、blur、refraction、dispersion、corner_radius。工作区主题仅为默认值。
- `GlassCard(..., background=..., draggable=True/False)`：使用者独立决定拖动能力。
  只从标题区拖动，控件和图像输入区不会误触移动。
- 自由位置保存为终端相对位置，普通模型更新不回弹到自动布局。更换布局或显式 reflow
  恢复排列；越过屏幕边缘时约束到工作区。拖动事件返回卡片标识及逻辑坐标。
- 工作区聚焦背景与单卡背景是两种设置。`dim` 只压暗；`mica` 明确指壁纸材质；
  `frosted` 才执行真实桌面背景模糊。

## 原生绘制方案

1. 每个工作区在当前显示器使用一个 DDA 会话及 GPU 桌面镜像。只处理 dirty/move
   区域，卡片和聚焦背景排除自身采样。只有指针变化时不更新背景。
2. 使用自有圆角 SDF shader：通透中心、小幅局部染色、边缘法线位移、低强度 RGB
   色散、Fresnel 边缘和随指针/拖动变化的高光。文字与图像层保持清晰，不一起折射。
3. frosted 使用独立的降采样、横纵高斯 pass；单卡可不同参数。dim/Mica 不冒充该效果。
4. 光学层通过私有帧来源给既有 Compositor，静态 UI 不因此逐帧重画。
   卡片移动或相关桌面区域更新时才重绘；隐藏时释放采样会话。
5. 所有原生失败可观测，明确回退。旋转/HDR/混合显卡等未验证能力不宣称已支持。
   桌面采样只留在 GPU、无截图文件、不暴露采样纹理给 Python、无网络传输。

## 修改顺序与验收

1. 增加每卡配置、拖动策略与验证；整理 SDK 层次。
2. 实现独立 GPU 光学层、真实 frosted 和清晰的实际材质/失败诊断。
3. Card 路由后的标题拖动授权、捕获取消、边界、相对位置、可禁用策略及事件。
4. 更新示例，让不同卡片同时展示不同背景，提供独立切换和拖动开关。
5. 验证各卡独立性、实像透色/折射/模糊、DDA 自反馈排除、停稳/隐藏开销、拖动与
   控件输入互不干扰、源替换及关闭。用自有彩色背景夹具验证视觉，不用深色纯底掩盖效果。
6. 更新文档、验证记录、现有研发 PR，运行原库测试、原生构建和 CI，不自动合并发布。

## Card 与容器补充调研

- [Qt DragHandler](https://doc.qt.io/qt-6/qml-qtquick-draghandler.html) 将手势处理器放在
  元素侧，并显式管理 grab/cancel；据此把拖动选择交给 Card，而不是 HWND 消息处理器。
- [WPF 路由事件](https://learn.microsoft.com/en-us/dotnet/desktop/wpf/events/routed-events-overview)
  与[预览事件](https://learn.microsoft.com/en-us/dotnet/desktop/wpf/events/preview-events)
  说明容器预览和祖先冒泡的用途。本库采用小型树和 bool 消费返回值，不复制完整依赖
  属性、模板或框架事件系统。
- [已处理事件](https://learn.microsoft.com/en-us/dotnet/desktop/wpf/events/marking-routed-events-as-handled-and-class-handling)
  明确处理状态影响后续路由。本库在路由完成后才调用 Card 默认拖动；原生控件的
  change/click 则是动作通知，消费通知不回滚控件状态，两者在 API 文档中区分。

CardGroup 提供 row/column/stack、继承 visible/enabled 和嵌套 preview/bubble；原生
容器不另建 HWND。单卡通过有效 pointer_down token 授权一次拖动，松开/捕获丢失后
token 失效；其后每帧移动不再经过 Python。

## 实测后的采样反馈修正

原型中，即使已排除卡片的 DDA 像素，桌面 dirty 元数据仍会包含自身呈现区域，导致
不必要的连续光学重绘。因此增加 GPU 区域比较：只有背景 RGB 真正变化才提交镜像。
每次完成只异步回读16字节变化包围盒，不回读背景像素；无变化时不重新绘制光学表面。
采样、比较和绘制都留在原生线程；静止和动态场景的验证结果单独记录，不从 API
调用成功推断性能。此措施增加约一个 tick 的背景更新延迟，不能宣称零延迟。
