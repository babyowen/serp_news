import sys
import json
import os
from datetime import datetime, timedelta
from news_fetcher import fetch_serpapi_baidu_news, fetch_serpapi_google_news, fetch_baidu_news_web, fetch_serpapi_bing_news
from main import is_baidu_news_yesterday, parse_baidu_news_date, is_google_news_yesterday, parse_google_news_date
import re

# 用法: python test_fetcher.py serp_baidunews|serp_googlenews|baidu_news_web|serp_bingnews 关键词

def save_json(data, filename):
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"已保存到 {filename}")

def get_output_path(fetch_date, basename):
    return os.path.join("output", fetch_date, basename)

def test_serp_baidunews(keyword):
    fetch_date = datetime.now().strftime("%Y-%m-%d")
    data = fetch_serpapi_baidu_news(keyword)
    print(f"抓取到 {len(data.get('organic_results', []))} 条 serp_baidunews 新闻")
    raw_file = get_output_path(fetch_date, f"raw_serp_baidunews_{keyword}_{fetch_date}.json")
    save_json(data, raw_file)
    yesterday_news = []
    for item in data.get("organic_results", []):
        date_str = item.get('date', '')
        if is_baidu_news_yesterday(date_str):
            item = item.copy()
            item['fetch_date'] = fetch_date
            item['parsed_date'] = parse_baidu_news_date(date_str)
            item['keyword'] = keyword
            yesterday_news.append(item)
    print(f"昨天新闻数量: {len(yesterday_news)}")
    out_file = get_output_path(fetch_date, f"yesterday_serp_baidunews_{keyword}_{fetch_date}.json")
    existing = []
    unique_set = set()
    if os.path.exists(out_file):
        with open(out_file, "r", encoding="utf-8") as f:
            try:
                existing = json.load(f)
                for e in existing:
                    unique_set.add((e.get('title', ''), e.get('link', '')))
            except Exception:
                existing = []
    new_items = []
    for item in yesterday_news:
        key = (item.get('title', ''), item.get('link', ''))
        if key not in unique_set:
            new_items.append(item)
            unique_set.add(key)
    all_items = existing + new_items
    save_json(all_items, out_file)
    print(f"已写入去重后昨天新闻到 {out_file}，总数: {len(all_items)}，本次新增: {len(new_items)}")

def test_serp_googlenews(keyword):
    fetch_date = datetime.now().strftime("%Y-%m-%d")
    data = fetch_serpapi_google_news(keyword)
    print(f"抓取到 {len(data.get('news_results', []))} 条 serp_googlenews 新闻")
    raw_file = get_output_path(fetch_date, f"raw_serp_googlenews_{keyword}_{fetch_date}.json")
    save_json(data, raw_file)
    yesterday_news = []
    for item in data.get("news_results", []):
        date_str = item.get('date', '')
        if is_google_news_yesterday(date_str):
            item = item.copy()
            item['fetch_date'] = fetch_date
            item['parsed_date'] = parse_google_news_date(date_str)
            item['keyword'] = keyword
            yesterday_news.append(item)
    print(f"昨天新闻数量: {len(yesterday_news)}")
    out_file = get_output_path(fetch_date, f"yesterday_serp_googlenews_{keyword}_{fetch_date}.json")
    existing = []
    unique_set = set()
    if os.path.exists(out_file):
        with open(out_file, "r", encoding="utf-8") as f:
            try:
                existing = json.load(f)
                for e in existing:
                    unique_set.add((e.get('title', ''), e.get('link', '')))
            except Exception:
                existing = []
    new_items = []
    for item in yesterday_news:
        key = (item.get('title', ''), item.get('link', ''))
        if key not in unique_set:
            new_items.append(item)
            unique_set.add(key)
    all_items = existing + new_items
    save_json(all_items, out_file)
    print(f"已写入去重后昨天新闻到 {out_file}，总数: {len(all_items)}，本次新增: {len(new_items)}")

def test_baidu_news_web(keyword):
    fetch_date = datetime.now().strftime("%Y-%m-%d")
    data = fetch_baidu_news_web(keyword)
    print(f"抓取到 {len(data)} 条 baidu_news_web 新闻")
    raw_file = get_output_path(fetch_date, f"raw_baidu_news_web_{keyword}_{fetch_date}.json")
    save_json(data, raw_file)
    yesterday_news = []
    for item in data:
        date_str = item.get('date', '')
        if is_baidu_news_yesterday(date_str):
            item = item.copy()
            item['fetch_date'] = fetch_date
            item['parsed_date'] = parse_baidu_news_date(date_str)
            item['keyword'] = keyword
            yesterday_news.append(item)
    print(f"昨天新闻数量: {len(yesterday_news)}")
    out_file = get_output_path(fetch_date, f"yesterday_baidu_news_web_{keyword}_{fetch_date}.json")
    existing = []
    unique_set = set()
    if os.path.exists(out_file):
        with open(out_file, "r", encoding="utf-8") as f:
            try:
                existing = json.load(f)
                for e in existing:
                    unique_set.add((e.get('title', ''), e.get('link', '')))
            except Exception:
                existing = []
    new_items = []
    for item in yesterday_news:
        key = (item.get('title', ''), item.get('link', ''))
        if key not in unique_set:
            new_items.append(item)
            unique_set.add(key)
    all_items = existing + new_items
    save_json(all_items, out_file)
    print(f"已写入去重后昨天新闻到 {out_file}，总数: {len(all_items)}，本次新增: {len(new_items)}")

def is_bing_news_yesterday(date_str):
    if not date_str:
        return False
    now = datetime.now()
    yesterday = (now - timedelta(days=1)).date()
    # 处理"X 小時"
    match = re.match(r"(\d+)\s*小時", date_str)
    if match:
        hours_ago = int(match.group(1))
        news_time = now - timedelta(hours=hours_ago)
        return news_time.date() == yesterday
    # 处理"X 分鐘"
    match = re.match(r"(\d+)\s*分鐘", date_str)
    if match:
        minutes_ago = int(match.group(1))
        news_time = now - timedelta(minutes=minutes_ago)
        return news_time.date() == yesterday
    # 处理"X 天"
    match = re.match(r"(\d+)\s*天", date_str)
    if match:
        days_ago = int(match.group(1))
        news_time = now - timedelta(days=days_ago)
        return news_time.date() == yesterday
    # 兼容其它格式
    return False

def test_serp_bingnews(keyword):
    fetch_date = datetime.now().strftime("%Y-%m-%d")
    data = fetch_serpapi_bing_news(keyword)
    print(f"抓取到 {len(data.get('organic_results', []))} 条 serp_bingnews 新闻")
    raw_file = get_output_path(fetch_date, f"raw_serp_bingnews_{keyword}_{fetch_date}.json")
    save_json(data, raw_file)
    yesterday_news = []
    for item in data.get("organic_results", []):
        date_str = item.get('date', '')
        if is_bing_news_yesterday(date_str):
            item = item.copy()
            item['fetch_date'] = fetch_date
            item['parsed_date'] = parse_google_news_date(date_str)
            item['keyword'] = keyword
            yesterday_news.append(item)
    print(f"昨天新闻数量: {len(yesterday_news)}")
    out_file = get_output_path(fetch_date, f"yesterday_serp_bingnews_{keyword}_{fetch_date}.json")
    existing = []
    unique_set = set()
    if os.path.exists(out_file):
        with open(out_file, "r", encoding="utf-8") as f:
            try:
                existing = json.load(f)
                for e in existing:
                    unique_set.add((e.get('title', ''), e.get('link', '')))
            except Exception:
                existing = []
    new_items = []
    for item in yesterday_news:
        key = (item.get('title', ''), item.get('link', ''))
        if key not in unique_set:
            new_items.append(item)
            unique_set.add(key)
    all_items = existing + new_items
    save_json(all_items, out_file)
    print(f"已写入去重后昨天新闻到 {out_file}，总数: {len(all_items)}，本次新增: {len(new_items)}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python test_fetcher.py serp_baidunews|serp_googlenews|baidu_news_web|serp_bingnews 关键词")
        sys.exit(1)
    source = sys.argv[1].lower()
    keyword = sys.argv[2]
    if source == "serp_baidunews":
        test_serp_baidunews(keyword)
    elif source == "serp_googlenews":
        test_serp_googlenews(keyword)
    elif source == "baidu_news_web":
        test_baidu_news_web(keyword)
    elif source == "serp_bingnews":
        test_serp_bingnews(keyword)
    else:
        print("暂不支持该新闻源。支持: serp_baidunews, serp_googlenews, baidu_news_web, serp_bingnews")
        sys.exit(1) 