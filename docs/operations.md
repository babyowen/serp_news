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

1. 检查批次日志 `output/YYYY-MM-DD/run_*.log` 是否显示全部步骤成功。
2. 在前端按日期和主关键词 `烟草服务银行` 筛选，检查新闻、分数和摘要是否可见。
3. 数据库核验时确认写入表为 `scored_news`，并以 `fetchdate` 和 `keyword='烟草服务银行'` 过滤。

本机 macOS 的 `tobacco_gov_crawler.py` 另有 launchd 配置；不要将该本机路径或 launchd 命令直接套用于 Linux 服务器。
