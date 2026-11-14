# 运维手册

本文档面向运行生产环境的管理员。代码由 Git 更新，`.env` 由服务器本地维护；提示词和运行配置保存在 `SERP_CONFIG_STORE` 指向的部署目录外文件中。

**Issue #18 首次上线必须先按 [配置迁移说明](configuration.md) 从旧生产文件初始化并验证，再执行下面的代码更新。不能先覆盖旧 `config.py` 再用默认值初始化。**

## 代码更新

在服务器的项目目录执行：

```bash
git fetch origin
git switch main
git pull --ff-only origin main
```

确认当前配置包含银行监测主题：

```bash
.venv/bin/python -c "from config import SEARCH_KEYWORDS; print(SEARCH_KEYWORDS['烟草服务银行'])"
```

项目的 Web 前端由 Flask 的 `app.py` 提供，不需要额外构建前端静态资源。代码更新后，按服务器现有的 systemd、supervisor 或进程管理方式重启 Flask 服务。更新代码不会替换生效配置；可用 `python config_cli.py --store /srv/serp-news-state/runtime.sqlite3 status` 核对版本。

## 环境变量

`.env` 被 Git 忽略，不会随 `git pull` 更新。生产服务器的实际运行目录中必须存在：

```ini
MYSQL_TABLE=scored_news
AUTO_MIGRATE_DEDUP_INDEX=0
SERP_CONFIG_STORE=/srv/serp-news-state/runtime.sqlite3
```

AI 阶段模型来自 `.env` 的 `LLM_*` 变量（必填：`LLM_BASE_URL`、`LLM_API_KEY`、各阶段 `LLM_<阶段>_MODEL`；可选参数如 `LLM_SCORING_TEMPERATURE=1`、`LLM_REGION_TEMPERATURE=0.2`）。修改这些变量后先 `python config_cli.py check-model` 验证连通，再重启常驻 Web 服务；只改动 LLM_* 不需要动 `SERP_CONFIG_STORE` 或恢复配置存储。

`AUTO_MIGRATE_DEDUP_INDEX=0` 是日常生产批次的安全默认值：数据库写入使用应用层查重，不会执行索引或其他表结构变更。仅在已确认历史数据且安排维护窗口时，才可临时设为 `1` 执行迁移；完成后立即恢复为 `0`。

修改 `.env` 后，重新启动读取该文件的常驻 Web 服务。定时任务每次启动 `main.py` 时会读取 `.env`；无需因为该变量单独重建定时任务。

## 公积金业务类型初始化（Issue #10）

业务类型字段不会由日常流水线自动创建。先在测试表验证：

```bash
MYSQL_TABLE=scored_news_test .venv/bin/python news_business_type_schema.py --table scored_news_test
MYSQL_TABLE=scored_news_test .venv/bin/python news_business_type_analyzer.py --limit 20
```

确认管理端“业务类型管理”页面的标签、合并预览和补标结果后，再在维护窗口执行生产初始化：

```bash
.venv/bin/python news_business_type_schema.py --table scored_news
```

**部署顺序不可颠倒：**先完成上述 schema 初始化并确认成功，再部署包含公积金业务类型功能的代码，最后恢复或执行日常流水线。以 `--keyword 公积金` 运行摘要器时，缺少 `business_types` 字段会明确报错；未指定关键词的全量运行会继续处理其它关键词，但会跳过公积金的地域和业务类型标注。

随后可分批执行历史补标；默认仅处理 `business_types IS NULL` 的高分公积金新闻，不会覆盖已有标签：

```bash
.venv/bin/python news_business_type_analyzer.py --date-from YYYY-MM-DD --date-to YYYY-MM-DD --limit 100
```

仅在人工核验后需要重标已有结果时，才附加 `--force`。不要在未完成测试表验证前对生产库执行全量补标。

## 江苏机关事务首次上线

按[专题验收与启用说明](government-affairs-acceptance.md)完成候选评测、业务复核和一次性增量启用。通用部署及服务启动不自动补默认配置，不覆盖生产修改；后续部署不重复安装。该专题无需新增数据库字段。

## 每日任务

定时任务应从项目根目录使用项目虚拟环境执行：

```bash
cd /path/to/serp_news && .venv/bin/python main.py
```

不传日期时，主流程处理前一日新闻。若只补跑银行监测主题，使用：

```bash
.venv/bin/python main.py YYYY-MM-DD --keyword 烟草服务银行
```

该主关键词会依次检索工商银行、农业银行、中国银行、建设银行、交通银行、中信银行、浦发银行和南京银行，并写入生产表 `scored_news`。

## 运行后核验

Issue #18 / PR #21 部署当天及后两天的完整验收，按 [上线检查方案](post-deployment-verification.md) 执行；部署前先填写运行记录并留存基线，明后天可直接让 AI 按该文档只读检查。

1. 检查批次日志 `output/YYYY-MM-DD/run_*.log` 是否显示全部步骤成功。
2. 在前端按日期和主关键词 `烟草服务银行` 筛选，检查新闻、分数和摘要是否可见。
3. 数据库核验时确认写入表为 `scored_news`，并以 `fetchdate` 和 `keyword='烟草服务银行'` 过滤。

本机 macOS 的 `tobacco_gov_crawler.py` 另有 launchd 配置；不要将该本机路径或 launchd 命令直接套用于 Linux 服务器。


## 平台日期自动复核

每日 `main.py` 收尾执行到期日期复核，无需新增定时任务。复核保持原采集日期和配置绑定，成功后增量评分入库；达到次数上限后保留证据归档，不要求每日人工核查。查看 `output/date_reviews/last_run.report`、批次原始 JSON 的 `date_review` 及 `DATE_REVIEW` 日志。详情与手动命令见 [平台日期复核与评分时效](platform-date-review.md)。

## 机关事务官微与飞书结果

原00:30主任务处理已启用的「江苏机关事务」专题时，会采集[清单中的官微](government-affairs-wechat-sources.md)。开关默认关闭。部署后在实际运行目录的 `.env` 显式配置以下变量。

```ini
ENABLE_GOVERNMENT_WECHAT=1
WECHAT_API_KEY=填入现有极致了账户密钥
WECHAT_API_VERIFYCODE=
WECHAT_API_BASE_URL=https://www.dajiala.com
```

`WECHAT_API_KEY` 必填。现有账户已验证 `WECHAT_API_VERIFYCODE` 可留空。`WECHAT_API_BASE_URL` 留空时使用 `https://www.dajiala.com`。服务器通过已有 `FEISHU_USER_ID`、`FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 发送结果卡片。原国资官微任务保持停用。

如以后停用「江苏机关事务」专题，同时设置`ENABLE_GOVERNMENT_WECHAT=0`；独立检查会明确跳过，避免仍对已停用业务报漏跑。

主任务默认处理北京时间昨天。补跑时把 `YYYY-MM-DD` 换成业务日。续跑使用同日成功检查点，每次运行单独记录结果。

```bash
cd /www/wwwroot/serp_news || exit 1
/www/wwwroot/serp_news/.venv/bin/python -B main.py YYYY-MM-DD --keyword 江苏机关事务
```

### 每天09:00独立检查

在宝塔增加独立Shell任务，执行周期为每天北京时间09:00（cron表达式`0 9 * * *`）。先确认宝塔/服务器调度时区为Asia/Shanghai；若服务器使用UTC，计划应设为01:00 UTC。命令中的`TZ`只设置进程时区，不能改变宝塔触发时间。

```bash
cd /www/wwwroot/serp_news || exit 1
TZ=Asia/Shanghai /www/wwwroot/serp_news/.venv/bin/python -B wechat_notifications.py --check-missing
```

2026-11-14只读核对时，00:30任务使用项目 `.venv/bin/python`，服务器时区为 Asia/Shanghai。检查任务需独立于主任务触发，这样主任务未启动时仍有提醒。检查任务和主任务共用项目运行目录，运行用户需有读写权限，文件系统需支持 `flock`。

启用时记录首个生效的北京时间00:30运行日期及对应业务日（前一天），在该次主任务计划触发后的09:00开始检查。独立检查读取 `output/wechat_runs/`，识别业务批次未启动、已中断、仍运行及待发送结果。每条待发结果每个北京时间检查日尝试一次。已确认发送的结果和同一检查日的漏跑提醒均去重。

### 诊断与恢复

- `output/YYYY-MM-DD/diagnostics/wechat_collection.json` 保存逐账号采集状态、文章和费用。同目录的 `wechat_collection_state.json` 是续跑缓存，两者一起保留。同日主流程可恢复失败来源，正常零篇单独标记。
- `output/wechat_runs/YYYY-MM-DD/` 保存各次运行和通知状态，`stages/<run_id>/` 保存评分、入库和摘要的阶段报告。主任务收尾最多尝试发送三次，失败记录进入待发送状态，由独立检查处理。
- 同日主流程与采集阶段都有互斥保护。主流程异常退出后，仍在工作的业务子进程继续持有同日锁和运行租约，直到最后一个子进程退出；父进程消失不代表可以立即续跑。不要删除锁文件或缓存来强制并行；先确认已有进程是否结束。
- 这些目录需由任务运行用户读写，并随运行数据备份。清理记录会丢失续跑缓存和通知去重依据。

飞书结果分别显示新增入库、重复和失败，缺少证据的阶段显示未知。图片或视频因文字不足跳过评分时保留诊断。正常零篇、部分失败及异常终止也会记录结果。

采集数按官微文章统计，新增入库只计已提交的新数据库记录。多个来源的同标题文章可能合并到一条数据库记录，摘要成功数按本次实际更新的数据库记录统计，因此各阶段数量可能不同；运行阶段记录保留候选文章与数据库记录的对应关系。

修复飞书配置后可运行以下命令，逐条补发尚未确认送达的终态结果。

```bash
cd /www/wwwroot/serp_news || exit 1
/www/wwwroot/serp_news/.venv/bin/python -B wechat_notifications.py --retry-pending
```
