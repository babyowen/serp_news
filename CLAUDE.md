# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此代码库中工作时提供指导。

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
```

### 环境设置
```bash
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
```

## 架构概览

### 核心模块
1. **main.py** - 运行完整流水线的编排脚本，负责协调各个模块的执行顺序
2. **fetch_and_filter.py** - 多源新闻采集与过滤，从4个新闻源API采集数据
3. **fetch_content.py** - 具有5级兜底策略的内容提取系统
4. **news_scorer.py** - AI驱动的新闻评分（0-5分制），支持并发处理
5. **news_summarizer.py** - 三轮AI摘要生成（草稿→评判→优化→热点追踪）
6. **write_to_mysql.py** - 数据库持久化，包含去重逻辑
7. **app.py** - Flask Web界面，提供数据可视化
8. **error_handler.py** - 统一错误处理系统，使用装饰器模式
9. **config.py** - 包含API密钥、关键词映射和AI提示的中央配置

### 流水线执行流程

系统按以下顺序处理新闻（main.py编排）：

1. **采集阶段** (fetch_and_filter.py)
   - 对每个主关键词的所有搜索关键词进行API调用
   - 从多个新闻源采集并合并结果
   - 按日期过滤并去重
   - 输出：`output/{date}/{date}_{主关键词}.json`

2. **内容提取阶段** (fetch_content.py)
   - 读取采集的新闻列表
   - 使用5级兜底策略提取正文内容
   - 输出：更新原JSON文件，添加content字段

3. **AI评分阶段** (news_scorer.py)
   - 使用DeepSeek-reasoner模型对每篇新闻评分
   - 支持并发处理（ScoringClientPool连接池）
   - 评分标准：0-5分（根据重要性、地域、相关度）
   - 输出：更新原JSON文件，添加score字段

4. **摘要生成阶段** (news_summarizer.py)
   - 筛选评分≥3的新闻
   - 三轮AI交互生成高质量摘要
   - 输出：`output/{date}/summary_{date}_{关键词}.json`

5. **数据库入库阶段** (write_to_mysql.py)
   - 将评分新闻和摘要写入MySQL
   - 自动去重（基于title+link）
   - 统计新闻源域名

### 内容提取的5级兜底策略

fetch_content.py 按顺序尝试以下方法，直到成功提取内容：

1. **自定义规则** (`config_grab_rules.py`) - 针对特定网站的定制化CSS选择器
2. **Trafilatura** - 通用正文提取库
3. **Newspaper3k** - 另一个正文提取库
4. **Playwright** - 使用无头浏览器渲染JavaScript
5. **Selenium** - 使用Chrome浏览器自动化

这种策略确保即使某些网站有反爬措施，也能成功获取内容。

### AI集成机制

**评分系统** (news_scorer.py)：
- 使用DeepSeek-reasoner模型
- 0-5分评分标准（在config.py的NEWS_SCORE_SYSTEM_MSG中定义）
- 支持关键词特定的评分提示（KEYWORD_SPECIFIC_SYSTEM_PROMPTS）
- ScoringClientPool管理连接池，每100次请求后重建客户端
- 使用concurrent.futures实现多线程并发评分

**摘要系统** (news_summarizer.py)：
- 第一轮：生成初稿（今日综述、新闻总结、观点总结）
- 第二轮：专家评判 → 根据建议优化
- 第三轮：与前一天对比，标注热点追踪
- 支持DeepSeek和百炼双平台自动切换

### 配置系统

**关键词映射** (config.py)：
```python
SEARCH_KEYWORDS = {
    "主关键词": ["搜索关键词1", "搜索关键词2", ...],
    # 例如：
    "政府基金": ["政府基金", "引导基金", "母基金"],
}
```

**环境变量** (.env文件)：
- SERPAPI_KEY - SerpAPI密钥
- DEEPSEEK_API_KEY - DeepSeek API密钥
- BAILIAN_API_KEY - 百炼API密钥（可选）
- MYSQL_* - MySQL数据库连接信息

**自定义抓取规则** (config_grab_rules.py)：
- CUSTOM_GRAB_RULES字典：域名 → CSS选择器映射
- 针对特定网站的定制化提取函数

### 错误处理架构

error_handler.py 提供三层错误处理：

1. **全局异常处理器** - 捕获未处理的异常
2. **装饰器@with_error_handling** - 包装函数，自动捕获和记录错误
3. **ErrorHandler类** - 提供log_error()方法记录结构化错误日志

错误日志格式：
- `output/error_log.txt` - 结构化错误日志（JSON格式）
- `output/run_log.txt` - 运行日志和跳过记录

**关键特性**：
- 单个关键词失败不影响其他关键词
- 使用safe_subprocess_run()替代subprocess.run()
- 自动检查点和断点续传（通过检测已存在文件）

### 数据库模式

**scored_news表** - 主要新闻数据：
- 包含title, link, source, content, score, keyword等字段
- 唯一键：(title, link)防止重复

**summary_news表** - 生成的摘要：
- 包含date, keyword, summary, platform, model等字段
- round字段记录摘要轮次

**news_source_stats表** - 源统计：
- 记录每个关键词每天的新闻来源域名统计

**news_websites表** - 网站元数据：
- 存储网站名称映射

### 输出文件结构

```
output/
├── {date}/
│   ├── {date}_{主关键词}.json          # 采集+提取+评分后的完整数据
│   ├── tmp_{date}_{主关键词}_{搜索关键词}.json  # 临时采集文件
│   ├── summary_{date}_{关键词}.json     # AI生成的摘要
│   └── short_summary_{date}_{关键词}.json  # 500字短摘要
├── run_log.txt                          # 运行日志
└── error_log.txt                        # 错误日志
```

## 重要开发注意事项

### 关键词处理逻辑
- 主关键词（main keyword）可以映射到多个搜索关键词
- 系统会为每个搜索关键词单独调用API，然后合并结果
- 合并时会添加search_keyword字段标识来源

### 检查点和恢复机制
main.py 在执行每个阶段前会检查输出文件是否存在：
- 如果存在且所有新闻都有content字段，跳过内容提取
- 如果存在且所有新闻都有score字段，跳过AI评分
- 这使得系统可以从中断处恢复执行

### 并发处理
- news_scorer.py使用ThreadPoolExecutor进行并发评分
- 默认最大并发数可通过参数调整
- ScoringClientPool确保连接不会过度使用

### 防屏蔽措施
- 随机延迟（在fetch_content.py中）
- 使用真实浏览器模拟（Playwright/Selenium）
- 轮换User-Agent

### Web界面特性
- 按日期和关键词浏览新闻
- 支持按评分筛选
- 显示新闻源统计
- 可下载JSON文件和查看日志

## 测试

可用的测试脚本：
- `test_fetcher.py` - 测试各个新闻源API的采集功能
- `test_news_fetcher.py` - 采集器额外测试
- `test_prompt_building.py` - 测试AI提示构建

测试示例：
```bash
# 测试Google News采集
python test_fetcher.py serp_googlenews "养老"

# 测试百度新闻采集
python test_fetcher.py serp_baidunews "公积金"
```

测试输出会保存到 `output/test/` 目录。
