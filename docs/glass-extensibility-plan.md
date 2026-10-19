# 独立效果器、自定义 Card 控件与动画钩子：调研及改进方案

日期：2026-10-19。基线：`b5c35bb`，分支 `codex/liquid-glass-cards`。

**状态：设计方案，尚未实现。** 本文中的新类、参数、注册入口和事件名称均为拟议接口，
不代表当前版本已经支持；当前运行时接口见 [GlassWorkspace](glass-workspace.md)、
[GlassCanvas](glass-canvas.md)和[WebView](glass-webview.md)。

## 结论与需求边界

设计合理，且应扩展到控件粒度：Card 本身和其中每个 UI 控件都可绑定独立效果器。
Liquid Glass 应是内置效果器之一，不应成为 Card 的类型限制。效果、控件和动画分开：

- 效果器决定如何处理图像/背景，不决定业务行为、焦点、拖动和布局。
- 控件决定尺寸、绘制、命中、交互及无障碍语义；可组合已有控件，也可注册全新原生控件。
- 动画由使用者通过事件钩子和可选帧回调定义。框架提供时钟、属性提交及调度，
  不要求使用固定弹簧、曲线、动画 DSL 或内建状态机。
- CardGroup 仍负责容器布局和事件路由。Canvas/FrameProvider 只负责图像与传输；
  WebView 仍是其上的独立服务，不加入效果器或控件协议。

这里同时覆盖两种扩展：新增卡片内部控件（如旋钮、仪表、时间轴），以及新增可复用
Card 类型（如监控卡、播放器卡、自定义标题区/外观模板）。

## Exa 调研及依据

本轮按效果实例、GPU 成本、原生控件扩展和动画事件四个方向完成8次有效搜索，
共请求40条候选，包含重复项，服务实际返回量可能不同。另有3次并发请求发生连接错误，顺序重试后恢复；失败请求不算
已审阅来源。以下10份官方文档是结论依据。参考其架构，不新增 Qt、Skia、WinUI 或 WebView
依赖。Apple 的部分 SwiftUI 页面只返回标题，因此没有据此推断未读取到的实现细节。

| 来源 | 已核对内容 | 本项目的设计取舍 |
| --- | --- | --- |
| [Apple UIGlassContainerEffect](https://developer.apple.com/documentation/UIKit/UIGlassContainerEffect) | 多个独立玻璃视图可由容器组合渲染，效果位于 contentView 后方 | 将背景效果与可读内容分开；跨卡融合应是显式能力，不能与“共享 GPU 资源”混淆 |
| [Microsoft Composition effects](https://learn.microsoft.com/en-us/windows/apps/develop/composition/composition-effects) | 同一效果定义可生成参数和动画独立的多个实例 | 共享编译结果，每个节点拥有自己的参数、交互和动画状态 |
| [CompositionEffectBrush](https://learn.microsoft.com/en-us/uwp/api/windows.ui.composition.compositioneffectbrush?view=winrt-26100) | 实例可独立设置来源/属性；这个接口不允许任意自定义 Shader | 借鉴 definition/instance 模型，保留现有 D3D11 后端以实现自己的 HLSL 扩展 |
| [Qt MultiEffect](https://doc.qt.io/qt-6/qml-qtquick-effects-multieffect.html) | 常见效果可合并，模糊/阴影及额外像素范围代价较高，部分属性会切换 Shader | 限制离屏范围，参数更新不触发编译；仅在语义允许时融合 pass |
| [Skia RuntimeShaderBuilder](https://api.skia.org/classSkRuntimeShaderBuilder.html) | 已有 RuntimeEffect 与实例的 uniform 数据、child inputs 分开 | 用类型化参数及明确输入槽，不让每帧 Python 字典解释成为渲染路径 |
| [Direct2D custom effects](https://learn.microsoft.com/en-us/windows/win32/direct2d/custom-effects) | 自定义效果有输入、属性、变换及所需输入/失效区域映射 | 效果注册时声明采样范围和输入依赖；模糊/位移不能只标记控件原矩形 |
| [Direct2D shader linking](https://learn.microsoft.com/en-us/windows/win32/direct2d/effect-shader-linking) | 简单像素处理可能融合，多点采样的模糊存在融合限制 | 不承诺任意效果链都能变成一次 draw；首版不实现通用 Shader 链接器 |
| [Qt custom render node](https://doc.qt.io/qt-6/qtquick-scenegraph-customrendernode-example.html) | 自定义节点可内联绘制或离屏到纹理；离屏增加 pass/资源，原生资源有线程归属 | 无效果控件保留直接绘制，自定义节点按需隔离，不给每个控件建立窗口或帧提供者 |
| [WinUI templated controls](https://learn.microsoft.com/en-us/windows/apps/winui/winui3/xaml-templated-controls-winui-3) | 可复用控件类型与其视觉模板可分离 | Python 组合已有节点；真正新增原生行为/绘制由 ControlType 注册承担 |
| [Qt FrameAnimation](https://doc.qt.io/qt-6/qml-qtquick-frameanimation.html) | 可用帧事件、elapsedTime/frameTime 编写自定义动画，优于固定16ms定时器假定 | 提供可选帧钩子与真实单调时间，使用者自行实现运动规律 |

以上证明这种职责划分有成熟先例；具体的 D3D11 管线、API 和迁移方式是针对本仓库
作出的工程方案，不是上述项目的原样实现或性能保证。

## 当前实现审计

当前已经做到每卡参数独立，不能说所有 Card 共用一组材质参数。尚缺以下扩展点：

| 位置 | 现状 | 改进 |
| --- | --- | --- |
| `src/comfortable_tui/glass.py` 的 GlassBackground | `kind` 白名单同时承载 liquid/frosted/mica/solid、光学参数及 corner_radius | 分开形状、系统材质/回退和效果绑定；旧参数保留兼容转换 |
| 同文件的 GlassControl | 九种固定 kind，无 effects 字段；Python 子类不能新增原生绘制类型 | 加效果绑定和注册控件句柄，新增类型不再修改全局 kind 分支 |
| `native/glass/glass.cpp` 的 Card::apply_material | 根据 kind 决定光学资源、系统材质和窗口区域 | Card 管理宿主及形状，效果实例自行决定所需 GPU 输入/资源 |
| `native/glass/optics.cpp` | 一套硬编码 HLSL 和 liquid/frosted 分支、固定 blur/lens passes | 提取内置 Liquid/Frosted 实现到效果注册表，以相同接口接入自定义效果 |
| `native/glass/glass.cpp` 的 draw/row_height/hit/interactive | 控件绘制、尺寸、命中和焦点以 kind 分支实现 | 内置控件及扩展控件采用同一个原生注册契约 |
| `native/glass/accessibility.hpp` | MSAA role/value/action 与 kind 分支绑定 | 无障碍描述必须属于 ControlType，而不是由绘制结果推测 |
| `native/glass/glass.cpp` 的 Spring/layout | Card 的位置弹簧是固定实现，缺少可替换的动画钩子 | 区分布局目标和呈现状态，把现有 Spring 改为可替换默认处理器 |

还需处理一个结构事实：目前原生控件共同绘制到一张 UI 纹理，而外部 Canvas 是另一
合成层。因此“给每个控件添加 effects 参数”还不够，必须提供可选择的局部输入与离屏
渲染入口，才能让效果影响指定控件而不影响相邻控件。

## 最小架构

```text
Python：Card/控件描述、效果参数、业务事件、用户动画钩子
                        │ 变化时提交
                        ▼
C++ Glass 服务：控件注册表、效果注册表、事件与帧调度、资源生命周期
      │
      ├─ Card：终端跟踪、形状、标题/内容槽、输入与无障碍
      │    ├─ 内置 Control / 自定义 ControlType / 组合组件
      │    └─ Canvas / WebView（继续复用现有通用画布）
      │
      └─ Effect 实例：背景 / 内容 / 装饰 → 原生绘制及合成
                        │
                        ▼
             现有 GPU 图像来源、纹理与帧租约
```

实现只需两个核心注册契约：**ControlType** 和 **EffectType**。动画是节点上的事件/
帧钩子，不再设计一套通用 Python 动画引擎。内部需要编译对象与实例，但不强制用户
操作一长串 factory、builder、material、pipeline 包装类。

### 效果定义与每节点实例

效果定义包含原生实现/Shader、参数 schema、输入槽、采样边界及更新依赖。GPU 程序
按设备与代码版本缓存。绑定到节点后创建独立实例，持有参数、交互状态及必要的局部资源。

同一 Liquid 定义可以用于100个节点，但这些节点的折射强度、悬停状态、动画进度和
退休时机互不影响。共享不可变预设不会隐式共享可变状态；显式联动由使用者事件代码完成。

LiquidGlass 应声明为复合的背景效果，可内部包含模糊、折射、色散、边缘光照多道 pass。
效果是算法与依赖的单位，不要求一个效果恰好等于一个 Shader。Mica 保留为系统背景
来源策略，不伪装成可任意采样的自定义 HLSL 效果。

每个节点提供三个有顺序的挂载位置：

| 位置 | 输入与用途 | 默认行为 |
| --- | --- | --- |
| background | 节点之后的指定背景；用于液态玻璃、磨砂等 | 内容仍清晰，不自动折射文字/网页 |
| content | 节点自己的图像；用于灰度、颜色、模糊等处理 | 只作用于选定节点；父级全内容处理必须显式指定 |
| overlay | 节点形状/边界、交互信息；用于高光、光晕、描边等 | 视觉外扩不自动扩大点击区域 |

效果链在槽内按声明顺序执行。普通控件默认无额外效果；主题可给特定角色提供默认绑定。
`effects=None` 表示采用主题角色默认，空列表表示明确禁用，显式列表替换默认。容器
提供默认样式时，每个子节点仍建立独立实例，不共享可变对象。

形状与 corner_radius 属于节点几何；效果可以读取形状掩模，但命中、裁剪、无障碍
区域使用同一份几何数据。纯像素效果不能悄悄改变控件的逻辑尺寸。

### 必须区分两种“背景”

Card 的液态背景可以使用现有 DDA 桌面来源。该来源刻意排除了本应用的卡片窗口，
所以不能用它替代“卡片内部按钮背后的卡片内容”。

控件的局部背景应取同一卡片中绘制到该控件之前的父级/兄弟内容，排除控件自身、后续
兄弟和前景。遇到前面的 Canvas/WebView，须使用原生合成阶段已有的纹理和帧租约，
纳入该局部背景；不能靠再次截取 HWND 或另建同通道消费者来获得图像。

因此需要受控的局部合成入口与绘制顺序。首版支持有限输入槽和线性效果链；不暴露
任意跨卡依赖 DAG，不允许自引用。跨卡液体融合是后续显式容器功能，不能因共用缓存
而自动融合。上层可以复用/扩展内部合成能力，但不往公开 FrameProvider 协议添加 UI 语义。

### 自定义控件及 Card 类型

提供两条路径：

1. **Python 组合**：组件工厂/子类把参数转成已有控件树，适合 MonitorCard、PlayerCard
   等复用组件。只在结构或参数改变时构建，不在每帧重建控件树。需要可复用的局部容器
   节点来承载自定义组合控件；这与现有“容纳多张卡”的 CardGroup 是不同层次。
2. **C++ 原生 ControlType**：真正新增旋钮、仪表、曲线编辑器等绘制/交互类型，通过
   SDK 注册。新增 Card 外观可复用节点绘制契约及标题/内容槽模板；窗口跟踪、任务栏归属、
   拖动授权与焦点组仍由宿主管理，不让新控件各自创建宿主窗口。

原生控件注册至少包含以下契约；不把这些能力压缩成单独一个 paint 回调：

| 契约 | 职责 |
| --- | --- |
| 类型名、版本、属性 schema | 在挂载/变更时验证并转换为原生状态；未知类型明确报错 |
| measure/layout | 根据约束计算尺寸和子节点位置；替代固定 row_height 分支 |
| paint | 绘制自己的内容；无效果时可直接画入目标，不强制离屏 |
| hit_test / 输入默认处理 | 原生命中、指针捕获、键盘处理；使用者可通过既有事件路由拦截默认行为 |
| focus / semantics | Tab 策略、role/name/value/action、子节点语义及边界 |
| 资源生命周期 | 挂载、resize/DPI、隐藏、设备丢失、解除和最终销毁 |

首版以相同工具链构建的可信 C++/pybind11 扩展接入，注册入口校验 SDK/ABI 版本并保留
模块寿命。原生回调不能跨边界抛异常，也不能在绘制时隐式调用 Python。不做任意 DLL
搜索或热卸载；未知扩展不直接回退为 button/text 来掩盖错误。

现有九类控件也迁移到相同注册表，避免内置与自定义使用两套输入/布局实现。Canvas
仍是已有特殊来源节点，自定义控件可在组合树中承载它；不把每个新控件包装成另一个
FrameProvider，也不改变当前每卡一个 Canvas 的限制，除非单独完成多画布合成改造。

## 自定义动画：事件钩子即可，不限定运动算法

核心提供事件、时钟和属性更新入口。用户可实现弹簧、关键帧、惯性、翻转、路径、噪声
或完全即时更新；现有 Spring 只是默认插件，允许每 Card/控件替换或移除。

拟议钩子按需订阅，不给所有节点强制增加高频 Python 回调：

- 生命周期：mount、unmount_request、detached、visibility、resize、dpi_changed。
- 输入/状态：pointer_enter/leave/down/move/up/cancel、focus_in/out、pressed_changed、
  selected、value_changed、enabled_changed。
- 布局：layout_target_changed，包含旧呈现状态、旧/新布局目标及版本。让用户决定如何
  从当前位置到新位置，而不是由框架先执行固定回弹。
- 帧：显式订阅的 frame 回调，提供单调 time、真实 dt、帧序号；返回是否还需下一帧。
  未订阅/已停止时没有空转回调。该钩子是渲染更新机会，不冒充显示器已经扫描显示。

可更新的视觉属性包括呈现偏移、scale、opacity，以及已声明可变的效果参数。布局目标
与呈现状态分开，避免每帧修改宽高触发布局循环。输入命中和 Canvas source 坐标使用
实际呈现变换的逆变换；文字编辑、焦点及无障碍边界不能停留在旧位置。

| 使用方式 | 执行与限制 |
| --- | --- |
| Python 事件/帧钩子 | 在 App/poll 线程调用，适合业务联动与用户算法；帧钩子为主动选择，不能保证 GIL 被阻塞时继续执行 |
| C++ 原生钩子 | 使用同一事件语义和属性目标，由原生循环驱动；适合连续拖动、跟手及持续效果，不需每帧经过 pybind11 |

同一属性一个时刻只有一个写入者。默认动画可被用户处理器替代；重定向时把当前
呈现状态、速度和剩余状态交给处理器决定，不擅自从旧起点重新播放。停止、取消和
完成通知只发一次，通知携带节点代次，迟到事件不得修改删除后复用同名的新节点。

输入事件沿 Group preview → 控件 → Card → Group bubble → Workspace 路由。用户
消费后可取消默认动画/交互；资源释放、失焦和取消捕获仍须执行。退出动画可以延迟
视觉移除，但节点立即退出输入/焦点环；必须允许超时或立即删除，不能令退休永久等待。

允许纯 Python 使用者在事件钩子中立即设置属性，不要求编译 C++。原生钩子是性能
选项，框架不引入必需的动画解释器。系统 reduced_motion 仍应传给钩子，并由宿主提供
明确的强制即时/降级策略，避免用户处理器让可访问性设置失效。

## 性能与正确性约束

1. **共享程序，不共享实例状态。** Shader 按代码/编译变体/设备缓存；改变数值只更新
   常量缓冲，不重新编译。实例/参数更新不能影响别的节点。
2. **无效果保留直接绘制。** 只有需要采样自己或局部背景的节点才隔离到纹理。局部纹理
   按真实区域及采样外扩分配，由同设备池复用；不为每个控件创建窗口、设备或独立线程。
3. **按依赖更新。** 桌面变化、父级前缀内容变化、Canvas 帧序号、几何、交互、参数和
   用户声明的时间依赖分别标记。无时间依赖且输入未变的效果不持续执行。
4. **采样边界正确。** 每个 pass 声明所需输入区域；链式效果反向传播范围，不能始终只
   使用控件原矩形，也不能把两道连续采样简单当成二者最大半径。现有 Liquid 组合中的
   原图折射与未位移模糊分支需按实际依赖迁移，不凭名称推断其采样拓扑。
5. **不默认复制整卡。** 少量颜色运算尽量内联；需要 blur/shadow 才付出相应离屏代价。
   对不能融合的多点采样保留必要 pass，不承诺“独立效果器零开销”。
6. **轻量参数通道。** 属性变更按版本合并，原生帧内只读取已验证状态；效果参数动画
   不反复提交完整 Workspace，也不在每帧创建 Python 图节点。布局改变仍走结构快照。
7. **线程与租约。** 复用现有 Glass STA/Compositor 工作线程的职责，资源只在对应执行域
   使用。Canvas 的已有消费者统一管理帧租约，效果读取不得额外抢占单消费者通道。
8. **视觉变形与命中一致。** 布局级 scale/translation 有确定逆映射；交互内容的任意
   Shader 几何扭曲需要自定义解析命中映射。没有映射时首版拒绝这种绑定，背景折射不影响内容命中。
   根 Card 的呈现变换还需同步实际 HWND 边界/区域及裁剪，不能只缩放纹理而让窗口截断效果。
9. **颜色与透明度契约。** 明确工作色域及预乘 alpha，禁止将法线/位移数据当颜色转换。
   不在扩展首版顺便承诺 HDR 或跨 GPU 零拷贝能力；遵循现有图像格式和设备约束。
   颜色/原始数据纹理区别及透明度约定参考 [Skia SkSL 文档](https://docs.skia.org/docs/user/sksl/)。
10. **更新及删除原子化。** 新效果/控件准备失败保留旧状态并报告错误；移除或替换后等
    场景确认、相关帧租约及 GPU 使用结束才回收资源。取消捕获/失焦先于最终销毁。

自定义 HLSL 与原生扩展是可信应用代码，限制输入数量、绑定、格式、尺寸和显存预算
有助于控制错误，却不等于浏览器级 Shader 沙箱。首版不让网页消息直接注册/编译 Shader，
也不增加像素读回 API。能力验证集中在原生注册及状态变更边界，Python 保留轻量便捷校验。

## 拟议使用形态

**以下为方案伪代码，当前不可直接运行。** 名称可在实现阶段调整，但职责不应变化。

```python
# 效果可附着到 Card 和各个控件；每次附着得到独立状态。
card = GlassCard(
    "player", "Player",
    effects=[LiquidGlass(target="background", refraction=18)],
    controls=[
        GlassControl("play", "button", "Play",
                     effects=[LiquidGlass(target="background", refraction=6)]),
        CustomControl("gain", type=gain_knob_type, value=0.5,
                      effects=[ShaderEffect(glow_program, target="overlay", intensity=0.2)]),
        GlassCanvas("preview", existing_canvas),  # 也可为已有 WebView
    ],
)

# 事件只传状态；动画函数由应用定义，可即时更新，也可订阅后续帧。
def on_hover(event):
    if event.entered:
        event.node.request_frames(my_animation_tick)
    else:
        event.node.cancel_frames(my_animation_tick)

def my_animation_tick(frame):
    state = my_animation_algorithm(frame.time, frame.dt, frame.node.visual_state)
    frame.node.set_visual(opacity=state.opacity, scale=state.scale)
    frame.node.set_effect_parameter("glow", "intensity", state.glow)
    return not state.finished

# 可复用 Card 类型是组合/模板，不需要另写窗口跟踪或重新实现 WebView。
class PlayerCard(CardComponent):
    def build(self):
        return make_player_card(self.player, effects=self.effects, hooks=self.hooks)
```

自定义 EffectType 与 ControlType 在可信原生扩展中注册，Python 拿到类型句柄后只构造
描述及处理事件。只有自定义组合/动画业务逻辑时，不要求用户编写 C++。

## 实施顺序与验收

| 阶段 | 工作 | 必须通过的验收 |
| --- | --- | --- |
| 1：原生节点契约 | 把九类内置控件的 measure/paint/hit/focus/semantics 抽到小型注册表，区分节点几何、样式及呈现状态 | 原有行为和无效果绘制性能不回退；Tab、拖动、Canvas/WebView 及无障碍映射保持一致 |
| 2：事件/帧扩展 | 增加状态与布局目标钩子、可选 Python/C++ 帧钩子及视觉属性增量提交，迁出现有固定 Spring | 自定义入场/悬停/退出可替换默认动画；停止后不空转；持有 GIL 时原生动画继续 |
| 3：独立效果 | 提取 Liquid/Frosted，增加每节点绑定、局部背景及内容/装饰输入；保留直接绘制路径 | 同卡多个按钮/旋钮使用不同效果，调一个不改变其他；局部背景含先前 Canvas 内容且没有自采样反馈 |
| 4：用户扩展 SDK | 发布带版本的 ControlType/EffectType 注册入口、HLSL 包规范、Card 组合/模板接口 | 独立扩展在不修改框架 kind 分支的情况下新增旋钮控件与自定义 glow，继承焦点/事件/语义 |
| 5：动态寿命与迁移 | 动态增删、效果替换、默认主题实例化、旧 GlassBackground 兼容转换、设备重建 | 重复增删及快速切换无旧事件串线/显存增长，模块存活至消费者解除，不改 FrameProvider 协议 |
| 6：示例与文档 | 新增 effects_controls 示例和 SDK/动画钩子指南；原混合示例回归 | 一个 Card 内展示自定义控件、不同效果、用户动画和 WebView；CPU/GPU 来源均可运行 |

阶段1先保证没有额外通用框架开销；阶段2让动画扩展不依赖全部效果功能完成。阶段3的
局部背景与 Canvas 合成是实质改造，不能用只添加 effects 字段的占位实现替代。

迁移时旧 GlassBackground(kind="liquid"/"frosted") 转换为相应背景效果，corner_radius
转到节点形状，solid/transparent/Mica 保持来源/回退语义。显式新 effects 与旧 background
同时出现时应明确报冲突，不能出现两个优先级不清的控制面。现有动画作为可选默认钩子
维持外观兼容。公开新接口时升级 Glass 控制协议，保持 FrameProvider 协议与浏览器安全协议不变。

验收数据要分别记录无效果基线、12卡及不同活动控件数的 CPU 时间、GPU 时间、显存、
Shader 编译次数、pass/重绘次数与输入响应；测试静止、悬停、持续动画、窗口移动、DPI/
resize、频繁增删、设备异常及 GIL 阻塞。GPU 计时用异步查询，不能通过每帧同步读回来测性能。
这里不预先承诺尚未实测的 FPS、显存上限或开销百分比。

本轮产物为调研和方案；不宣称新增效果器、控件注册或动画钩子已经可用，也不改变当前
Windows-only 图形支持范围。
