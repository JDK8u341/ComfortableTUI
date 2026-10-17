# WebView 与跨平台附加层：调研来源

日期：2026-10-17。WebView 检索 8 组，附加层补充检索 17 组，共返回 125 条候选结果，
按完整 URL 去重为 121 条。完整分析和唯一实施计划见
[合并报告](../../docs/webview-research.md)，来源清单见 [sources.json](sources.json)。
候选结果包括旧版本、社区讨论和同一文档的不同 URL，不等于 121 个独立验证结论。

sources.json 保留 webview 原检索记录，追加 overlay 记录和 combined 统计。
返回抓取 URL 不代表已取得有效全文：Apple 部分 HTML 只有标题，改用官方 DocC JSON；
Freedesktop GitLab 两个地址返回防机器人页面，使用上游协议镜像核对，未绕过挑战。

本轮只进行方案调研和文档整理，没有实现 WebView 或新平台附加层，也没有执行新的
浏览器/跨平台性能测试。
