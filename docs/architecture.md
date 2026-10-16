# 架构与动画

## 数据流

```text
Terminal / host
   ↓ decoded Key / Mouse / Paste / Capability
App.dispatch → focused Widget → ancestor handlers
   ↓ callbacks change model / animation targets / invalidate
target layout → Animator.step(dt) → projected layout
   ↓
Widget.draw → clipped Canvas + optional PixelCanvas placements
   ↓
Renderer: previous frame vs current frame → dirty spans → ANSI
KittyEncoder: changed RGB frame → zlib/base64 chunks
   ↓
App.encode → synchronized transaction when negotiated → one terminal write
```

## 保留控件树

`Widget` 长期保存状态，拥有子控件和自己的动画值。一个控件只能有一个父节点或宿主。
`mount(app)` 注册动画；`unmount()` 递归释放。PageStack 的页面在退场完成后才移除。
没有全局动画注册表，因此销毁应用和重复打开页面不会遗留活动动画。

表单焦点由 App 的共享高亮绘制：四条边分别追踪目标，在文字已绘制的画布上做覆盖率
混色。控件默认继承父画面底色，避免退焦后留下深色矩形。ListView 与 Tabs 自行呈现选择，
不额外叠加表单高亮。

`visible=False` 从布局和交互中移除；`disabled=True` 保留显示但阻止交互。
`find(id)` 搜索整个子树，未找到时抛 `KeyError`。动态修改树用 `add/remove`。

## 帧时钟

App 默认目标 60fps，允许 1–240。`run()` 使用 `time.monotonic()`；每轮更新定时器、
线程回调和 tick。只有 dirty 或存在活动动画时才创建并编码画布。tick 本身必须轻量。
静止时仍按帧间隔读取输入和检测终端大小，但不产生帧缓冲或传输数据。

一次渲染先布局，设置动画目标；统一推进一次动画；再投影当前位置并绘制。
原生扩展不改变业务、布局或动画语义。`render(dt=...)` 提供可确定的测试时钟。
真实运行的单帧 dt 上限为 0.25 秒，避免暂停恢复后视觉突变；直接 `Value.step()` 与
无终端 `render()` 不采用此上限。

## 逻辑几何与动画几何

控件的 `rect` 为本帧可见单元格区域。HBox/VBox/Grid 先分配整数目标几何。
ListView 按固定行高或正在变化的插入/删除高度构建逻辑偏移，再从 `scroll.current`
投影到屏幕。选中条目保持逻辑索引，光标追踪逻辑 y；中间动画值不会改变选择判断。
PageStack 用整个页面的弹簧偏移做平移；输入始终路由到栈顶。

Collapsible 的 reveal 与列表插入/删除属于刻意改变占位高度的动画，允许兄弟布局随之
重排。Stack 的一般宽高变化目前直接重排，并未为每个控件边框自动添加弹簧。

## Value

```python
from comfortable_tui import Value

x = Value(0, speed=18, damping=1)
x.set(100)
x.step(1 / 60)
x.set(20)     # 速度保留
x.kick(2)    # 注入速度，让它偏离目标再回来
x.snap()     # 直接完成
x.jump(0)    # 同时改当前位置、目标，清空速度
```

`speed` 必须大于 0，单位为 rad/s（弹簧）或 1/s（指数趋近）。阻尼非负。
位置误差 < 0.001 且速度绝对值 < 0.01 时吸附到目标，停止动画。
`active` 表示仍有位移或速度；`Animator(reduced_motion=True)` 直接 snap。

Tabs 同时维护标题选中条和各页偏移。离场页继续绘制，只有选中页接收焦点/鼠标。
快速重新选择仍在移动的页面时保留速度，未在场的页面才从边界进入。

自定义控件用 `self.value(...)` 自动登记所有权，通常只需设置目标，勿自行重复 step。
独立 Value（如像素示例）需要调用方自行推进。

## Canvas 与差分

每个 `Cell` 包含 grapheme、Style、显示宽度。双宽字符后半格用 width=0 标记；写入其
任何一半会清理旧字形。`regex` 按 `\X` 分段，`wcwidth` 估算终端显示宽度。
画布文本清除终端控制字符；不接受内嵌 ANSI 作为样式。

Renderer 保存帧快照，按行找出不相等的连续区间。对双宽尾格的差异会回退到完整字形。
每个区间先移动光标，只有 Style 变化时才发 SGR；不每帧清屏。缩放或 invalidate 强制
完整刷新。`encode()` 相同帧返回空字符串。应用层将文本与图形合并为一次输出。

`Canvas.clipped(rect)` 可嵌套，退出自动恢复。跨裁剪边界的双宽字符整字跳过，避免半个
汉字。图像协议没有对应的字符级 scissor，部分离屏 PixelCanvas 自动改用 braille。

## 输入、焦点和模态

输入解析器保留不完整转义序列、UTF-8 分片和粘贴边界。App 首先处理全局绑定和 Tab，
再将事件交给焦点节点，未消费的事件沿父链向上。鼠标从顶层往里 hit-test；按下后
捕获到相应控件，松开在外面不会误激活按钮。

Tabs 只暴露活动内容的焦点；PageStack 只暴露栈顶；modal 建立单独焦点域。
modal 关闭恢复原焦点。Button 和 ListView 的 Enter 支持协议提供的松开确认。
基础输入框与编辑器按压/重复产生文本或移动光标。

## 并发与异常

UI API 在 UI 线程使用。`run_worker` 的结果由线程安全队列转回主线程。
定时器使用应用逻辑时钟；错过多个周期只运行一次，避免恢复后的回调风暴。
异常默认传播并触发终端模式恢复。Worker 异常交给 on_error，未提供时显示通知。
`with App(...)` 保证退出时卸载树；运行中的工作线程需任务自行结束。
