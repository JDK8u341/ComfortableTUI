# 在玻璃卡片中嵌入 WebView

`GlassWebView` 将已有 `BrowserPage` 显示为卡片控件，内部仅组合 `WebView` 与
[通用 GlassCanvas](glass-canvas.md)。已有 `WebView` 可以直接交给 GlassCanvas，无需重建页面。
玻璃 UI 和浏览器依然是两个
上层服务：卡片本身由自有 Win32/D3D11 UI 绘制，只有显式创建 BrowserService 时
才启动 CEF。FrameProvider、CPU/GPU 图像协议和浏览器安全策略均不因此改变。

## 运行示例

先按[玻璃工作区安装说明](glass-workspace.md)构建 `_native_glass` 协议6，并准备已有
[浏览器运行时](webview.md)。本次不需要修改或重新编译 CEF 服务。

```powershell
.\.venv\Scripts\python.exe -m examples.glass_workspace.webview
.\.venv\Scripts\python.exe -m examples.glass_workspace.webview --render-mode cpu
.\.venv\Scripts\python.exe -m examples.glass_workspace.webview --preview
```

默认使用 GPU。`--browser PATH` 可指定现有 ctui_browser.exe；省略时沿用 BrowserService
的环境变量/本地构建路径发现规则。`--preview` 是自有图形测试宿主，普通入口仍附着
真实终端。示例创建两个独立网页，通过明确授权的消息主题分享笔记，输入框互不镜像。
页面使用 `script-src 'self'`，不放宽 unsafe-eval，也不授予宿主 evaluate 权限。

## API

```python
from comfortable_tui import BrowserService, GlassCard, GlassWebView, GlassWorkspace

with BrowserService() as browser:
    page = browser.open("https://your-app.example/", origins=["https://your-app.example"],
                        render_mode="gpu", width=352, height=300)
    view = GlassWebView("browser", page, label="App panel", height=300)
    with GlassWorkspace([GlassCard("app", "My app", [view], width=400)]) as workspace:
        # workspace.bind(app)，然后 app.run()；或 start(hwnd) 后周期 poll()。
        pass
```

构造参数：`GlassWebView(id, page, label="", height=240, enabled=True, fit="stretch",
auto_resize=True, on_event=None, on_browser_event=None)`。

| 参数/方法 | 行为 |
| --- | --- |
| page | 调用方创建、拥有的 BrowserPage；控件不创建服务或改变页面权限 |
| height | 网页视口逻辑高度16..1024；整卡仍须满足1200及显示器高度限制 |
| fit | stretch / contain / cover / pixel，默认 stretch |
| auto_resize | 按原生 transform 给出的逻辑视口自动 page.resize；默认 True |
| on_event | 普通 GlassEvent 回调，返回 True 可消费后续默认输入处理 |
| on_browser_event | BrowserPage loaded/message/load_error 等原事件；在 poll/App 线程调用 |
| interacting | 当前是否将网页按键交给浏览器；只读观察用途 |
| enter_interaction() | 在已聚焦视图上进入网页交互；返回是否成功 |
| leave_interaction() | 退出网页交互、释放已按下鼠标按钮并通知页面失焦 |

当前 CEF 服务使用 device_scale_factor=1，因此自动适配采用逻辑 CSS 像素，合成器再按
显示器 DPI 缩放；没有承诺为每个缩放比例输出原生分辨率字体。`auto_resize=False` 可由
应用直接调用 page.resize 保持固定来源分辨率。用户的外部 resize 也经过同样的附着确认。

## 键盘与鼠标

从终端进入卡片的快捷键由应用绑定；示例为 F6。卡片标题本身也是一个焦点停靠点。

- Tab/Shift+Tab 遍历标题及可交互控件，越界进入下一/上一张卡；跳过禁用控件、隐藏卡
  和禁用容器。纯状态卡仍可停靠标题，不会困住 Tab。
- Ctrl+Tab/Shift+Ctrl+Tab 直接换卡。标题区的左/上为上一卡，右/下为下一卡。
- 网页尚未交互时，方向键继续换卡；Enter 或点击网页进入交互，文字和方向键交给网页。
- Escape 第一次退出网页交互，第二次返回终端。F6 始终返回终端，Ctrl+Q 请求退出。
- Tab 始终属于卡片导航，当前不会遍历网页 DOM；网页内部输入框可用鼠标聚焦。
- 原生输入框保留编辑按键，滑块保留左右调值；非编辑控件上/下移动控件焦点。

支持基本左/右/中键、双击、移动、滚轮、按键修饰符及 Unicode 提交文字。原生卡片
通道尚未实现完整 IME 预编辑/候选框定位、触摸、拖放或所有高级输入行为；不要将其
视为旧 Qt OverlayCanvas 全部输入能力的替代品。失焦/捕获丢失释放是生命周期处理，
不能通过 preview 消费来取消，以免网页留在按下或聚焦状态。

## 生命周期与资源

工作区自动轮询 BrowserService，同一 App/工作区中一个服务只注册一次。来源更新及
附着/解除确认由控件自动接入，外部宿主只需调用 workspace.poll()，无需重复 service.poll()。

浏览器 resize 会创建新来源。控件在待确认期间保留全部可能使用的旧来源，忽略较旧
场景的附着确认；最新来源确认后才退休旧缓冲区。移除控件也须等原生移除确认才解除
页面消费者。业务无需访问 `_attachments` 等私有字段。

隐藏工作区/容器会暂停页面显示并释放网页焦点，恢复后重新呈现。禁用视图只禁止交互，
不会关闭业务页面。关闭已显示的 BrowserPage 会禁用视图，最后画面及其来源保留到
原生解除附着；先关闭 workspace，再关闭页面/BrowserService 是推荐的退出顺序。

每张卡当前支持一个图像视图。一张 GPU BrowserPage 不能同时交给多个独立显示消费者；
需要多张独立网页时调用 service.open()，不自动复制页面或扩大传输协议。应用可以按需
复用同一 BrowserProfile（共享应用内存储），或指定不同 profile、message_group、主题
权限隔离数据。跨应用的 profile 根目录隔离及 BrowserPolicy 的默认拒绝保持不变。

## 验证

```powershell
python examples/verify_glass_webview.py --render-mode cpu --report .build/glass-browser-cpu.json
python examples/verify_glass_webview.py --render-mode gpu --report .build/glass-browser-gpu.json
```

检查实际原生键盘焦点、Tab 越界、反向路径、网页输入、跨页面消息、快速 resize 的
缓冲区退休、容器隐藏和实际帧后端。使用明确主题的消息桥回传 DOM 状态，不使用 eval。
若 Windows 拒绝前台请求，日志 FOCUS 后需激活验证器自己的宿主；不绕过系统前台限制。
