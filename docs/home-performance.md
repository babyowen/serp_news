# 首页性能排查与部署（Issue #22）

2026-09-09 生产首页返回 200，但首字节约 45 秒。独立请求计时：统计 6.646 秒（7 行）、列表 34 秒（331 行）、API 选项 6.933 秒（4 行），合计 47.579 秒，请求 50.009 秒、531562 字节。PyMySQL execute 时间包含执行及接收，并非纯服务端 SQL 时间。

生产 EXPLAIN 已确认三条查询都是 `type=ALL`、`key=null`，预计各扫描 109962 行。现有索引只有 PRIMARY(id) 和 keyword(100) 前缀索引，缺少日期索引。该问题在 PR #21 之前已存在，旧版与新版首页 SQL 原本相同。

## 改动

- 显式维护工具 `home_index_cli.py` 新增 fetchdate 的非唯一 BTREE 索引；启动 Web、采集和入库均不会调用它。默认只读，必须指定 `--apply` 才执行 DDL。
- 已有可用日期前导索引时不重复创建；同名不同定义拒绝覆盖；保留所有旧索引与数据。
- DATE / DATETIME / TIMESTAMP 或较短 CHAR / VARCHAR 使用完整列索引；长字符串、TEXT 使用前 10 字符索引，SQL 仍保留完整日期条件。前缀索引不保证覆盖查询，需按真实 EXPLAIN 验证收益。
- 首页每次读取选中主题的 50 条新闻；点击主题、翻页均使用普通 GET 链接，无 JavaScript 依赖，保留筛选参数。切换主题、提交筛选从第一页开始。
- 主题统计仍覆盖全部匹配数据，空主题仍可见。API 选项保留原来的日期范围语义（包含历史主题的 API）。
- 列表按日期降序、评分降序、id 降序排列。同日期同评分用 id 保证确定性；在数据不变时翻页无重复遗漏。分页不是跨请求快照，正在入库/重评分时列表位置可能变化，验收使用已结束日期。
- 摘要只读取前 101 字符，维持前 100 字符加省略号的显示，不修改原文。
- 日期、评分、主题和页码无效返回 400。超出实际页数跳至最后一页。

## 服务器步骤

项目目录 `/www/wwwroot/serp_news`，外部配置 `/www/serp-news-state/runtime.sqlite3`。在获取已审查的新代码后执行，使用现有 `.venv`；不需要升级生产依赖。

先核对配置版本，记录结果：

```bash
cd /www/wwwroot/serp_news
.venv/bin/python config_cli.py --store /www/serp-news-state/runtime.sqlite3 status
.venv/bin/python home_index_cli.py
```

检查输出的实际表名、fetchdate 类型、已有索引及计划 SQL。此步骤只读。典型计划是：

```sql
ALTER TABLE `scored_news`
  ADD INDEX `idx_scored_news_fetchdate` (`fetchdate`),
  ALGORITHM=INPLACE, LOCK=NONE;
```

确认采集/入库任务已结束，避开每日 00:30 任务，在低负载时执行：

```bash
.venv/bin/python home_index_cli.py --apply
```

索引对旧代码也有效，可先从独立 worktree 使用该脚本完成索引，再切换 Web 代码。若脚本不在生产项目目录，先用现有方式加载生产 `.env`，确保 MYSQL_* 指向生产而非其他环境。

DDL 指定 INPLACE/LOCK=NONE，不支持时直接失败，不回退到复制整表或更强锁。在线建索引仍消耗 IO，并且开始/结束阶段可能需要元数据锁；会话 lock_wait_timeout=10，避免长时间等待该锁。读取超时为 3600 秒，仅维护命令启用，普通请求仍为 30 秒。如果连接超时或中断，不要立即反复提交 DDL；先检查腾讯云运行任务及 `SHOW INDEX`，确认服务器是否仍在执行。参考 [MySQL ALTER TABLE](https://dev.mysql.com/doc/refman/8.0/en/alter-table.html) 和 [在线 DDL 限制](https://dev.mysql.com/doc/refman/8.0/en/innodb-online-ddl-limitations.html)。

创建成功后重新运行默认检查应提示已有索引。部署新代码后在宝塔重启 Web；保持原有 .env、配置 SQLite、输出目录和定时任务设置。

## 验收

以下检查只读，不调用 LLM、采集或入库：

```bash
.venv/bin/python home_profile.py --date 2026-09-08
curl --max-time 60 -sS -o /dev/null \
  -w 'HTTP %{http_code} 首字节 %{time_starttransfer}s 总计 %{time_total}s 大小 %{size_download}B\n' \
  'http://127.0.0.1:5001/?date_from=2026-09-08&date_to=2026-09-08'
```

`home_profile.py` 在独立进程内调用实际首页路由，对每条真实 SQL 输出 EXPLAIN、execute 耗时及行数；数据库会话强制只读，不输出新闻或凭据。总耗时包含 EXPLAIN，和 curl 不应直接等同。

依次执行三次，记录首轮及后续结果，避免并发刷新干扰测量。目标：代表性单日首字节低于 5 秒，三条查询不再因缺失日期索引全表扫描。范围覆盖大部分历史时优化器仍可能合理选择全表扫描，不能仅凭 type=ALL 判断所有查询异常；若单日仍慢，分析新计划及云数据库负载再调整，不强制 FORCE INDEX。

浏览器确认：全量统计与部署前一致；7 个主题可切换；第一页最多 50 条；上下页无遗漏重复（稳定历史日期）；评分/来源/API 筛选保留；摘要省略正常；空主题正常。最后核对 config_cli status 的版本和哈希与开始时相同。

将三轮实测与计划记录到 Issue #22；**目前生产优化后耗时待测，不因本地测试通过就宣称达到 5 秒目标**。

## 回退

Web 代码可以按原部署流程回退；新增非唯一日期索引与旧代码兼容，通常保留即可，不需要回滚配置或业务数据。脚本不删除索引；如确需删除，先确认是本次创建且无其他用途，另行安排维护。

## 本地验证

```bash
python run_config_tests.py
```

首页测试使用 SQLite 关系数据执行真实筛选/排序/分页（仅转换占位符及 LEFT 语法），验证内容、统计、跨页顺序和链接；索引规划以 MySQL 元数据形状验证。它们不能替代生产 MySQL 的 DDL 兼容性、执行计划和耗时验收。

2026-09-09 本地验证：`venv/bin/python run_config_tests.py` 通过 210 项测试及 12 个子用例，独立日志回归 19 项通过。其中新增首页/索引专项 31 项。未连接生产数据库、未执行生产 DDL，线上验收仍待执行。
