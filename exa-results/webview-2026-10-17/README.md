# WebView 与跨平台附加层：调研来源

日期：2026-10-17。WebView 检索 8 组，附加层补充检索 17 组，会话与消息补充检索
5 组，共返回 150 条候选结果，按完整 URL 去重为 141 条。完整分析和唯一实施计划见
[合并报告](../../docs/webview-research.md)，来源清单见 [sources.json](sources.json)。
候选结果包括旧版本、社区讨论和同一文档的不同 URL，不等于 141 个独立验证结论。

sources.json 保留 webview、overlay 记录，追加 sessions_and_messaging 及 combined 统计。
返回抓取 URL 不代表已取得有效全文：Apple 部分 HTML 只有标题，改用官方 DocC JSON；
Freedesktop GitLab 两个地址返回防机器人页面，使用上游协议镜像核对，未绕过挑战。
会话隔离与消息使用当前 CEF 上游头文件核对；上游测试只读未运行，其中部分磁盘共享
测试被 #if 0 禁用，不能作为本项目已经验证的依据。

会话与业务消息属于独立的上层服务；图像 SDK 不加入 Cookie、profile 或主题字段。

本轮只进行方案调研和文档整理，没有实现 WebView 或新平台附加层，也没有执行新的
浏览器/跨平台性能测试。
