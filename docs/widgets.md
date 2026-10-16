# 控件手册

所有控件可从 `comfortable_tui` 直接导入。下表给出本版的完整控件族；括号内为主要
位置参数，其他为关键字参数。通用参数见 [Widget API](api.md#widget)。

## 文本、分隔与反馈

| 控件 | 参数和状态 | 行为 |
| --- | --- | --- |
| `Label(text="", align="left")` | `.text`、`set_text(text)`；left/center/right | 显式换行，多行截断，不自动折行 |
| `Paragraph(text="")` | 同 Label，`lines(width)` | 按 grapheme 自动折行；保留换行，长单词也换行 |
| `Rule()` | `style` | 横向分隔线 |
| `Spacer()` | width/height/flex | 占位，可用 flex 吸收剩余空间 |
| `ProgressBar(value=0, total=100)` | `set_value(value)`；`.progress` 为 Value | 八分之一块精度的动画进度；显示百分比 |
| `Spinner(label="Working")` | `.running`、`.elapsed` | 10Hz 字符切换；running=False 显示完成标记 |
| `Log(max_lines=1000)` | `write(text)`、`.lines` | 有界日志，默认尾随；上下键、Home/End 查看 |
| `Sparkline(values=())` | `.samples` | 最近 viewport 宽度个采样，自动缩放 |
| `BarChart(values: dict)` | `.samples` | 名称、水平条、数值；按可见高度裁剪 |

## 表单

| 控件 | 参数和状态 | 交互 / 回调 |
| --- | --- | --- |
| `Button(label, on_press=None)` | `.label`、`activate()` | Enter / Space / 鼠标；`on_press()` |
| `Checkbox(label, checked=False)` | `.checked`，`on_change=None` | 切换布尔；`on_change(bool)` |
| `Switch(label, checked=False)` | 同 Checkbox，附 `.position` 动画 | 开关滑块位置平滑移动 |
| `Input(value="")` | placeholder/password/max_length/validator | 左右/Home/End 编辑，退格/Delete，Ctrl+U 清空，Ctrl+A/E 到首尾 |
| `TextArea(value="")` | `.text`、`.cursor`、on_change/max_length | 多行纯文本编辑，自动跟随光标；上下左右/Home/End/Enter |
| `Slider(value=0)` | minimum/maximum/step/on_change；`.value_` | 左右调节、Home/End、鼠标拖动；`set_value(value)` |
| `Select(options, value=0)` | `.selected`、`set_value(index)` | Enter 打开选项弹窗，左右直接切换；`on_change(index, text)` |
| `RadioGroup(items, value=0)` | 继承 ListView，选中索引代表值 | 上下选择互斥项，`on_change(index)` |
| `SelectionList(items, checked=())` | `.checked` 为索引 set；on_toggle | Space / Enter 勾选；`on_toggle(set_of_indices)` |

Input 的具体关键字：

```python
Input("", placeholder="name", password=False, max_length=4096,
      validator=None, on_change=None, on_submit=None)
```

- `validator(text)` 返回 True / None 表示通过；False 表示默认错误；字符串表示错误信息。
- `.error` 存放验证结果，输入框不额外占用错误行；可用 Label 展示以适配表单布局。
- `on_change(text)` 每次文本编辑调用；`on_submit(text)` 只在无错误时调用。
- `set_value(text)` 清理控制字符、限制 grapheme 数量、把光标移到末尾并触发验证与回调。
- password 只遮蔽画面，不改变内存中的文本；水平滚动与光标位置按遮蔽后的字符计算。
- TextArea 是基础纯文本编辑器，不提供选区、撤销、剪贴板写入或语法高亮。
- Spinner 的 reduced-motion 模式用固定省略号；自定义 PixelCanvas 动画需要调用方自行停止。

## 集合与数据

### ListView

```python
ListView(items=(), item_height=1, on_select=None, on_change=None)
```

- `.items` 为字符串列表，`.selected` 为索引；空列表显示 `(empty)`。
- `select(index)` 自动限制边界；`on_change(index)` 只在实际选择改变时调用。
- `on_select(index, text)` 对 Enter 或鼠标确认执行。
- 上下、Home/End、PageUp/PageDown、滚轮导航；光标与滚动均保存连续状态。
- `insert_item(index, text, animate=True)` 从零高度展开。
- `remove_item(index, animate=True)` 收拢到零后移除并释放动画。
- `set_items(items)` 替换内容并清理未完成的增删动画；保留有效范围内的选中索引。
- 覆盖 `item_text(index)` 可以更改行文本。只为可见行绘制，不为每个数据项创建 Widget。
- `.scroll`、`.cursor`、`.edge` 为动画值；通常使用 select，不直接设置 scroll。

SelectionList 插入/删除时会同步调整勾选索引。RadioGroup 方向键改变选择即改变值。
直接修改 `.items` 不会更新缓存；动态更新必须使用上述方法。

### DataTable

```python
DataTable(columns, rows=(), column_widths=None,
          on_select=None, on_change=None)
```

固定表头和分隔行；正文复用 ListView 的虚拟化与动画。`column_widths` 为各列单元格宽度，
默认按容器均分。左右键水平滚动，上下和翻页键选择行。`set_rows(rows)` 更新数据。
确认回调为 `on_select(index, tuple_of_cell_strings)`。本版选择单位为行；无排序、多列
单元格编辑、冻结列和拖动列宽功能。对超长中文列的水平裁切按 grapheme 处理。

### Tree / DirectoryTree

```python
root = TreeNode("Project", [TreeNode("src", [TreeNode("app.py")])], expanded=True)
tree = Tree(root)
files = DirectoryTree(".")
```

`TreeNode(label, children=[], expanded=False, data=None)` 保留节点对象。Tree 通过迭代遍历
展开可见节点；左键收起或回父节点，右键展开，Enter 切换；`refresh()` 更新可见内容。
`DirectoryTree` 打开一层目录时才读取该层，不递归扫描整盘，不跟随目录 symlink；读失败
显示错误叶节点。目录访问在 UI 线程同步进行，网络盘或超大目录应以 worker 预加载自定义
TreeNode 数据。刷新文件系统树需重建数据或清空相应节点 children 后 expand/refresh。

## 布局和导航

| 控件 | 参数 | 行为 |
| --- | --- | --- |
| `VBox(*children, gap=0, padding=0)` | 固定 height 优先，剩余按 flex | 纵向分配；measure 为自然高度和 |
| `HBox(*children, gap=0, padding=0)` | 固定 width 优先，剩余按 flex | 横向分配；measure 为最大子高度 |
| `Grid(*children, columns=2, gap=1)` | 固定列数 | 行优先，均分网格 |
| `Panel(child, title="", padding=0)` | 一个子控件 | 边框/标题，焦点在子树内时突出边框 |
| `ScrollView(child)` | 一个可测量子树 | 按 child.measure 布局；滚轮和方向键滚动，Tab 进入子控件时保证可见 |
| `Tabs(tabs: dict[str, Widget], on_change=None)` | 至少一个页面；`.selected` | 选中条与内容滑动过渡；标签聚焦时左右切换或点标题；`select(index)`，`on_change(index)` |
| `Collapsible(title, child, expanded=True)` | `.expanded`、`toggle()` | Enter/Space/标题点击；展开高度渐变；不指定 height 时可推动兄弟布局 |
| `PageStack(root)` | `.top`、`push(page)`、`pop()` | 整页弹簧滑入/滑出，挂起页面保留状态，根页不可 pop |

容器超出空间时裁剪，不会自动让整个屏幕滚动。使用 ScrollView 指明滚动域。
Frame 的 Layout 使用整数单元格；一般宽高变化直接重排，动画目前集中于列表、滚动、
开关、按钮反馈、折叠和页面切换。

表单共享焦点高亮以浮点边缘在控件之间移动，使用覆盖率混色；文字仍保持在网格上。
控件默认从父画面继承背景，显式 style 可指定独立表面。Tabs 过渡时同时绘制进出两页，
但仅选中页接收交互；快速反向选择会保留运动速度。标题超出宽度时自动滚动。

## 像素图形

```python
def paint(surface, seconds):
    surface.clear()
    surface.circle(64, 32, 10, (255, 124, 184))

pixels = PixelCanvas(128, 64, painter=paint, animate=True, backend="auto",
                     cell_width=None, cell_height=None)
```

前两个参数是**像素尺寸**。`cell_width` / `cell_height` 控制布局中的终端格数。
`backend="auto"` 协商成功使用 Kitty，否则 braille；可显式指定 kitty / braille。
`surface` 是 RGB 帧缓冲，`painter(surface, elapsed_seconds)` 在绘制时运行。
`animate=True` 定期请求重绘；非动画的 surface 手动改动后调用 invalidate。
braille 用 2×4 采样点/格，单格前景色取点的平均值，不是完整彩色图像显示。
像素缩放由终端按 cell placement 实现，终端字体纵横比可能改变图像比例。
