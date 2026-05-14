# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

自动化新闻采集与AI分析系统。流水线：采集 → 正文提取 → AI评分 → 摘要生成 → 数据库写入。系统支持中文关键词监控，结果存储在MySQL中，Flask Web界面可视化。

## 常用命令

```bash
# 运行完整流水线（默认处理昨天新闻）
python main.py
python main.py YYYY-MM-DD          # 指定日期
```

### 单独模块执行
```bash
python fetch_and_filter.py "养老" YYYY-MM-DD
python fetch_content.py "养老" YYYY-MM-DD
python fetch_content.py "测试" YYYY-MM-DD --url="https://example.com/news"  # 单URL调试
python news_scorer.py "养老" YYYY-MM-DD
python news_summarizer.py --keyword "养老" --date YYYY-MM-DD
python write_to_mysql.py --date YYYY-MM-DD
```

### 500字短摘要（独立后处理，不在main.py中自动执行）
```bash
python news_item_summarizer.py YYYY-MM-DD
python news_item_summarizer.py YYYY-MM-DD --keyword "江苏地区银行"
```

### 测试
```bash
python test_fetcher.py serp_googlenews "测试关键词"
python test_keywords.py "测试关键词"
```

### 环境设置
```bash
source .venv/bin/activate
pip install -r requirements.txt
playwright install
```

## 架构：子进程流水线

`main.py` 通过 `subprocess` 依次调用各模块，而非直接 import 执行。每个步骤是独立进程，单个失败不阻断流水线。每个步骤开始前检查输出文件是否已存在，存在则跳过（支持断点续跑）。

1. **新闻采集** (`fetch_and_filter.py` → `news_fetcher.py`) — 多源采集（Google/百度/Bing/DuckDuckGo/GNews），去重合并
2. **正文抓取** (`fetch_content.py`) — 5级兜底：自定义规则(`config_grab_rules.py`) → trafilatura → newspaper3k → Playwright → Selenium
3. **AI评分** (`news_scorer.py`) — DeepSeek-reasoner，0-5分制，`main.py`中默认3线程并发
4. **摘要生成** (`news_summarizer.py`) — 单轮摘要为默认；启用`ENABLE_MULTI_ROUND_SUMMARY`可切换为三轮流程；内置 DeepSeek/百炼双平台自动切换
5. **数据库写入** (`write_to_mysql.py`) — MySQL持久化

## 核心架构：关键词两层结构

系统使用两层关键词映射（定义在 `config.py:SEARCH_KEYWORDS`）：

- **主关键词** — 业务分类和评分提示选择，如 "江苏地区银行"、"养老"
- **搜索关键词** — 实际调用新闻API的词，如 "工商银行"、"农业银行"

数据流转：
```
主关键词 → 遍历搜索关键词 → 调用新闻API
         ↓
    tmp_{date}_{main_kw}_{search_kw}.json（临时，合并后删除）
         ↓
    {date}_{main_kw}.json（去重合并）
         ↓
    {date}_{main_kw}_scored.json（AI评分后）
         ↓
    {date}_{main_kw}_summary.json（摘要后）
```

每条新闻在 `scored_news` 表中：`keyword` = 主关键词，`search_keyword` = 搜索关键词。

### 添加新关键词组
1. 在 `config.py:SEARCH_KEYWORDS` 添加映射
2. (可选) 创建专门评分提示词并注册到 `KEYWORD_SPECIFIC_SYSTEM_PROMPTS`
3. `DEFAULT_KEYWORDS` 会自动从 `SEARCH_KEYWORDS.keys()` 生成

## 关键配置

- **`config.py`** — 关键词映射、API密钥、AI提示词、模型配置
- **`config_grab_rules.py`** — 站点专属抓取规则（注册表机制，优先匹配）
- **`.env`** — API密钥和MySQL连接信息

## 数据库表

- **scored_news** — 新闻主表（标题、内容、评分、`short_summary`字段由`news_item_summarizer.py`写入）
- **summary_news** — 生成的摘要（含平台/模型信息）
- **news_source_stats** — 源域名统计
- **news_websites** — 网站元数据

## 其他模块

- **news_item_summarizer.py** — 单条新闻500字摘要，直接读写MySQL（不走JSON中间文件）
- **error_handler.py** — 统一错误处理，`@with_error_handling` 装饰器
- **icon_manager.py** — 安全打印（GBK终端编码）
- **app.py** — Flask Web界面

## 注意事项

- 内容提取含防屏蔽：User-Agent伪装、SSL忽略、同站点1-4秒间隔
- `msn.cn` 域名直接跳过正文抓取
- GBK/GB2312 站点用 requests 自动识别编码
- 流程图见 `serp_news_flow.mmd`
