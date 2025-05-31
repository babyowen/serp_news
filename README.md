# 新闻采集与正文抓取自动化系统

## 项目简介
本项目实现了多新闻源自动采集、正文抓取与统一格式化存储，具备高可用性、自动化、易维护等特点。支持关键词批量处理、自动适配反爬机制、详细日志追踪，并对依赖环境和驱动做了项目级隔离。

---

## 主要功能

### 1. 多新闻源采集与统一格式化
- 支持 Google News、Baidu News、Bing News、DuckDuckGo News 等多源采集。
- 采集结果统一格式化为 JSON 文件，按日期和关键词分类存储。
- 自动去重、黑名单过滤。

### 2. 自动正文抓取（多重兜底+定制化）
- 对每条新闻链接，自动抓取正文并统计字数。
- **抓取顺序**：
  1. **trafilatura**（高效静态正文提取）
  2. **newspaper3k**（新闻站点适配性强，静态提取）
  3. **Playwright 渲染页面**，先用 **Newspaper3k** 提取正文，失败再用 **Readability（python-readability）** 提取正文
  4. **Selenium定制化抓取兜底**（针对特定站点专属选择器）
- Playwright 负责动态渲染页面，Newspaper3k/Readability 专注于正文内容提取，极大提升抓取成功率和兼容性。
- 针对特定新闻站点定制化选择器，极大提升抓取成功率：
  - 针对特定新闻站点可灵活扩展定制化选择器，极大提升抓取成功率。
  - 定制化抓取规则集中在 config_grab_rules.py 文件，采用注册表机制，未来可随时扩展，无需修改主流程。
- 所有通用方法和定制化抓取均失败时，正文为空，字数为0。

### 3. 反爬虫与稳定性机制
- Playwright/Selenium 抓取时自动使用自定义 User-Agent，模拟真实浏览器，提升兼容性。
- 同一网站连续抓取时自动随机延时（1~4秒），降低被封风险。
- 支持忽略 SSL 证书错误，兼容部分证书异常站点。

### 4. 日志记录与失败追踪
- 每次运行自动记录到 `output/run_log.txt`：
  - 处理的关键词、对应 JSON 文件
  - 成功抓取正文的数量
  - 抓取失败的新闻（标题+链接）
  - 是否使用定制化抓取（custom_grab 字段，日志中有详细统计）
- 便于后续人工补录或问题排查。

### 5. 关键词批量处理与可配置
- 支持通过 `config.py` 配置关键词列表。
- `main.py` 可自动批量处理所有关键词。
- `fetch_content.py` 支持命令行单关键词处理，便于测试和调试。
- 支持命令行参数指定日期（如 `python main.py 2025-05-28`），便于补抓历史数据。
- 支持 `--test` 参数区分测试/正式模式，日志中有标记。

---

## 运行方法

### 依赖安装
```bash
pip install -r requirements.txt
```

### 单关键词正文抓取
```bash
python fetch_content.py 关键词 [日期, 格式:YYYY-MM-DD] [--test]
# 例：python fetch_content.py 公积金 2025-05-28 --test
```

### 批量采集与处理
```bash
python main.py [日期, 格式:YYYY-MM-DD]
# 例：python main.py 2025-05-28
```

---

## 目录结构示例
```
serp_news/
├── config.py
├── main.py
├── news_fetcher.py
├── fetch_content.py
├── requirements.txt
├── drivers/                # chromedriver自动下载目录
├── output/
│   ├── 2025-05-28/
│   │   ├── 2025-05-28_公积金.json
│   │   └── ...
│   └── run_log.txt
└── README.md
```

---

## 依赖说明
- trafilatura
- newspaper3k
- selenium
- webdriver-manager
- requests
- beautifulsoup4
- python-dateutil
- playwright
- readability-lxml
- 其它见 requirements.txt

---

## 特色与优势
- 自动适配反爬虫与动态渲染，极大提升正文抓取成功率
- Playwright 渲染+Newspaper3k/Readability 提取，兼容性强，适配现代新闻站点
- 针对主流新闻站点定制化抓取，灵活可扩展
- 驱动与依赖项目隔离，升级无忧
- 日志详尽，便于追踪与维护
- 支持灵活扩展与定制

---

如有问题或需定制化扩展，欢迎联系开发者。

# 常用命令格式与功能说明

## 批量采集与处理

- **抓取"昨天"新闻（默认）**
  ```bash
  python main.py
  ```
  自动按 config.py 配置的关键词，抓取昨天的新闻并保存。

- **抓取指定日期新闻**
  ```bash
  python main.py YYYY-MM-DD
  # 例：python main.py 2025-05-28
  ```
  抓取指定日期的新闻，便于补抓历史数据。

## 单关键词正文抓取与补录

- **抓取指定关键词的新闻正文（默认昨天）**
  ```bash
  python fetch_content.py 关键词
  # 例：python fetch_content.py 公积金 yyyy-mm-dd
  ```

- **抓取指定关键词和日期的新闻正文**
  ```bash
  python fetch_content.py 关键词 YYYY-MM-DD
  # 例：python fetch_content.py 公积金 2025-05-28
  ```

- **测试模式运行（日志中标记"测试"）**
  ```bash
  python fetch_content.py 关键词 [YYYY-MM-DD] --test
  # 例：python fetch_content.py 公积金 2025-05-28 --test
  ```

## 单新闻链接抓取调试（开发/测试专用）

- **直接抓取并打印单个新闻链接正文（不写入文件，适合定制化规则调试）**
  ```bash
  python fetch_content.py test --url="https://www.cnstock.com/commonDetail/448150"
  # 或
  python fetch_content.py test --url https://www.cnstock.com/commonDetail/448150
  ```
  - 只需将 test 换成任意关键词（此模式下不会用到）。
  - 结果会直接在终端输出，包括字数、是否定制化、正文预览。
  - 适合遇到抓取不正确的网站时，快速调试定制化规则。

## 其它说明
- 所有运行日志统一写入 `output/run_log.txt`，便于追踪和问题排查。
- 采集结果按日期和关键词存储于 `output/日期/` 子目录。
- 依赖安装：
  ```bash
  pip install -r requirements.txt
  ```

如需更多用法或定制化扩展，欢迎查阅源码或联系开发者。

## 正文抓取定制化机制（规则注册表+动态调度）

本项目正文抓取采用多重兜底机制（trafilatura、newspaper3k、Playwright渲染+正文提取、Selenium定制化），并实现了**定制化抓取规则注册表+动态调度机制**，高效支持多站点定制化抓取。

#### 机制说明
- 定制化抓取规则集中维护在 [config_grab_rules.py](config_grab_rules.py) 文件中，每条规则包含：
  - 匹配函数（如 `lambda url: 'cnstock.com' in url`）
  - 抓取函数（如 `def grab_cnstock(driver): ...` 或 `def grab_cnstock_playwright(page): ...`）
- 主流程遍历规则表，**第一个命中的规则即执行其抓取函数**，返回正文。
- **定制化抓取支持 Playwright 和 Selenium 两种方式**，主流程会优先尝试 Playwright 定制化，失败后再用 Selenium 定制化。
- 只要命中定制化规则（无论是否抓取到正文），立即 return，不再进入通用抓取分支。
- 未命中定制化规则时，自动走通用抓取逻辑（常见正文容器、正文提取库等）。
- 每条新闻正文结果会在 JSON 中增加 `custom_grab` 字段，布尔值，准确反映是否为定制化抓取。

#### custom_grab 字段说明
- `custom_grab: true`：命中定制化规则，无论是否抓取到正文，均视为定制化抓取。
- `custom_grab: false`：未命中定制化规则，走通用抓取。
- 这样可以准确统计、分析定制化抓取的命中情况，避免混入通用抓取内容。

#### 日志与调试
- 日志会详细记录每次抓取是否命中定制化规则、抓取是否成功、失败原因等。
- 只要命中定制化规则，即使正文为空，也会在日志和 json 中体现为定制化抓取。
- 推荐在定制化抓取函数中输出详细调试信息，便于排查和维护。

#### 扩展与维护
- 新增定制化站点时，只需在 `config_grab_rules.py` 增加一条规则和一个抓取函数（Playwright 或 Selenium 版本），无需修改主流程。
- 规则表支持任意复杂的匹配逻辑（如正则、域名、路径等）。
- 每条规则可加调试输出，便于排查。
- 详见 [config_grab_rules.py](config_grab_rules.py) 示例。

---

如有问题或需定制化扩展，欢迎联系开发者。

---

## 2024-06-09 抓取链路与调试记录

- 升级正文抓取链路为：trafilatura → newspaper3k（静态）→ Playwright 渲染+Newspaper3k/Readability 正文提取 → Selenium 定制化兜底。
- requirements.txt 增加 playwright 和 readability-lxml，修正依赖。
- Playwright 首次运行需执行 `playwright install` 安装浏览器内核。
- 实测 Playwright+Newspaper3k 能抓取大部分新闻，但部分站点（如 MSN）会返回隐私说明或个性化内容，正文提取可能误判。
- 发现自动化抓取与人工访问页面内容有差异，主要因 Cookie/同意记录、User-Agent、反爬策略等不同。
- 结论：部分站点需定制化规则或人工补录，自动化抓取难以 100% 覆盖所有新闻。

## 新闻源统计与可视化支持

- **自动统计所有采集过的新闻源**
  - 程序每次运行后会自动将所有采集到的新闻源域名（如 www.jfdaily.com）累积写入 `output/news_sources.txt`，每个域名一行，无重复。
  - 该文件会自动累积历史所有采集过的新闻源，便于后续做域名-中文名映射、前端展示、统计分析等。
  - 推荐在前端或数据分析阶段，结合 `news_sources.txt` 和你自定义的"域名-中文名"映射表，实现友好的新闻源展示。

- **统计每天每个关键词下各新闻源采集条数**
  - 程序会自动将每天每个关键词下各新闻源采集到的新闻条数，追加写入 `output/news_source_stats.json`。
  - 文件结构为：
    ```json
    [
      {"date": "2025-05-31", "keyword": "公积金", "domain": "www.qlwb.com.cn", "count": 3},
      {"date": "2025-05-31", "keyword": "养老", "domain": "www.jfdaily.com", "count": 5}
    ]
    ```
  - 便于后续做可视化分析（如：每日各关键词新闻源分布、趋势统计等）。

- **新闻源中文名映射建议**
  - 由于新闻源域名是动态累积的，建议你在前端或分析阶段维护一份"域名-中文名"映射表，结合 `news_sources.txt` 自动补全。
  - 这样可以灵活应对新新闻源的出现，无需在采集时提前维护所有域名。

- **其它说明**
  - 所有统计文件均在 `output/` 目录下，便于统一管理和后续数据分析。
  - 如需其它统计维度或格式，可随时扩展。