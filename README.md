# 🤖 新闻采集与分析自动化系统

## 📖 项目简介

欢迎来到我们的**全自动、智能化新闻处理流水线**！

你可以把它想象成一个不知疲倦的机器人团队，7x24小时为你工作。它们的目标是：
1.  **自动“阅读”**：从各大新闻网站上，把跟你关心的关键词（如“养老”、“公积金”）相关的新闻都找回来。
2.  **智能“理解”**：用AI大脑深度阅读每篇新闻，判断它的重要性，并写出高质量的摘要。
3.  **整齐“归档”**：把所有处理好的信息，分门别类地存入我们的专属数据库和文件柜，方便随时查看和分析。

本项目从繁琐的人工浏览和复制粘贴中解放出来，真正实现“一次设定，长期受益”。

### ✨ 核心特性

- 🌐 **多源采集**：像侦察兵一样，同时从Google News、百度新闻、Bing News、DuckDuckGo News四个平台搜集信息。
- 🔑 **智能抓取**：配备“万能钥匙”，通过多级兜底策略（定制规则 > 工具库 > 模拟浏览器）确保高成功率地获取新闻全文。
- 🤖 **AI智能分析**：集成DeepSeek等大模型（当前使用 deepseek-v4-flash），实现高精度的AI评分（0-5分）和智能摘要。支持关键词特定的评分提示、并发处理和DeepSeek/百炼双平台自动切换。
- ⚡ **并发处理**：支持多线程并发AI评分（默认最大并发数3），大幅提升处理效率。
- 🔄 **全自动化**：从采集到入库，一键运行，并支持断点续传。
- 📊 **数据可视化**：提供简洁的Web界面，让数据结果和统计分析一目了然。
- 🛡️ **超强容错**：拥有完善的错误处理和日志系统，确保系统稳定运行，过程可追溯。

## 🎯 系统核心模块

我们的"新闻工厂"由五个核心步骤组成，就像一条精密的流水线：

1.  **新闻采集系统 (信息侦察兵)**: 负责从四个新闻源API自动采集新闻数据。
2.  **正文抓取系统 (万能钥匙)**: 负责打开新闻链接，把完整的正文内容给拿回来。
3.  **AI智能评分系统 (首席评审官)**: 阅读每一篇新闻，并根据其重要性给出一个0-5分的评分（支持并发处理）。
4.  **智能摘要系统 (内容创作团队)**: 将高分新闻精炼成一份高质量的决策参考。
5.  **数据库存储系统 (数字图书馆)**: 将处理好的数据，结构化存储到MySQL数据库。

此外还有两个支撑系统：
- **Web可视化界面 (中央控制台)**: 提供数据查看和管理的Web界面。
- **错误处理与日志系统 (安全与监控中心)**: 提供完善的错误处理和日志记录机制。

> 想了解每个模块更详细的工作原理和设计思路吗？请查阅我们更详尽的蓝图：[**`xuqiu.md` (功能需求文档)**](./xuqiu.md)。

## 🚀 快速开始

### 1. 环境要求

- **Python 3.8+**
- **MySQL 5.7+**
- **Chrome浏览器**

### 2. 安装与配置

1.  **克隆项目**
    ```bash
    git clone <repository-url>
    cd serp_news
    ```

2.  **安装依赖**
    ```bash
    pip install -r requirements.txt
    ```

3.  **安装Playwright浏览器驱动**
    ```bash
    playwright install
    ```

4.  **配置环境变量**
    
    在项目根目录创建 `.env` 文件，并填入你的配置信息：
    ```ini
    # API配置
    SERPAPI_KEY=your_serpapi_key
    DEEPSEEK_API_KEY=your_deepseek_api_key
    BAILIAN_API_KEY=your_bailian_api_key  # 可选，当token超限时使用
    
    # 数据库配置
    MYSQL_HOST=localhost
    MYSQL_PORT=3306
    MYSQL_USER=your_username
    MYSQL_PASSWORD=your_password
    MYSQL_DB=serp_news
    ```

5.  **创建数据库表**
    
    连接到你的MySQL数据库，执行以下SQL语句创建所需的表：
    ```sql
    -- 新闻评分表
    CREATE TABLE IF NOT EXISTS `scored_news` (
      `id` int(11) NOT NULL AUTO_INCREMENT,
      `date` varchar(64) DEFAULT NULL,
      `title` varchar(255) DEFAULT NULL,
      `link` text,
      `source` varchar(255) DEFAULT NULL,
      `fetchdate` date DEFAULT NULL,
      `sourceapi` varchar(255) DEFAULT NULL,
      `thumbnail` text,
      `keyword` varchar(255) DEFAULT NULL,
      `content` longtext,
      `wordcount` int(11) DEFAULT NULL,
      `custom_grab` tinyint(1) DEFAULT NULL,
      `score` int(11) DEFAULT NULL,
      `search_keyword` varchar(255) DEFAULT NULL,
      PRIMARY KEY (`id`),
      UNIQUE KEY `title_link` (`title`,`link`(255))
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    
    -- 新闻摘要表
    CREATE TABLE IF NOT EXISTS `summary_news` (
      `id` int(11) NOT NULL AUTO_INCREMENT,
      `date` date DEFAULT NULL,
      `keyword` varchar(255) DEFAULT NULL,
      `summary` longtext,
      `platform` varchar(255) DEFAULT NULL,
      `model` varchar(255) DEFAULT NULL,
      `round` int(11) DEFAULT '1',
      `judge_suggestion` longtext,
      PRIMARY KEY (`id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    
    -- 新闻源统计表
    CREATE TABLE IF NOT EXISTS `news_source_stats` (
      `id` int(11) NOT NULL AUTO_INCREMENT,
      `date` date NOT NULL,
      `keyword` varchar(50) NOT NULL,
      `domain` varchar(100) NOT NULL,
      `count` int(11) NOT NULL,
      PRIMARY KEY (`id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    
    -- 新闻网站表
    CREATE TABLE IF NOT EXISTS `news_websites` (
      `id` int(11) NOT NULL AUTO_INCREMENT,
      `website` varchar(255) DEFAULT NULL,
      `name` varchar(255) DEFAULT NULL,
      PRIMARY KEY (`id`),
      UNIQUE KEY `website` (`website`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    ```

6.  **配置关键词**
    
    打开 `config.py` 文件，根据你的需求修改 `SEARCH_KEYWORDS` 字典：
    ```python
    SEARCH_KEYWORDS = {
        # "主关键词": ["搜索关键词1", "搜索关键词2", ...],
        "养老": ["养老"],
        "公积金": ["公积金"],
        "政府基金": ["政府基金", "引导基金", "母基金"],
        "江苏省国资委": ["江苏省国资委", "江苏国信集团", "江苏交通控股", "东部机场", "苏豪", "中江集团", "江苏农垦", "徐矿集团", "江苏沿海集团", "江苏港口集团", "江苏铁路集团", "江苏环保集团", "江苏盐业集团", "江苏粮食集团", "江苏省高投", "金陵饭店", "钟山宾馆", "江苏水源", "江苏体育集团", "江苏国金集团", "江苏省国投集团", "江苏规划设计集团", "江苏省数据集团", "江苏凤凰集团", "江苏文投", "江苏文化投资集团"],
        "中国烟草": ["中国烟草", "江苏烟草"]
    }
    ```

### 3. 一键运行

```bash
# 1. 运行完整自动化流程 (默认处理昨天的数据)
python main.py

# 2. 启动Web界面查看结果
python app.py  # 默认端口5001
```
默认情况下，系统会自动处理**昨天**的新闻。你也可以在运行 `main.py` 时传入指定日期，例如 `python main.py 2025-06-10`。

## 🛠️ 高级用法：手动执行单个模块

除了全自动运行，你也可以单独运行流水线中的某一个步骤，这对于调试和特定任务处理非常有用。

| 模块 | 命令示例 |
| :--- | :--- |
| **新闻采集** | `python fetch_and_filter.py "养老" 2025-06-10` |
| **正文抓取** | `python fetch_content.py "养老" 2025-06-10` |
| **单网页测试** | `python fetch_content.py "关键词" 2025-06-10 --url="https://example.com/news"` |
| **AI评分** | `python news_scorer.py "养老" 2025-06-10` |
| **500字短摘要** | `python news_item_summarizer.py 2025-06-10` |
| **公积金地域分析** | `python news_region_analyzer.py --keyword 公积金 --date 2025-06-10` |
| **数据入库** | `python write_to_mysql.py --date 2025-06-10` |

### 📝 单网页抓取测试说明

当你想要测试系统对某个具体网页的正文抓取效果时，可以使用单网页测试功能：

```bash
# 基础用法：测试抓取指定URL的正文
python fetch_content.py "测试关键词" 2025-06-10 --url="https://www.example.com/news/article.html"

# 实际示例：测试抓取人民网新闻
python fetch_content.py "养老" 2025-06-10 --url="http://finance.people.com.cn/n1/2025/0610/c1004-40265431.html"

# 测试新浪财经新闻
python fetch_content.py "公积金" 2025-06-10 --url="https://finance.sina.com.cn/china/gncj/2025-06-10/doc-inafcusu1234567.shtml"
```

**参数说明：**
- `"测试关键词"`：**必需参数**，用于日志记录的关键词标识，可以是任意文本
- `2025-06-10`：**必需参数**，日期格式为 YYYY-MM-DD（在测试模式下主要用于日志记录）
- `--url="目标网址"`：要测试抓取的具体网页URL

**测试结果显示：**
- **字数**：成功抓取的正文字符数
- **定制化**：是否使用了针对该网站的定制化抓取规则
- **正文预览**：显示前500字符的正文内容预览

这个功能特别适用于：
- 🔍 **调试新网站**：测试系统对新发现网站的抓取效果
- 🛠️ **验证规则**：检验定制化抓取规则是否正常工作
- 📊 **效果评估**：比较不同抓取策略的效果差异

**常见问题排查：**

如果遇到 "Could not reach host. Are you offline?" 错误：
1. **检查网络连接**：确认能正常访问互联网
2. **重试访问**：网站可能有临时访问限制，稍后重试
3. **检查代理设置**：如果使用代理，确认代理配置正确
4. **测试其他网站**：尝试抓取其他网站确认是否为特定网站问题

示例排查命令：
```bash
# 测试不同网站
python fetch_content.py "测试" 2025-06-10 --url="http://www.people.com.cn/finance/"
python fetch_content.py "测试" 2025-06-10 --url="https://www.sina.com.cn/"
```

## 🛡️ 容错与日志

### 强大的容错机制
- **单步失败不影响整体**：某个关键词处理失败不会中断其他关键词。
- **智能断点续传**：自动跳过已完成的数据，程序中断后可直接重新运行。
- **多级兜底策略**：正文抓取和API调用都具备重试和后备方案。

### 详细的日志系统
所有运行日志和错误信息都可以在 `output/` 目录下找到：
- **`run_log.txt`**：程序运行状态日志，记录了每个步骤的执行情况、统计数据和跳过原因。
- **`error_log.txt`**：结构化的错误日志，当出现问题时，这里会提供详细的错误时间、脚本、关键词和错误信息，便于快速排查。

---
*该文档最后更新于：2026-05-14*
