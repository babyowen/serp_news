# PR #21 上线检查方案：部署当天及之后两天

本方案用于确认 Issue #18 配置版本化改造上线后，原提示词和配置得到保留，任务正常运行，采集、正文、评分、入库、摘要及管理功能没有出现回归。它是一份后续可重复执行的检查说明，不代表生产验收已经完成。

本轮计划部署日为 **2026-09-09**，时区 **Asia/Shanghai**。明天 **2026-09-10** 执行 D+1 检查，后天 **2026-09-11** 执行 D+2 检查。若实际部署延期，以实际时间重新计算，不机械套用日期。

## 1. 执行范围和证据原则

- 默认只读取生产配置、日志、输出文件、网页和业务数据库；检查报告及导出证据写入独立目录。业务数据库备份由腾讯云负责，不在本方案中重复安排。
- 不为检查启动整条生产流水线、重评、修改关键词/提示词、恢复版本、合并业务标签、修改数据库结构或发送测试通知。写操作功能在隔离配置及测试环境验证；正常业务操作如由管理员主动执行，可以记录其结果。
- 不删除或修改批次绑定来消除报错，不重新初始化生产配置，不用默认模板替换生产调优值。异常修复、补跑及回退应作为独立动作说明影响后执行。
- 不导出 `.env` 全文、密码、API 密钥、Authorization 或 Cookie。完整配置导出含提示词，应限制访问且不提交 Git；一般报告只记录版本、哈希、数量和必要的脱敏样例。
- 每个结论必须附证据时间、业务日期及证据位置；缺少访问权限、基线或样本时标记“未验证/样本不足”，不能写成“通过”。

## 2. 部署记录：今天先填写

下面字段由部署者提供，后续检查者先读取，不猜测服务器路径、任务时刻或部署是否成功。

| 字段 | 待填写内容 |
| --- | --- |
| 实际部署开始/完成时间、时区 | 待填写 |
| 服务器项目绝对路径 | `/www/wwwroot/serp_news`（用户已确认） |
| 实际 Python/虚拟环境路径 | 定时任务为 `/www/wwwroot/serp_news/.venv/bin/python`；Web 服务的实际解释器仍需核对 |
| 部署前代码提交 / 部署后代码提交 | 待填写；PR #21 审查修复提交为 `45e369e`，部署应包含它及最终合并内容 |
| 服务方式、服务名称、运行账户、worker 数 | 宝塔面板启动（已确认）；面板项目名称、运行账户和 worker 数待填写 |
| Web 地址、反向代理访问/错误日志位置 | 待填写；不记录登录密码 |
| 定时任务来源、命令、工作目录、账户、时刻和时区 | 宝塔每天 00:30，项目根目录执行 `.venv/bin/python main.py`，脚本见下；账户及服务器/面板时区仍需核对；独立烟草调度待确认 |
| `SERP_CONFIG_STORE` 绝对路径 / 配置存储 ID | 待填写；建议选部署目录及站点根目录以外的位置，例如 `/www/serp-news-state/runtime.sqlite3`，此示例不代表已创建 |
| 初次生效完整版本 / 完整配置 SHA-256 | 待填写 |
| MySQL 数据库名、实际 `MYSQL_TABLE`、只读访问方式 | 待填写；不记录密码 |
| 摘要开关 / 烟草主题是否启用 / 公积金字段是否具备 | 待填写 |
| 旧生产源码留存路径 / 配置导出与完整备份路径 | 待填写，按 [配置迁移说明](configuration.md) 生成 |
| 检查证据目录 `CHECK_ROOT` | 待填写；使用部署目录外、仅管理员可访问的目录 |
| 部署前最近一次正常完整批次及其业务日期 | 待填写 |
| 部署窗口内仍在运行或准备续跑的旧批次 | 待填写；单独记录承接方法和版本 |
| 部署后主动修改配置、重评、补跑等操作 | 无则明确填写“无”，有则记录时间、版本及主题 |

用户提供的当前宝塔计划任务脚本为：

```bash
#!/bin/bash
PROJECT_DIR="/www/wwwroot/serp_news"
cd $PROJECT_DIR
set -a
source .env 2>/dev/null
set +a
.venv/bin/python main.py >> $PROJECT_DIR/cron.log 2>&1
```

因此主要调度日志为 `/www/wwwroot/serp_news/cron.log`，追加写入 stdout/stderr。检查时同时查看宝塔执行记录和业务日期目录内的 `run_*.log`，按时间划分各次运行。`source .env` 的错误被重定向丢弃，脚本也未对 `cd` 失败立即退出；须核对目录、`.env` 可读性、运行账户和实际环境，不以宝塔任务显示完成作为业务成功证据。本方案记录现状，不自动修改计划任务脚本。

生产首先按 [配置迁移说明](configuration.md) 从**最新旧源码**迁移、核验、保存恢复材料，再更新源码及重启服务。不要先 `git pull` 覆盖旧配置再进行首次迁移。

本地已经核对的旧快照有 22 段提示词、7 个主关键词、17 个检索词、2 个专用映射、27 个黑名单词，规则评分为空。其完整迁移配置哈希为 `5387ea5879f562b20fa47f5c19bd514b2a5a7f475da1665642ff7abdc5db6342`。**这些只是已核验快照的参考值**；服务器此后如有合法调整，应以部署前重新核验的实际配置为基线，保留差异说明，不能为了匹配旧哈希而覆盖新调整。

### 部署前必须留存的运行基线

1. 最近一次正常完整批次的原始日志、运行起止时间、各阶段结果，以及对应日期的 JSON 文件清单和逐文件哈希。
2. 最近 7 个已完成业务日期的“日期 × 主关键词”指标；另取目标日之前 7/14/21/28 天的同星期数据，用于与现有预警逻辑对照。缺失日期注明任务是否执行，不能直接当作正常零数据。
3. 指标至少包含：总新闻数、非空正文数、未评分数、各分数数量、摘要应处理数及缺失数、来源分布、公积金标注覆盖情况。
4. 保存少量稳定历史记录的 ID、主题、日期和字段哈希作为只读参照。后续确认这些记录无意外改变；有正常补摘要、重评或标签合并时按操作记录解释差异。
5. 保存部署前配置原件，以及首次迁移后的版本、逐段提示词哈希和完整配置摘要。

若部署前没有留存运行基线，后续可从旧日志、已有历史文件、只读历史统计补充，但须标记“事后重建基线”；不能宣称已证明所有历史字段均未改变。

## 3. 三次检查的时间与目标

| 检查阶段 | 时间 | 重点与完成条件 |
| --- | --- | --- |
| T0：部署当天 | 2026-09-09，更新服务后 | 迁移一致性、真实服务加载路径、鉴权/会话、网页、定时任务配置；记录部署前基线。旧日期续跑不能冒充新版本完整日验收 |
| D+1：首个完整观察周期 | 2026-09-10 的 00:30 任务结束后 | 默认 `main.py` 处理昨天，所以通常检查 `fetchdate=2026-09-09`；确认实际启动时间晚于部署及实际业务日期，追踪全流程和数据质量 |
| D+2：第二个完整观察周期 | 2026-09-11 的 00:30 任务结束后 | 通常检查 `fetchdate=2026-09-10`；与 D+1 和旧基线比较，验证配置持续可用、历史绑定不变，以及重启/多 worker 下的会话稳定性 |

检查时间以“00:30 + 正常运行耗时 + 30 分钟缓冲”为起点。正常耗时取历史完整批次；超过历史耗时中位数的 2 倍或仍无结束证据时，先查运行状态和错误，不重启或重复启动任务。运行未完成时标记“待完成”，晚些再取数。2026-09-09 当天 00:30 的旧批次通常处理 9 月 8 日数据，应按真实部署时间归入旧基线或过渡批次，不冒充部署后的首个完整周期。

若没有两次部署后的完整自然批次，将观察周期顺延，不能仅凭日历已过两天就验收。

## 4. 检查清单与判断标准

每次报告逐项标记：通过 / 警告 / 失败 / 未验证 / 不适用。不适用须写明关闭的开关或缺少的业务条件。

| ID | 检查内容和证据 | 判断标准 |
| --- | --- | --- |
| C01 | 服务器 `git rev-parse HEAD`、服务启动时间、实际解释器/工作目录/账户、定时任务配置 | 部署内容包含最终合并；Web 和调度使用正确目录与环境。磁盘代码已更新但旧进程未重启，不能判通过 |
| C02 | CLI `status`、Web 模型/历史页显示的版本、任务日志 `[CONFIG]` | 指向同一配置存储 ID。没有配置缺失/校验失败/默认值回退；正常新批次使用启动时选定版本 |
| C03 | 部署基线与当前版本的逐段提示词 SHA-256、原始源码归档 | 未主动改动时逐段及全文哈希完全一致；主动调整只允许有说明的差异。归档提示词同样检查 |
| C04 | 主关键词及顺序、检索词、专用映射、黑名单、规则评分、三阶段模型及请求参数 | 与生效版本及变更记录一致；不能只核对提示词数量或模型名称 |
| C05 | 只读查询 `batch_pins`、各子进程版本日志、上次检查保留的绑定 | 同一主题续跑不换版本；旧绑定无意外增删改；混版本目录按已记录的逐主题方式运行。未操作日期无异常新绑定 |
| C06 | 原始 `run_*.log`、调度日志、各阶段统计和进程退出信息 | 逐主题确认采集、正文、评分、入库、摘要结果；跳过必须有文件/配置依据，不能仅看“流程完成”、总体状态或退出码 |
| C07 | `output/业务日期/` 下原始/评分 JSON、修改时间、条数和解析结果 | 已运行主题的文件可解析且结构合理；空数组只有在来源与日志证实无新闻时可接受；历史文件变化有重评/续跑记录 |
| C08 | 按主关键词、`search_keyword`、`sourceapi` 聚合；对照采集日志 | 预期主题和检索词被执行；来源突然全灭、整主题消失需要解释。来源本来无新闻与调用失败分开记录 |
| C09 | 目标日及历史窗口的新闻量、高分量（>=4） | 波动超过 20% 是调查线索，不单独判失败；稳定主题突然归零或整体大降需结合请求日志、文件和数据库定位 |
| C10 | 文件与记录的 `fetchdate`、新闻发布日期 `date`、实际业务日期 | 用 `fetchdate` 对齐批次，不能与新闻发布日期混用。抽查旧新闻过滤、跨日边界，按现有过滤规则解释保留项 |
| C11 | 原始文件中正文非空比例、长度分布、抓取错误及人工抽样 | 没有整批正文为空/仅导航或报错页；与正常基线比较。数据库本身跳过空正文，不能只看库内正文完整率 |
| C12 | 评分缺失/0/1–5 分布、异常响应、LLM 超时/鉴权/限流日志 | 有效分数在 0–5；0 分是合法业务结果，不能当作未评分。突然全 0、全缺失或分布突变须核对模型调用与规则 |
| C13 | 评分文件条目与入库日志、库内记录身份匹配、同主题去重 | 每条未入库结果能归因于去重、空正文或明确失败；不同主题可保留同新闻，不能按全库标题重复误判。历史已存在记录不一定计入目标日新增数 |
| C14 | score>=3 且有正文的摘要候选、`short_summary`、摘要日志 | 开启摘要且批次结束后，缺失项须解释；正文<=500 字直接写原文是正常行为，不要求都调用 LLM。关闭摘要时标记不适用 |
| C15 | 高分公积金记录的 `region`、`business_types`、来源正文及管理看板 | 字段/标注功能启用时核对覆盖和合理性；地域与业务类型由摘要阶段处理，不能以独立地域步骤“成功”代替证据；没有候选则样本不足 |
| C16 | 中国烟草主题、`tobacco_gov_crawler.py` 日志、对应记录 | 只在启用该主题/既有调度时要求执行；不把本机 launchd 配置当成 Linux 服务器配置；无新文章须有来源依据 |
| C17 | 首页、日期详情、数据库页的 GET；不同主题、分数和来源筛选 | 页面正常且与同范围 SQL/文件一致；日期详情读文件，首页/数据库页读 MySQL，差异先按数据来源解释 |
| C18 | 管理关键词、模型、历史、运行监控、业务类型和业务看板 GET | 已认证访问正常，未认证管理页 401；管理响应 `Cache-Control: no-store`；提示词页面版本正确，无随机 CSRF/会话错误 |
| C19 | 隔离回归及管理员正常业务操作记录 | 保存/冲突/恢复、任务启动/重评、标签合并有隔离测试证据；生产没有真实操作样本时明确标记其写入链路未做生产实测，不为验收修改生产数据 |
| C20 | 新闻量预警计算日志、已有实际通知及错误记录 | 现有规则为同星期最多四个有效样本、总量与>=4分新闻波动严格大于20%；没触发时没通知属正常，触发却未投递需排查；不发送测试消息 |

### 数量波动与质量波动如何判断

- 对照最近 7 个完成日的中位数，并核对同星期样本；记录样本数、是否节假日、来源波动、主动关键词调整或停机窗口。配置不同的主题不能不加解释地直接比较。
- 总量/高分量变化超过 20% 标记警告；下降超过 50%、稳定主题归零或单来源持续消失优先调查，**均不是自动回滚门槛**。
- 正文、评分、摘要覆盖率较可比基线下降 10 个百分点时调查；分母不足 20 条时优先逐条抽样，不用小样本比例宣布系统异常。
- 整批无数据且有抓取/鉴权/入库错误、配置版本错误、原提示词未经授权改变，属于明确失败；新闻自然减少不能直接归因于 PR。
- 每个启用主题抽查 3 条（不足则全查）：尽量覆盖高分、低/0分和不同来源；公积金另看地域/业务标签、银行主题另看主题相关性。保留记录 ID/标题片段/链接和判断理由，不把摘要流畅等同于事实正确。

## 5. 可执行的只读取证方式

### 5.1 配置、代码与提示词摘要

从服务器项目根目录、实际虚拟环境执行。`config_cli.py` 不自动加载 `.env`，须显式指定真实的存储绝对路径。示例中的配置存储占位路径必须先替换，不能视为已设置的服务器路径。

```bash
cd /www/wwwroot/serp_news
git rev-parse HEAD
git status --short
.venv/bin/python config_cli.py --store /actual/outside/project/runtime.sqlite3 status
.venv/bin/python config_cli.py --store /actual/outside/project/runtime.sqlite3 history --limit 20
```

下面 Python 片段在该目录用 `.venv/bin/python` 执行，只读取配置并输出哈希和非密钥摘要；可将输出保存到本次独立证据目录。启动环境由项目根目录 `.env` 读取，已有进程环境优先，检查时不要临时设置 `SERP_CONFIG_REVISION` 来掩盖服务实际的环境配置。

```python
import hashlib
import json
import os
from runtime_config import get_store

store = get_store()
snapshot = store.read()
document = snapshot.document
print(json.dumps({
    "store_path": str(store.path),
    "active": snapshot.summary(),
    "task_revision_env": os.getenv("SERP_CONFIG_REVISION") or None,
    "prompts": {name: {"status": p["status"],
        "sha256": hashlib.sha256(p["text"].encode("utf-8")).hexdigest()}
        for name, p in document["prompts"].items()},
    "keywords": document["settings"]["SEARCH_KEYWORDS"],
    "keyword_prompt_ids": document["keyword_prompt_ids"],
    "blacklist_count": len(document["settings"]["blacklist_keywords"]),
    "scoring_rule_count": len(document["settings"]["NEWS_RULE_BASED_SCORING"]),
    "models": document["models"],
}, ensure_ascii=False, indent=2))
```

上面的 shell/脚本只能证明该次读取的环境。还需通过实际 Web 页面和任务日志核对常驻进程；多个 worker 应反复访问，结合各 worker 日志检查，不能只测一个终端进程。

如需完整配置差异，使用 `config_cli.py export` 写到证据目录中**尚不存在的新文件**，与 T0 导出比较；完整内容仅留管理员私有目录。日常检查不调用 `import`、`restore`、`init`、`pin_batch` 或 `prepare_batch`。

### 5.2 批次绑定与原始日志

用 SQLite 只读连接读取实际配置文件，不调用会创建绑定的业务函数。`directory` 必须是服务实际使用的绝对输出目录；符号链接/不同工作目录应先解析核实。

```python
from pathlib import Path
import sqlite3
from runtime_config import get_store

store_path = get_store().path
directory = str(Path("/www/wwwroot/serp_news/output/2026-09-09").resolve())
with sqlite3.connect(store_path.as_uri() + "?mode=ro", uri=True) as connection:
    rows = connection.execute(
        "SELECT b.keyword, m.store_id, b.revision, r.sha256 "
        "FROM batch_pins b JOIN revisions r ON r.id=b.revision "
        "CROSS JOIN metadata m WHERE m.id=1 AND b.output_directory=? "
        "ORDER BY b.keyword", (directory,)).fetchall()
    for row in rows:
        print(row)
```

```bash
# 换成实际业务日期；查看全部原始日志，不能只看管理页筛选后的摘要
rg -n '\[CONFIG\]|version=|sha256=|总体完成|统计|失败|ERROR|Traceback|SKIP|跳过|429|401|403|timeout' output/2026-09-09/run_*.log
```

同时留存 `/www/wwwroot/serp_news/cron.log` 中该次任务的 stdout/stderr、宝塔任务执行记录、Web 服务及反向代理错误日志。`cron.log` 若已轮转，应包括对应归档。无匹配输出不等于日志正常；缺少文件、无权限和日志未采集需分别记录。`output/run_status.json` 是辅助证据，可能只表示最后一次后台派发或留下过期状态；宝塔直接执行 `main.py`，不能要求每次任务都由后台状态文件完整反映。

读取 JSON 时逐文件解析，记录 SHA-256、条数、正文空值数、分数分布和错误。对比库与文件时按 `(keyword, title, link)` 并考虑项目的同主题标题去重回退规则匹配，而非要求两边计数简单相等。

### 5.3 MySQL 只读统计

通过已有数据库客户端/只读账户执行。先确认数据库和 `MYSQL_TABLE`，下面使用 `scored_news` 作示例；替换表名须核对合法标识符。不要导入 `write_to_mysql.py` 来做检查，它会在导入时建立业务写连接。

```sql
SELECT DATABASE(), @@session.time_zone, NOW();
SHOW COLUMNS FROM scored_news;

-- D+1 示例；D+2 改为 2026-09-10，历史窗口按实际目标日平移。
SET @target_date = '2026-09-09';
SET @baseline_from = DATE_SUB(@target_date, INTERVAL 28 DAY);
START TRANSACTION READ ONLY;

SELECT fetchdate, keyword, COUNT(*) AS total,
       SUM(CASE WHEN TRIM(COALESCE(content, '')) <> '' THEN 1 ELSE 0 END) AS with_content,
       SUM(CASE WHEN score IS NULL THEN 1 ELSE 0 END) AS missing_score,
       SUM(CASE WHEN score = 0 THEN 1 ELSE 0 END) AS zero_score,
       SUM(CASE WHEN score >= 4 THEN 1 ELSE 0 END) AS high_score,
       SUM(CASE WHEN score < 0 OR score > 5 THEN 1 ELSE 0 END) AS invalid_score,
       SUM(CASE WHEN score >= 3 AND TRIM(COALESCE(content, '')) <> '' THEN 1 ELSE 0 END) AS summary_eligible,
       SUM(CASE WHEN score >= 3 AND TRIM(COALESCE(content, '')) <> ''
                 AND TRIM(COALESCE(short_summary, '')) = '' THEN 1 ELSE 0 END) AS missing_summary
FROM scored_news
WHERE fetchdate BETWEEN @baseline_from AND @target_date
GROUP BY fetchdate, keyword ORDER BY fetchdate, keyword;

SELECT fetchdate, keyword, sourceapi, COUNT(*) AS total
FROM scored_news
WHERE fetchdate BETWEEN @baseline_from AND @target_date
GROUP BY fetchdate, keyword, sourceapi ORDER BY fetchdate, keyword, sourceapi;

SELECT keyword, score, COUNT(*) AS total
FROM scored_news WHERE fetchdate = @target_date
GROUP BY keyword, score ORDER BY keyword, score;

SELECT keyword, title, link, COUNT(*) AS duplicate_count
FROM scored_news WHERE fetchdate = @target_date
GROUP BY keyword, title, link HAVING COUNT(*) > 1 LIMIT 100;

ROLLBACK;
```

这些 SQL 返回没有记录的日期/主题时不会自动补零。检查者必须用批次的关键词清单补齐缺项，并区分“已执行且零条”和“未执行/未取到证据”。分页或客户端截断不能当作完整结果。

确认存在 `region` 和 `business_types` 字段后，再在只读事务中抽查目标日高分公积金记录；没有字段时记录功能未具备，不执行迁移脚本：

```sql
START TRANSACTION READ ONLY;
SELECT id, fetchdate, title, link, score, region, business_types,
       CHAR_LENGTH(content) AS content_length,
       CHAR_LENGTH(short_summary) AS summary_length
FROM scored_news
WHERE fetchdate = @target_date AND keyword = '公积金' AND score >= 3
ORDER BY id LIMIT 30;
ROLLBACK;
```

查询窗口受限于目标日前 28 天；数据量大时先检查索引/执行计划，采用现有只读副本或缩小窗口，并注明副本延迟。无需导出整库。

### 5.4 网页与写操作功能

- 用部署记录中的真实地址，检查 `/`（显式设置 `date_from`/`date_to`）、`/date/业务日期`、`/database`；页面筛选与 SQL 使用相同主题、日期、分数条件。
- 已登录后读取 `/admin/keywords`、`/admin/models`、`/admin/config-history`、`/admin/runs`、`/admin/business-types`、`/admin/business-type-dashboard`。不要点击保存、恢复、启动、补评或确认合并按钮来取证。
- 使用无登录状态的访问验证管理 GET 返回 401。已认证 GET `/admin/config-history?version=garbage` 和 `/admin/config-export?version=garbage` 应返回 400，正常版本应能读取；真实 503 要追查配置存储，不能当作参数错误忽略。
- 检查管理页面/错误响应的 `Cache-Control: no-store`，并检查多个 worker 是否持续出现会话/CSRF 错误。日志和报告中不记录 Cookie/token。
- 生产不做缺少 CSRF 的 POST 探测：若保护意外失效，会真的触发任务或修改数据。离线验证使用 `python run_config_tests.py`，或 `python run_config_tests.py -k test_config_followup` 验证本轮 23 项回归；运行时使用独立临时目录和 mock，不与生产任务争用测试输出。若服务器没有测试依赖，在隔离环境使用相同部署提交及 `requirements-dev.txt`，不为检查升级生产虚拟环境。
- 合并前本地已通过 179 项测试、12 项子测试和 19 项日志检查。此结果证明回归用例通过，不替代服务器依赖、服务启动及真实数据流验收。

## 6. 异常分级和处置

| 等级 | 典型情况 | 检查者下一步 |
| --- | --- | --- |
| 失败：配置/历史安全 | 原提示词未经记录改变、存储损坏或指向错误位置、旧主题绑定异常改变 | 保存证据并立即通知用户，建议暂停新任务；不要自动恢复或重置。按配置迁移文档选择匹配的代码与配置恢复方案 |
| 失败：业务链路 | 已完成周期整批无数据且有阶段错误、入错表、鉴权/LLM持续失败、持续 503、主要功能不可用 | 定位最早失败阶段，区分部署配置、第三方服务及代码回归；提出最小修复/补跑方案，不先盲目重跑 |
| 警告：需解释 | 数量/质量波动、单来源超时、个别摘要缺失、低样本、已知独立脚本限制 | 给出受影响主题/数量和证据，解释能否自然恢复及是否需要专项跟进 |
| 待完成/未验证 | 任务还在跑、缺日志、无数据库访问、没有功能样本 | 列出缺少证据和下一次检查时点，不宣称通过 |

回退不是单独 `git reset`：旧代码需要匹配的旧配置源码，新代码需要有效的外部存储。已经生成的新数据/绑定保留取证，不自行删除。实际恢复、重启、暂停调度、重评或修数据由用户另行确认执行范围。

## 7. 每次检查的报告模板

报告保存在 `CHECK_ROOT/检查日期-实际时间/report.md`，原始证据放在同目录中，不覆盖 T0 或上一次检查。给用户的答复必须区分已经验证与尚未验证。

```markdown
# 上线检查记录
- 检查阶段：T0 / D+1 / D+2
- 检查时间与时区：
- 部署提交与实际服务启动时间：
- 目标业务日期 / 批次起止时间：
- 生效配置版本与哈希 / 批次绑定版本：
- 基线范围、样本数量及配置差异：
- 总体结论：通过 / 有警告 / 失败 / 待完成 / 证据不足

| 检查项 C01–C20 | 结果 | 关键数据及基线对比 | 证据路径/查询时间 | 说明 |
| --- | --- | --- | --- | --- |

| 主题 | 原始文件条数 | 评分文件条数 | 目标日库内条数 | >=4分 | 缺正文/缺评分/缺摘要 | 波动原因 |
| --- | --- | --- | --- | --- | --- | --- |

- 提示词/配置是否保留；所有差异的解释：
- 运行中、失败、跳过和没有样本的功能：
- 与昨日相比的新问题/已恢复问题：
- 是否发现应暂停新任务的情况及理由：
- 建议动作、影响范围和待用户确认事项：
- 下一次检查日期/时点、需要补齐的材料：
```

D+2 可宣布“本次上线观察通过”的条件：两次真实完整批次均有证据；配置及历史绑定符合预期；主要链路和页面通过；没有未解释的严重数据损失；剩余警告有明确影响范围。生产写操作若仅在隔离环境验证，应继续在结论中保留该验证边界，不写“所有生产功能均已实测”。

## 8. 明后天如何调用 AI

可以直接发送：

> 请按 docs/post-deployment-verification.md 执行 D+1（或 D+2）检查。先读取部署记录，确认业务日期及任务是否完成，再只读取证。不要主动修改生产配置、重跑任务或更新数据库。生成逐项检查报告，缺少证据的项目明确标记，发现问题先说明原因和建议。

执行者应先核对服务器代码/配置版本及本方案，而不是沿用旧会话中的“已通过”结论。若没有服务器读取权限，向用户集中索取：部署记录、配置哈希摘要、目标日及基线日志/文件统计、上述 SQL 聚合结果和必要页面证据；不要索取整份 `.env` 或数据库备份。

本方案按用户主动调用执行，不创建定时自动检查或通知。
