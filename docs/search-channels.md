# 联网搜索渠道

保留原来的模型 Agent 检索任务、网页阅读和 RAG 入库流程，统一检索工具现在接入 22 个服务渠道；百度百科开关开启时另增加百度百科。Bing HTML 和 RSS 属于同一服务，统计只算一个渠道。

| 类型 | 服务 |
| --- | --- |
| 通用搜索 | Bing、DuckDuckGo、百度、搜狗、360、Google、Brave、Yahoo |
| 百科与游戏 | 维基百科、Wikidata、萌娘百科、PRTS、哔哩哔哩蔚蓝档案 Wiki |
| 开发资料 | GitHub、Stack Overflow、MDN、npm |
| 论文与图书 | Crossref、arXiv、Open Library、Internet Archive |
| 社区 | Hacker News |

每个渠道有实际搜索适配器，不把针对某个域名的 Bing 搜索重复计数为独立引擎。垂直渠道只覆盖相应领域，不承诺每个渠道都对任意中文问题有结果。当前网络中部分国外服务不可达，网站也可能拒绝自动访问；它们会记录失败并继续其他来源，不能保证 22 个服务同时在线。

总检索预算仍不超过原来的 20 秒；发现阶段最多 12 个并发请求、正文阶段最多 8 个并发请求。候选轮流取自不同入口，去重和相关性检查后并行读取，单个网站不能耗尽整轮预算。取消后不继续加入结果，不缓存失败空结果。逐渠道状态与候选数显示在原有日志页。

原网页正常读取；部分官方公开 API 提供的正文摘要会明确标为“公开 API 摘要（非全文）”，只有实际取得足够内容才交给模型和知识库。仅有标题或搜索摘要不冒充正文。抓取时间不是内容发布时间。保留公开网址校验、私人关键词/密钥过滤、百科开关和原有模型审核，不使用浏览器登录态，不绕过验证码或访问限制。

接口参考：[GitHub 搜索 API](https://docs.github.com/en/rest/search)、[Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)。
