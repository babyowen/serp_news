# 江苏机关事务：开发验收与首次启用

## 配置保护约定

全国采集供江苏参考；初始包包含 11 个有序检索词及专属评分 system 提示词。既有评分 user 提示词和通用单条摘要提示词继续从运行配置读取，不复制或覆盖生产调整。

代码发布和服务启动不会自动安装专题。首次上线流程明确执行一次 `enable-topic --apply`；后续发布不把该命令放入启动脚本、定时任务或通用部署脚本。用户在后台改词、改提示词或删除专题后，重启/部署不会恢复安装包默认值。

增量安装只添加 SEARCH_KEYWORDS 中的新专题、一个提示词 ID 和一条映射。配置缺失/损坏、旧名称“机关事务管理局”、部分配置、同名不同内容、专题标题赋分规则、版本冲突均拒绝自动覆盖。完全一致时返回 noop，不新建版本。

## 开发验证

从项目根目录运行：

```bash
python run_config_tests.py
```

测试使用临时配置、替代模型/数据库及禁止网络的隔离环境。固定评测集在 `tests/fixtures/government_affairs_eval.jsonl`，55 条均明确标为合成样本：40 条基础样本（20 相关、10 不相关、10 易混淆）以及边界、换关键词、指令注入变体。测试通过只证明程序行为；实际模型准确性和客户业务判断仍须上线前验证。

## 首次启用顺序

以下为发布操作说明，本次开发未执行生产操作。先确认实际服务器、项目 Python、外置配置绝对路径和有效 LLM 环境变量。使用目标版本代码，保留当前生产配置与数据库备份。不要同步开发 SQLite、开发 .env 或默认配置覆盖生产。

1. 仅预览增量，记录 current_version 和 candidate_sha256：

```bash
python config_cli.py --store /actual/state/runtime.sqlite3 enable-topic --topic government-affairs
python evaluate_government_affairs.py --store /actual/state/runtime.sqlite3
```

两者均不调用模型、不启用专题。evaluate 预览列出实际评分接入点、模型、请求参数（不含密钥）和配置/样本/实现指纹。工具从自身代码根目录加载 .env，已有进程环境变量优先；明确传入 --store。

2. 获得本次模型费用授权后执行固定集评测。输出必须是已存在目录中的新文件：

```bash
python evaluate_government_affairs.py --store /actual/state/runtime.sqlite3 --run --output /actual/reviews/government-affairs-eval.json
```

评测在临时外置配置和子进程内读取候选，复用真实评分入口，不切换生产配置，不采集新闻、不写业务 MySQL、不发送通知。每条非空正文最多 3 次调用；SDK 内层重试关闭。空正文不调用模型。报告记录原始评分输出、耗时、尝试数、异常和可用 token 用量；报告失败返回非零。

3. 业务人员逐条对照固定样本正文、预期范围和理由检查报告，尤其检查“国省单位普通动态不能仅凭单位级别给高分”、会议/活动是否有具体举措，以及碳普惠、资产、后勤等跨领域误报。保留原报告副本，在待启用报告的 human_review 填写：
   - approved：确认后才改为 true；
   - reviewer：真实复核人；
   - reviewed_at：含时区的 ISO 时间；
   - reviewed_ids：全部已复核样本 ID。

不要修改实际得分或 passed 以绕过失败。需要调整提示词时，修改候选包/生产适用配置后重跑评测。模型、参数、源配置版本、候选、评分实现或样本变化都会使旧报告失效。未运行真实模型的测试报告不能用于生产启用。

4. 以预览记录的值正式增量启用；备份路径必须不存在：

```bash
python config_cli.py --store /actual/state/runtime.sqlite3 enable-topic --topic government-affairs --apply \
  --expected-version 'STORE_UUID:REVISION' \
  --expected-candidate-sha256 'PREVIEW_SHA256' \
  --evaluation-report /actual/reviews/government-affairs-eval.json \
  --backup /actual/backups/before-government-affairs.sqlite3 \
  --note '机关事务首次启用，评测及逐条业务复核完成'
```

保存前再次验证版本；事务内再检查并发修改。备份失败、报告不匹配、保存失败都不改变当前配置。若期间有任何配置修改，重新预览、评测和复核，不能绕过版本检查。新增 11 个词会增加日常 API 和模型用量，应在第一次运行前确认预算。

5. 核对新配置版本、仅三处增量、所有旧专题及提示词保持原值。确认正常定时任务可发现专题，再按约定日期执行专题批次。首日无历史基线只记录“暂无历史基线”，不会产生低量告警。

## 日常运行与恢复

从项目根目录启动，日期和版本按批次记录填写：

```bash
python main.py YYYY-MM-DD --keyword 江苏机关事务
```

- 采集诊断：`output/YYYY-MM-DD/diagnostics/government_affairs_fetch.json` 逐词记录 ok / empty / failed、数量、错误、配置版本和采集身份指纹。搜索接口失败即使有部分结果也保持 failed；正文添加字段、已存在的央视视频过滤及人民网协议转换不导致正常续跑误判。
- 部分采集失败时不会自动收费重搜。保留合并 JSON、诊断和批次日志；查明原因后，在同一固定配置版本下人工恢复。若决定整组重搜，先把该专题合并 JSON 和诊断移到独立备份目录，再执行专题批次；不要删除其他专题或批次绑定。已有评分/正文产物还须一并归档，避免把新采集与旧评分混用。
- 评分只接受完整的单个 0–5 数字。正常 0 分、空正文 0 分、模型失败分别是 ok、empty_content、failed。失败 score 为 null，并有 score_error / score_attempts；排序置后，不能充当无关新闻。缺专属提示词直接失败，专题不走标题直接赋分规则。
- 修复失败评分：核对该日期批次绑定，使用原版本；仅重新处理 score=null，不重评有效 0 分：

```bash
SERP_CONFIG_REVISION='STORE_UUID:REVISION' python news_scorer.py 江苏机关事务 YYYY-MM-DD --rescore
SERP_CONFIG_REVISION='STORE_UUID:REVISION' python main.py YYYY-MM-DD --keyword 江苏机关事务
```

  主流程的专题入库会按 title + keyword + link 只回填数据库 score IS NULL 的行，也支持回填有效 0 分。
- 摘要阈值仍为 >=3。正文 <=500 字直接使用，较长正文沿用现有摘要模型；有应摘要但最终失败的本专题条目，步骤返回失败，可续跑补缺失摘要。
- 页面按日期/专题分页，点击“查看摘要/检索来源”加载完整摘要和首次命中检索词。null 分显示“未完成评分”；摘要按纯文本展示。
- 高分量告警仍使用 >=4、历史 7/14/21/28 天同星期基线和原有阈值；本功能不改告警算法。

## 发布验收记录（待实际发布填写）

- [ ] 目标环境与外置配置路径核实，备份可读。
- [ ] 真实评分模型固定集全部通过，逐条业务复核完成。
- [ ] 增量启用差异核对，旧配置原样保留。
- [ ] 11 个检索词有独立诊断，空结果和失败状态正确。
- [ ] >=3 摘要、空分恢复、页面详情和告警基线验证。
- [ ] 修改专题配置后重启/普通部署仍保留调整；删除专题不会自动重新启用。
- [ ] 首次运行及后续观察结论留档，再决定关闭 Issue #20。

回退时分别处理代码和运行配置。使用配置历史恢复会创建新版本；应比较从启用以来的其他人工调整，避免整版本恢复误撤销无关修改。不要用开发库替换生产配置。
