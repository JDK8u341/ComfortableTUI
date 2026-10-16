# 终端协议与兼容性

## 兼容层级

| 能力 | 基础 VT 终端 | 增强 VT 终端 | Kitty graphics 终端 |
| --- | --- | --- | --- |
| 字符控件、中文、差分输出 | 支持，宽度依赖终端/字体 | 支持 | 支持 |
| truecolor / 256 色 | 可选择降级到无色 | 按实际终端能力配置 | 按实际终端能力配置 |
| bracketed paste / SGR mouse | 终端须实现；不支持时用键盘 | 支持时自动生效 | 支持时自动生效 |
| DEC 2026 原子呈现 | 探测失败保持关闭 | 探测成功后启用 | 单独探测 |
| 按键 repeat/release | 传统输入按下确认 | Kitty keyboard 协商后启用 | 与图像能力独立协商 |
| PixelCanvas | braille 采样显示 | braille 或图像 | RGB 图像；仍可强制 braille |

不能从操作系统名称推断协议能力。Windows Terminal 的文本模式与 Kitty 图形支持是
独立问题。本版使用查询响应决定增强协议，不依靠 TERM 字符串宣称支持。
不实现 Sixel、iTerm2 inline images 或 GPU 共享内存；没有这些协议时采用 braille。

## ANSI/VT 字符后端

- 使用 alternate screen，隐藏宿主光标、关闭自动换行，退出恢复。
- 光标定位：`CSI row ; col H`，使用 1 起始的终端坐标。
- 颜色与属性：SGR，支持 RGB、256 色、无色三种编码。
- 帧间按 Cell 内容和 Style 精确比较，只有变化区间发送。变化区间按行分割，宽字整体刷新。
- 不依赖终端滚动命令和删除字符命令，从而避免不同滚动区域实现差异。

`Renderer(colors="256")` 使用 RGB cube 量化；没有完整灰度优选算法。
`NO_COLOR` 不会禁用反显/粗体，因此无色模式依然能看到焦点。

## 同步更新

依据 [DEC mode 2026 规范](https://github.com/contour-terminal/vt-extensions/blob/master/synchronized-output.md)：

```text
探测          ESC [ ? 2026 $ p
支持的响应    ESC [ ? 2026 ; 1 $ y 或 ... ; 2 $ y
开始一帧      ESC [ ? 2026 h
结束一帧      ESC [ ? 2026 l
```

查询无响应或模式未知时不启用。App.encode 把文本差分、图像删除与图像更新放进同一
事务，然后实际输出一次。同步更新减少终端解析半帧时的撕裂；它不会提高连接带宽。
本版没有针对 tmux/screen 的转义透传封装，在复用器里若查询被过滤会保留基础后端。

## Kitty keyboard

依据 [官方 keyboard protocol](https://sw.kovidgoyal.net/kitty/keyboard-protocol/)：

1. 查询 `CSI ? u`，收到能力响应后才启用。
2. `CSI > 3 u` 推入 flags：disambiguate + report event types。
3. 解析 `CSI unicode-key ; modifiers : event-type u`。
4. 1=press，2=repeat，3=release；解析方向键等功能键和 Ctrl/Shift/Alt/Super 位。
5. 退出时仅在本程序确实 push 过模式后发送 `CSI < u`，避免弹出宿主状态。

本版解析 shifted alternate key、associated text、功能键事件类型与主要修饰位；不提供
base-layout 快捷键自动匹配。方向键/翻页键的传统 final 形式也保留增强协议的事件类型。
Button 与列表确认键采用松开确认；导航键处理 press/repeat。

## 粘贴与鼠标

启用 bracketed paste（2004）和 SGR mouse（1006），采用 1000 点击 + 1002 拖动。
粘贴起止符分片到达仍能识别；最大粘贴文本默认 1,000,000 字符，超过截断。
输入控件另有 grapheme 长度限制，粘贴中的控制字符作为文本清理，不能触发退出快捷键。

单独 Escape 在没有后续输入约 35ms 后确认，避免把方向键的起始 ESC 当成返回。
网络延迟较高时可在自定义宿主中调整 InputParser 的 flush 时机。

## Kitty graphics

依据 [官方 graphics protocol](https://sw.kovidgoyal.net/kitty/graphics-protocol/)：

- 查询一个 1×1 RGB 数据包，专用查询 ID=31，收到 OK 才设置 graphics_supported。
- 图像为 RGB24，`f=24`；zlib 压缩 `o=z`；传输/显示 `a=T`。
- base64 每块最多 4096 字符，用 `m=1/0` 表示续块/尾块。
- `q=2` 静默确认；`C=1` 不让协议移动文本光标；以专属 image ID 与 placement p=1 更新。
- `c/r` 指定终端单元格宽高；图像尺寸由 `s/v` 指定。
- 只重传变化 RGB 或位置；离开场景时仅删除库拥有的图像 ID。

图像显示区域跨越 Canvas 裁剪边界时改为 braille，防止图像越过 Panel 或屏幕。
弹窗或底行通知可能覆盖图像区域时先移除相应背景图像，关闭后再发送。
本版直接压缩传输，不宣称具备 GPU 路径或仅发送局部像素损伤区域。

## 平台运行层

POSIX 使用 termios/tty raw mode、select、非分行 UTF-8 读取。
Windows 使用 Win32 控制台模式开启 VT 输入和输出，再以 msvcrt 读取，兼容传统扩展键码，
增量处理 UTF-16 代理对。窗口尺寸每轮检测，变化后完整重绘。

`try/finally` 在正常退出、Ctrl+C 和 Python 异常时恢复终端；SIGKILL/进程崩溃无法运行
清理逻辑，必要时使用宿主终端的 reset 功能。终端已处于交互程序内部的嵌套运行不受支持。

验证范围：本机 Windows ConPTY 进行了真实启动、导航、输入和退出检查；解析器与
图像分块有自动化回环测试。CI 覆盖 Windows/Linux/macOS；真实 Kitty 图像呈现需在
实现协议的终端中另行目视验证，编码回环测试不能代替端到端显示测量。
