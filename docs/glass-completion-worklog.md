# Glass 扩展收尾工作记录

2026-10-21；从 `4ec369f` 继续。用户已授权按剩余四项一直完成至验收及发布。
现有独立效果链、Python帧钩子、原生无状态控件SDK和性能验收已完成，不重新实现。

本批顺序：

1. 原生Motion注册/实例/按需帧回调；Card布局目标与呈现状态分开，默认Spring保留，
   自定义回调可替换；控件视觉变换同步绘制、Canvas位置、命中和无障碍坐标。
2. 原生控件实例create/update/destroy（UI线程）；控件内部row/column/stack容器，
   统一布局、裁剪、焦点、事件路由和Canvas/WebView寿命。
3. 独立overlay效果层、可配置节点形状，以及显式可信HLSL包注册。
4. 自动冷启动及动态压力、真实CEF/终端集成、文档/示例、CI、合并发布。

原生控件模型改变时升级Control ABI和Glass控制协议，FrameProvider 1不变。
不引入WebView/Qt等产品绘制依赖，不加入逐帧Python解释图层；Qt只作自动测试夹具。
优先API驱动、WARP像素及原生诊断，硬件/桌面无法覆盖的场景如实标明。
具体实现以最终SDK为准；本文件为工作记录，不把上述目标当成现有API。

进度：原生Motion注册与按需实例、可替换Card布局运动、原生控件create/update/destroy、
保留HWND的resize已接入并通过首轮真实窗口测试。Glass协议7、Control ABI3、Motion ABI1。
已增加局部row/column/stack树、每Card单个按需墨水图集、递归事件路由、多Canvas支持；
Python322项回归通过，WARP/Motion四项CTest通过。最新增加的restart/事件唤醒和完整
嵌套Canvas/WebView夹具仍需重编并验证；不能把之前二进制的结果算到新补丁上。
上述为早期进度。2026-10-22更新：overlay、圆角、HLSL包和Motion效果参数sink均已实现。
最新正常/纯Python各329通过1跳过、CTest4通过、新集成13项、效果/CEF8项、真实终端advanced
和CPU/GPU各1/4/12卡压力通过。SDK文档已补齐。继续打包、安装、CI、合并和发布；
以[验收报告](validation/glass/completion.md)与GitHub状态为准。
