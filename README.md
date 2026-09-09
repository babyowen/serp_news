# 新闻采集与AI分析自动化系统

每日自动采集、评分、摘要、归档的新闻处理流水线，配合 Bootstrap 管理前端。

## 核心特性

- **7步流水线**：采集 → 正文提取 → AI评分 → 数据库写入 → 单条摘要 → 地域分析 → 烟草爬虫
- **多源采集**：Google News、百度新闻、Bing News、DuckDuckGo News、GNews，共5个平台
- **多级兜底正文提取**：定制规则 → trafilatura → newspaper3k → Playwright → Selenium
- **AI智能评分**：0-5分量化评估，支持关键词专属评分标准，3线程并发
- **单条500字摘要**：对3分及以上新闻生成精炼摘要
- **地域标注**：公积金等主题自动标注城市级地域标签
- **公积金业务类型**：高分公积金新闻支持最多两个可治理的一级/二级业务标签
- **银行新闻监测**：`烟草服务银行`主题覆盖8家指定银行，优先筛选江苏省内重要动态
- **烟草官网爬取**：中国烟草官网3个栏目定向采集
- **Bootstrap管理前端**：关键词配置、模型查看、运行监控及公积金标注覆盖率看板

## 快速开始

### 环境要求

- Python 3.10+
- MySQL 5.7+
- Chrome浏览器（用于 Playwright 正文提取）

### 安装

```bash
pip install -r requirements.txt
playwright install
cp .env.example .env   # 填入API密钥和MySQL连接信息
```

### 配置环境变量

在项目根目录创建 `.env` 文件：

```ini
# API配置
SERPAPI_KEY=your_serpapi_key
DEEPSEEK_API_KEY=your_deepseek_api_key

# 数据库配置
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=your_username
MYSQL_PASSWORD=your_password
MYSQL_DB=serp_news

# 可选：切换到测试表（开发用）
# MYSQL_TABLE=scored_news_test

# 流程开关（默认全部开启）
# ENABLE_ITEM_SUMMARIZER=1
# ENABLE_REGION_ANALYZER=1
# 日常生产批次默认不执行表结构变更；仅维护窗口显式设为 1
AUTO_MIGRATE_DEDUP_INDEX=0
```

### 初始化提示词与运行配置

首次生产升级请先阅读 [配置迁移与恢复说明](docs/configuration.md)：**在覆盖旧生产文件前，先从旧文件显式迁移。** 已有配置不会自动替换为仓库默认值。

全新本地开发环境可显式初始化：

```bash
export SERP_CONFIG_STORE="$HOME/.local/share/serp-news-dev/runtime.sqlite3"
python config_cli.py --store "$SERP_CONFIG_STORE" init --defaults
```

将该绝对路径写入本地 `.env` 的 `SERP_CONFIG_STORE`。运行配置文件必须在部署目录之外；Web 和定时任务读取同一配置路径。初次未初始化、文件损坏或版本不兼容时程序会明确报错。

### 创建数据库表

```sql
CREATE TABLE IF NOT EXISTS `scored_news` (
  `id` int NOT NULL AUTO_INCREMENT,
  `date` varchar(64) DEFAULT NULL,
  `title` varchar(255) DEFAULT NULL,
  `link` text,
  `source` varchar(255) DEFAULT NULL,
  `fetchdate` date DEFAULT NULL,
  `sourceapi` varchar(255) DEFAULT NULL,
  `thumbnail` text,
  `keyword` varchar(255) DEFAULT NULL,
  `content` longtext,
  `wordcount` int DEFAULT NULL,
  `custom_grab` tinyint(1) DEFAULT NULL,
  `score` int DEFAULT NULL,
  `import_batch_id` varchar(50) DEFAULT NULL,
  `content_hash` varchar(32) DEFAULT NULL,
  `search_keyword` varchar(255) DEFAULT NULL,
  `short_summary` text,
  `region` varchar(255) DEFAULT NULL,
  `business_types` json DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `keyword_title_link` (`keyword`(100),`title`,`link`(255))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `news_source_stats` (
  `id` int NOT NULL AUTO_INCREMENT,
  `date` date NOT NULL,
  `keyword` varchar(50) NOT NULL,
  `domain` varchar(100) NOT NULL,
  `count` int NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `news_websites` (
  `id` int NOT NULL AUTO_INCREMENT,
  `website` varchar(255) DEFAULT NULL,
  `name` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `website` (`website`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 运行

> `write_to_mysql.py` 日常默认使用应用层查重，不会修改表结构。仅在维护窗口显式设置 `AUTO_MIGRATE_DEDUP_INDEX=1` 时，才会将旧的全局 `title_link` 唯一索引迁移为主关键词级 `keyword_title_link` 唯一索引。迁移前应先确认历史数据不存在冲突；相同新闻可分别保留在不同主关键词分组中。

```bash
# 运行完整流水线（默认处理昨天新闻，7步）
python main.py
python main.py YYYY-MM-DD          # 指定日期
python main.py YYYY-MM-DD --keyword 烟草服务银行  # 仅补跑一个主关键词

# 启动Web管理界面（端口5001）
python app.py
```

### 烟草服务银行监测

主关键词 `烟草服务银行` 用于前端分类、数据归档和专属评分；实际检索以下8家银行名称：工商银行、农业银行、中国银行、建设银行、交通银行、中信银行、浦发银行、南京银行。

- 银行总行重大事项优先评为5分。
- 江苏省内重要银行新闻，以及同时涉及银行和烟草的新闻，原则上评为4分。
- 以这8家银行为主体的品牌宣传、服务纪实或企业形象稿固定评为3分。
- 新闻主体不是这8家银行时直接评为1分。

该主题会出现在首页的主关键词筛选和管理端关键词列表中。单独补跑时使用 `python main.py YYYY-MM-DD --keyword 烟草服务银行`。

### 生产更新

Issue #18 首次上线须先完成 [旧配置迁移](docs/configuration.md)，再更新生产代码。部署前和每次代码更新后的检查步骤见 [运维手册](docs/operations.md)。其中 `.env` 被 Git 忽略，服务器需要手动保留或添加：

```ini
AUTO_MIGRATE_DEDUP_INDEX=0
```

## 单独模块执行

```bash
python fetch_and_filter.py "养老" YYYY-MM-DD                              # 新闻采集
python fetch_content.py "养老" YYYY-MM-DD                                 # 正文提取
python fetch_content.py "测试" YYYY-MM-DD --url="https://example.com"     # 单URL调试
python news_scorer.py "养老" YYYY-MM-DD                                   # AI评分
python news_item_summarizer.py YYYY-MM-DD                                 # 单条摘要
python news_region_analyzer.py --keyword 公积金 --date YYYY-MM-DD         # 地域分析
python news_business_type_schema.py --table scored_news_test               # 初始化业务类型测试表
python news_business_type_analyzer.py --date-from YYYY-MM-DD --date-to YYYY-MM-DD  # 公积金业务类型补标
python tobacco_gov_crawler.py                                             # 烟草爬虫
python write_to_mysql.py --date YYYY-MM-DD                                # 数据入库
python write_to_mysql.py --date YYYY-MM-DD --keyword 烟草服务银行          # 仅导入一个主关键词
AUTO_MIGRATE_DEDUP_INDEX=1 python write_to_mysql.py --date YYYY-MM-DD     # 仅维护窗口执行历史表索引迁移
```

## 架构

`main.py` 通过 `subprocess` 依次调用各模块（使用 `sys.executable` 确保环境一致）。每个步骤是独立进程，单个失败不阻断流水线。支持断点续跑。

| 步骤 | 脚本 | 说明 | 控制 |
|------|------|------|------|
| 1 | `fetch_and_filter.py` → `news_fetcher.py` | 5源采集，去重合并 | - |
| 2 | `fetch_content.py` | 5级兜底正文提取 | - |
| 3 | `news_scorer.py` | AI评分0-5分，3线程并发 | - |
| 4 | `write_to_mysql.py` | MySQL持久化 | - |
| 5 | `news_item_summarizer.py` | 单条500字摘要；公积金同时标注地域和业务类型 | `ENABLE_ITEM_SUMMARIZER` |
| 6 | `news_region_analyzer.py` | 公积金地域分析 | 条件触发+`ENABLE_REGION_ANALYZER` |
| 7 | `tobacco_gov_crawler.py` | 烟草官网3栏目爬取 | 条件触发（含中国烟草时） |

### 共享工具模块

- **`db_utils.py`** — 统一数据库连接（含3次重试）+ `MYSQL_TABLE` 环境变量切换测试表
- **`llm_client_pool.py`** — 统一LLM客户端池，按(api_key, base_url)复用
- **`config_manager.py`** — 对关键词修改做版本检查，供管理页面调用
- **`run_manager.py`** — 运行状态管理

### 关键配置文件

- **`config.py`** — 外部配置的兼容读取入口
- **`config_defaults.json`** — 显式初始化用的默认模板（22 段现用/归档提示词）
- **`SERP_CONFIG_STORE`** — 部署目录外的配置文件，保存生效版本、完整历史与批次绑定
- **`config_grab_rules.py`** — 站点专属抓取规则
- **`.env`** — API密钥、MySQL连接、流程开关

### 数据库表

- **scored_news** — 新闻主表（含评分、短摘要、地域、业务类型）
- **summary_news** — 日报摘要（已废弃）
- **news_source_stats** — 源域名统计
- **news_websites** — 网站元数据

管理端的 `/admin/business-type-dashboard` 提供公积金高分新闻的业务类型标注覆盖率、一级/二级标签分布和最近标注记录；可按 `fetchdate` 筛选，且仅做只读统计。

### Flask管理前端

- **公开路由**：`/`（新闻看板）、`/date/<date>`（单日浏览）、`/database`（数据库查看）
- **管理路由**（需Basic Auth）：`/admin/keywords`、`/admin/models`、`/admin/runs`、`/admin/business-type-dashboard`（公积金标注看板）、`/admin/business-types`（二级标签合并）

## 测试

配置改造及相关业务的离线回归（隔离配置和工作目录、阻止网络连接）：

```bash
pip install -r requirements-dev.txt
python run_config_tests.py
```


```bash
# 开发测试：用 scored_news_test 表
MYSQL_TABLE=scored_news_test python main.py YYYY-MM-DD

# 验证模块加载
python -c "from config import SEARCH_KEYWORDS; print(SEARCH_KEYWORDS)"

# 回归测试：银行监测、历史日期过滤、应用层查重、业务类型标注
python -m unittest -v test_bank_news_feature.py test_historical_date_filter.py test_write_to_mysql_dedup.py test_business_type_feature.py
```

## 注意事项

- 现用 AI 阶段初始模型为 `deepseek-v4-flash`，实际模型及请求参数从外部配置的 `models` 读取
- `tobacco_gov_crawler.py` 通过 macOS launchd 每日凌晨 1:00 自动运行
- `.env` 中 `MYSQL_TABLE` 会影响定时任务的写入目标表，开发后注意恢复
- 内容提取含防屏蔽：User-Agent伪装、SSL忽略、同站点1-4秒间隔
- `msn.cn` 跳过、`tv.cctv.com` 跳过、`people.com.cn` 强制HTTP
- Flask 默认端口 5001（macOS AirPlay 占用 5000）
- `output/` 目录已 gitignore

---
*该文档最后更新于：2026-08-15*
