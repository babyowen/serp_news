# 新闻采集与正文抓取自动化系统

## 项目简介
本项目实现了多新闻源自动采集、正文抓取、AI打分与摘要总结的全自动链路，具备高可用性、自动化、易维护等特点。支持关键词批量处理、自动适配反爬机制、详细日志追踪，并对依赖环境和驱动做了项目级隔离。所有采集和正文抓取逻辑均严格筛选"昨天"的新闻，无法补充更早的历史新闻。

---

## 主要功能

### 1. 多新闻源采集与统一格式化
- 支持 Google News、Baidu News、Bing News、DuckDuckGo News 等多源采集。
- 采集结果统一格式化为 JSON 文件，按日期和关键词分类存储。
- 自动去重（标题+链接）、黑名单过滤、跳过视频新闻（如 tv.cctv.com）。
- 支持命令行参数指定日期（仅采集该日期"昨天"的新闻，主要用于补录当天漏采）。

### 2. 自动正文抓取（多重兜底+定制化）
- 对每条新闻链接，自动抓取正文并统计字数。
- 抓取顺序：
  1. **trafilatura**（高效静态正文提取）
  2. **newspaper3k**（新闻站点适配性强，静态提取）
  3. **Playwright 渲染页面**，先用 **Newspaper3k** 提取正文，失败再用 **Readability（python-readability）** 提取正文
  4. **Selenium定制化抓取兜底**（针对特定站点专属选择器）
- 针对特定新闻站点定制化选择器，极大提升抓取成功率，所有定制化规则集中在 `config_grab_rules.py`，可灵活扩展。
- 自动统计新闻源域名、采集条数，便于后续可视化。
- 支持单条新闻链接抓取调试，便于开发和补录。

### 3. AI新闻评分（news_scorer.py）
- 支持批量/单条新闻评分，自动跳过已评分文件。
- 评分采用大模型（如 deepseek-reasoner），可配置 API Key、模型等。
- 评分结果按分数降序保存，日志详细记录各分数段分布。
- 支持 --test_json 单条测试，便于 prompt 调优。

### 4. 新闻摘要总结（news_summarizer.py）
- 对3分及以上新闻自动生成摘要，支持多模型、多平台切换（如 deepseek、bailian）。
- 自动判断 token 长度，超限时自动切换模型。
- 摘要结果支持多版本累积，便于横向对比。
- 日志详细记录 prompt、token 统计、摘要结果等。

---

## 依赖与环境

- **Python 3.8+ 推荐。**
- 依赖安装：
  ```bash
  pip install -r requirements.txt
  ```
- **首次运行 Playwright 需执行：**
  ```bash
  playwright install
  ```
- Selenium/Chromedriver 由 webdriver-manager 自动下载，如遇异常可手动升级或清理缓存。

---

## 目录结构说明

```
serp_news/
├── config.py
├── main.py
├── news_fetcher.py
├── fetch_content.py
├── news_scorer.py
├── news_summarizer.py
├── config_grab_rules.py
├── requirements.txt
├── output/
│   ├── 2025-05-28/
│   │   ├── 2025-05-28_公积金.json
│   │   ├── 2025-05-28_公积金_scored.json
│   │   └── ...
│   ├── run_log.txt
│   ├── news_sources.txt           # 历史所有采集过的新闻源域名
│   └── news_source_stats.json     # 每天每关键词各新闻源采集条数
└── README.md
```

- `news_sources.txt`：历史所有采集过的新闻源域名，便于后续做域名-中文名映射、可视化。
- `news_source_stats.json`：每天每个关键词下各新闻源采集到的新闻条数，便于趋势分析。
- `run_log.txt`：所有采集、正文、评分、摘要等环节的详细日志。

---

## 运行方法与命令说明

### 1. 新闻采集（main.py）
- **默认抓取昨天所有关键词新闻：**
  ```bash
  python main.py
  ```
- **抓取指定日期（仅采集该日期"昨天"的新闻，主要用于补录当天漏采）：**
  ```bash
  python main.py YYYY-MM-DD
  # 例：python main.py 2025-05-28
  ```

### 2. 新闻正文抓取（fetch_content.py）
- **抓取指定关键词的新闻正文（默认昨天）：**
  ```bash
  python fetch_content.py 关键词
  # 例：python fetch_content.py 公积金
  ```
- **抓取指定关键词和日期的新闻正文（只处理该日期下的新闻）：**
  ```bash
  python fetch_content.py 关键词 YYYY-MM-DD
  # 例：python fetch_content.py 公积金 2025-05-28
  ```
- **测试模式（日志中标记"测试"）：**
  ```bash
  python fetch_content.py 关键词 [YYYY-MM-DD] --test
  # 例：python fetch_content.py 公积金 2025-05-28 --test
  ```
- **抓取单条新闻链接正文（开发调试用）：**
  ```bash
  python fetch_content.py test --url="https://news.example.com/xxx"
  ```

### 3. 新闻AI评分（news_scorer.py）
- **批量评分（对所有关键词/日期自动处理）：**
  ```bash
  python news_scorer.py
  ```
- **指定关键词和日期评分：**
  ```bash
  python news_scorer.py 关键词 YYYY-MM-DD
  # 例：python news_scorer.py 公积金 2025-06-01
  ```
- **单条新闻评分测试：**
  ```bash
  python news_scorer.py --test_json '{"title": "标题", "content": "正文", "keyword": "公积金"}'
  ```

### 4. 新闻摘要总结（news_summarizer.py）
- **指定关键词、日期进行摘要总结：**
  ```bash
  python news_summarizer.py --keyword 关键词 --date YYYY-MM-DD
  # 例：python news_summarizer.py --keyword 公积金 --date 2025-06-01
  ```
- **指定模型进行摘要总结（可选）：**
  ```bash
  python news_summarizer.py --keyword 关键词 --date YYYY-MM-DD --model qwen-plus-latest
  # 例：python news_summarizer.py --keyword 公积金 --date 2025-06-01 --model qwen-plus-latest
  ```

---

## 7. 常见问题与FAQ

### Q1: 能否补抓历史数据？  
A: **不支持补抓历史数据。**  
本项目所有采集和正文抓取逻辑均严格筛选"昨天"的新闻（即使指定日期参数，也只会处理昨天的数据），因此无法补充更早的历史新闻。请确保每日定时运行以避免数据缺失。

### Q2: 各主程序如何带参数运行？分别实现什么效果？

详见上文"运行方法与命令说明"部分。

### Q3: 如何调试某个新闻链接的正文抓取？
A: 运行如下命令，结果会直接输出到终端，便于调试定制化规则：
```bash
python fetch_content.py test --url="https://news.example.com/xxx"
```

### Q4: Playwright/Selenium 报错怎么办？
A:  
- 首次运行 Playwright 需执行 `playwright install` 安装浏览器内核。
- Selenium/Chromedriver 由 webdriver-manager 自动下载，如遇异常可手动升级或清理缓存。

### Q5: 采集结果、日志、统计文件在哪里？
A:  
- 所有输出、日志、统计均在 `output/` 目录下，按日期/关键词分类存储，便于管理和分析。

---

## 扩展性与维护

- **定制化抓取规则**：只需在 `config_grab_rules.py` 增加规则和抓取函数，无需改动主流程。
- **AI评分/摘要模型**：支持灵活切换和扩展，API Key、模型参数集中在 `config.py`。
- **日志与统计**：所有关键环节均有详细日志，便于追踪和问题排查。
- **所有主流程和关键函数均有详细注释，便于二次开发和维护。**

---

## 其它说明

- 采集、正文、评分、摘要等所有环节均有详细日志，便于追踪和复盘。
- 采集结果、统计、日志等全部本地化存储，便于后续数据分析和可视化。
- 如需自定义关键词、黑名单、API Key，请编辑 `config.py`。

---

## 版本与作者信息

- 最后更新时间：2024-06
- 作者/维护者：liuliang

如需进一步补充"常见报错与解决方法"、"贡献指南"等内容，可随时扩展。
如有问题或需定制化扩展，欢迎联系开发者。