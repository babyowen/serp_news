from news_fetcher import fetch_serpapi_baidu_news, fetch_serpapi_google_news, fetch_serpapi_bing_news
from config import DEFAULT_KEYWORDS
import json
from datetime import datetime, timedelta
import re
from dateutil import parser
import os

# 当前主程序使用的新闻源和API类型
NEWS_SOURCE = "SerpApi"
NEWS_API = "Baidu News API"

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

def main():
    try:
        keywords = list(DEFAULT_KEYWORDS)
    except Exception:
        keywords = [DEFAULT_KEYWORDS]
    fetch_date = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(os.path.join("output", fetch_date), exist_ok=True)
    log_lines = []
    for keyword in keywords:
        print(f"\n==== 关键词：{keyword} ====")
        all_yesterday_news = []
        count_baidu = 0
        count_google = 0
        count_bing = 0

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
                item = item.copy()
                item['fetch_date'] = fetch_date
                item['parsed_date'] = parse_baidu_news_date(date_str)
                item['keyword'] = keyword
                item['sourceapi'] = 'serp_baidunews'
                all_yesterday_news.append(item)
                count_baidu += 1
        log_lines.append(f"{fetch_date} {keyword} serp_baidunews: {count_baidu} 条")

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
                item = item.copy()
                item['fetch_date'] = fetch_date
                item['parsed_date'] = parse_google_news_date(date_str)
                item['keyword'] = keyword
                item['sourceapi'] = 'serp_googlenews'
                all_yesterday_news.append(item)
                count_google += 1
        log_lines.append(f"{fetch_date} {keyword} serp_googlenews: {count_google} 条")

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
                item = item.copy()
                item['fetch_date'] = fetch_date
                item['parsed_date'] = (datetime.strptime(fetch_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
                item['keyword'] = keyword
                item['sourceapi'] = 'serp_bingnews'
                all_yesterday_news.append(item)
                count_bing += 1
        log_lines.append(f"{fetch_date} {keyword} serp_bingnews: {count_bing} 条")

        # 合并去重：标题+链接唯一
        unique = {}
        for item in all_yesterday_news:
            key = (item.get('title', '').strip(), item.get('link', '').strip())
            if key not in unique:
                unique[key] = item
        deduped_news = list(unique.values())

        # 统计去重后每个API的数量
        api_counts = {}
        for item in deduped_news:
            api = item.get('sourceapi', 'unknown')
            api_counts[api] = api_counts.get(api, 0) + 1
        for api, count in api_counts.items():
            log_lines.append(f"{fetch_date} {keyword} deduped_{api}: {count} 条")

        deduped_filename = get_output_path(fetch_date, f"yesterday_{keyword}_{fetch_date}.json")
        with open(deduped_filename, "w", encoding="utf-8") as f:
            json.dump(deduped_news, f, ensure_ascii=False, indent=2)
        print(f"合并去重后昨天新闻已保存到 {deduped_filename}，数量：{len(deduped_news)}")
        log_lines.append(f"{fetch_date} {keyword} deduped_saved: {len(deduped_news)} 条")

    # 写入日志文件
    log_path = os.path.join("output", fetch_date, "run_log.txt")
    with open(log_path, "a", encoding="utf-8") as logf:
        for line in log_lines:
            logf.write(line + "\n")
    print(f"日志已写入 {log_path}")

if __name__ == "__main__":
    main() 