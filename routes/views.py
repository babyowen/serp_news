"""公开路由 — 首页综合看板、新闻浏览、数据库查看"""
import os
import json
import datetime
from flask import render_template, send_from_directory, request, Blueprint, abort, url_for
from db_utils import get_connection, get_table_name
from runtime_config import value

views_bp = Blueprint("views", __name__)
OUTPUT_DIR = "output"


@views_bp.route("/")
def index():
    DEFAULT_KEYWORDS = value("DEFAULT_KEYWORDS")
    # 日期参数：默认昨天到昨天（单日）
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    date_from = request.args.get("date_from", yesterday)
    date_to = request.args.get("date_to", yesterday)
    score_min = request.args.get("score_min", "")
    score_max = request.args.get("score_max", "")
    source = request.args.get("source", "")
    sourceapi = request.args.get("sourceapi", "")

    selected_keyword = request.args.get("keyword", next(iter(DEFAULT_KEYWORDS), ""))
    if selected_keyword not in DEFAULT_KEYWORDS and DEFAULT_KEYWORDS:
        abort(400, "无效的新闻主题")
    try:
        page = int(request.args.get("page", "1"))
        if page < 1 or page > 1000000:
            raise ValueError
        if any(datetime.date.fromisoformat(d).isoformat() != d for d in (date_from, date_to)):
            raise ValueError
        if date_from > date_to:
            raise ValueError
        for score in (score_min, score_max):
            if score != "" and not 0 <= int(score) <= 5:
                raise ValueError
        if score_min != "" and score_max != "" and int(score_min) > int(score_max):
            raise ValueError
    except ValueError:
        abort(400, "日期、评分或页码无效")
    page_size = 50

    # 构建查询
    conditions = ["fetchdate BETWEEN %s AND %s"]
    params = [date_from, date_to]

    if score_min != "":
        conditions.append("score >= %s")
        params.append(int(score_min))
    if score_max != "":
        conditions.append("score <= %s")
        params.append(int(score_max))
    if source:
        conditions.append("source LIKE %s")
        params.append(f"%{source}%")
    if sourceapi:
        conditions.append("sourceapi = %s")
        params.append(sourceapi)

    # 仅显示本地配置的关键词，避免生产库历史数据污染
    placeholders = ",".join(["%s"] * len(DEFAULT_KEYWORDS))
    conditions.append(f"keyword IN ({placeholders})" if DEFAULT_KEYWORDS else "1=0")
    params.extend(DEFAULT_KEYWORDS)

    where = " AND ".join(conditions)

    conn = get_connection(dict_cursor=True)
    try:
        cursor = conn.cursor()

        # 统计
        table = get_table_name()
        cursor.execute(
            f"SELECT keyword, COUNT(*) as cnt FROM {table} WHERE {where} GROUP BY keyword",
            params
        )
        stored_kw_stats = cursor.fetchall()
        count_by_keyword = {row["keyword"]: row["cnt"] for row in stored_kw_stats}
        kw_stats = [
            {"keyword": keyword, "cnt": count_by_keyword.get(keyword, 0)}
            for keyword in DEFAULT_KEYWORDS
        ]
        total = sum(row["cnt"] for row in kw_stats)

        selected_total = count_by_keyword.get(selected_keyword, 0)
        pages = max(1, (selected_total + page_size - 1) // page_size)
        page = min(page, pages)
        news_list = []
        if selected_total:
            cursor.execute(
                f"SELECT id, keyword, title, link, source, fetchdate, sourceapi, score, "
                f"LEFT(short_summary, 101) AS short_summary FROM {table} "
                f"WHERE {where} AND keyword = %s "
                "ORDER BY fetchdate DESC, score DESC, id DESC LIMIT %s OFFSET %s",
                [*params, selected_keyword, page_size, (page - 1) * page_size],
            )
            news_list = cursor.fetchall()

        # 筛选选项：当前日期范围内的所有来源和API
        cursor.execute(
            f"SELECT DISTINCT sourceapi FROM {table} WHERE fetchdate BETWEEN %s AND %s ORDER BY sourceapi",
            (date_from, date_to)
        )
        api_options = [r["sourceapi"] for r in cursor.fetchall()]

        cursor.close()
    finally:
        conn.close()

    filters = dict(date_from=date_from, date_to=date_to, score_min=score_min,
                   score_max=score_max, source=source, sourceapi=sourceapi)
    keyword_urls = {kw: url_for("views.index", **filters, keyword=kw)
                    for kw in DEFAULT_KEYWORDS}
    def page_url(number):
        return url_for("views.index", **filters, keyword=selected_keyword, page=number)

    return render_template("index.html", **filters,
                           total=total, kw_stats=kw_stats,
                           grouped={selected_keyword: news_list} if DEFAULT_KEYWORDS else {},
                           api_options=api_options, selected_keyword=selected_keyword,
                           keyword_urls=keyword_urls, page=page, pages=pages,
                           selected_total=selected_total,
                           previous_url=page_url(page - 1) if page > 1 else None,
                           next_url=page_url(page + 1) if page < pages else None)


@views_bp.route("/date/<date>")
def show_date(date):
    date_dir = os.path.join(OUTPUT_DIR, date)
    if not os.path.exists(date_dir):
        return f"日期 {date} 不存在", 404

    # 解析文件：优先用_scored.json，提取真实关键词
    files = [f for f in os.listdir(date_dir) if f.endswith(".json") and "_" in f and not f.startswith("raw_") and not f.startswith("tmp_")]
    keyword_map = {}  # keyword -> best filename (prefer _scored)
    for fname in files:
        # 去掉日期前缀和.json后缀，取中间部分作为关键词
        # e.g. "2026-05-12_养老.json" -> "养老"
        #      "2026-05-12_养老_scored.json" -> "养老"
        name = fname.replace(".json", "")
        name = name.split("_", 1)[-1]  # 去掉日期前缀 -> "养老" or "养老_scored"
        kw = name.replace("_scored", "")
        if not kw:
            continue
        # 优先保留 _scored 文件
        if kw not in keyword_map or "_scored" in fname:
            keyword_map[kw] = fname

    keywords = sorted(keyword_map.keys())

    keyword = request.args.get("keyword")
    if not keyword:
        return render_template("keyword_select.html", date=date, keywords=keywords)

    # 读取该关键词对应的文件
    fname = keyword_map.get(keyword)
    news_data = []
    if fname:
        path = os.path.join(date_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                items = json.load(f)
                if isinstance(items, dict):
                    items = [items]
                filtered_items = []
                for item in items:
                    filtered_items.append({
                        "title": item.get("title", ""),
                        "link": item.get("link", ""),
                        "source": item.get("source", ""),
                        "date": item.get("date", ""),
                        "fetchdate": item.get("fetchdate", ""),
                        "sourceapi": item.get("sourceapi", ""),
                        "thumbnail": item.get("thumbnail", None),
                        "keyword": item.get("keyword", ""),
                        "score": item.get("score", ""),
                    })
                news_data.append({"file": fname, "items": filtered_items})
        except Exception as e:
            news_data.append({"file": fname, "items": [], "error": str(e)})
    return render_template("date.html", date=date, keyword=keyword, news_data=news_data, keywords=keywords)


@views_bp.route("/output/<path:filename>")
def download_file(filename):
    return send_from_directory(OUTPUT_DIR, filename)


@views_bp.route("/database")
def show_database():
    conn = get_connection(dict_cursor=True)
    try:
        cursor = conn.cursor()
        table = get_table_name()
        cursor.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT 50")
        scored_news = cursor.fetchall()
        cursor.execute("SELECT * FROM summary_news ORDER BY id DESC LIMIT 50")
        summary_news = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()
    return render_template("database.html", scored_news=scored_news, summary_news=summary_news)
