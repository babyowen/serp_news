from news_fetcher import fetch_serpapi_baidu_news, fetch_serpapi_google_news, fetch_serpapi_bing_news, fetch_serpapi_duckduckgo_news
from config import DEFAULT_KEYWORDS, blacklist_keywords
import json
from datetime import datetime, timedelta
import re
from dateutil import parser
import os
import sys

def is_baidu_news_yesterday(date_str):
    """
    判断Baidu News返回的date字段是否为昨天。
    支持：
    - 包含"昨天"
    - 具体日期等于昨天
    - "几小时前"/"几分钟前"，根据当前时间推算是否属于昨天
    """
    if not date_str:
        return False
    now = datetime.now()
    yesterday = (now - timedelta(days=1)).date()
    if "昨天" in date_str:
        return True
    if "前天" in date_str:
        return False
    # 处理"几小时前"
    match = re.match(r"(\d+)小时前", date_str)
    if match:
        hours_ago = int(match.group(1))
        news_time = now - timedelta(hours=hours_ago)
        return news_time.date() == yesterday
    # 处理"几分钟前"
    match = re.match(r"(\d+)分钟前", date_str)
    if match:
        minutes_ago = int(match.group(1))
        news_time = now - timedelta(minutes=minutes_ago)
        return news_time.date() == yesterday
    # 处理具体日期（如"2024-07-23 11:45"或"2024-07-23"）
    try:
        # 只保留数字和-，防止有"昨天18:47"这种混合格式
        date_part = ''.join([c for c in date_str if c.isdigit() or c == '-'])
        if len(date_part) == 8:  # 20240723
            date_part = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:]}"
        if len(date_part) == 10:
            date_part = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:]}"
            news_date = datetime.strptime(date_part, "%Y-%m-%d").date()
            return news_date == yesterday
    except Exception:
        pass
    # 其它情况（如"几天前"等）不算昨天
    return False

def parse_baidu_news_date(date_str):
    """
    将Baidu News的date字段解析为具体日期（YYYY-MM-DD），解析失败返回None。
    """
    if not date_str:
        return None
    now = datetime.now()
    if "昨天" in date_str:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    if "前天" in date_str:
        return (now - timedelta(days=2)).strftime("%Y-%m-%d")
    match = re.match(r"(\d+)小时前", date_str)
    if match:
        hours_ago = int(match.group(1))
        news_time = now - timedelta(hours=hours_ago)
        return news_time.strftime("%Y-%m-%d")
    match = re.match(r"(\d+)分钟前", date_str)
    if match:
        minutes_ago = int(match.group(1))
        news_time = now - timedelta(minutes=minutes_ago)
        return news_time.strftime("%Y-%m-%d")
    # 处理具体日期
    try:
        date_part = ''.join([c for c in date_str if c.isdigit() or c == '-'])
        if len(date_part) == 8:
            date_part = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:]}"
        if len(date_part) == 10:
            return date_part
    except Exception:
        pass
    return None

def parse_google_news_date(date_str):
    """
    尝试将Google News的date字段解析为具体日期（YYYY-MM-DD），解析失败返回None。
    支持"昨天"、"几小时前"、"几分钟前"、中文日期、标准日期、国际化日期（如05/24/2025, 11:21 PM, +0000 UTC）等。
    """
    if not date_str:
        return None
    now = datetime.now()
    # 处理"昨天"
    if "昨天" in date_str:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    # 处理"小时前"
    match = re.match(r"(\d+)小时前", date_str)
    if match:
        hours_ago = int(match.group(1))
        news_time = now - timedelta(hours=hours_ago)
        return news_time.strftime("%Y-%m-%d")
    # 处理"分钟前"
    match = re.match(r"(\d+)分钟前", date_str)
    if match:
        minutes_ago = int(match.group(1))
        news_time = now - timedelta(minutes=minutes_ago)
        return news_time.strftime("%Y-%m-%d")
    # 优先用dateutil解析
    try:
        dt = parser.parse(date_str, fuzzy=True)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    # 兜底：尝试处理中文日期
    try:
        date_part = re.sub(r"[年月日]", "-", date_str)
        date_part = re.sub(r"-+", "-", date_part).strip("-")
        if len(date_part) >= 10:
            date_part = date_part[:10]
            return date_part
    except Exception:
        pass
    return None

def is_google_news_yesterday(date_str):
    """
    判断Google News返回的date字段是否为昨天。
    """
    parsed = parse_google_news_date(date_str)
    if not parsed:
        return False
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    return parsed == yesterday

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

def get_output_path(fetch_date, basename):
    return os.path.join("output", fetch_date, basename)

def is_duckduckgo_news_yesterday(date_str):
    """
    判断DuckDuckGo News返回的date字段是否为昨天。
    支持：
    - '1 day ago'（昨天）
    - 'X days ago'（X=1为昨天）
    - 'X hours ago'（需判断当前时间是否属于昨天）
    - 'X minutes ago'（同上）
    """
    if not date_str:
        return False
    now = datetime.now()
    yesterday = (now - timedelta(days=1)).date()
    # 1 day ago
    if date_str.strip() == '1 day ago':
        return True
    # X days ago
    m = re.match(r"(\d+) days ago", date_str)
    if m:
        days = int(m.group(1))
        return days == 1
    # X hours ago
    m = re.match(r"(\d+) hours ago", date_str)
    if m:
        hours = int(m.group(1))
        news_time = now - timedelta(hours=hours)
        return news_time.date() == yesterday
    # X minutes ago
    m = re.match(r"(\d+) minutes ago", date_str)
    if m:
        minutes = int(m.group(1))
        news_time = now - timedelta(minutes=minutes)
        return news_time.date() == yesterday
    return False

def parse_duckduckgo_news_date(date_str):
    """
    将DuckDuckGo News的date字段解析为具体日期（YYYY-MM-DD），解析失败返回None。
    """
    if not date_str:
        return None
    now = datetime.now()
    if date_str.strip() == '1 day ago':
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    m = re.match(r"(\d+) days ago", date_str)
    if m:
        days = int(m.group(1))
        return (now - timedelta(days=days)).strftime("%Y-%m-%d")
    m = re.match(r"(\d+) hours ago", date_str)
    if m:
        hours = int(m.group(1))
        news_time = now - timedelta(hours=hours)
        return news_time.strftime("%Y-%m-%d")
    m = re.match(r"(\d+) minutes ago", date_str)
    if m:
        minutes = int(m.group(1))
        news_time = now - timedelta(minutes=minutes)
        return news_time.strftime("%Y-%m-%d")
    return None

def main():
    try:
        keywords = list(DEFAULT_KEYWORDS)
    except Exception:
        keywords = [DEFAULT_KEYWORDS]
    # 支持命令行参数指定日期
    if len(sys.argv) > 1:
        fetch_date = sys.argv[1]
    else:
        fetch_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    os.makedirs(os.path.join("output", fetch_date), exist_ok=True)
    log_lines = []
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_lines.append(f"=====🕒 本次运行时间: {run_time}=====")
    for keyword in keywords:
        log_lines.append(f"\n==============================")
        log_lines.append(f"🔑 关键词: {keyword}")
        all_yesterday_news = []
        count_baidu = 0
        count_google = 0
        count_bing = 0
        count_duck = 0

        # --- SerpApi Baidu News ---
        print("\n【SerpApi Baidu News API】")
        news_data_baidu = fetch_serpapi_baidu_news(keyword)
        raw_filename_baidu = get_output_path(fetch_date, f"raw_serp_baidunews_{keyword}_{fetch_date}.json")
        with open(raw_filename_baidu, "w", encoding="utf-8") as f:
            json.dump(news_data_baidu, f, ensure_ascii=False, indent=2)
        print(f"完整API返回内容已保存到 {raw_filename_baidu}")
        for item in news_data_baidu.get("organic_results", []):
            date_str = item.get('date', '')
            if is_baidu_news_yesterday(date_str):
                news_date = parse_baidu_news_date(date_str)
                filtered_item = {
                    'title': item.get('title', ''),
                    'link': item.get('link', ''),
                    'fetchdate': news_date,
                    'date': date_str,
                    'source': item.get('source', ''),
                    'sourceapi': 'serp_baidunews',
                    'keyword': keyword,
                    'thumbnail': item.get('thumbnail', None)
                }
                all_yesterday_news.append(filtered_item)
                count_baidu += 1
        log_lines.append(f"🌐 Baidu News: {count_baidu} 条")

        # --- SerpApi Google News ---
        print("\n【SerpApi Google News API】")
        news_data_google = fetch_serpapi_google_news(keyword)
        raw_filename_google = get_output_path(fetch_date, f"raw_serp_googlenews_{keyword}_{fetch_date}.json")
        with open(raw_filename_google, "w", encoding="utf-8") as f:
            json.dump(news_data_google, f, ensure_ascii=False, indent=2)
        print(f"完整API返回内容已保存到 {raw_filename_google}")
        for item in news_data_google.get("news_results", []):
            date_str = item.get('date', '')
            if is_google_news_yesterday(date_str):
                news_date = parse_google_news_date(date_str)
                filtered_item = {
                    'title': item.get('title', ''),
                    'link': item.get('link', ''),
                    'source': item.get('source', {}).get('name', '') if isinstance(item.get('source', {}), dict) else '',
                    'date': item.get('date', ''),
                    'fetchdate': news_date,
                    'sourceapi': 'serp_googlenews',
                    'thumbnail': item.get('thumbnail', None),
                    'keyword': keyword
                }
                all_yesterday_news.append(filtered_item)
                count_google += 1
        log_lines.append(f"🌐 Google News: {count_google} 条")

        # --- SerpApi Bing News ---
        print("\n【SerpApi Bing News API】")
        news_data_bing = fetch_serpapi_bing_news(keyword)
        raw_filename_bing = get_output_path(fetch_date, f"raw_serp_bingnews_{keyword}_{fetch_date}.json")
        with open(raw_filename_bing, "w", encoding="utf-8") as f:
            json.dump(news_data_bing, f, ensure_ascii=False, indent=2)
        print(f"完整API返回内容已保存到 {raw_filename_bing}")
        for item in news_data_bing.get("organic_results", []):
            date_str = item.get('date', '')
            if is_bing_news_yesterday(date_str):
                news_date = (datetime.strptime(fetch_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
                filtered_item = {
                    'title': item.get('title', ''),
                    'link': item.get('link', ''),
                    'source': item.get('source', ''),
                    'date': item.get('date', ''),
                    'fetchdate': news_date,
                    'sourceapi': 'serp_bingnews',
                    'thumbnail': item.get('thumbnail', None),
                    'keyword': keyword
                }
                all_yesterday_news.append(filtered_item)
                count_bing += 1
        log_lines.append(f"🌐 Bing News: {count_bing} 条")

        # --- SerpApi DuckDuckGo News ---
        print("\n【SerpApi DuckDuckGo News API】")
        news_data_duck = fetch_serpapi_duckduckgo_news(keyword)
        raw_filename_duck = get_output_path(fetch_date, f"raw_serp_duckduckgo_news_{keyword}_{fetch_date}.json")
        with open(raw_filename_duck, "w", encoding="utf-8") as f:
            json.dump(news_data_duck, f, ensure_ascii=False, indent=2)
        print(f"完整API返回内容已保存到 {raw_filename_duck}")
        for item in news_data_duck.get("news_results", []):
            date_str = item.get('date', '')
            if is_duckduckgo_news_yesterday(date_str):
                news_date = parse_duckduckgo_news_date(date_str)
                filtered_item = {
                    'title': item.get('title', ''),
                    'link': item.get('link', ''),
                    'source': item.get('source', ''),
                    'date': item.get('date', ''),
                    'fetchdate': news_date,
                    'sourceapi': 'serp_duckduckgo_news',
                    'thumbnail': item.get('thumbnail', None),
                    'keyword': keyword
                }
                all_yesterday_news.append(filtered_item)
                count_duck += 1
        log_lines.append(f"🌐 DuckDuckGo News: {count_duck} 条")

        # 合并去重：标题+链接唯一
        unique = {}
        for item in all_yesterday_news:
            # 黑名单过滤
            text_to_check = f"{item.get('source','')} {item.get('title','')} {item.get('link','')}"
            matched_kw = next((kw for kw in blacklist_keywords if kw.lower() in text_to_check.lower()), None)
            if matched_kw:
                log_lines.append(f"🚫 黑名单过滤: [{matched_kw}] | 标题: {item.get('title','')} | 来源: {item.get('source','')} | 链接: {item.get('link','')}")
                continue
            key = (item.get('title', '').strip(), item.get('link', '').strip())
            if key not in unique:
                unique[key] = item
        deduped_news = list(unique.values())

        # 剔除tv.cctv.com视频新闻，并记录日志
        filtered_news = []
        skipped_video_items = []
        for item in deduped_news:
            url = item.get('link')
            if url and 'tv.cctv.com' in url:
                skipped_video_items.append({'title': item.get('title', ''), 'link': url})
                continue
            filtered_news.append(item)
        if skipped_video_items:
            log_lines.append("📺 跳过仅含视频的新闻（tv.cctv.com）：")
            for item in skipped_video_items:
                log_lines.append(f"  - {item['title']} | {item['link']}")

        # 统计去重后每个API的数量
        api_counts = {}
        for item in filtered_news:
            api = item.get('sourceapi', 'unknown')
            api_counts[api] = api_counts.get(api, 0) + 1
        for api, count in api_counts.items():
            log_lines.append(f"✅ 去重后 {api}: {count} 条")
        log_lines.append(f"⭐️ 去重后总保存: {len(filtered_news)} 条")
        log_lines.append("")

        deduped_filename = get_output_path(fetch_date, f"{fetch_date}_{keyword}.json")
        with open(deduped_filename, "w", encoding="utf-8") as f:
            json.dump(filtered_news, f, ensure_ascii=False, indent=2)
        print(f"合并去重后昨天新闻已保存到 {deduped_filename}，数量：{len(filtered_news)}")

    # 写入全局统一日志
    log_path = os.path.join("output", "run_log.txt")
    with open(log_path, "a", encoding="utf-8") as logf:
        for line in log_lines:
            logf.write(line + "\n")
    print(f"日志已写入 {log_path}")

if __name__ == "__main__":
    main() 