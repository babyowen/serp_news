"""公开路由 — 日期列表、新闻浏览、数据库查看"""
import os
import json
from flask import render_template, send_from_directory, request, Blueprint
from db_utils import get_connection

views_bp = Blueprint("views", __name__)
OUTPUT_DIR = "output"


@views_bp.route("/")
def index():
    dates = [d for d in os.listdir(OUTPUT_DIR) if os.path.isdir(os.path.join(OUTPUT_DIR, d))]
    dates.sort(reverse=True)
    return render_template("index.html", dates=dates)


@views_bp.route("/date/<date>")
def show_date(date):
    date_dir = os.path.join(OUTPUT_DIR, date)
    if not os.path.exists(date_dir):
        return f"日期 {date} 不存在", 404
    files = [f for f in os.listdir(date_dir) if f.endswith(".json") and "_" in f and not f.startswith("raw_")]
    keywords = set()
    file_map = {}
    for fname in files:
        parts = fname.split("_")
        if len(parts) >= 2:
            keyword = parts[-1].replace(".json", "")
            keywords.add(keyword)
            file_map.setdefault(keyword, []).append(fname)
    keywords = sorted(keywords)
    keyword = request.args.get("keyword")
    if not keyword:
        return render_template("keyword_select.html", date=date, keywords=keywords)
    news_data = []
    for fname in file_map.get(keyword, []):
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
        cursor.execute("SELECT * FROM scored_news ORDER BY id DESC LIMIT 50")
        scored_news = cursor.fetchall()
        cursor.execute("SELECT * FROM summary_news ORDER BY id DESC LIMIT 50")
        summary_news = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()
    return render_template("database.html", scored_news=scored_news, summary_news=summary_news)
