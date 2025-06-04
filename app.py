from flask import Flask, render_template, send_from_directory, request
import os
import json
import pymysql
from dotenv import load_dotenv

app = Flask(__name__)

OUTPUT_DIR = 'output'

# 加载.env文件
load_dotenv()
import os as _os

MYSQL_HOST = _os.getenv('MYSQL_HOST')
MYSQL_PORT = int(_os.getenv('MYSQL_PORT', 3306))
MYSQL_USER = _os.getenv('MYSQL_USER')
MYSQL_PASSWORD = _os.getenv('MYSQL_PASSWORD')
MYSQL_DB = _os.getenv('MYSQL_DB')

@app.route('/')
def index():
    # 获取所有日期文件夹
    dates = [d for d in os.listdir(OUTPUT_DIR) if os.path.isdir(os.path.join(OUTPUT_DIR, d))]
    dates.sort(reverse=True)
    return render_template('index.html', dates=dates)

@app.route('/date/<date>')
def show_date(date):
    date_dir = os.path.join(OUTPUT_DIR, date)
    if not os.path.exists(date_dir):
        return f"日期 {date} 不存在", 404
    # 展示所有 *_关键词.json 文件（新合并格式）
    files = [f for f in os.listdir(date_dir) if f.endswith('.json') and '_' in f and not f.startswith('raw_')]
    # 提取所有关键词
    keywords = set()
    file_map = {}  # keyword -> [file, ...]
    for fname in files:
        parts = fname.split('_')
        if len(parts) >= 2:
            keyword = parts[-1].replace('.json', '')
            keywords.add(keyword)
            file_map.setdefault(keyword, []).append(fname)
    keywords = sorted(keywords)
    # 获取当前选择的关键词
    keyword = request.args.get('keyword')
    if not keyword:
        # 只渲染关键词选择页面
        return render_template('keyword_select.html', date=date, keywords=keywords)
    # 展示该关键词下所有新闻文件
    news_data = []
    for fname in file_map.get(keyword, []):
        path = os.path.join(date_dir, fname)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                items = json.load(f)
                if isinstance(items, dict):
                    items = [items]
                # 只保留新格式字段
                filtered_items = []
                for item in items:
                    filtered_items.append({
                        'title': item.get('title', ''),
                        'link': item.get('link', ''),
                        'source': item.get('source', ''),
                        'date': item.get('date', ''),
                        'fetchdate': item.get('fetchdate', ''),
                        'sourceapi': item.get('sourceapi', ''),
                        'thumbnail': item.get('thumbnail', None),
                        'keyword': item.get('keyword', '')
                    })
                news_data.append({
                    'file': fname,
                    'items': filtered_items
                })
        except Exception as e:
            news_data.append({'file': fname, 'items': [], 'error': str(e)})
    return render_template('date.html', date=date, keyword=keyword, news_data=news_data, keywords=keywords)

@app.route('/output/<path:filename>')
def download_file(filename):
    # 允许下载原始json
    return send_from_directory(OUTPUT_DIR, filename)

@app.route('/database')
def show_database():
    # 连接数据库
    conn = pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        charset='utf8mb4'
    )
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    # 查询scored_news
    cursor.execute("SELECT * FROM scored_news ORDER BY id DESC LIMIT 50")
    scored_news = cursor.fetchall()
    # 查询summary_news
    cursor.execute("SELECT * FROM summary_news ORDER BY id DESC LIMIT 50")
    summary_news = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('database.html', scored_news=scored_news, summary_news=summary_news)

if __name__ == '__main__':
    app.run(debug=True) 