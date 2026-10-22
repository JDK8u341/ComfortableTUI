# 卡片中的通用画布

`GlassCanvas` 是 Card 对现有画布的承载适配器，不是新的渲染器。数据仍由外部提供者
写入 `FrameBuffer` 或共享 GPU 纹理；C++ 合成器直接读取。Python 只处理配置、事件
和附着确认，不中转 GPU 帧。玻璃工作区与浏览器服务都是 Canvas 之上的服务。

```text
GlassCard → GlassCanvas → OverlayCanvas / WebView（OverlayCanvas 子类）
                               ↓
                   FrameBuffer / GpuFrameSource → C++ Compositor
```

## 同一接口承载不同内容

```python
from comfortable_tui import GlassCanvas, GlassCard, OverlayCanvas, WebView

# 使用现有提供者及 CanvasEvent 回调，不需要改成浏览器。
image = OverlayCanvas(frame_source, on_event=handle_canvas_event, fit="contain")
image_card = GlassCard("image", "Image", [GlassCanvas("view", image, height=180)])

# 同一个承载接口可以接收已有 WebView。
web = WebView(browser_page, fit="stretch", on_browser_event=handle_browser_event)
web_card = GlassCard("web", "Web", [GlassCanvas("view", web, height=300, auto_resize=True)])

# 只需要图像、无需现有画布回调时，可直接传入 CPU/GPU 来源。
plain = GlassCanvas("frame", another_frame_source, height=160)
```

`GlassWebView(id, page, ...)` 只是构建 `WebView(page)` 再交给 `GlassCanvas` 的便捷入口。
Card 和原生 GlassHost 没有浏览器专用控件或另一条图像协议。已有 `GlassControl(kind="canvas",
source=...)` 仍作为更低层的声明接口存在，其回调使用 GlassEvent；`GlassCanvas` 则把
事件转换成已有画布使用的 CanvasEvent，并复用该画布自己的默认行为。

## API 与事件

`GlassCanvas(id, canvas, label="", height=240, enabled=True, fit=None,
auto_resize=False, on_event=None)`：

| 参数 | 约定 |
| --- | --- |
| canvas | OverlayCanvas 及其子类、FrameBuffer 或 GpuFrameSource |
| height | 逻辑像素高度16..1024，整卡内容上限仍为1200 |
| fit | 默认继承 canvas.fit；显式值覆盖卡片显示策略 |
| auto_resize | 默认 False；True 时按逻辑视口请求画布自身的 resize 策略；WebView 调用 page.resize，普通图像提供者仍自行决定源尺寸 |
| on_event | 外层 GlassEvent 路由回调；返回 True 取消该事件的后续默认处理 |

已有 `canvas.on_canvas_event` 收到 `attaching`、`buffer_attached`、`detached`、`show/hide`、
`focus_in/out`、`transform`、鼠标、滚轮、按键、文字提交事件。键名、source_position、
modifiers、button/buttons 和 commit 等字段沿用 CanvasEvent 约定。transform 包含
物理像素视口、来源像素尺寸、缩放与 DPI；鼠标位置已经转换到来源像素。

画布可以消费方向键；未消费时切换相邻卡片。Tab/Ctrl+Tab 和 F6 保留给宿主。
WebView 继续采用 Enter/点击进入、Escape 退出的交互策略。
失焦或捕获丢失时释放按下按钮，避免网页或提供者停留在拖动状态。

原生卡片支持的是这份文档所列的事件子集。旧 Qt 叠加层的完整 IME 预编辑、触摸、
拖放、穿透输入和 set_ime 候选框定位尚未在卡片宿主实现；`fps/smooth/host` 等旧叠加层
配置也不控制卡片窗口。不要把“可承载现有画布”理解成不同宿主的全部窗口能力相同。

## 生命周期

同一个画布实例只能挂载到一个显示宿主，不能同时放进 App 的叠加层和 GlassCard。
切换宿主前必须等旧宿主解除附着。卡片移除后，原生场景确认才发送 detached。
`canvas.set_buffer(new_source)` 自动通知工作区；旧来源保留至新场景确认。
调用方拥有提供者、缓冲区、WebView 和 BrowserService，工作区不替调用方关闭它们。
推荐先关闭工作区，再关闭画布/页面及其来源或服务。

目前每张卡最多一个画布，可和多个原生控件混排；多画布使用多张卡及 CardGroup。
每个来源通道在一个工作区只能出现一次。CPU 来源与 GPU 来源的约束相同；GPU
BrowserPage 也不可同时提供给多个独立消费者。显示/隐藏请使用 CardGroup.visible、
workspace.show/hide 或移除控件；画布自己的终端布局属性不用于卡片布局。

## 混合示例与验证

```powershell
python -m examples.glass_workspace.mixed
python -m examples.glass_workspace.mixed --render-mode cpu
# 可替换普通图像卡的来源；提供者须继续运行：
python -m examples.glass_workspace.mixed --gpu-source CHANNEL_NAME
python examples/verify_glass_windows.py --hosted-canvas --report .build/glass-canvas-cpu.json
python examples/verify_glass_windows.py --hosted-canvas --gpu-provider .build/glass-native/Release/ctui_glass_signal.exe --report .build/glass-canvas-gpu.json
```

示例混排原生控件、可点击暂停的 CPU 图像和两个独立网页，使用同一个 CardGroup/
焦点系统。GPU 来源无需重写卡片接口。依赖、构建和浏览器权限见
[玻璃工作区](glass-workspace.md)及[卡片 WebView](glass-webview.md)。
