# Glass 效果器改造记录

更新：2026-10-21。分支：`codex/liquid-glass-cards`。

之前因关机保存的草稿已继续实施。本轮完成真实图像效果链、自定义原生效果回调、
实例生命周期、参数 API 整理、Canvas/WebView 接入、自动测试及独立 SDK 文档。
当前协议为 Glass 6 / Effect ABI 2；Frame 1 / Browser Security 2 保持不变。
完整使用契约见[效果 SDK](glass-effects-sdk.md)，早期特殊分支绘制不再是实现依据。

## 已落实

- 删除 Card 绘制中的 tint/glow 特殊分支和成功 no-op 回调；
  tint、blur、liquid、glow 都通过独立纹理和真实 D3D11 Shader 执行。
- background 读取已合成前缀，content 处理节点内容，overlay 在内容链后执行；
  无效果时保留旧直接路径。液体折射限定 background，保持命中几何。
- 单消费者获得 Canvas 帧后处理 SRV，不创建另一个 FrameConsumer；支持真实 CEF CPU/GPU 帧。
- 效果描述不可变，参数向量是唯一参数来源；节点的原生状态独立。
  增量合并不重新配置工作区、不重绘墨水、不重建设备资源；
  场景版本和节点代次共同过滤迟到更新。
- 可信扩展以显式 v2 capsule 注册，schema 原生校验；回调在合成器线程运行，
  同线程释放/替换 GPU 资源；窗口关闭不释放调用方拥有的帧源和页面。
- 依赖半径沿链相加，content 外扩为透明、background 在来源边缘钳制；
  保留单链64MiB、全效果256MiB纹理预算，拒绝无界分配。
- 新示例 `examples.glass_workspace.effects`，可选独立 negative Shader 扩展；
  `--preview` 为持续自有窗口演示，默认仍附加真实终端。

## 验证入口

- `native/glass/effects_tests.cpp`：WARP 像素测试、D3D11调试层警告检查、扩展ABI、
  生命周期、设备替换、纹理释放与异常回调。
- `tests/test_glass_effects.py`：参数权威schema、不可变描述、拒绝/回滚和迟到对象引用。
- `examples/verify_glass_effects.py`：真实合成器及可选真实CEF CPU/GPU页面、
  同ID替换、空闲绘制和线程归属计数。
- 检查记录：`docs/validation/glass/effects-pixels.json`、
  `docs/validation/glass/effects-integration.json`。
- 电脑插件已检查持续演示，确认两张卡片实际渲染及滑块独立；
  检查后将预览改为独立左右卡片，避免成组紧凑布局遮挡宿主按钮。

## 明确范围

本轮没有合并或发布。原生帧订阅、布局目标/任意变换钩子、独立 HLSL 包装格式、
自定义控件私有生命周期和多级控件局部容器仍属于扩展总计划的后续项；
它们不是本轮效果SDK可用性的前置条件，不能宣称已经提供。
真实驱动丢失仍沿用显式失败和重建工作区语义；没有新增自动静默恢复。
视觉输出裁剪在现有节点边界内；不承诺跨 HWND 发光、内容扭曲逆向命中或 HDR。

从本文件继续时，应先查看 `git status --short --branch` 与 `git log -5 --oneline`，
再阅读效果 SDK 和验证 JSON；不要重新从 no-op 草稿开始实现。
