# Government WeChat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为江苏机关事务增加38官微，发送完整处理结果，并提供09:00独立漏跑检查。

**Architecture:** 配置清单驱动独立采集模块，输出可供现有流水线合并的文章与逐账号诊断。父进程用持久化运行记录汇总实际阶段结果，复用飞书发送函数。独立CLI检查昨日运行记录及待发送结果。

**Tech Stack:** Python、requests、BeautifulSoup、pytest、现有Flask/MySQL流水线。

**Spec:** docs/superpowers/specs/2026-11-14-government-wechat-design.md

## Global Constraints

- 所有日期以Asia/Shanghai为准，正常任务处理昨天。
- 38个启用来源及宁波待核验，9个官网仅记录，旧国资官微排除。
- 保留现有关键词配置版本、历史数据、已成功的正文/评分/摘要，现有同标题去重。
- 本轮开发不运行生产流水线、模型或业务写入，不把凭据放进源码、日志、报告。
- 使用run_config_tests.py离线测试入口。暂时性请求最多3次，凭据/额度/永久失效不重试。
- ENABLE_GOVERNMENT_WECHAT显式启用，默认关闭以兼容既有部署；启用而缺失凭据必须报失败。
- 环境变量WECHAT_API_KEY、WECHAT_API_VERIFYCODE、WECHAT_API_BASE_URL；基址默认https://www.dajiala.com。

## Review Focus

- 失败后追加文章时，已有评分文件不能掩盖新增文章。
- 跨日、分页重复和空列表必须产生正确完整性状态。
- 混合搜索/官微及同标题合并时，通知不得夸大新增入库。
- 硬中断、同时执行、通知超时和旧批次文件不能伪造成功。
- 空账号、身份不匹配、验证页和供应商原始错误不能泄露凭据或变成有效文章。

### Task 1: 来源注册表与采集器

**Files:** topic_packages/government_affairs_wechat.json、wechat_collector.py、test_wechat_collector.py。

**Interfaces:** `load_registry(path=None) -> dict`；`collect_wechat(target_date, *, registry_path=None, client=None, output_root='output') -> dict`。返回报告含date、registry_sha256、complete、accounts、articles、api_cost（本次费用）、total_api_cost（该业务日累计）。每个account含ghid、name、status(ok/empty/failed)、count、error、费用。文章含keyword/main_keyword=江苏机关事务、search_keyword/source=真实账号名、sourceapi=极致了api、date/fetchdate、title/link/content/wordcount、wechat_ghid/wechat_article_id、wechat_content_status(ok/insufficient/failed)、publication_check。报告与文章持久化在output/<date>/diagnostics/wechat_collection.json，通过同日锁避免重复请求。

- [x] 编写并运行失败用例，覆盖38启用/1停用、业务日、分页多图文、身份校验、正文兜底、错误脱敏、正常零篇、失败续跑、缓存与费用。
- [x] 实现接口和CLI `python -B wechat_collector.py YYYY-MM-DD`，CLI仅采集，报告complete决定退出码。
- [x] 列表POST /fbmain/monitor/v3/post_history，ghid/url/nickname/offset/key/verifycode；详情GET /fbmain/monitor/v3/article_detail，mode=2。所有有效日期证据交现有assess_publication_evidence处理并绑定link。
- [x] 来源匹配biz，微信文章标识使用biz/mid/idx，保留可访问原文URL。分页以覆盖目标日或is_end终止，重复游标/异常页标失败。正文不足为终态，正文请求失败可重试。
- [x] 运行 `.venv/bin/python -B run_config_tests.py -k wechat`（主代理将测试文件登记），通过后自检并提交本任务文件。

### Task 2: 运行记录、飞书与09:00检查

**Files:** wechat_notifications.py、test_wechat_notifications.py。

**Interfaces:** `start_run(target_date, *, output_root='output') -> dict`返回run_id/date/started_at/status；`finish_run(run, outcome, *, output_root='output', send_fn=None) -> dict`。outcome含status(success/partial/failed/interrupted)、collection报告摘要、scoring、database、summary阶段字典、error。`check_missing(*, now=None, output_root='output', send_fn=None) -> dict`。生产发送复用news_volume_alert.send_feishu，导入应无业务数据库副作用。

- [x] 先测试正常/零/部分失败/中断结果卡片，真实新插入和重复分别展示，未知阶段标未知，发送失败待发、重复检查去重、北京时间目标日和09:00边界。
- [x] 在output/wechat_runs/<业务日>/按run_id保存开始/结束/通知状态，写入原子化并加锁。新运行即使同业务日也单独通知，重复finish或补发不重复送达已确认结果。
- [x] 运行状态使用进程信息辨别终止与仍运行，检查09点前不报警，过时记录不算完成。最新失败结果已经通知时不额外重复报警；缺少结果或通知失败按真实情况报告/补发。
- [x] 实现CLI `python -B wechat_notifications.py --check-missing`，加载项目.env但不调用业务流程，开关关闭时显式skip。
- [x] 运行 `.venv/bin/python -B run_config_tests.py -k wechat` 并自检提交本任务文件。

### Task 3: 流水线接入和真实计数

**Files:** main.py、government_affairs_pipeline.py、news_scorer.py、fetch_content.py、write_to_mysql.py、news_item_summarizer.py、run_manager.py、run_config_tests.py、相关测试、.env.example。

**Interfaces:** Task1报告进入collect新增可选参数；Task2的outcome由各阶段实际结果构成。数据库与摘要子进程通过仅官微运行设置的报告环境变量写结构化结果，包含提交后的新增/重复/失败及官微候选关联，不能用当天总量代替本次操作。

- [x] 基线回归先运行；新用例复现补采新增文章被已有评分跳过、数据库报告计数与失败传播、主流程finally通知及异常路径。
- [x] 使用显式北京时间默认日期，目标关键词且开关启用时开始运行记录。整次同日官微主流程互斥，避免并发交错。
- [x] 安全合并官微文章并保存来源清单摘要。重跑先校验现有诊断指纹，复用既有搜索和成功官微结果，失败来源可续补，历史无官微字段可兼容升级。
- [x] 官微正文已验证的复用，终态正文不足保持诊断；失败正文在官微重试路径恢复，避免通用浏览器误抓验证页。
- [x] 增量补评分后进入既有入库/摘要，准确持久化阶段报告并在完整流程finally发送一条结果，异常也保存终态。
- [x] 离线端到端从main入口验证38来源接入、部分失败恢复、零篇结果、隔日09点检查，并用既有及未来关键词样例检查兼容性。通过全套隔离回归。

### Task 4: 来源清单、运维与最终审阅

**Files:** docs/government-affairs-wechat-sources.md、docs/operations.md、docs/government-affairs-wechat-verification.md、README.md。

- [x] 来源文档列39官微候选的官方入口/真实昵称/ID/验证情况/启用状态及9官网待接入，注明南京换号与宁波迁移。
- [x] 写明环境配置和独立宝塔任务 `0 9 * * *`，明确Asia/Shanghai、绝对路径、状态目录、启用日期、补发与续跑操作。
- [x] 全套隔离回归、差异检查、真实入口集成测试完成后记录实际命令/数量/限制。已有线上样本证据与新代码验证分别注明日期和范围。
- [ ] 独立审阅整个分支，修复重要问题并复验。交付本地分支、来源文档与验证文档。
