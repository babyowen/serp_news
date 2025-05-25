from flask import Flask, render_template, send_from_directory, request
import os
import json

app = Flask(__name__)

OUTPUT_DIR = 'output'

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
    # 只展示yesterday_开头的json文件
    files = [f for f in os.listdir(date_dir) if f.startswith('yesterday_') and f.endswith('.json')]
    # 提取所有关键词
    keywords = set()
    file_map = {}  # keyword -> [file, ...]
    for fname in files:
        parts = fname.split('_')
        if len(parts) >= 3:
            keyword = parts[-2]
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
                news_data.append({
                    'file': fname,
                    'items': items
                })
        except Exception as e:
            news_data.append({'file': fname, 'items': [], 'error': str(e)})
    return render_template('date.html', date=date, keyword=keyword, news_data=news_data, keywords=keywords)

@app.route('/output/<path:filename>')
def download_file(filename):
    # 允许下载原始json
    return send_from_directory(OUTPUT_DIR, filename)

if __name__ == '__main__':
    app.run(debug=True) 