# 公共 API 参考

导入路径统一为 `comfortable_tui`。控件构造参数见[控件手册](widgets.md)。以下补充所有
基础设施与生命周期 API；下划线成员为内部实现，不承诺兼容。

## App

```python
App(root, theme=None, fps=60, reduced_motion=None,
    colors=None, synchronized=None, mouse=True)
```

`theme` 默认深色；`colors` 为 truecolor / 256 / none，None 参考 NO_COLOR。
`synchronized=None` 自动探测，True/False 强制开关。`reduced_motion=None` 读取环境变量。

| 方法 / 属性 | 合约 |
| --- | --- |
| `run()` | 独占当前交互终端直到 exit；异常时恢复终端后向外传播 |
| `exit()` | 请求退出循环 |
| `close()` | 请求退出、关闭 modal、递归卸载 root、取消未启动 worker |
| `with App(...) as app` | 离开时 close；推荐的生命周期 |
| `render(width=80, height=24, dt=1/60)` | 推进逻辑时钟和动画，返回 Canvas；dt 为有限非负秒 |
| `encode(frame)` | 返回 ANSI + 图像差分；以实际写出顺序调用，不要丢弃后续仍当已显示 |
| `invalidate()` | 下一轮请求重绘；与 renderer.invalidate 的强制全屏刷新不同 |
| `dispatch(event)` | 路由一个输入/能力事件，可用于测试和自定义宿主 |
| `focus(widget)` | 若在当前焦点域可聚焦则设置，返回 bool |
| `focus_next(direction=1)` | 按树顺序循环切换焦点；负数向前 |
| `bind(key, callback)` | 全局键绑定，回调无参数；覆盖同名绑定 |
| `show_modal(widget)` / `close_modal()` | 一层模态，替换已有 modal，关闭恢复焦点 |
| `notify(text, duration=3)` | 底行临时消息，再次调用替换并重置计时 |
| `call_later(delay, callback, repeat=False)` | 返回 Timer；重复需要正 delay，非重复可为零 |
| `call_from_thread(callback)` | 将无参数回调排队到 UI 线程 |
| `run_worker(function, on_result, on_error=None)` | 最多 4 个线程，返回 Future；完成回调排队到 UI 线程 |
| `.root / .focused / .modal / .theme` | 根节点、焦点、弹窗、主题 |
| `.animator / .renderer` | 管理动画与编码，便于诊断 |
| `.keyboard_release / .graphics_supported` | 协商后的实际能力，默认 False |

Timer：`.cancel()` 停止后续执行；`.cancelled` 可检查。重复定时器按执行后的当前逻辑
时间加 interval 排下一次，错过的周期不补执行。通知不是 modal，不抢焦点。

## Widget

```python
Widget(id="", width=None, height=None, flex=1,
       style=None, disabled=False, visible=True)
```

- `width/height`：固定单元格尺寸；None 在父布局内参与 flex 分配。
- `.rect / .target_rect`：当前分配的 Rect；一般控件二者相同。内部动画另存为 Value。
- `.parent / .children / .app`：所有权与所属应用。一个 Widget 只能拥有一个宿主。
- `.theme / .focused`：当前主题和是否正被聚焦。
- `.focusable` 类属性默认为 False；自定义交互控件设为 True。

| 方法 | 用途 |
| --- | --- |
| `add(*children)` | 追加子控件，返回 self；检测重复所有权与树环 |
| `remove(child)` | 立即卸载、释放动画、解除父节点 |
| `mount(app)` / `unmount()` | 注册/释放控件树动画，通常由 App/容器调用 |
| `walk()` | 深度优先全部节点，包含不可见节点 |
| `focusables()` | 当前子树可聚焦节点；容器可覆盖限制范围 |
| `find(id)` | 返回首个 id 匹配节点，未找到抛 KeyError |
| `measure(width)` | 自然高度，用于 ScrollView 与尺寸测量 |
| `layout(rect)` | 设置区域并为子节点布局，自定义容器覆盖 |
| `tick(dt)` | 每轮轻量更新，默认递归到可见孩子 |
| `paint(canvas)` | 绘制自身；不直接输出到终端 |
| `content_style(canvas, semantic=None)` | 语义前景继承已绘制背景；显式 style 优先 |
| `focus_rect()` / `.focus_indicator` | 共享表单高亮区域；False 由控件自行呈现选择 |
| `draw(canvas)` | 裁剪并调用 paint/孩子 draw，通常不覆盖 |
| `on_event(event)` | 返回 True 消费事件，False 让事件往父节点传播 |
| `hit_test(x, y)` | 从最上层子控件向里找交互目标 |
| `value(initial=0, speed=18, damping=1)` | 创建归此控件所有的 Value |
| `invalidate()` | 请求应用刷新 |

## Value / Animator

`Value(current=0, speed=18, damping=1, target=None, velocity=0)`。
公开状态：current / target / velocity / speed / damping；建议使用 set/jump，不随意写 current。

| API | 行为 |
| --- | --- |
| `set(target)` | 仅改有限目标值，保留速度 |
| `jump(value)` | 同时设置 current/target，速度清零 |
| `snap()` | 完成到 target |
| `kick(distance)` | 根据 speed 注入速度；指数模式忽略 |
| `step(dt)` | 推进，返回是否更新；dt 非负且有限 |
| `.active` | 是否仍有位置误差或速度 |
| `Animator(reduced_motion=False)` | 显式值集合 |
| `animator.add(value)` / `remove(value)` | 注册/解除，重复注册无副作用 |
| `animator.step(dt)` / `.active / .values` | 统一推进、活动状态、所有值 |

## Canvas / Cell / Rect

`Rect(x=0, y=0, width=0, height=0)` 不可变；方法 `contains(x,y)`、`inset(padding=1)`、
`intersect(other)`。Rect 参数与 Canvas 坐标单位都是整数单元格。

`Cell(text=" ", style=Style(), width=1)` 不可变；width=0 为宽字尾格、2 为双宽首格。
`Canvas(width,height,style=Style())` 的 `.cells` 为行优先数组，`.width/.height/.clip` 可读。

| API | 行为 |
| --- | --- |
| `put(x,y,glyph,style=Style())` | 写入第一个 grapheme；跨 clip 的宽字跳过 |
| `text(x,y,text,style=Style(),width=None)` | 一行文本，返回已使用格数；换行即停 |
| `fill(rect,style,glyph=" ")` | 填充，glyph 必须恰好占一格 |
| `box(rect,style,title="")` | 圆角字符边框与可选标题 |
| `with canvas.clipped(rect)` | 临时与现有 clip 求交 |
| `plain()` | 无 ANSI 的多行快照 |
| `style_at(x,y,default=Style())` | 读取已绘制样式，越界返回 default |
| `tint(x,y,width,height,style,opacity=1)` | 浮点覆盖率混色，保留字符/宽字；style.reverse 可反转覆盖超过一半的格 |
| `text_width(text)` | 清理控制字符后的显示格数 |
| `crop_text(text,width)` | 按 grapheme 截取到指定单元格数 |

## Renderer / FrameStats

`Renderer(synchronized=False, colors="truecolor", native=True)`。
`encode(frame)` 返回 ANSI 差分并保存帧快照；`invalidate()` 丢弃上一帧，强制下一次完整刷新。
`.backend` 为 python/cpp；`.stats` 为 FrameStats(bytes, changed_cells, spans)。
stats 统计字符编码阶段，App.encode 额外图像传输字节不包含在内。

## 输入事件

- `Key(name, text="", phase="press", modifiers=frozenset())`：phase 为 press/repeat/release。
  特殊键名使用小写 up/down/left/right/home/end/pageup/pagedown/delete/backspace/enter/
  escape/tab/shift+tab。Ctrl 组合使用 `ctrl+q` 形式。
- `Paste(text)`：完整 bracketed paste。
- `Mouse(x,y,button=0,phase="press")`：零起始坐标；phase 为 press/release/move/scroll。
  button 为 SGR 原始位掩码；0 左键、1 中键、2 右键，64/65 为滚轮上下。
- `Capability(name,supported)`：synchronized / kitty_keyboard / kitty_graphics。
- `InputParser(max_paste=1_000_000)`：`feed(text,flush=False)` 返回事件列表；不完整序列保留。
  单独 Escape 需 timeout 后 flush；直接宿主自行决定超时。

## Style / Theme

```python
Style(fg=(218,225,240), bg=(17,21,34), bold=False,
      italic=False, underline=False, reverse=False)
```

RGB 通道取 0–255，fg/bg 可为 None 使用终端默认色。`with_(**changes)` 返回新的 Style，
`blend(other, amount)` 插值 RGB 并保留文字属性；`ansi(colors="truecolor")` 编码 SGR。
Theme 不可变，语义字段：background/panel/accent/
muted/selection/success/error；`Theme.light()` 提供浅色主题。
自定义主题用 `dataclasses.replace(Theme(), accent=...)`。

## Surface / KittyEncoder

`Surface(width,height,background=(17,21,34))` 为 RGB bytearray；`.pixels` 是严格 width×height×3
字节。`clear(color=None)`、`pixel(x,y,color)`、`rect(x,y,width,height,color)`、
`line(x0,y0,x1,y1,color)`、`circle(cx,cy,radius,color)`；坐标可为浮点，绘制时取整。
本版无抗锯齿和字体光栅化。

`KittyEncoder(image_id=None)`：`encode(surface,rect,force=False)` 返回压缩图像分块，
重复画面/位置返回空；`delete()` 删除该 ID 并重置缓存。通常由 PixelCanvas 与 App 自动管理。
独立编码器的 image_id 由调用方确保不与其他终端应用冲突。
