# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

自动化新闻采集与AI分析系统。7步流水线：采集 → 正文提取 → AI评分 → 数据库写入 → 单条摘要 → 地域分析 → 烟草爬虫。Bootstrap管理前端支持关键词配置、模型查看、运行监控。

## 常用命令

```bash
# 运行完整流水线（默认处理昨天新闻，7步）
python main.py
python main.py YYYY-MM-DD          # 指定日期

# 单独模块执行
python fetch_and_filter.py "养老" YYYY-MM-DD
python fetch_content.py "养老" YYYY-MM-DD
python fetch_content.py "测试" YYYY-MM-DD --url="https://example.com/news"  # 单URL调试
python news_scorer.py "养老" YYYY-MM-DD
python news_item_summarizer.py YYYY-MM-DD
python news_region_analyzer.py --keyword 公积金 --date YYYY-MM-DD
python news_business_type_schema.py --table scored_news_test
python news_business_type_analyzer.py --date-from YYYY-MM-DD --date-to YYYY-MM-DD
python tobacco_gov_crawler.py
python tobacco_gov_crawler.py --date YYYY-MM-DD    # 指定日期（默认昨天）
python tobacco_gov_crawler.py --dry-run             # 仅解析不入库
python write_to_mysql.py --date YYYY-MM-DD

# 启动Web管理界面
python app.py

# 环境设置
cp .env.example .env   # 填入API密钥和MySQL连接信息
pip install -r requirements.txt
playwright install
```

## Git 推送

```bash
# 标准 push（需要 gh auth login 已登录）
git push

# 如果 HTTPS 认证失败，用 gh token 内联推送
git -c credential.helper='!f() { echo "username=babyowen"; echo "password=$(/opt/homebrew/bin/gh auth token)"; }; f' push
```

## 架构：子进程流水线

`main.py` 通过 `subprocess` 依次调用各模块。每个步骤是独立进程，单个失败不阻断流水线。每个步骤开始前检查输出文件是否已存在，存在则跳过（支持断点续跑）。

| 步骤 | 脚本 | 说明 | 控制 |
|------|------|------|------|
| 1 | `fetch_and_filter.py` → `news_fetcher.py` | 5源采集，去重合并 | - |
| 2 | `fetch_content.py` | 5级兜底正文提取 | - |
| 3 | `news_scorer.py` | AI评分0-5分，3线程并发 | - |
| 4 | `write_to_mysql.py` | MySQL持久化 | - |
| 5 | `news_item_summarizer.py` | 单条500字摘要（公积金同时写地域和业务类型） | `ENABLE_ITEM_SUMMARIZER` |
| 6 | 地域分析 | 由单条摘要阶段一并处理 | - |
| 7 | `tobacco_gov_crawler.py` | 烟草官网5板块爬取，支持`--date`指定日期 | 条件触发（含中国烟草时） |

> `news_region_analyzer.py` 保留为独立脚本，可用于补充或应急处理地域数据；主流程不再单独启动它。

## 关键词两层结构

定义在 `config.py:SEARCH_KEYWORDS`，主关键词用于业务分类，搜索关键词用于API调用。`DEFAULT_KEYWORDS` 自动从 `SEARCH_KEYWORDS.keys()` 生成。前端 `/admin/keywords` 可直接管理。

数据流转：
```
主关键词 → 遍历搜索关键词 → 调用新闻API
         ↓
    tmp_{date}_{main_kw}_{search_kw}.json（临时，合并后删除）
         ↓
    {date}_{main_kw}.json → _scored.json（AI评分后）
```

### 烟草服务银行

- 主关键词 `烟草服务银行` 对应8个搜索词：工商银行、农业银行、中国银行、建设银行、交通银行、中信银行、浦发银行、南京银行。
- 这是江苏烟草使用的银行重要新闻分类，不要求新闻涉及烟草。评分规则以 `config.py` 的 `NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK` 为准：总行重大事项5分，江苏省内重要动态或银行与烟草同时出现的实质新闻原则上4分，品牌宣传/服务纪实固定3分，非8家银行主体直接1分。
- 单主题全流程命令：`python main.py YYYY-MM-DD --keyword 烟草服务银行`。省略日期时，`main.py` 处理前一日数据。

## 共享工具模块

- **`db_utils.py`** — 统一数据库连接（含3次重试）+ `MYSQL_TABLE` 环境变量切换测试表 + `ping_connection` 保活
- **`llm_client_pool.py`** — 统一LLM客户端池，按(api_key, base_url)复用，自动回收
- **`config_manager.py`** — 读写 config.py 中的关键词和模型配置（供前端调用）
- **`run_manager.py`** — 运行状态管理（启动/监控/历史/日志）

## 关键配置

- **`config.py`** — 关键词映射、AI提示词（通用+关键词专属）、模型配置、黑名单
- **`config_grab_rules.py`** — 站点专属抓取规则（`CUSTOM_GRAB_RULES`注册表）
- **`.env`** — API密钥、MySQL连接、流程开关、管理员认证

### 数据库去重迁移

- 日常运行必须保持 `AUTO_MIGRATE_DEDUP_INDEX=0`：写入脚本使用应用层、主关键词范围内的查重，不修改表结构。
- 只有维护窗口才可显式设为 `1`，将旧的全局 `title_link` 唯一索引迁移为 `keyword_title_link`。执行前必须检查历史重复数据。
- `.env` 在 `.gitignore` 中；部署时 `git pull` 不会更新它，需在服务器实际运行目录的 `.env` 手工添加或确认该变量。

## 术语约定

- **日期**：统一以 `fetchdate` 字段为准（抓取日期），而非新闻自身的 `date` 字段（可能是相对时间如"昨天"、"7小时前"）
- **生产表**：`scored_news` — 线上正式数据
- **测试表**：`scored_news_test` — 本地开发测试数据
- 通过 `.env` 中 `MYSQL_TABLE` 环境变量控制读写哪张表，默认应设为 `scored_news_test`

## 数据库表

- **scored_news** — 新闻主表/生产表（含 `short_summary`、`region` 和 `business_types` 字段）
- **scored_news_test** — 新闻测试表（含 `short_summary`、`region` 和 `business_types`，本地开发用）
- **summary_news** — 日报摘要（已废弃，保留表结构）
- **news_source_stats** — 源域名统计
- **news_websites** — 网站元数据

## Flask管理前端

- **公开路由**：`/`（新闻看板：筛选+统计+Tab分组列表）、`/date/<date>`（单日浏览）、`/database`（数据库查看）
- **管理路由**（需Basic Auth）：`/admin/keywords`、`/admin/models`、`/admin/runs`
- 模板在 `templates/`，管理页在 `templates/admin/`
- 路由代码在 `routes/views.py`（公开）和 `routes/admin.py`（需认证）

## 测试

```bash
# 开发测试：用 scored_news_test 表
MYSQL_TABLE=scored_news_test python main.py YYYY-MM-DD

# 验证模块加载
python -c "from config import SEARCH_KEYWORDS; print(SEARCH_KEYWORDS)"

# Issue #14/#15 相关回归测试
python -m unittest -v test_bank_news_feature.py test_historical_date_filter.py test_write_to_mysql_dedup.py
```

## 注意事项

- 所有 AI 模块（评分/摘要/地域）统一使用 `deepseek-v4-flash` 模型，配置在 `config.py`
- 日志系统：每次运行生成独立批次日志 `output/{date}/run_{YYYYMMDD_HHMMSS}.log`，通过 `RUN_LOG_PATH` 环境变量传递给子进程；手动运行单个脚本时 fallback 到 `output/run_log.txt`
- `/admin/models` 页面展示当前启用的 Prompt（按评分/摘要/公积金/地域分组）
- `/admin/business-type-dashboard` 是公积金业务类型的只读覆盖率看板，支持按 `fetchdate` 筛选，展示待补标、类型分布和最近标注记录
- `/admin/business-types` 用于查看公积金二级标签、预览并确认同一级标签合并；合并规则按生产/测试表隔离
- 内容提取含防屏蔽：User-Agent伪装、SSL忽略、同站点1-4秒间隔
- `msn.cn` 跳过、`tv.cctv.com` 跳过、`people.com.cn` 强制HTTP
- `tobacco_gov_crawler.py` 独立脚本，固定4分，直接写MySQL不经过JSON，按 `title` 去重
- `tobacco_gov_crawler.py` 爬取5个板块：行业要闻、各地新闻、基层工作、数字化转型、专卖管理
- `tobacco_gov_crawler.py` 支持 `--date YYYY-MM-DD` 指定日期，不传则默认抓取昨天
- `tobacco_gov_crawler.py` HTTP错误和异常均记录到日志；三板块全空时返回退出码1（标记为访问异常）
- `tobacco_gov_crawler.py` 列表页请求支持重试（默认3次，指数退避5s/10s/15s），通过 `--retries` 和 `--retry-delay` CLI参数控制
- `tobacco_gov_crawler.py` 每次运行结束后通过 `lark-cli` 发送飞书私聊通知，汇报板块数/入库数/文章标题；`.env` 中 `FEISHU_USER_ID` 控制接收人，不配置则跳过
- `output/` 目录已 gitignore
- Flask 默认端口 5001（macOS AirPlay 占用 5000）
- 路由中所有表名通过 `get_table_name()` 获取，`.env` 中 `MYSQL_TABLE` 控制读写哪张表
- `main.py` 实时更新 `output/run_status.json` 中的步骤状态（running/success/failed），流水线结束标记 finished
- `main.py` 烟草步骤使用 `check=True`，失败时自动重试（最多2次），并通过 `_update_step()` 正确更新 `run_status.json`
- `main.py` 使用 `sys.executable` 调用子进程，确保与当前 Python 环境一致（不硬编码 `python`）
- `tobacco_gov_crawler.py` 通过 macOS launchd 定时任务每日凌晨 1:00 自动运行（`~/Library/LaunchAgents/com.tobacco.gov.crawler.plist`）
- `.env` 中的 `MYSQL_TABLE` 会影响定时任务的写入目标表，开发期间切换测试表后注意恢复
