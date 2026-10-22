# Glass 局部容器、形状与控件实例

2026-10-22，Glass协议7、Control ABI3。`GlassContainer` 位于一个 Card 的内部；
`GlassCardGroup` 管理多个 Card 窗口。这两个层次不创建新的 Canvas 传输协议。

```python
from comfortable_tui import GlassCard, GlassContainer, GlassControl
left = GlassControl("left", "button", "Previous", corner_radius=18)
right = GlassControl("right", "button", "Next", weight=2)
row = GlassContainer("actions", [left, right], layout="row", gap=12, padding=8)
card = GlassCard("player", "Player", [row], width=420)
# 运行时增删；同对象保留私有状态，之后调用workspace.update()。
row.children.append(GlassControl("extra", "button", "Extra"))
```

## 布局和形状

| 参数 | 行为 |
| --- | --- |
| layout="column" | 按固有高度纵向排列，gap分隔 |
| layout="row" | 按子节点weight分配可用宽度，行高为最大子高度 |
| layout="stack" | 子节点同一左上角，列表后部绘制在前部上面 |
| gap / padding | 0..128逻辑像素；stack不使用gap |
| weight | 0..1024，不含0；仅影响所在row的相对宽度 |
| visible / enabled | 继承给后代；隐藏不占布局高度，禁用保留布局但不能输入 |
| corner_radius | 0..128逻辑像素，自身与后代的圆角裁剪/命中；不改变原生控件固有画笔样式 |
| effects / motion | 与普通控件相同的独立绑定 |

每Card至多128节点（含容器）、深度8、8个Canvas；ID在整张Card内唯一。
共享同一个节点对象、环、过深树、未知父节点、过窄行和超过1200逻辑像素的内容会被拒绝。
尺寸改变需要提交模型；布局不是CSS，也没有隐式滚动、伸缩高度或自动文本换行测量。

圆角在GPU合成、嵌套裁剪和鼠标命中保持一致；边缘抗锯齿不扩大命中区域。
父平移和透明度传给后代，clip随父节点移动。overlay可外扩到节点边界之外，
但仍受祖先裁剪和Card窗口边界限制，不扩大布局或输入区域。

## 输入与可访问性

非Canvas控件路由：CardGroup预览 → 外层至内层局部容器预览 → 叶节点 →
内层至外层容器冒泡 → Card → CardGroup冒泡 → Workspace。
`on_preview_event`/`on_event` 返回真可消费后续Python路由；原生编辑默认动作可能已同步执行。
Card拖动/关闭保留既有可拦截默认行为。Canvas/WebView继续产生其CanvasEvent。

点击按绘制逆序查找最前的有效目标，并检查所有祖先clip和圆角；Tab按树前序遍历可聚焦叶节点。
容器本身不占键盘焦点。隐藏/禁用祖先会取消后代焦点、按压与Canvas捕获。
MSAA沿用简单子节点桥接，容器报告grouping角色；支持位置、命中和状态，
没有宣称实现完整分层UIA语义树。

## 原生控件私有状态

`GlassControlType` version=3，capsule `comfortable.glass.controls.v3`。
现有measure/paint/hit/input/accessible_value后追加可选回调：

```cpp
void *create(const comfortable::GlassControl &);
void destroy(void *);
void update(void *, const comfortable::GlassControl &);
```

create/destroy成对；update需要实例。它们在Glass UI线程执行。
paint/input通过`control.instance`访问当前节点状态；measure可能在提交线程执行，
不能依赖该指针。不要把GPU context或其他线程的设备资源放进UI私有状态。
同对象快照、重排和resize保留实例，update收到新描述；同ID新对象/类型替换则销毁旧实例。
destroy不得抛异常，Motion先于Control实例销毁。DPI/适配器重建可能重新创建状态。
可编译示例见`examples/glass_workspace/native_controls/controls.cpp`。

## Canvas、效果与性能

`GlassCanvas` 和 `GlassWebView` 可位于任何容器层，仍复用既有单消费者、
来源替换确认、事件转发和调用方所有权。一个通道只能对应一个显示节点；
需要两份内容时创建两个来源/页面。动态删除先等场景确认，再解除attach，
不会代替应用关闭FrameBuffer/BrowserPage。

需要局部容器、效果、动画或圆角时，每Card按需使用一个墨水图集，而非每控件一个
窗口/线程/FrameProvider。stack节点也使用不重叠图集位置，避免一个控件的效果
把另一个控件像素误当输入。普通平面无效果路径继续使用原单面绘制。
图集源预算每Card128MiB、全部UI源256MiB，尺寸上限16384；效果纹理另有预算。

容器自己的content/overlay输入是透明墨水；不会自动离屏展平整个子树。
background读取已绘制前缀，overlay在子树绘制后叠加；需要整棵内容后处理时使用Card的content链。
这明确区分节点装饰与整组离屏处理，避免每层容器默认复制整组纹理。

设计参考经Exa核对：[Qt GraphicsItem](https://doc.qt.io/qt-6/qgraphicsitem.html)
对局部坐标、父子变换和输入几何的职责划分；Qt只用于自动化测试夹具，产品不依赖Qt绘制。
运行示例见[原生动画指南](glass-native-motion.md)，测量边界见[验收报告](validation/glass/completion.md)。
