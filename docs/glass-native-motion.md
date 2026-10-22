# Glass 原生动画与布局目标钩子

2026-10-22。Windows；Glass 协议 **7**、Motion ABI **1**、Control ABI **3**。
效果 ABI 仍为2，FrameProvider 为1。旧 Glass 模块和控件插件需要一起重编。

`GlassMotion` 是每个 Card/控件独立的原生动画实例绑定。应用提供 C++ 回调，
框架提供输入状态、布局目标、真实时钟、调度与呈现；没有动画 DSL 或强制曲线。
没有绑定时，Card 继续使用原来的弹簧。Python `request_frames` 保持可用，
适合业务回调；需要脱离 GIL 的动画使用本接口。

## 运行完整示例

在仓库根目录、Windows x64、MSVC C++17、Python 3.10+、匹配的 pybind11 环境运行：

```powershell
$env:COMFORTABLE_TUI_BUILD_GLASS='1'
python -m pip install --no-build-isolation -e .
python examples/glass_workspace/native_controls/setup.py build_ext --build-lib .build/glass-controls --build-temp .build/glass-controls-temp
python -m examples.glass_workspace.advanced
# 可选真实浏览器；未指定路径时使用 BrowserService 的运行时发现规则：
python -m examples.glass_workspace.advanced --webview --render-mode gpu
```

默认附加运行它的真实终端；F6进入卡片、Tab遍历、Esc回终端。
两个 InstrumentCard 展示原生旋钮、局部容器、自定义 HLSL、独立 overlay、普通 Canvas；
`--webview` 将第二张卡的画布换为已有 WebView。按钮可重播原生效果动画或改变布局目标。
CEF 是可选内容来源，不负责绘制原生 UI。

## Python 绑定

```python
from comfortable_tui import GlassMotion, register_glass_motions
import ctui_glass_controls  # 应用显式导入可信模块
register_glass_motions(ctui_glass_controls)
card.motion = GlassMotion("example.ease", (14,))
control.motion = GlassMotion("example.ease")  # 省略尾部参数使用类型默认值
workspace.update()                         # 类型/绑定改变才提交场景
workspace.restart_motion(card, control)    # 原生重播，不提交场景、不重建实例
```

`GlassCanvas`、`GlassWebView`、`GlassContainer` 接受相同 `motion` 参数。
绑定对象被移除或同ID替换后，旧对象的 restart 会被拒绝；原生命令还检查代次。
一个节点至多一个原生动画实例；组合多个效果/曲线应由该回调统一处理。

## C++ 契约

公共头 `comfortable/glass_motion.hpp`。完整可编译例子是
`examples/glass_workspace/native_controls/controls.cpp` 的 `example.ease` 和 `example.pulse`。

```cpp
struct State { float speed; };
void *create(const float *p, std::size_t) { return new State{p[0]}; }
void destroy(void *p) { delete static_cast<State *>(p); }
bool frame(void *p, const comfortable::GlassMotionFrame &f,
           comfortable::GlassVisual &v) {
    auto &s = *static_cast<State *>(p);
    float k = f.reduced_motion ? 1.f : float(1-std::exp(-s.speed*f.dt));
    v.x += (f.target_x-v.x)*k;
    v.y += (f.target_y-v.y)*k;
    bool done = std::abs(v.x-f.target_x)+std::abs(v.y-f.target_y) < .04f;
    if (done) { v.x=f.target_x; v.y=f.target_y; }
    return !done;
}
// GlassMotionType{name, 1, schema, count, create, destroy, frame}
// 模块 glass_motions() 返回 GlassMotionExtension 的静态 capsule：
// comfortable.glass.motions.v1
```

需要 `<cmath>`。类型表、schema、字符串静态存活；宿主固定模块引用。
同 MSVC、指针宽度、CRT、Debug/Release、迭代器调试模式；每批至多32个类型，
名称不可覆盖，参数至多16个且在原生注册/绑定边界校验范围和默认值。

| Frame 字段 | 含义 |
| --- | --- |
| time / dt / elapsed / index | 单调秒时钟、实际帧间隔、从首次调用算起的墙钟时间、从0开始的索引 |
| card | 当前实例是否属于 Card |
| target_x/y | Card：相对宿主左上的布局目标，逻辑像素；控件：局部偏移目标0 |
| layout_x/y | Card：当前布局目标；控件：未变换布局框在 Card 内的位置 |
| width / height | 当前节点布局尺寸，逻辑像素 |
| pointer_x/y | 节点局部指针坐标，不保证指针在节点内；用 hovered 判定 |
| visible / exiting | 是否显示；Card 隐藏转场时的退出状态 |
| hovered / focused / pressed | 输入状态；容器的 hovered/focused 包含后代 |
| reduced_motion | 系统/主题的减少动态效果请求 |
| changes | 目标/尺寸/布局1、悬停2、焦点4、按压8、可见性16、初始/重播32、激活64、减少动态设置变化128 |
| effects / effect_count | 当前节点效果列表的临时写入接口与数量 |

`GlassVisual.x/y`：Card 为相对宿主的实际呈现位置；控件为布局框上的额外偏移。
`opacity` 为0..1。父控件的平移/透明度传给后代。绘制、Canvas视口、命中和MSAA位置
使用同一平移结果，Card 则移动真实 HWND。支持平移和透明度，未提供 scale/rotation、
任意 Shader 变形的逆命中或窗口外扩。控件布局变化时可用 layout_x/y 的差值补偿位置，
示例 ease 展示了这种重排动画。

## 调度、效果参数和寿命

- create/update/frame/destroy 的节点状态位于 Glass UI 工作线程；绘制 GPU 效果仍在
  Compositor 渲染线程。不调用 Python/GIL，不阻塞、重入关闭或保存临时接口指针。
- 返回 true 请求下一帧，false 在状态变化或 restart 前休眠。指针/激活通知被锁存，
  不会因为按下/松开发生在两帧之间而漏掉唤醒。隐藏时给一次回调后暂停，Card 退出可继续。
- 首帧、restart、隐藏后恢复的 dt=0；elapsed 包含隐藏时间。减少动态时给一次回调再停，
  应直接提交最终视觉状态。设置变化会唤醒已经停稳的实例。
- `f.effects->set(index, values, count)` 只写当前节点既有效果参数，索引对应完整 effects
  列表。schema在原生边界校验；回调成功后合并到既有链并 invalidate，不上传像素、
  不重新提交场景。不能改变类型、阶段、颜色或添加效果。该接口不是无CPU分配承诺。
- 动画参数是原生呈现状态；Python描述不逐帧回读。之后完整模型提交会重新使用Python
  描述，回调可在同一帧再次覆盖。一个参数应指定一个写入者，避免Python与原生争写。
- 同对象、同类型快照/resize保留实例；参数绑定改变则重建动画，restart不重建。
  移除/替换/关闭先取消输入，再释放节点资源。DPI/设备重建可能重建实例。
- 非有限坐标、超界透明度及回调异常会报告错误并走失败清理；可信本机代码不是沙箱，
  不承诺恶意指针、驱动错误或任意扩展回调的事务回滚。

## 自动验证与设计依据

```powershell
python -m examples.verify_glass_completion --webview
ctest --test-dir .build/glass-native -C Release --output-on-failure
```

验证包含GIL阻塞、停稳休眠、restart、私有状态、窗口resize、嵌套输入与真实CEF；
夹具会显示自己的窗口并自动关闭，独立于真实终端示例。
记录及边界见[验证报告](validation/glass/completion.md)。

Exa核对的参考：Microsoft [Composition animation](https://learn.microsoft.com/en-us/windows/apps/develop/composition/composition-animation)
和 [Time-based animation](https://learn.microsoft.com/en-us/windows/apps/develop/composition/time-animations)
说明独立呈现调度与可复用动画定义；本实现使用自己的 C++ 回调。
Card透明度通过 [IDCompositionEffectGroup.SetOpacity](https://learn.microsoft.com/en-us/windows/win32/api/dcomp/nf-dcomp-idcompositioneffectgroup-setopacity(float))
更新DComp呈现，不对窗口不存在的 SetOpacity API 作假设。
