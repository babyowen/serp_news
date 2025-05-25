# 多新闻源"昨天新闻"聚合抓取项目

## 项目简介
本项目通过多新闻源API（如SerpApi Google News、Baidu News、Bing News、GNews等）批量抓取指定关键词的"昨天新闻"，实现结构化输出、智能日期解析、去重、批量测试与日志记录，并配有简单Web前端浏览。结构清晰，便于后续扩展更多新闻源和API。

---

## 主要功能
- 支持多新闻源API抓取，结构化输出新闻数据。
- 智能解析多种日期格式，精准筛选"昨天新闻"。
- 支持批量关键词抓取、自动保存原始与筛选后数据。
- 多源结果合并、标题+链接去重，便于后续分析。
- 命令行参数灵活指定关键词和新闻源，便于批量测试。
- 完善的日志记录和数据文件命名规范。
- 提供简单Web前端，便于按日期、关键词浏览抓取结果。

---

## 目录结构
```
serp_news/
├── main.py                # 主流程，批量抓取、筛选、合并、去重、保存
├── news_fetcher.py        # 各新闻源API抓取与分页逻辑
├── config.py              # 配置文件，管理API Key、默认关键词等
├── test_fetcher.py        # 测试脚本，支持单独测试任意新闻源
├── requirements.txt       # 依赖包
├── .env                   # API Key等敏感信息
├── app.py                 # Flask Web前端
├── templates/             # 前端页面模板
│   ├── index.html         # 日期选择页
│   ├── keyword_select.html# 关键词选择页
│   └── date.html          # 新闻详情页
├── output/                # 自动生成，存放所有抓取结果
│   └── YYYY-MM-DD/        # 按抓取日期归档
│       ├── raw_serp_googlenews_关键词_日期.json
│       ├── raw_serp_baidunews_关键词_日期.json
│       ├── raw_serp_bingnews_关键词_日期.json
│       ├── yesterday_关键词_日期.json
│       └── run_log.txt
└── README.md
```

---

## 数据文件命名与存储规范
- **原始API返回内容**：
  - `raw_平台_新闻源_关键词_抓取日期.json`  
    例：`raw_serp_baidunews_公积金_2024-07-23.json`
- **筛选后"昨天新闻"**：
  - `yesterday_关键词_抓取日期.json`  
    例：`yesterday_公积金_2024-07-23.json`
- **日志**：
  - `run_log.txt` 记录每次抓取、去重、保存等统计信息
- **所有数据按抓取日期归档**，便于溯源和批量分析。

---

## 结构化输出与字段说明
- **保留API返回的全部原始内容**，不强制字段一致。
- 主程序仅补充以下元信息字段，便于后续合并、查重、分析：
  - `fetch_date`：抓取日期（YYYY-MM-DD）
  - `parsed_date`：解析后的具体日期（YYYY-MM-DD），解析失败为null
  - `keyword`：本次抓取的关键词
  - `sourceapi`：数据来源（如serp_googlenews、serp_baidunews、serp_bingnews等）
- 其它字段均为API原始返回内容，便于后续灵活扩展和分析。

---

## 各新闻源"昨天新闻"判断逻辑
- **SerpApi Baidu News**
  - 支持"昨天"、"几小时前"、"几分钟前"、具体日期（如2024-07-23 11:45），详见`is_baidu_news_yesterday`和`parse_baidu_news_date`。
  - 只保留属于昨天的新闻。
- **SerpApi Google News**
  - 支持"昨天"、"几小时前"、"几分钟前"、标准日期、国际化日期（如05/24/2025, 11:21 PM, +0000 UTC）、中文日期等，详见`is_google_news_yesterday`和`parse_google_news_date`。
- **SerpApi Bing News**
  - 支持"X 小時"、"X 分鐘"、"X 天"等格式，详见`is_bing_news_yesterday`。
- **GNews API**
  - 直接通过API的from/to参数限定为昨天，无需本地判断。
- **其它新闻源**
  - 建议参考上述逻辑，结合API返回的时间字段，自定义本地判断函数。

---

## 多新闻源合并与去重
- 主程序自动合并所有API抓取到的"昨天新闻"，以`(title, link)`为唯一键去重。
- 去重后统计每个API贡献的新闻数量，便于分析各源覆盖率。
- 最终结果统一保存，便于后续批量分析和溯源。

---

## 如何扩展新新闻源/API
1. **在`news_fetcher.py`中新增抓取函数**，命名规范如`fetch_xxx_news`，支持关键词、分页、原始数据返回。
2. **在`main.py`中调用新函数**，并实现对应的日期解析与"昨天新闻"筛选逻辑。
3. **只需补充元信息字段（fetch_date、parsed_date、keyword、sourceapi）**，无需强制结构统一。
4. **数据文件命名遵循统一规范**，便于归档和批量处理。
5. **如需批量测试**，在`test_fetcher.py`中增加对应测试入口。

---

## 批量抓取与测试
- 支持命令行参数批量指定关键词和新闻源，自动循环抓取、保存、去重。
- 测试脚本`test_fetcher.py`可单独测试任意API，自动保存原始和筛选后JSON。
- 日志详细记录每次抓取、筛选、去重、保存等信息。

---

## Web前端功能说明
- 采用Flask+Jinja2实现，相关代码见`app.py`和`templates/`目录。
- 支持：
  - 按日期浏览所有抓取结果
  - 按关键词筛选、查看"昨天新闻"详情
  - 支持下载原始JSON文件
- 启动方法：
  1. `pip install -r requirements.txt`
  2. `python app.py`
  3. 浏览器访问 http://127.0.0.1:5000/

---

## 依赖与环境管理
- 推荐使用`python -m venv venv`创建虚拟环境。
- 依赖包见`requirements.txt`，如：
  - `requests`（HTTP请求，抓取API和网页）
  - `python-dotenv`（读取.env中的API Key）
  - `python-dateutil`（智能日期解析）
  - `beautifulsoup4`（网页抓取与解析，如需）
  - `flask`（Web前端）
  - `jinja2`（Flask模板渲染）
- `.env`文件存储API Key，避免泄露。

---

## 常见问题与维护建议
- **API参数限制**：部分API参数（如`tbs`、`so`等）可能无效，建议本地筛选"昨天新闻"。
- **日期解析失败**：持续完善日期解析逻辑，兼容更多格式。
- **反爬与稳定性**：如遇网页反爬，优先用官方API，网页爬取仅作补充。
- **数据一致性**：所有新闻源输出字段、文件命名、归档方式保持一致，便于后续批量分析。
- **日志与原始数据**：建议长期保留，便于溯源和问题排查。

---

## 未来扩展建议
- 支持更多新闻源（如NewsAPI、Newsdata.io、Mediastack等），只需按本规范新增函数和解析逻辑。
- 支持MySQL等数据库存储，便于大规模分析。
- 支持定时任务、自动化批量抓取。
- 支持更智能的查重（如内容相似度、长标题归一化等）。

---

## 联系与反馈
如有问题或建议，欢迎在项目Issue区留言。 