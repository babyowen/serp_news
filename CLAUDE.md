# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此代码库中工作时提供指导。

## 项目概述

这是一个自动化新闻采集与AI分析系统，从多个新闻源（Google News、百度新闻、Bing News、DuckDuckGo News）获取新闻，提取内容，使用AI（DeepSeek模型）进行重要性评分，并生成摘要。系统支持中文关键词监控，将结果存储在MySQL中，并提供Flask Web界面进行可视化。

## 主要命令

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

### 环境设置
```bash
# 安装依赖
pip install -r requirements.txt

# 安装Playwright浏览器
playwright install
```

## 架构概览

### 核心模块
1. **main.py** - 运行完整流水线的编排脚本
2. **fetch_and_filter.py** - 多源新闻采集与过滤
3. **fetch_content.py** - 具有5级兜底策略的内容提取
4. **news_scorer.py** - AI驱动的新闻评分（0-5分制）
5. **news_summarizer.py** - 三轮AI摘要生成
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
- **摘要**：三轮流程（草稿→评判审查→优化→热点追踪）
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