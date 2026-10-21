# Glass 图像效果 SDK

2026-10-21。Windows / D3D11；Glass 控制协议 **6**，效果/控件扩展 ABI **2**。
Canvas 帧协议仍为 1，浏览器安全协议仍为 2。重编运行时和使用 Glass 头文件的原生扩展。

效果器是 Card/控件的图像处理服务。它不负责打开浏览器、创建数据源或消费第二份
FrameProvider；现有 Canvas/WebView 在合成器取帧后把 SRV 交给效果链。
运行时没有新增 WebView、Qt 或 Python 绘制回调依赖。自动化夹具使用 PySide6 创建自己的测试宿主。

## 运行示例

```powershell
$env:COMFORTABLE_TUI_BUILD_GLASS='1'
.\.venv\Scripts\python.exe setup.py build_ext --inplace
.\.venv\Scripts\python.exe -m examples.glass_workspace.effects
# 持续自有窗口演示（不是终端附加）：
.\.venv\Scripts\python.exe -m examples.glass_workspace.effects --preview
# 另一个编译模块提供 example.negative，显式导入注册，无 DLL 扫描。
.\.venv\Scripts\python.exe examples/glass_workspace/native_controls/setup.py build_ext --build-lib .build/glass-controls --build-temp .build/glass-controls-temp
.\.venv\Scripts\python.exe -m examples.glass_workspace.effects --extension .build/glass-controls
```

示例附加到运行它的真实终端。两个 SignalCard 拥有独立 Canvas、参数和控件，
滑块只改变当前卡的图像效果。“Animate A glow”使用已有按需帧钩子实现用户自己的动画。

## Python 接口

```python
from comfortable_tui import GlassCard, GlassControl, GlassEffect

readout = GlassControl("readout", "text", text="Ready", effects=[
    GlassEffect("blur", parameters=(1,)),
    GlassEffect("glow", target="overlay", parameters=(.8, 10), tint=0x85E5D1),
])
card = GlassCard("player", "Player", [readout], effects=[
    GlassEffect("liquid", target="background", parameters=(18, .35)),
])
# 工作区运行后；每个参数更新都是完整向量，省略的尾部参数恢复 schema 默认值。
workspace.set_effect_parameters(card, readout, 0, [2])
```

`GlassEffect(type, target="content", parameters=(), tint=0xFFFFFF, opacity=1)`
是不可变描述，可供多个节点复用。原生实例仍逐节点独立。参数更新通过替换目标节点
列表中的描述完成；不会改写其他节点共享的 Python 描述。参数 schema 由 C++ 注册表定义，
使用 `_native_glass.effect_schema(name)` 查询；Python 只检查字符串、大小和有限数，
数值范围/默认值的权威校验在原生边界完成。

| 类型 | 参数顺序、范围、默认值 | 图像操作 |
| --- | --- | --- |
| tint | amount：0..1，.35 | 把输入颜色混向 tint，保持输入 alpha |
| blur | radius：0..24，6 | 半径以逻辑像素计；两道可分离模糊，0 为恒等 |
| liquid | refraction：0..48，12；dispersion：0..3，.35 | 对采样背景做圆角 SDF 边缘折射和色散 |
| glow | intensity：0..4，1；radius：0..64，18 | 模糊 alpha 后在原内容下合成指定 tint 的辉光 |

`opacity` 是 tint/glow 的混合强度，可信扩展也会接收到该值。
blur/liquid 首版要求 opacity=1，避免没有落实的混合控制。没有额外的
`blur/refraction/radius/progress` 同名字段；这些值只从参数向量读取。
未知类型、超范围参数、非有限值和超长参数向量会被拒绝。

### 三个阶段的边界

1. **background**：控件读取自身绘制前已完成的前缀合成图，含背景和此前的 Canvas 层；
   Card 读取桌面采样材质。背景效果不会扭曲文字、Canvas 内容或输入命中。
2. **content**：普通控件处理自身行的墨水（含焦点/悬停装饰）；Canvas/WebView 处理帧图像，
   不改变标签。Card 在自己的全部层完成后处理整卡合成图，包含子控件和材质。
3. **overlay**：在 content 链后处理完成的节点图像，适合 glow 等后处理装饰。
   它是效果链的末阶段；输出只替换一次，不把含原图的结果重复 alpha 混合到原图上。

不同阶段按以上顺序执行，同阶段保持列表顺序。首版没有“完全独立的透明装饰纹理”
回调；自定义 overlay 回调返回包含输入的完整结果。效果输出裁剪在当前节点边界内，
不扩大布局、命中矩形或 HWND。普通控件是整行矩形；尚未提供自定义圆角采样形状。

liquid 只允许 background。框架没有为任意内容几何扭曲提供逆向命中映射；
可信扩展也必须保持 content 的交互几何不变。鼠标、焦点、Tab 和浏览器交互仍走原路径。

Card 的 background 效果需要 liquid/frosted 采样材质，不能绑定到 solid/mica/transparent
来源。旧 `GlassBackground` 继续选择来源、形状和回退；当显式背景链存在时，
旧 blur/refraction/dispersion/tint-opacity 不再重复处理输入，形状和捕获策略仍保留。
减少透明度/高对比度或采样不可用时沿用不透明回退，停用背景链；内容链保持独立。

`GlassCanvas(..., effects=[...])` 和 `GlassWebView(..., effects=[...])` 都接受相同描述。
它们继续接受已有 CPU/GPU 来源，浏览器 cookies、权限及消息隔离规则不变。

## 原生扩展

公共头：`comfortable/glass_effect.hpp`。独立可编译示例：
`examples/glass_workspace/native_controls/effects.cpp`。
这是与宿主同架构、MSVC、CRT、Debug/Release 配置构建的可信原生代码，
不是网页 Shader 沙箱，也不提供任意 HLSL 字符串的 Python 编译入口。

```cpp
const GlassEffectParameter parameters[] = {{"amount", 0, 1, 1}};
const GlassEffectType types[] = {{
    "example.negative", 2, 0, parameters, 1, 0,
    create_device_resources, destroy_device_resources, render
}};
const GlassEffectExtension extension = [] {
    GlassEffectExtension value;
    value.types = types;
    value.count = 1;
    return value;
}();
// pybind11 模块提供 glass_effects()：
// return py::capsule(&extension, glass_effect_extension_capsule);
```

```python
import ctui_glass_effects
from comfortable_tui import register_glass_effects
register_glass_effects(ctui_glass_effects)
```

| 成员/回调 | 契约 |
| --- | --- |
| name / version | 全局唯一名称，类型 version=2；注册不可覆盖/卸载 |
| shader_id | 扩展必须为0；内置 Shader 编号不可伪装 |
| parameters | 最多16个 schema，name/min/max/default；描述表、名字和字符串静态存活 |
| sampling_radius | 0..128逻辑像素，声明此 pass 的最大依赖半径；链式半径相加 |
| create_device_resources(device) | 同一渲染线程首次绘制时调用；返回实例资源，失败返回 nullptr |
| destroy_device_resources(resource) | resize、设备替换、detach、关闭时在该合成器线程释放；不得抛异常 |
| render(resource,paint) | 同一渲染线程，无 GIL/无 Python；只绘制，不 Present、消费帧、保存借用指针或操纵窗口 |

`GlassEffectPaint` 提供 context、input SRV、output RTV、输入输出大小、原始输入坐标原点、
dpi、tint、opacity 及已填充 schema 默认值的完整参数向量。输入/输出是同尺寸的局部
扩展 patch，包含依赖边缘。每次调用输出先清零，D3D 状态清空；扩展需绑定自己
使用的 VS/PS、常量、viewport、blend、sampler 等状态。示例提供完整负片 Shader。

输入/输出纹理不同，采用 BGRA8、预乘 alpha、当前 UNORM 工作空间；不承诺线性光 HDR。
回调不能保存借用的 context、SRV、RTV 和参数指针供异步线程使用；返回 true 表示已发出
绘制命令，false/异常会报告原生渲染错误并进入现有失败关闭路径。提交阶段的无效描述
在替换前被拒绝；不承诺驱动丢失或已执行的恶意原生 Shader 能事务性回滚。
真实 DXGI device-removed 错误仍需应用关闭并重建工作区，自动静默恢复未提供。

注册按批次原子提交，并保留 Python 模块/capsule 至解释器结束。
类型定义与实例状态分离，内置 Shader 程序按设备共享；各节点/链持有自己的纹理和扩展
resource。扩展如需跨实例缓存只读程序，应在自己模块中按设备缓存，不得共享可变实例参数。

## 性能与生命周期

- 没有效果的卡片保持旧直接绘制路径；只有控件效果存在时才拆分墨水行并增加材质底层。
  多行引用同一墨水来源，只创建一个 FrameConsumer，Canvas 仍由其现有消费者取得一次帧租约。
- 原生参数增量合并同节点、同效果位置的积压更新；参数变化不 configure Workspace、
  不重绘 D2D 墨水、不重编 Shader、不重建效果纹理。只有效果类型/结构、来源或尺寸
  变化才走结构更新；停止动画后没有 Python 帧钩子或 GPU 效果空转。
- 链的输入、两个 ping-pong 工作面、最终输出共四个表面，半径依赖按链相加；
  content 扩展边缘为透明，background 可采样节点外已合成的前缀图像；
  超出来源纹理范围时钳制到边缘，避免模糊产生透明暗边。
  首版保守按类型最大采样半径分配。单链纹理上限64MiB，全部效果链上限256MiB，
  另受已有工作区表面预算约束。超限显式失败，不强行分配。
- detach、替换和关闭在原合成器线程释放扩展设备资源。节点实例代次过滤迟到更新；
  参数更新不会修改共享的 Python 描述。调用方继续拥有自己的 Canvas/BrowserPage。
- `effects_rendered` 统计真实 GPU draw pass，包括 crop、模糊横纵向和最终裁剪。
  它不是节点数，也不是 GPU 耗时或显示器实际帧率。首版未增加生产路径 CPU 像素读回。

## 自动验证

多卡片性能、异步GPU计时及稳定性入口见[性能与自动化验收](glass-effects-performance.md)。
内置Shader字节码在SDK模块内缓存，设备资源和每节点纹理仍独立。
私有 `_native_glass.effect_diagnostics()` 返回当前 `surface_bytes` 及累计
`surface_allocations`、`shader_compilations`、`program_sets`，只在资源创建时计数，
不在帧循环采集GPU查询。它是测试诊断入口，不是每卡驱动显存或全进程扩展资源统计。

```powershell
cmake -S . -B .build/glass-native -A x64
cmake --build .build/glass-native --config Release --target ctui_glass_effects_test
ctest --test-dir .build/glass-native -C Release -R glass_effect_pixels --output-on-failure
.\.venv\Scripts\python.exe -m pytest tests/test_glass_effects.py tests/test_glass.py -q
.\.venv\Scripts\python.exe -m examples.verify_glass_effects --report .build/glass-effects.json
# 已构建匹配的 CEF helper 时，可再验证实际 CPU/GPU WebView：
.\.venv\Scripts\python.exe -m examples.verify_glass_effects --webview --browser .build/browser-gpu/Release/ctui_browser.exe
```

WARP 测试真正执行 Shader 并读回像素，覆盖 tint、blur、glow、liquid、效果顺序、alpha、
局部背景边界、参数隔离、扩展回调、resize、设备替换、失败回调及预算拒绝。
有调试层时检查 D3D11 warning/error，读回只在测试程序内。
集成夹具验证真实合成器、无重建参数更新、静止输入、动态同ID替换和释放；
这是自有窗口验证，不冒充外部终端附加或视觉审美验收。
已有真实终端示例保留原宿主发现/跟踪方式。详见[验证记录](validation/glass/README.md)。

## 本轮核对的资料

通过 Exa 读取两份 Microsoft 官方页面：
[PSSetShaderResources](https://learn.microsoft.com/en-us/windows/win32/api/d3d11/nf-d3d11-id3d11devicecontext-pssetshaderresources)
说明输出绑定冲突会使 SRV 变空；
[D3D11 多线程](https://learn.microsoft.com/en-us/windows/win32/direct3d11/overviews-direct3d-11-render-multi-thread-intro)
要求串行访问 immediate context。本实现使用独立纹理和合成器线程执行域，
没有把 Python 提交线程接入像素处理。
