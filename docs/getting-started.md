# 安装与入门

## 环境

- Python 3.10 或以上。
- Windows：使用支持 VT 的终端，例如 Windows Terminal；Linux/macOS：使用 VT 终端。
- Unicode 字体；中文和 emoji 的实际宽度由终端字体、终端 Unicode 实现共同决定。
- 依赖仅为 `wcwidth`（显示宽度）与 `regex`（Unicode grapheme 分段）。

先克隆再安装；项目目前未发布到 PyPI：

```sh
git clone https://github.com/JDK8u341/ComfortableTUI.git
cd ComfortableTUI
python -m venv .venv
```

PowerShell：

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m comfortable_tui
```

Linux/macOS：

```sh
. .venv/bin/activate
python -m pip install -e .
python -m comfortable_tui
```

不需要 C++ 编译器。可选扩展见[性能指南](performance.md)。

## 最小应用

```python
from comfortable_tui import App, Button, Input, Label, Panel, VBox

label = Label("你好，终端", height=1)
name = Input(placeholder="请输入名字", height=1)
root = Panel(
    VBox(label, name,
         Button("确认", lambda: label.set_text(f"欢迎，{name.text}"), height=1),
         gap=1),
    "个人资料",
)
with App(root, fps=60) as app:
    app.run()
```

`width` / `height` 使用终端单元格。VBox 的固定高度子控件先分配空间，其余子控件按
`flex` 权重平分剩余高度。HBox 对宽度采用同样规则。表单中的单行控件应写 `height=1`。
希望按自然高度排布并滚动时，用 `ScrollView(VBox(...))`。

## 交互

| 操作 | 行为 |
| --- | --- |
| Tab / Shift+Tab | 在当前页面或弹窗中前后移动焦点 |
| 方向键、Home、End、PageUp、PageDown | 优先执行控件的选择/编辑/滚动；未被处理的方向键移动到相邻控件 |
| Enter / Space | 激活按钮/复选框；列表 Enter 选择，SelectionList Space 勾选 |
| Escape | 关闭弹窗；在 PageStack 内回到上一页 |
| Ctrl+Q / Ctrl+C | 退出应用 |
| 鼠标单击、滚轮 | 选择、按钮激活、滚动 |
| 终端粘贴 | bracketed paste 中的内容整体交给输入控件 |

Kitty keyboard 协商成功后，Button/Checkbox/Switch/Select 和 ListView 的 Enter 在松开时
确认，并忽略确认键重复事件。传统终端在按下时确认。输入框 Enter 提交使用按下事件。

## 状态更新

使用 `set_text()`、`set_value()`、`set_items()` 等方法会自动请求重绘。
直接改普通属性后调用 `widget.invalidate()`。动画值用 `set(target)`，切勿每帧重建整个控件树。

```python
from comfortable_tui import ProgressBar

progress = ProgressBar(0, height=1)
# 把 progress 放进 root 后：
# app.call_later(0.1, lambda: progress.set_value(75))
```

耗时 I/O 使用 `app.run_worker(function, on_result, on_error=...)`。回调会排队回 UI 线程。
外部线程也可调用 `app.call_from_thread(callback)`；不要直接在工作线程里修改控件。
工作任务需要自己设置网络超时或取消机制；退出应用不会强制终止已运行的 Python 线程。

## 主题与无动画模式

```python
from comfortable_tui import App, Theme

# with App(root, theme=Theme.light(), reduced_motion=True) as app:
#     app.run()
```

- `COMFORTABLE_TUI_REDUCED_MOTION=1`：默认将动画直接置于目标值。
- `NO_COLOR`：禁用颜色序列；粗体、反显仍保留。
- `App(colors="256")`：使用 256 色量化输出。
- `App(synchronized=False)`：显式关闭同步更新；默认自动探测。

## 无终端运行

```sh
python -m comfortable_tui --snapshot
```

```python
with App(root) as app:
    frame = app.render(100, 32, dt=1 / 60)
    print(frame.plain())
    ansi = app.encode(frame)
```

`render()` 不读取标准输入、不修改终端模式，可用于测试和自定义宿主。
`encode()` 返回最终输出，包含单元格差分与可能的图像数据；调用方负责实际写出。
