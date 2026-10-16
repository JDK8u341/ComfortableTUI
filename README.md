# ComfortableTUI

Spring-driven Python terminal interfaces, inspired by the 128×64 OLED UI in
[osu-mc](https://github.com/chhongzh/osu-mc). Includes a retained widget tree,
smooth scrolling and focus, animated page transitions, Unicode-aware cell
rendering, and an optional RGB pixel canvas using Kitty graphics.

![Motion preview from actual cell frames](docs/images/motion.gif)

The preview is an offline cell-frame illustration, not a terminal FPS recording.

**状态：0.1 alpha。** 面向开发者的通用 TUI 库，Python 3.10+，支持 Windows、Linux、macOS
的 VT 终端。字符文字仍然受终端网格限制；像素画布在支持 Kitty graphics 的终端中可按像素移动。

```sh
python -m pip install -e .
python -m comfortable_tui
```

```python
from comfortable_tui import App, Button, Input, Label, Panel, VBox

message = Label("Hello / 你好", height=1)
name = Input(placeholder="Your name", height=1)
root = Panel(VBox(message, name,
                  Button("Greet", lambda: message.set_text(f"Hello, {name.text}!"), height=1),
                  gap=1), "First app")
with App(root) as app:
    app.run()
```

Tab / Shift+Tab changes focus, arrows navigate, Enter activates, Escape closes
dialogs or returns to a previous page, Ctrl+Q exits. Mouse click, wheel and
bracketed paste are supported. Enhanced keyboard terminals can activate on release.
F6 switches between the menu bar and its last focused content; Enter/Down enters
the selected page. The focused menu title shows a `›` marker and underline.

- [入门与安装](docs/getting-started.md)
- [原理调研与来源](docs/research.md)
- [Exa 调研：焦点与菜单动画优化](docs/motion-research.md)
- [架构与动画](docs/architecture.md)
- [全部控件与交互](docs/widgets.md)
- [公共 API](docs/api.md)
- [终端协议与兼容性](docs/terminal-protocols.md)
- [性能、原生扩展与基准](docs/performance.md)
- [示例](examples/README.md)
- [测试、扩展和贡献](docs/development.md)

```sh
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python examples/benchmark.py --frames 300
```

The default installation needs no compiler. An opt-in C++17 / pybind11 extension
scans dirty spans; see the performance guide for build commands and measured limits.
The core is an independent implementation. No osu-mc source or assets are bundled.
MIT licensed.
