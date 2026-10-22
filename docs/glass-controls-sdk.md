# Glass 原生控件 SDK 与动画钩子

日期：2026-10-22。Windows，Glass 控制协议 **7**，Control 扩展 ABI **3**；帧协议1、浏览器安全协议2不变。

第一批提供原生控件注册、Python Card组合、输入钩子、按需帧回调和控件墨水透明度增量。
第二批已增加独立 EffectType、真实离屏 GPU 效果链、原生 Shader 扩展及参数增量；
完整 API、阶段语义与运行命令见[图像效果 SDK](glass-effects-sdk.md)。
现已支持可替换Card布局动画和[原生帧钩子](glass-native-motion.md)、
[私有实例与局部容器](glass-containers.md)、[HLSL包](glass-shader-packages.md)。
任意内容变形的逆向命中映射不在当前 API 内。

## 设计参考

本轮用Exa完成5次有效搜索，每次请求5条候选，共请求25条（含重复，不等于审阅25份文档），
另外读取4份官方页面。参考其职责划分，没有增加Qt/Flutter/WinUI/WebView运行时依赖：

- [Apple WWDC25 UIKit新设计](https://developer.apple.com/videos/play/wwdc2025/284/)：
  浮动控件、按功能分组、同心轮廓及清晰内容层。示例旋钮沿用卡片轮廓与强调色，保留可读刻度。
- [Qt自定义渲染节点](https://doc.qt.io/qt-6/qtquick-scenegraph-customrendernode-example.html)：
  原生节点可直接参与绘制，离屏纹理有代价。九种内置控件和扩展控件统一注册分派，
  不给每个控件新增窗口、线程或FrameProvider。
- [Microsoft Composition effects](https://learn.microsoft.com/en-us/windows/apps/develop/composition/composition-effects)：
  效果定义和独立实例分开。效果器实现见独立 SDK；本页的墨水透明度接口继续保持轻量。
- [Flutter CustomPainter](https://api.flutter.dev/flutter/rendering/CustomPainter-class.html)：
  绘制、命中、语义分别声明，按需重绘可避开build/layout。本项目由同一ControlType提供
  这些行为，帧回调提交不重建场景。

下面的D3D11工程实现是本项目的取舍，不是上述项目的性能保证。

## 构建、运行

在仓库根目录使用Windows x64、Python 3.10+、pybind11、MSVC C++17。
扩展和运行时须使用相同MSVC版本、架构、CRT、Debug/Release和迭代器调试模式。
本地示例采用当前`.venv`的Python、Release `/MD`。

```powershell
$env:COMFORTABLE_TUI_BUILD_GLASS='1'
.\.venv\Scripts\python.exe -m pip install --no-build-isolation -e .
.\.venv\Scripts\python.exe examples/glass_workspace/native_controls/setup.py build_ext --build-lib .build/glass-controls --build-temp .build/glass-controls-temp
.\.venv\Scripts\python.exe -m examples.glass_workspace.controls
```

默认发现`.build/glass-controls/ctui_glass_controls*.pyd`，也可传`--extension <完整pyd路径>`。
示例附加到实际运行终端。F6进入卡片、Tab遍历、旋钮支持点击和左右键、Esc返回终端。
“Animate A readout”展示使用者自行编写的阻尼函数。
`MixerCard`是Python组合模板；`example.dial`是另一个编译模块中的原生控件。
多个MixerCard共享类型，独立保存value、焦点和呈现状态。

## 注册类型与绘制

公共头为`native/glass/include/comfortable/glass_control.hpp`，模型在`glass.hpp`。
完整代码：`examples/glass_workspace/native_controls/controls.cpp`。

```cpp
const GlassControlType types[] = {{
    "example.dial", 3, true, 0x33, "Adjust",
    measure, paint, hit, input, accessible_value, create, destroy, update
}};
const GlassControlExtension extension = [] {
    GlassControlExtension e;
    e.types = types;
    e.count = 1;
    return e;
}();
// pybind11导出glass_controls()，返回指向extension的命名capsule。
```

```python
import ctui_glass_controls
from comfortable_tui import GlassControl, register_glass_controls
register_glass_controls(ctui_glass_controls)
dial = GlassControl("volume", "example.dial", "Volume", value=.5)
```

| 回调/字段 | 契约 |
| --- | --- |
| measure(control) | 返回16..1056逻辑像素行高，默认空模型也必须有效。无副作用，可在提交线程及原生UI线程调用。Python预检和原生布局均使用提交模型；不要依赖input会同步修改的value/text决定高度 |
| paint(painter,control,state) | 同一Glass STA直接绘制，不持有painter、不调用Python。state包含hover/focus/pressed/DPI/禁用后opacity/accent/reduced_motion |
| hit_test(control,x,y,width,height) | 行局部逻辑坐标，不依赖paint已经执行；限制在行矩形内，示例只命中圆形 |
| input(control,event) | 原生操作；activate为点击释放、Enter/Space或无障碍默认动作，key为虚拟键。返回none/click/change，change进入原有确认序号机制；类型负责值域，运行时拒绝非有限value |
| focusable | 加入Tab和控件遍历；disabled不可聚焦 |
| accessibility_role/value/default_action | MSAA角色、值与动作；边界和命中使用同一几何，非交互控件不冒充可聚焦 |

`GlassPainter`提供fill/text/line/circle，坐标相对控件左上角，尺寸为逻辑像素。
绘制限制在局部矩形内，不用视觉越界扩大命中。create/destroy/update为可选实例钩子，
无私有状态时三者可省略；契约与例子见[局部容器与实例](glass-containers.md)。
自定义标题栏或任意变换尚未提供。Shader注册与效果器设备资源生命周期见独立效果SDK。
不要用Python paint回调或每控件独立窗口绕过这个边界。

扩展是**可信本机代码**，不是浏览器沙箱。注册验证ABI、描述结构、指针宽度、MSVC、
迭代器调试、CRT、类型名、角色、必需回调与默认高度。每批最多32类型，批次原子提交；
类型不可覆盖/卸载。运行时保留模块及capsule；描述表和字符串必须静态存活。
显式导入后注册，不扫描插件目录、不自动加载DLL。

## 事件、帧和呈现

非Canvas控件新增pointer_enter/leave/move/down/up/cancel，与现有焦点、change/click采用
Group预览→局部容器预览→Control→局部容器冒泡→Card→Group冒泡→Workspace路由；坐标为节点局部逻辑像素。
这是输入通知，原生编辑/input默认动作同步执行，不承诺Python预览能回滚。
Card拖动/关闭仍使用原有可拦截默认行为，Canvas/WebView保持CanvasEvent。

```python
def animate(frame):
    done = frame.reduced_motion or frame.elapsed >= .3
    workspace.present(card, readout, opacity=1 if done else .3+.7*frame.elapsed/.3)
    return not done

request = workspace.request_frames(animate, node=readout)
request.cancel()  # 幂等，同时释放回调及节点引用
```

- callback只在App/poll线程执行。没有订阅时不生成回调；返回严格True才继续，新订阅
  下一poll开始。异常取消该订阅并向调用方传播。原生绘制不等待这些Python回调。
- GlassFrame包含单调time、真实dt、首次调用起的elapsed、从0起的index、reduced_motion。
  首帧dt=0，不假定16ms、不截断dt。
- 隐藏/终端页不可用时暂停，绑定节点在原生报告所属卡片隐藏时也暂停，恢复首帧dt=0。elapsed是墙钟经过时间，含隐藏时段。
  系统/主题减少动态效果时只给一次回调然后停止，使用者应据flag提交最终值。
- node按对象身份绑定，移除/替换自动取消；关闭工作区取消全部。无node回调自行管理目标。
- present(card,control,opacity=...)接受对象实例与0..1有限值，只更新控件墨水。原生合并
  同节点积压更新，最多384目标，与普通命令队列分开。不configure、不布局、不分配纹理。
  完整模型后续提交保留Python模型当前opacity；几何、命中、语义不变。
- Canvas是独立合成层，present拒绝其opacity，避免只淡出标题却保留画面。
  原生GlassMotion可以统一控制Canvas视图与标题的呈现。Card布局目标/透明度、
  原生帧订阅和效果参数更新见[原生动画SDK](glass-native-motion.md)。

模型与事件带节点实例代号，同ID替换后旧事件、增量和未确认原生值不能写入新实例。
不要复制私有代号或伪造事件。协议6及更早的原生模块与Control扩展必须重编为协议7/ABI3。

## 验证

```powershell
$env:COMFORTABLE_GLASS_EXTENSION='.build/glass-controls'
.\.venv\Scripts\python.exe -m pytest tests/test_glass_extensions.py tests/test_glass_native_extensions.py -q
$plugin=(Get-ChildItem .build/glass-controls/ctui_glass_controls*.pyd | Select-Object -First 1).FullName
.\.venv\Scripts\python.exe -m examples.verify_glass_controls --extension $plugin --report .build/glass-controls-native.json
```

第二条使用自己的Qt夹具窗口，不是假装终端附加。真实终端示例可增加
`--smoke-seconds 6 --report .build/glass-controls-terminal.json`，确认卡片实际属于同一终端HWND。
CI构建独立扩展并执行无GPU注册测试；桌面交互检查在本机执行。
