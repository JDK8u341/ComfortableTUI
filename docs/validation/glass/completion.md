# 扩展改造验收

2026-10-22，Windows本机桌面、物理DPI120（125%）。
Glass协议7 / Control ABI3 / Effect ABI2 / Motion ABI1 / Frame1 / BrowserSecurity2。

## 执行结果

| 检查 | 结果 |
| --- | --- |
| 全库正常与纯Python | 各329通过、1跳过，包含独立原生控件插件注册 |
| CTest | 4通过：帧协议、WARP效果像素、效果稳定性、Motion与布局契约 |
| 新扩展集成 | 13项通过，见completion-integration.json |
| 原效果器与真实CEF | 8项通过，CPU/GPU attach与detach；静态400ms效果draw为0 |
| 真实终端 | advanced示例在Windows Terminal附加；两卡同属CASCADIA_HOSTING_WINDOW_CLASS，CPU Canvas与CEF GPU页面同时显示 |
| CPU/GPU压力 | 各1/4/12卡、每组12次动态替换/resize，见completion-stress.json |

新集成覆盖：私有控件与独立Motion实例、resize保留HWND、持有GIL时原生动画继续、
停稳停止、restart不重建、原生效果参数动画无场景提交/纹理重分配/HLSL重编、同ID替换、
嵌套双Canvas、圆角命中、MSAA几何与事件顺序/Tab、祖先隐藏/禁用、真实CEF CPU/GPU嵌套。
最终Control3建3毁、Motion3建3毁、wrong_thread=0。

像素测试使用生产合成Shader检查圆角与祖先交集、背景替换alpha、独立overlay与外扩halo，
另验证HLSL参数、非法绑定/include拒绝、失败不占名称。减少动态设置可唤醒停稳Motion。

压力测试各组稳态效果表面分配与Shader编译增量均为0，关闭后效果纹理计数归零；
CPU静态输入空闲pass为0。12卡效果纹理16,425,216字节（约15.7MiB）。
GPU来源在持有GIL期间持续进展。这些计数不是屏幕FPS或输入到光子的延迟。
沿用[性能报告](../../glass-effects-performance.md)的口径，不含源纹理、驱动缓存与DComp内存。

## 复现

先按[动画SDK](../../glass-native-motion.md)构建运行时和示例插件：

```powershell
$env:COMFORTABLE_GLASS_EXTENSION='.build/glass-controls'
python -m pytest -q
python -m ruff check .
python -m mkdocs build --strict
ctest --test-dir .build/glass-native -C Release --output-on-failure
python -m examples.verify_glass_completion --webview
python -m examples.verify_glass_effects --webview
python -m examples.benchmark_glass_workspace --source cpu
python -m examples.benchmark_glass_workspace --source gpu
python -m examples.glass_workspace.advanced --webview --smoke-seconds 9 --report .build/advanced-terminal.json
```

集成验证器显示并关闭自己的Qt夹具；最后一个命令须在真实可见终端运行。
Qt不参与产品渲染。报告去除用户路径、HWND和背景截图。

## 范围

未跨物理显示器切DPI、未重置真实显卡驱动、未验证其他终端品牌。
已有100/125/150/200%合成缩放和设备替换检查不能替代这些硬件行为。
Motion支持平移/透明度，没有scale/rotation或任意内容扭曲逆命中。
容器自身效果不自动展平后代；Card content链处理整卡合成。
没有自动静默设备恢复、HDR、跨HWND发光或完整层级UIA树。
图形部分仍为Windows-only。
