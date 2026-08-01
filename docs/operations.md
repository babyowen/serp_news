# 运维手册

本文档面向运行生产环境的管理员。代码和环境变量分别管理：代码由 Git 更新，`.env` 由服务器本地维护。

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

项目的 Web 前端由 Flask 的 `app.py` 提供，不需要额外构建前端静态资源。代码更新后，按服务器现有的 systemd、supervisor 或进程管理方式重启 Flask 服务。

## 环境变量

`.env` 被 Git 忽略，不会随 `git pull` 更新。生产服务器的实际运行目录中必须存在：

```ini
MYSQL_TABLE=scored_news
AUTO_MIGRATE_DEDUP_INDEX=0
```

`AUTO_MIGRATE_DEDUP_INDEX=0` 是日常生产批次的安全默认值：数据库写入使用应用层查重，不会执行索引或其他表结构变更。仅在已确认历史数据且安排维护窗口时，才可临时设为 `1` 执行迁移；完成后立即恢复为 `0`。

修改 `.env` 后，重新启动读取该文件的常驻 Web 服务。定时任务每次启动 `main.py` 时会读取 `.env`；无需因为该变量单独重建定时任务。

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
