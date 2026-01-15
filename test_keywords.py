# -*- coding: utf-8 -*-
# =========================================
# 关键词新闻采集测试脚本
# 主要功能：测试指定关键词能否搜索到足够的新闻
# =========================================

import sys
from datetime import datetime, timedelta
from news_fetcher import (
    fetch_serpapi_google_news,
    fetch_serpapi_baidu_news,
    fetch_serpapi_bing_news,
    fetch_serpapi_duckduckgo_news
)

def is_baidu_news_yesterday(date_str):
    """判断百度新闻是否为昨天"""
    if not date_str:
        return False
    now = datetime.now()
    yesterday = (now - timedelta(days=1)).date()
    if "昨天" in date_str:
        return True
    if "前天" in date_str:
        return False
    import re
    match = re.match(r"(\d+)小时前", date_str)
    if match:
        hours_ago = int(match.group(1))
        news_time = now - timedelta(hours=hours_ago)
        return news_time.date() == yesterday
    match = re.match(r"(\d+)分钟前", date_str)
    if match:
        minutes_ago = int(match.group(1))
        news_time = now - timedelta(minutes=minutes_ago)
        return news_time.date() == yesterday
    try:
        date_part = ''.join([c for c in date_str if c.isdigit() or c == '-'])
        if len(date_part) == 8:
            date_part = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:]}"
        if len(date_part) == 10:
            date_part = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:]}"
            news_date = datetime.strptime(date_part, "%Y-%m-%d").date()
            return news_date == yesterday
    except Exception:
        pass
    return False

def is_google_news_yesterday(date_str):
    """判断Google新闻是否为昨天"""
    if not date_str:
        return False
    now = datetime.now()
    if "昨天" in date_str:
        return True
    import re
    match = re.match(r"(\d+)小时前", date_str)
    if match:
        hours_ago = int(match.group(1))
        news_time = now - timedelta(hours=hours_ago)
        return news_time.date() == (now - timedelta(days=1)).date()
    match = re.match(r"(\d+)分钟前", date_str)
    if match:
        minutes_ago = int(match.group(1))
        news_time = now - timedelta(minutes=minutes_ago)
        return news_time.date() == (now - timedelta(days=1)).date()
    from dateutil import parser
    try:
        dt = parser.parse(date_str, fuzzy=True)
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        return dt.strftime("%Y-%m-%d") == yesterday
    except Exception:
        pass
    return False

def is_bing_news_yesterday(date_str):
    """判断Bing新闻是否为昨天"""
    if not date_str:
        return False
    now = datetime.now()
    import re
    match = re.match(r"(\d+)h$", date_str)
    if match:
        hours_ago = int(match.group(1))
        return hours_ago <= 24
    match = re.match(r"(\d+)m$", date_str)
    if match:
        minutes_ago = int(match.group(1))
        return minutes_ago <= 1440
    match = re.match(r"(\d+)d$", date_str)
    if match:
        days_ago = int(match.group(1))
        return days_ago <= 1
    match = re.match(r"(\d+)\s*小時", date_str)
    if match:
        hours_ago = int(match.group(1))
        news_time = now - timedelta(hours=hours_ago)
        return news_time.date() == (now - timedelta(days=1)).date()
    match = re.match(r"(\d+)\s*天", date_str)
    if match:
        days_ago = int(match.group(1))
        news_time = now - timedelta(days=days_ago)
        return news_time.date() == (now - timedelta(days=1)).date()
    return False

def is_duckduckgo_news_yesterday(date_str):
    """判断DuckDuckGo新闻是否为昨天"""
    if not date_str:
        return False
    now = datetime.now()
    import re
    if date_str.strip() == '1 day ago':
        return True
    m = re.match(r"(\d+) days ago", date_str)
    if m:
        days = int(m.group(1))
        return days == 1
    m = re.match(r"(\d+) hours ago", date_str)
    if m:
        hours = int(m.group(1))
        news_time = now - timedelta(hours=hours)
        return news_time.date() == (now - timedelta(days=1)).date()
    m = re.match(r"(\d+) minutes ago", date_str)
    if m:
        minutes = int(m.group(1))
        news_time = now - timedelta(minutes=minutes)
        return news_time.date() == (now - timedelta(days=1)).date()
    return False

def test_keyword(keyword, test_date=None):
    """
    测试指定关键词的新闻采集情况

    Args:
        keyword: 测试关键词
        test_date: 测试日期（YYYY-MM-DD格式），默认为昨天
    """
    if test_date is None:
        test_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    print("=" * 60)
    print("新闻采集测试报告")
    print("=" * 60)
    print(f"测试关键词: {keyword}")
    print(f"测试日期: {test_date}")
    print()

    # 初始化统计
    stats = {
        "Google News": 0,
        "百度新闻": 0,
        "Bing News": 0,
        "DuckDuckGo News": 0
    }

    all_titles = []
    seen_titles = set()

    # 1. 测试Google News
    print("正在采集 Google News...")
    try:
        google_data = fetch_serpapi_google_news(keyword)
        google_results = google_data.get("news_results", [])
        for item in google_results:
            if is_google_news_yesterday(item.get("date", "")):
                title = item.get("title", "")
                if title and title not in seen_titles:
                    stats["Google News"] += 1
                    seen_titles.add(title)
                    all_titles.append(title)
    except Exception as e:
        print(f"  [错误] {e}")

    # 2. 测试百度新闻
    print("正在采集 百度新闻...")
    try:
        baidu_data = fetch_serpapi_baidu_news(keyword)
        baidu_results = baidu_data.get("organic_results", [])
        for item in baidu_results:
            if is_baidu_news_yesterday(item.get("date", "")):
                title = item.get("title", "")
                if title and title not in seen_titles:
                    stats["百度新闻"] += 1
                    seen_titles.add(title)
                    all_titles.append(title)
    except Exception as e:
        print(f"  [错误] {e}")

    # 3. 测试Bing News
    print("正在采集 Bing News...")
    try:
        bing_data = fetch_serpapi_bing_news(keyword)
        bing_results = bing_data.get("organic_results", [])
        for item in bing_results:
            if is_bing_news_yesterday(item.get("date", "")):
                title = item.get("title", "")
                if title and title not in seen_titles:
                    stats["Bing News"] += 1
                    seen_titles.add(title)
                    all_titles.append(title)
    except Exception as e:
        print(f"  [错误] {e}")

    # 4. 测试DuckDuckGo News
    print("正在采集 DuckDuckGo News...")
    try:
        duck_data = fetch_serpapi_duckduckgo_news(keyword)
        duck_results = duck_data.get("news_results", [])
        for item in duck_results:
            if is_duckduckgo_news_yesterday(item.get("date", "")):
                title = item.get("title", "")
                if title and title not in seen_titles:
                    stats["DuckDuckGo News"] += 1
                    seen_titles.add(title)
                    all_titles.append(title)
    except Exception as e:
        print(f"  [错误] {e}")

    # 输出统计结果
    print()
    print("采集统计:")
    print(f"- Google News: {stats['Google News']} 条")
    print(f"- 百度新闻: {stats['百度新闻']} 条")
    print(f"- Bing News: {stats['Bing News']} 条")
    print(f"- DuckDuckGo News: {stats['DuckDuckGo News']} 条")
    print(f"- 去重后总计: {len(all_titles)} 条")
    print()

    # 输出新闻标题列表
    print("=" * 60)
    print("新闻标题列表")
    print("=" * 60)
    for idx, title in enumerate(all_titles, 1):
        print(f"{idx}. {title}")

    print()
    print("=" * 60)
    print(f"测试完成！共找到 {len(all_titles)} 条不重复的昨天的新闻")
    print("=" * 60)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("使用方法: python test_keywords.py <关键词> [日期]")
        print("示例:")
        print("  python test_keywords.py '江苏省国资委'")
        print("  python test_keywords.py '潜在招标客户' '2025-01-09'")
        sys.exit(1)

    keyword = sys.argv[1]
    test_date = sys.argv[2] if len(sys.argv) > 2 else None

    test_keyword(keyword, test_date)
