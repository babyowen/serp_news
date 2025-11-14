## 待修复问题
- 数据库连接在导入时创建，异常即崩溃：`/Users/babyowen/Documents/GitHub/serp_news/write_to_mysql.py:45-53`
- 连接关闭逻辑失效，可能资源泄露：`/Users/babyowen/Documents/GitHub/serp_news/write_to_mysql.py:359-364`
- 百度新闻网页日期正则写法错误：`/Users/babyowen/Documents/GitHub/serp_news/news_fetcher.py:115`
- OpenAI兼容接口不支持 `timeout` 参数（易抛TypeError）：
  - `'/Users/babyowen/Documents/GitHub/serp_news/news_summarizer.py:381-386'`
  - `'/Users/babyowen/Documents/GitHub/serp_news/news_scorer.py:146-155'`
- 对 `msn.cn` 的硬跳过与定制规则冲突：`/Users/babyowen/Documents/GitHub/serp_news/fetch_content.py:317-320`
- 全局禁用JS可能影响正文抓取：`/Users/babyowen/Documents/GitHub/serp_news/fetch_content.py:131`
- Flask模板目录缺失风险：`/Users/babyowen/Documents/GitHub/serp_news/app.py:21-27,49,78,105`
- 轻微代码质量问题：未使用变量 `summary_logger`：`/Users/babyowen/Documents/GitHub/serp_news/news_summarizer.py:158-159`

## 修改方案
- 数据库连接管理
  - 将模块级连接/游标改为函数内惰性创建（`main()` 或各 `insert_*` 内），避免导入时崩溃。
  - 在 `finally` 显式关闭对象（直接判断并关闭 `cursor/conn`），或使用 `with` 语法管理连接。
- 修正日期正则
  - 将 `r"(.+?)\\s+(\\d{4}-\\d{2}-\\d{2}.*)"` 改为 `r"(.+?)\s+(\d{4}-\d{2}-\d{2}.*)"`，恢复网页端百度新闻日期解析。
- OpenAI超时设置
  - 移除 `chat.completions.create(..., timeout=...)`，改为通过客户端会话或HTTP层设置超时（保持接口兼容）。
- MSN抓取策略
  - 取消硬跳过；优先命中 `config_grab_rules.py` 定制规则，失败再走通用抓取或回退策略。
- JS禁用策略
  - 条件化禁用：仅在明确不依赖JS的站点使用；对动态站点优先使用 Playwright 或保留JS。
- Flask模板
  - 补齐 `templates/index.html`、`keyword_select.html`、`date.html`、`database.html`，或在启动时做存在性检查并给出友好提示。
- 代码清理
  - 删除未使用变量与保持模型命名一致，减少困惑。

## 验证步骤
- 单元与集成验证
  - 运行 `write_to_mysql.py --date <样例日期>`，观察导入不再因导入阶段崩溃，且连接正确关闭（无长连接）。
  - 运行 `news_fetcher.fetch_baidu_news_web`，检查 `date` 字段解析正常。
  - 分别执行一条评分和摘要调用，确认无 `TypeError: unexpected keyword argument 'timeout'`。
  - 对包含 `msn.cn` 的样例链接进行正文抓取，确认定制规则生效或能回退抓取。
  - 启动 Flask 并访问 `/`、`/date/<date>`、`/database`，确认模板渲染正常。
- 日志与资源
  - 检查 `output/run_log.txt` 与 `output/error_log.txt`，无新增致命错误；数据库连接数随流程结束归零。

## 影响范围与风险控制
- 变更集中在数据导入与抓取模块，接口保持不变；对上游 `main.py` 无需改动调用方式。
- 引入惰性连接与条件化JS策略可能改变性能特征；通过逐步验证与日志对比控制风险。

## 交付物
- 修复后的源码变更（含数据库连接管理、正则、接口调用、抓取策略）。
- 补充或示例模板文件；更新日志说明。
- 简要验证报告与问题清单关闭记录。