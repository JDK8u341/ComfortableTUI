# 弹性动效

默认使用 Expressive：焦点四边分别追踪目标，带适度回弹和拉伸；列表与表格的
选中条、按钮释放、开关、滑块、页面及展开区使用相应的语义弹簧。所有操作立即
改变逻辑状态，动画只描述视觉状态；快速反向操作保持当前位置与速度。

```python
from comfortable_tui import App, Button, MotionProfile, Spring

app = App(Button("Save"), motion="expressive")
app.set_motion("subtle")                  # 同样连续，降低空间回弹
app.set_motion(MotionProfile(focus=Spring(response=.34, damping=.55)))
```

`Spring.response` 为无阻尼自然周期（秒），内部角频率为 `2π/response`，不是总播放
时长或 Apple API 的 perceptual duration。`damping` 为物理阻尼比：小于 1 回弹，
1 临界阻尼，大于 1 过阻尼。停止条件沿用 Value 的位置/速度容差，不能等待动画结束
才执行回调。小距离通常在前 100–300ms 完成大部分位移，数学尾部可更长。

| token | 默认 response / damping | 用途 |
| --- | --- | --- |
| focus | .34 / .64 | 共享焦点与菜单，高亮前沿 ×1.2、后沿 ×.85 角频率 |
| selection | .28 / .66 | ListView、DataTable、Tree、RadioGroup 的选择位置 |
| control | .25 / .60 | 按钮反馈、开关、滑块、边界反馈 |
| scroll | .25 / 1 | 滚动和菜单头部摄像机，保持阅读稳定 |
| page | .36 / .82 | Tabs 和 PageStack 位移，回弹较轻 |
| reveal | .30 / .76 | 展开区、弹窗和通知 |
| data | .28 / 1 | ProgressBar 与 BarChart，避免超出真实目标 |
| effects | .18 / 1 | 边框、复选标记等颜色过渡 |

自定义控件可调用 `self.value(initial, role="control")`；挂载时使用所属 App 的
profile，切换 profile 保留动量。指定 role 后 profile 决定 speed/damping；不指定
role 的旧调用不变，包括 `damping=0` 的指数缓动。动态 Value 需用
`release_value` 释放，卸载控件时也会整体释放。

## 新增反馈

- 按钮的文字保持原位，焦点背景挤压后回弹并有局部颜色反馈，避免文字左右跳动。
- 复选框逻辑标记立即改变，颜色平滑过渡；开关直接修改 checked 也会同步位置。
- 滑块使用分数单元格的轨道和滑块背景，位置仍裁剪到合法范围。
- BarChart 每个可见标签拥有一个插值值，使用八分之一格柱条；旁边数字始终为最新
  样本。更新字典不创建无界动画，消失标签立即释放。ProgressBar 保持单调过渡。
- 弹窗进入时有轻微位移和背景变暗；关闭立即解除挂载、恢复焦点，退场仅保留文字
  单元格快照。快照不保留原生画布、浏览器、控件或输入事件；动画完成后释放。
  退场期间背景图像仍暂时隐藏，以免原生图像盖住文字快照。
- 通知底栏展开、收回，重复通知直接更新目标；其生命周期由 App 管理。

## 减少动态效果与兼容性

```powershell
$env:COMFORTABLE_TUI_MOTION = 'subtle'      # 温和弹性
$env:COMFORTABLE_TUI_REDUCED_MOTION = '1'   # 直接到达目标，优先于 profile
```

Python 中可使用 `App(..., reduced_motion=True)`。显式 motion 参数优先于
COMFORTABLE_TUI_MOTION，未设置时为 expressive。Reduced motion 不驱动 spinner
的装饰性时钟；已有采样、业务计时器和图像提供者仍独立运行，不会被此开关暂停。
还保留 truecolor、256 色和 NO_COLOR。无色时用反显维持可见焦点。

终端文字仍以整格定位。平滑感来自分数格背景覆盖、八分之一格图形、颜色和时间
连续的弹簧；不宣称文字达到 OLED 像素级运动。同步输出仅在协商成功后启用。
不强行提高默认 FPS；静止时动画停止且差分输出为空。静态 PixelCanvas 的 braille
采样缓存会在像素（包括直接写入 pixels）、背景或采样尺寸变化时失效，移动与
裁剪复用缓存；底层不复制浏览器/视频协议。

交互演示：`python examples/motion.py`。实际组合验证使用独立 ComfortableTop，
见[调研与验证记录](expressive-motion-research.md)。

实际感觉掉帧时，另见[帧调度与卡顿排查](frame-pacing.md)。Windows Python 3.12 的
低分辨率 monotonic 时钟已更换为 QPC 性能时钟，实际运行节奏与固定 dt 的物理
求解测试分开验证。
