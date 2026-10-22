# Glass HLSL 包与独立装饰阶段

2026-10-22，Windows D3D11，Glass协议7、Effect ABI2。
HLSL包是应用显式注册的可信GPU代码，不是网页/插件沙箱；不得把网页消息直接连接到注册入口。
不扫描目录、不联网下载、不自动加载DLL，也不增加WebView渲染依赖。

## 注册与使用

```python
from comfortable_tui import GlassEffect, register_glass_shader
name = register_glass_shader("examples/glass_workspace/shaders/duotone.json")
control.effects = [GlassEffect(name, parameters=(.65,))]
workspace.update()
# 运行后更新既有实例参数，不重编Shader：
workspace.set_effect_parameters(card, control, 0, [.3])
```

manifest与相对路径HLSL是完整可运行包。应用负责把文件随自己的程序分发。

```json
{
  "version": 1,
  "name": "example.duotone",
  "source": "duotone.hlsl",
  "entry": "main",
  "sampling_radius": 0,
  "parameters": [
    {"name": "amount", "minimum": 0, "maximum": 1, "default": 0.65}
  ]
}
```

v1只接受已列出的字段、无重复JSON键；manifest≤32KiB、UTF-8源码≤128KiB。
source解析后须在包目录内（包括符号链接后的目标）；不允许`#include`。
参数至多4个float，必须有有限且有序的minimum/default/maximum；采样依赖半径0..128。
类型名不可覆盖，每进程至多64个HLSL包；编译/反射失败不会占用名称。
注册属于冷路径，D3DCompile期间释放GIL；一次编译，按设备共享PS，每节点参数和纹理独立。

## Shader接口

```hlsl
cbuffer P : register(b0) { float4 crop; float4 params; float4 tint; float4 info; };
Texture2D image : register(t0);
SamplerState sampleLinear : register(s0);
float4 main(float2 uv : TEXCOORD0) : SV_Target {
    float4 c = image.SampleLevel(sampleLinear, uv, 0);
    return float4(lerp(c.rgb, tint.rgb*c.a, params.x), c.a);
}
```

使用`ps_4_0`，单float4 `SV_Target0`输出。输入可声明float2 TEXCOORD0和SV_POSITION。
反射只允许Texture2D t0、sampler s0和≤64字节常量b0，没有额外纹理/UAV/compute资源。
不需要的声明可被编译器优化掉。常量按以下偏移排列：

| 偏移字节 | 字段 | 意义 |
| --- | --- | --- |
| 0 | crop.xy / crop.zw | 扩展patch中的节点起点、节点尺寸，物理像素 |
| 16 | params | 按schema顺序填充的4个参数，空位0 |
| 32 | tint.rgb / tint.a | RGB颜色0..1、绑定opacity |
| 48 | info.xy / info.z / info.w | 保留0、overlay标志0或1、DPI比例 |

uv覆盖含采样边缘的输入patch；需要按节点坐标计算时结合crop和输入纹理尺寸。
纹理BGRA8 UNORM，Shader值为RGBA，**预乘alpha**；透明像素颜色应为0。
Shader负责遵守格式、数值和颜色契约，反射不能证明任意程序安全或执行时间有界。
工作空间不是线性HDR；采样半径必须声明足够大，链内依赖相加。
Canvas效果先处理来源图像再fit，核半径随图像缩放；不承诺固定屏幕像素半径。

## background / content / overlay

三个阶段分别构建独立输入/输出链，禁止同时采样和写入同一纹理：

- background处理当前节点前已合成的前缀，替换对应背景区域，保留圆角以外原前缀。
- content处理自身输入，结果替换自身内容。Card为整卡合成；普通控件为隔离墨水；Canvas为来源图像。
- overlay同样读取节点原始输入，但必须返回**只有装饰贡献**的预乘透明纹理，
  在节点内容/子树完成后进行一次alpha混合。不要把原输入又输出一遍，否则会重复叠色。

内置overlay tint返回着色覆盖贡献，overlay glow仅返回halo而非原图；glow可越过节点边界。
内容与装饰互不串成一条链，overlay不自动读取content滤镜后的输出。
输出外扩为声明依赖范围，不扩布局/命中，仍受祖先clip和Card HWND限制。
blur作为overlay也返回其模糊图像贡献，是否重复叠色由使用者的组合意图决定。
容器content/overlay输入仅是自己的透明墨水，不自动展平后代。

节点corner_radius约束内容和命中，overlay只遵循祖先裁剪以允许外发光。
自定义内容Shader应保持交互几何；未提供任意变形逆向命中。liquid只允许background。

## 验证与依据

WARP像素测试验证实际生产合成Shader的圆角/祖先交集、overlay alpha与外扩halo、
参数生效、编译缓存、非法资源绑定和禁用include。测试CPU读回仅用于断言，产品未新增读回API。
`package_shader_compilations`与内置`shader_compilations`分别计数。

Exa核对的Microsoft原始资料：[D3DCompile](https://learn.microsoft.com/en-us/windows/win32/api/d3dcompiler/nf-d3dcompiler-d3dcompile)、
[D3DReflect](https://learn.microsoft.com/en-us/windows/win32/api/d3dcompiler/nf-d3dcompiler-d3dreflect)、
[GetResourceBindingDesc](https://learn.microsoft.com/en-us/windows/win32/api/d3d11shader/nf-d3d11shader-id3d11shaderreflection-getresourcebindingdesc)。
这里的包限制是本项目的工程边界，不代表Microsoft提供了Shader沙箱保证。
更多设备资源契约见[原生效果SDK](glass-effects-sdk.md)，实际检查见[验收报告](validation/glass/completion.md)。
