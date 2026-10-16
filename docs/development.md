# 开发、测试与扩展

## 本地开发

```sh
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m build
```

原生扩展构建见[性能文档](performance.md)。GitHub Actions 在 Windows、Linux、macOS 的
Python 3.10/3.12/3.13 上测试纯 Python 与 C++ 模式，并验证文档构建。
CI 中的通过状态应以 GitHub 当前运行结果为准。

## 自定义控件

```python
from comfortable_tui import Key, Widget

class Counter(Widget):
    focusable = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.count = 0
        self.display = self.value(0, damping=0)

    def on_event(self, event):
        if isinstance(event, Key) and event.phase != "release" and event.name == "right":
            self.count += 1
            self.display.set(self.count)
            self.invalidate()
            return True
        return False

    def paint(self, canvas):
        style = self.theme.selection if self.focused else self.theme.background
        canvas.text(self.rect.x, self.rect.y, f"Count {self.display.current:.1f}", style,
                    self.rect.width)
```

只在 paint 绘制到 Canvas，不直接调用 print；动画由 App 统一推进。
自定义容器覆盖 `measure/layout`，用 `child.layout(rect)` 分配区域。
需要改变 paint 顺序或挂起孩子时才覆盖 draw/tick/focusables；注意所有权和裁剪。

## 测试策略

- 动画：30/60/120fps 同时长位置与速度一致；指数、欠阻尼、临界和过阻尼均检查。
- 渲染：使用 pyte 解释 ANSI，然后与 Canvas 内容逐行比较，包括随机变更与中文宽字替换。
- Unicode：组合音标、emoji ZWJ、宽字符、遮蔽输入与控制字符清理。
- 输入：分片 CSI、独立 Escape、粘贴分界任意切分、增强键事件、鼠标与能力响应。
- 控件：焦点域、禁用状态、松开确认、鼠标捕获、文本编辑、列表增删及动画值回收。
- 图像：zlib/base64 分块回环、重复帧静默、离场清理与模态覆盖。
- 应用：定时器、线程回调、窗口尺寸、异常时终端恢复。
- 集成：所有画廊页从 0×0 到 160×50 无终端渲染，以及示例构建器运行。

基础测试不是实际终端图形呈现、字体兼容或硬件帧率的证据。更换协议或终端层时，除自动化
测试外请在真实目标终端运行 gallery 和 pixel_oled，并检查退出后 shell 的输入/光标状态。

## 文档

文档源文件在 docs，可直接从 GitHub 阅读。可选静态站点构建：

```sh
python -m pip install -e ".[docs]"
python -m mkdocs build --strict
python -m mkdocs serve
```

本仓库没有自动发布文档或 PyPI 包。发布前更新版本、CHANGELOG、基准与支持范围。
对所有新公共 API 同时提供文档和可运行使用方式。

## 当前边界

- alpha API，无 CSS、Markdown、完整富文本、语法高亮或文本选区/撤销。
- 动画支持常用控件属性，未承诺每个控件矩形自动 tween。
- 不支持嵌套在另一个全屏 TUI 中运行。
- 无 touch、屏幕阅读器专用语义或 IME composition 事件；输入采用宿主终端提交后的字符。
- 同步更新、键盘增强、图像传输均需实际终端支持。
- 自定义 painter、网络盘目录和 worker 退出策略由应用负责控制耗时。
