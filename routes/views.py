"""公开路由 — 首页综合看板、新闻浏览、数据库查看"""
import os
import json
import datetime
from flask import render_template, send_from_directory, request, Blueprint
from db_utils import get_connection, get_table_name
from config import DEFAULT_KEYWORDS

views_bp = Blueprint("views", __name__)
OUTPUT_DIR = "output"


@views_bp.route("/")
def index():
    # 日期参数：默认昨天到昨天（单日）
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    date_from = request.args.get("date_from", yesterday)
    date_to = request.args.get("date_to", yesterday)
    score_min = request.args.get("score_min", "")
    score_max = request.args.get("score_max", "")
    source = request.args.get("source", "")
    sourceapi = request.args.get("sourceapi", "")

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
    conditions.append(f"keyword IN ({placeholders})")
    params.extend(DEFAULT_KEYWORDS)

    where = " AND ".join(conditions)

    conn = get_connection(dict_cursor=True)
    try:
        cursor = conn.cursor()

        # 统计
        table = get_table_name()
        cursor.execute(
            f"SELECT keyword, COUNT(*) as cnt FROM {table} WHERE {where} GROUP BY keyword ORDER BY cnt DESC",
            params
        )
        kw_stats = cursor.fetchall()
        total = sum(r["cnt"] for r in kw_stats)

        # 数据
        cursor.execute(
            f"SELECT keyword, title, link, source, fetchdate, sourceapi, score, short_summary "
            f"FROM {table} WHERE {where} ORDER BY fetchdate DESC, keyword, score DESC",
            params
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

    # 按关键词分组
    grouped = {}
    for item in news_list:
        kw = item["keyword"] or "未分类"
        grouped.setdefault(kw, []).append(item)

    return render_template("index.html",
                           date_from=date_from, date_to=date_to,
                           score_min=score_min, score_max=score_max,
                           source=source, sourceapi=sourceapi,
                           total=total, kw_stats=kw_stats,
                           grouped=grouped, api_options=api_options)


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
