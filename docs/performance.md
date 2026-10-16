# 性能、基准与原生扩展

## 为什么默认 Python

主要优化来自架构：稳定的控件树、统一动画推进、列表可见行绘制、局部差分、样式合并、
一帧一次写出和静止跳帧。小中型字符界面的布局/绘制不需要编译器即可满足常见帧预算。
对真正耗时的路径先测量；本版提供可选的 C++17/pybind11 精确差分扫描器。

C++ 扩展只替代 `diff_spans`，Python 仍承担布局、动画、Unicode 和 ANSI/Kitty 编码。
扫描器比较 Python Cell 的真实相等关系，不使用可能碰撞的哈希代替比较；因此需要 GIL。
它不保证所有工作负载更快，图像压缩或大面积重绘可能主导整帧成本。

## 可复现测量

```sh
python examples/benchmark.py --frames 300 --json docs/benchmark-results.json
```

脚本对每个规模预热 20 帧，然后推进 300 帧；每 6 帧移动一次选择，dt=1/120。
测量涵盖 layout、animation、draw、ANSI encode；包含 1,000/10,000 项列表的数据与状态，
只绘制可见行。最后让动画收敛，检查相同帧的输出为零字节。
没有原生模块时，两轮 backend 都是 python；编译后分别比较 python 和 cpp。

2026-10-17，本机 Python 3.12.14 / Windows 11，300 帧：

| 终端尺寸 / 项数 | Python 中位 / P95 (ms) | C++ 中位 / P95 (ms) | 平均差分字节/帧 |
| --- | --- | --- | --- |
| 80×24 / 1,000 | 1.250 / 2.085 | 1.193 / 1.673 | 413 |
| 120×40 / 10,000 | 2.269 / 3.392 | 2.235 / 3.161 | 632 |
| 200×60 / 10,000 | 4.298 / 6.054 | 4.404 / 5.660 | 879 |

原始记录：[benchmark-results.json](benchmark-results.json)。所有场景稳定后均输出 0 字节。
以上是初始版本记录；焦点/菜单优化后的同类测试见
[benchmark-motion.json](benchmark-motion.json) 和 [优化报告](motion-research.md)。
这是一次本机测试，运行顺序、温度、缓存和系统负载会影响结果；C++ 的小幅差异不应被
理解为稳定加速承诺。8.33ms 为 120fps 的 CPU 帧预算，16.67ms 为 60fps 的预算。

**这些数值不包含终端 I/O、SSH 延迟、终端解析、GPU 合成与实际屏幕呈现。** 它们不能
证明用户能看到同样帧率，也不是与 Textual 或 osu-mc 的横向跑分。

## 构建 pybind11 扩展

需要 Python 开发头文件与 C++17 编译器。Windows 使用 VS Build Tools 的 MSVC；
Linux 用 GCC/Clang；macOS 用 Xcode Command Line Tools。

先安装开发依赖：

```sh
python -m pip install -e ".[dev]"
```

PowerShell：

```powershell
$env:COMFORTABLE_TUI_BUILD_NATIVE = "1"
python -m pip install --no-build-isolation -e .
Remove-Item Env:COMFORTABLE_TUI_BUILD_NATIVE
```

POSIX：

```sh
COMFORTABLE_TUI_BUILD_NATIVE=1 python -m pip install --no-build-isolation -e .
```

开发时也可 `COMFORTABLE_TUI_BUILD_NATIVE=1 python setup.py build_ext --inplace`。
Windows 使用环境变量单独设置写法。`--no-build-isolation` 使构建可使用已安装的 pybind11；
默认纯 Python 安装不把 pybind11 强制加入用户运行依赖。

检查：

```sh
python -c "from comfortable_tui import Renderer; print(Renderer().backend)"
python -m pytest
```

在同一机器/ABI 构建的扩展优先使用；导入失败自动降级。强制比较纯 Python：

```powershell
$env:COMFORTABLE_TUI_PURE_PYTHON = "1"
python examples/benchmark.py
Remove-Item Env:COMFORTABLE_TUI_PURE_PYTHON
```

## 应用性能建议

- 回调里不要阻塞网络、磁盘或 sleep；将耗时工作送 worker。
- 列表数据使用 ListView/DataTable；ScrollView 中每个条目都是 Widget 时，不具备同样的虚拟化。
- 批量替换用 set_items/set_rows；避免每帧构造新控件。
- 高频自定义图形只在需要时 invalidate；保持像素画布尺寸接近实际用途。
- SSH 慢连接先降低 fps，避免全屏逐像素动画；同步更新只能减少撕裂，不能增加带宽。
- 对 10,000 项列表的连续插入/删除，高度动画需要重算偏移，属于 O(n)；普通稳定滚动使用缓存
  偏移和二分定位可见行。
- 颜色混合高亮会产生更多 SGR；必要时可用无色模式和 reduced motion。

PixelCanvas 的 braille 后端为 Python 采样，每终端格最多八次采样；大画布可能成为主要
开销。Kitty 后端使用标准库的 C 实现 zlib 压缩，但高熵 RGB 帧传输量可能更大。
