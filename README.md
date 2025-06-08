# 新闻采集与正文抓取自动化系统

## 项目简介
本项目实现了多新闻源自动采集、正文抓取、AI打分与摘要总结的全自动链路，具备高可用性、自动化、易维护等特点。支持关键词批量处理、自动适配反爬机制、详细日志追踪，并对依赖环境和驱动做了项目级隔离。所有采集和正文抓取逻辑均严格筛选"昨天"的新闻，无法补充更早的历史新闻。

## 近期主要更新（2024-06）

1. **摘要支持三轮流程**：
   - 第一轮：初稿摘要。
   - 第二轮：评判官建议+优化摘要。
   - 第三轮：热点追踪（自动对比前一天和今天的摘要，识别持续热点，结构和输出自动保存）。
2. `config.py` 新增多轮摘要相关prompt配置，支持自定义每轮system/user prompt。
3. `write_to_mysql.py` 新增 `fetch_latest_summary`，可自动获取前一天最大轮次摘要。
4. `fetch_and_filter.py` 日志主关键词自动推断，保证日志主控主题准确。
5. 所有多轮摘要、热点追踪结果、prompt、日志均自动保存，无需手动干预。
6. 其它细节优化：数据库写入、日志、关键词配置等。

---

## 主要功能

### 1. 多新闻源采集与统一格式化
- 支持 Google News、Baidu News、Bing News、DuckDuckGo News 等多源采集。
- 采集结果统一格式化为 JSON 文件，按日期和关键词分类存储。
- 自动去重（标题+链接）、黑名单过滤、跳过视频新闻（如 tv.cctv.com）。
- 支持命令行参数指定日期（仅采集该日期"昨天"的新闻，主要用于补录当天漏采）。
- **自动跳过已抓取关键词：** 在抓取新闻列表时，程序会自动判断 `output/{日期}/{日期}_{关键词}.json`（合并去重后的主输出文件）是否已存在，存在则跳过该关键词，避免重复抓取。例如：如果 `output/2025-06-05/2025-06-05_政府基金.json` 已存在，则不会再次抓取"政府基金"的新闻。

#### 采集数据格式说明（2024-06统一规范）

每条新闻的JSON结构如下，所有字段均为自动生成，便于后续数据库写入和分析：

```json
{
  "title": "新闻标题",
  "link": "新闻链接",
  "source": "新闻来源",
  "date": "原始API返回的时间字符串，如 '1 day ago'、'昨天'、'2025-06-06 09:00' 等",
  "fetchdate": "抓取日期，格式如 '2025-06-06'，即本地采集时的日期",
  "sourceapi": "采集来源标记，如 'serp_googlenews'、'serp_baidunews'、'serp_bingnews'、'serp_duckduckgo_news'",
  "thumbnail": "缩略图链接（如有）",
  "keyword": "主关键词",
  "main_keyword": "主关键词（与keyword一致，便于聚合）",
  "search_keyword": "实际用于采集的搜索关键词",
  "content": "正文内容（正文抓取后补充）",
  "wordcount": 123,
  "custom_grab": false
  // 其它字段视API返回和后续流程自动补充
}
```

- `date` 字段保留原始API返回的时间字符串，便于追溯和灵活解析。
- `fetchdate` 字段为本地抓取时的日期，所有入库、统计均以此为准。
- `sourceapi` 字段标记采集来源，便于后续溯源和分析。
- 其它字段如 `content`、`wordcount`、`custom_grab` 等在正文抓取和后续流程中自动补充。

所有采集、正文、评分、摘要等流程均以此格式为基础，确保数据链路一致。

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

## 数据库结构

### scored_news 表
```sql
CREATE TABLE scored_news (
    id INT AUTO_INCREMENT PRIMARY KEY,
    date VARCHAR(64),           -- 原始API时间字符串
    title VARCHAR(255),
    link TEXT,
    source VARCHAR(255),
    fetchdate DATE,             -- 本地抓取日期
    sourceapi VARCHAR(255),     -- 采集来源标记
    thumbnail TEXT,
    keyword VARCHAR(255),       -- 主关键词
    content LONGTEXT,
    wordcount INT,
    custom_grab BOOLEAN,
    score INT
);
```

#### 字段映射说明
- `date`：对应JSON的原始API时间字符串，便于追溯。
- `fetchdate`：对应JSON的抓取日期，所有统计、分析、分区均以此为准。
- `sourceapi`：对应JSON的采集来源标记。
- 其它字段一一对应。

所有JSON采集字段与数据库表字段严格一一对应，确保数据链路清晰、可追溯。

### summary_news 表
```sql
CREATE TABLE summary_news (
    id INT AUTO_INCREMENT PRIMARY KEY,
    date DATE,
    keyword VARCHAR(255),
    summary LONGTEXT,
    platform VARCHAR(255),
    model VARCHAR(255)
);
```

---

## 数据库写入功能说明

- 所有新闻数据和摘要数据会自动写入MySQL数据库。
- `write_to_mysql.py` 支持批量导入，自动读取 config.py 里的 DEFAULT_KEYWORDS。
- 支持命令行参数 `--date`，不带参数时自动导入昨天的数据。
- 自动批量导入所有关键词的 scored/summary 两类文件。
- 文件不存在时自动跳过并提示。

---

## 数据库写入命令

### 1. 安装依赖
```sh
pip install pymysql python-dotenv
```

### 2. 配置数据库连接
在项目根目录新建 `.env` 文件，内容如下（请用你自己的信息替换）：
```
MYSQL_HOST=your_host
MYSQL_PORT=3306
MYSQL_USER=your_user
MYSQL_PASSWORD=your_password
MYSQL_DB=serp_news
```

### 3. 批量写入数据库
- 导入昨天的数据：
```sh
python write_to_mysql.py
```
- 导入指定日期的数据（如2025-06-01）：
```sh
python write_to_mysql.py --date 2025-06-01
```

---

## 关键词配置机制（2024-06更新）

### 新机制说明

本项目关键词配置采用"主关键词+搜索用关键词"映射机制，极大提升了采集灵活性和主题聚合能力。

- **主关键词**：你关心的主题词，用于后续所有打分、摘要、数据库写入、统计分析等，是所有流程的核心。
- **搜索用关键词**：实际用于采集新闻的关键词，可以和主关键词相同，也可以完全不同，也可以有多个。
- **采集时**：遍历每个主关键词的所有搜索用关键词，采集到的新闻全部归属于主关键词。
- **打分、摘要、数据库写入时**：全部以主关键词为核心，所有新闻都归属于主关键词。
- **日志输出**：会标明主关键词和实际搜索用关键词，便于追溯和分析。

### 配置示例

在 `config.py` 中：

```python
SEARCH_KEYWORDS = {
    "养老": ["养老"],
    "公积金": ["公积金"],
    "政府基金": ["政府基金", "引导基金"],
    "江苏南京国资委": ["江苏省国资委", "南京市国资委"]
    # 你可以继续扩展更多主关键词和搜索用关键词
}
DEFAULT_KEYWORDS = list(SEARCH_KEYWORDS.keys())
DEFAULT_KEYWORD = DEFAULT_KEYWORDS[0]
```

- 采集时会用所有搜索用关键词去抓取，采集到的新闻都归属于主关键词（如 `2025-06-05_政府基金.json`）。
- 后续所有流程（打分、摘要、数据库写入）都只处理主关键词的 json 文件。
- 你可以灵活设定主关键词和搜索用关键词的关系，主题聚合更清晰。

### 旧机制说明（已废弃）

- 旧版的"二级关键词"配置（`SECONDARY_KEYWORDS`）已废弃，不再使用。
- 现在只需维护 `SEARCH_KEYWORDS`，无需再考虑二级关键词逻辑。

---

## 新增功能说明（2024-06）

### 1. MySQL 建表说明

为支持抓取统计数据的结构化存储，需在 MySQL 中新建如下表：

```sql
CREATE TABLE news_source_stats (
    id INT PRIMARY KEY AUTO_INCREMENT,
    date DATE NOT NULL,
    keyword VARCHAR(50) NOT NULL,
    domain VARCHAR(100) NOT NULL,
    count INT NOT NULL
);
```

### 2. write_to_mysql.py 新增抓取统计数据入库功能

- 新增 `insert_news_source_stats(json_path, target_date)` 函数，支持将 `output/news_source_stats.json` 中 `date=target_date` 的所有数据写入 `news_source_stats` 表。
- 查重逻辑：同一天、同关键词、同域名的数据只插入一次（`date+keyword+domain` 唯一）。
- 日志自动记录写入、跳过、异常等情况。
- 在主流程 `main()` 中自动调用，无需手动干预。
- 只会写入"昨天"的数据，避免重复或历史数据误入。

#### 用法

1. 确保已在数据库中建好 `news_source_stats` 表。
2. 正常运行 `python write_to_mysql.py`，会自动将 `output/news_source_stats.json` 中昨天的数据写入数据库。
3. 日志输出在 `output/run_log.txt`，可追踪写入详情。

#### 相关函数说明

```python
def insert_news_source_stats(json_path, target_date):
    # 读取 news_source_stats.json，过滤 date==target_date 的数据
    # 查重（date+keyword+domain），避免重复插入
    # 写入 news_source_stats 表
    # 日志记录写入、跳过、异常
```