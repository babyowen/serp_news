1 # CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

这是一个自动化新闻采集与AI分析系统，从多个新闻源（Google News、百度新闻、Bing News、DuckDuckGo News）获取新闻，提取内容，使用AI（DeepSeek模型）进行重要性评分，并生成摘要。系统支持中文关键词监控，将结果存储在MySQL中，并提供Flask Web界面进行可视化。

## 常用命令

### 主要操作
```bash
# 运行完整流水线（默认处理昨天的新闻）
python main.py

# 运行特定日期的新闻
python main.py 2025-09-01

# 启动Web界面
python app.py
```

### 单独模块执行
```bash
# 新闻采集
python fetch_and_filter.py "养老" 2025-09-01

# 内容提取
python fetch_content.py "养老" 2025-09-01

# 单URL内容提取测试
python fetch_content.py "测试" 2025-09-01 --url="https://example.com/news"

# AI评分
python news_scorer.py "养老" 2025-09-01

# 生成摘要
python news_summarizer.py --keyword "养老" --date 2025-09-01

# 写入数据库
python write_to_mysql.py --date 2025-09-01
```

### 500字短摘要生成
```bash
# 为评分≥3且尚未生成短摘要的新闻生成约500字短摘要
python news_item_summarizer.py 2025-09-01

# 不传日期默认处理昨天的数据
python news_item_summarizer.py

# 只处理某一天、某个主关键词的短摘要（按scored_news.keyword过滤）
python news_item_summarizer.py 2025-09-01 --keyword "江苏地区银行"
```

### 环境设置
```bash
# 激活虚拟环境（如果已创建）
source .venv/bin/activate  # macOS/Linux

# 安装依赖
pip install -r requirements.txt

# 安装Playwright浏览器
playwright install
```

### 测试命令
```bash
# 测试新闻采集功能
python test_fetcher.py serp_googlenews "测试关键词"
python test_fetcher.py serp_baidunews "测试关键词"

# 测试新关键词的新闻覆盖情况（推荐）
python test_keywords.py "测试关键词"
python test_keywords.py "测试关键词" "2025-01-10"
```

## 架构概览

### 核心模块
1. **main.py** - 运行完整流水线的编排脚本
2. **fetch_and_filter.py** - 多源新闻采集与过滤
3. **fetch_content.py** - 具有5级兜底策略的内容提取
4. **news_scorer.py** - AI驱动的新闻评分（0-5分制）
5. **news_summarizer.py** - AI摘要生成（默认单轮，可配置多轮优化）
6. **write_to_mysql.py** - 数据库持久化
7. **app.py** - Flask Web界面
8. **error_handler.py** - 统一错误处理系统
9. **config.py** - 包含API密钥和关键词的中央配置

### 内容提取策略
系统使用复杂的5级兜底方法：
1. 自定义提取规则（`config_grab_rules.py`）
2. Trafilatura库
3. Newspaper3k库
4. Playwright（JavaScript渲染）
5. Selenium（浏览器自动化）

### AI集成
- **评分**：使用DeepSeek-reasoner模型和详细提示进行0-5分重要性评分
- **摘要**：默认执行单轮摘要；如需启用“三轮流程（草稿→评判审查→优化→热点追踪）”，可在 `news_summarizer.py` 中将 `ENABLE_MULTI_ROUND_SUMMARY` 设为 `True`
- **并发处理**：支持多线程AI评分和连接池

### 配置结构
- **关键词**：在`config.py`中定义为主关键词→搜索关键词映射
- **API密钥**：通过环境变量管理（.env文件）
- **自定义规则**：`config_grab_rules.py`中的站点特定提取规则
- **AI提示**：评分和摘要的详细系统提示在`config.py`中

### 数据库模式
- **scored_news**：包含内容和评分的主要新闻表
- **summary_news**：包含平台/模型信息的生成摘要
- **news_source_stats**：源域名统计
- **news_websites**：网站元数据

### 错误处理
- 通过`error_handler.py`统一错误处理
- 结构化错误日志记录到`output/error_log.txt`
- 自动重试的优雅降级
- 进程级隔离（单个失败不会停止整个流水线）

### 输出结构
- `output/`目录中按日期和关键词组织的JSON文件
- 运行日志在`output/run_log.txt`
- 错误日志在`output/error_log.txt`
- 数据库持久化用于结构化查询

## 重要说明

- 系统专为中文新闻监控设计，但也支持任何语言
- 内容提取包含防屏蔽措施（随机延迟、浏览器模拟）
- AI评分包含关键词特定提示（例如，"江苏省国资委"的专门提示）
- Web界面提供基于关键词的过滤和源统计
- 系统支持从中断恢复（处理前检查现有文件）

## 核心架构:关键词数据流转

### 关键词两层结构
系统使用**两层关键词映射**架构（定义在 `config.py:SEARCH_KEYWORDS`）:

```python
SEARCH_KEYWORDS = {
    "主关键词": ["搜索关键词1", "搜索关键词2", ...],
    "养老": ["养老"],
    "江苏地区银行": ["工商银行", "农业银行", "中国银行", ...],
    "江苏省国资委": ["江苏省国资委", "江苏国信集团", "江苏交通控股", ...]
}
```

- **主关键词 (Main Keyword)**: 用于业务分类和评分提示选择,例如 "江苏地区银行"、"养老"
- **搜索关键词 (Search Keyword)**: 实际用于新闻API搜索的具体关键词,例如 "工商银行"、"农业银行"

### 数据流转过程

#### 1. 新闻采集阶段 (`fetch_and_filter.py`, `main.py:65-147`)
```
主关键词 → 遍历搜索关键词 → 调用新闻API
         ↓
    临时文件: tmp_{date}_{main_kw}_{search_kw}.json
         ↓
    合并去重: {date}_{main_kw}.json
```
每条新闻在数据库 `scored_news` 表中存储:
- `keyword` 字段 = 主关键词 (如 "江苏地区银行")
- `search_keyword` 字段 = 搜索关键词 (如 "工商银行")

#### 2. AI评分阶段 (`news_scorer.py`)
- 从数据库读取 `keyword` (主关键词)
- 在 `config.py:KEYWORD_SPECIFIC_SYSTEM_PROMPTS` 中查找对应的专门评分提示
- 如果找到专门提示,使用专门提示;否则使用通用提示 `NEWS_SCORE_SYSTEM_MSG`
- 示例: "江苏地区银行" → 使用 `NEWS_SCORE_SYSTEM_MSG_JIANGSU_BANKS`

#### 3. 500字短摘要生成 (`news_item_summarizer.py`)
- **不读取** config 的关键词配置
- 从数据库读取已评分新闻 (score≥3)
- 可选通过 `--keyword` 参数过滤 (该参数匹配数据库中的 `keyword` 字段)
- 示例: `python news_item_summarizer.py 2025-01-14 --keyword "工商银行"`

#### 4. 摘要生成阶段 (`news_summarizer.py`)
- 使用主关键词进行摘要
- 从 `scored_news` 表读取高分新闻
- 生成结构化摘要 (今日综述、新闻总结、观点总结)

### 关键配置文件
- **`config.py`**: 中央配置文件
  - `SEARCH_KEYWORDS`: 主关键词→搜索关键词映射
  - `DEFAULT_KEYWORDS`: 要处理的主关键词列表
  - `KEYWORD_SPECIFIC_SYSTEM_PROMPTS`: 主关键词→专门评分提示映射
  - AI模型配置、API密钥配置、评分提示词

### 添加新关键词组流程
1. 在 `config.py:SEARCH_KEYWORDS` 添加映射
2. (可选) 在 `config.py` 创建专门的评分提示词 (如 `NEWS_SCORE_SYSTEM_MSG_XXX`)
3. 在 `config.py:KEYWORD_SPECIFIC_SYSTEM_PROMPTS` 注册提示词映射
4. 在 `config.py:DEFAULT_KEYWORDS` 设置要处理的主关键词列表
