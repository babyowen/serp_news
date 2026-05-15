# -*- coding: utf-8 -*-
# =========================================
# 新闻采集过滤主程序
# 主要功能：从四大新闻API采集新闻，过滤昨天新闻，去重、黑名单过滤，保存结果
# =========================================
import os
import sys
import json
import argparse
from datetime import datetime, timedelta
import re
from dateutil import parser
from news_fetcher import fetch_serpapi_google_news, fetch_serpapi_baidu_news, fetch_serpapi_bing_news, fetch_serpapi_duckduckgo_news
from config import DEFAULT_KEYWORDS, SEARCH_KEYWORDS, blacklist_keywords
from error_handler import (
    setup_global_exception_handler,
    with_error_handling,
    log_script_start,
    log_script_complete,
    ErrorHandler
)

# 设置全局异常处理器
setup_global_exception_handler()

# 判断Baidu News返回的date字段是否为昨天
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

# 解析Baidu News的date字段为具体日期字符串
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

# 解析Google News的date字段为具体日期字符串
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

# 判断Google News返回的date字段是否为昨天
def is_google_news_yesterday(date_str):
    """
    判断Google News返回的date字段是否为昨天。
    """
    parsed = parse_google_news_date(date_str)
    if not parsed:
        return False
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    return parsed == yesterday

# 判断Bing News返回的date字段是否为昨天
def is_bing_news_yesterday(date_str):
    if not date_str:
        return False
    now = datetime.now()
    yesterday = (now - timedelta(days=1)).date()
    
    # 处理英文格式 "Xh" (小时)
    match = re.match(r"(\d+)h$", date_str)
    if match:
        hours_ago = int(match.group(1))
        news_time = now - timedelta(hours=hours_ago)
        # 24小时内的都认为是有效的
        return hours_ago <= 24
    
    # 处理英文格式 "Xm" (分钟)
    match = re.match(r"(\d+)m$", date_str)
    if match:
        minutes_ago = int(match.group(1))
        news_time = now - timedelta(minutes=minutes_ago)
        # 24小时内的都认为是有效的
        return minutes_ago <= 1440  # 24*60分钟
    
    # 处理英文格式 "Xd" (天)
    match = re.match(r"(\d+)d$", date_str)
    if match:
        days_ago = int(match.group(1))
        # 只要1天内的都认为是有效的
        return days_ago <= 1
    
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

# 获取输出文件路径
def get_output_path(fetch_date, basename):
    return os.path.join("output", fetch_date, basename)

# 判断DuckDuckGo News返回的date字段是否为昨天
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

# 解析DuckDuckGo News的date字段为具体日期字符串
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

# 主流程入口，采集四大新闻API，按日期过滤、去重、黑名单过滤、保存结果并写日志
@with_error_handling("fetch_and_filter.py", "main")
def main():
    """主函数：处理新闻采集和过滤任务"""
    # 记录脚本开始
    log_script_start("fetch_and_filter.py", sys.argv[1:])
    
    error_handler = ErrorHandler()
    success = True
    
    try:
        # 参数解析：第一个参数为关键词，第二个为日期，新增 --output、--main_keyword、--search_keyword
        parser = argparse.ArgumentParser()
        parser.add_argument('keyword', nargs='?', default=None)
        parser.add_argument('fetch_date', nargs='?', default=None)
        parser.add_argument('--output', type=str, default=None, help='指定输出文件路径')
        parser.add_argument('--main_keyword', type=str, default=None, help='主关键词')
        parser.add_argument('--search_keyword', type=str, default=None, help='搜索用关键词')
        args = parser.parse_args()

        keyword = args.keyword
        fetch_date = args.fetch_date
        output_path = args.output
        main_keyword = args.main_keyword
        search_keyword = args.search_keyword

        if not fetch_date or fetch_date.lower() == 'none':
            fetch_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        if not keyword:
            print("[ERROR] 必须指定搜索用关键词")
            return False
        os.makedirs(os.path.join("output", fetch_date), exist_ok=True)
        log_lines = []
        run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_lines.append(f"=====[时间] 本次运行时间: {run_time}=====")
        
        # 采集四大新闻API
        print("[INFO] 正在采集 Google News ...")
        google_data = fetch_serpapi_google_news(keyword)
        print("[INFO] 正在采集 Baidu News ...")
        baidu_data = fetch_serpapi_baidu_news(keyword)
        print("[INFO] 正在采集 Bing News ...")
        bing_data = fetch_serpapi_bing_news(keyword)
        print("[INFO] 正在采集 DuckDuckGo News ...")
        duck_data = fetch_serpapi_duckduckgo_news(keyword)

        # 处理各个搜索引擎的数据
        baidu_news = []
        for item in baidu_data.get('organic_results', []):
            date_str = item.get('date', '')
            if is_baidu_news_yesterday(date_str):
                item = item.copy()
                item['fetchdate'] = fetch_date
                item['sourceapi'] = 'serp_baidunews'
                baidu_news.append(item)

        bing_news = []
        for item in bing_data.get('organic_results', []):
            date_str = item.get('date', '')
            if is_bing_news_yesterday(date_str):
                item = item.copy()
                item['fetchdate'] = fetch_date
                item['sourceapi'] = 'serp_bingnews'
                bing_news.append(item)

        duck_news = []
        for item in duck_data.get('news_results', []):
            date_str = item.get('date', '')
            if is_duckduckgo_news_yesterday(date_str):
                item = item.copy()
                item['fetchdate'] = fetch_date
                item['sourceapi'] = 'serp_duckduckgo_news'
                duck_news.append(item)

        google_news = []
        for item in google_data.get('news_results', []):
            date_str = item.get('date', '')
            if is_google_news_yesterday(date_str):
                item = item.copy()
                # 保留原始date
                item['fetchdate'] = fetch_date
                item['sourceapi'] = 'serp_googlenews'
                google_news.append(item)

        # 统一处理 source 字段为字符串
        def normalize_source(item):
            source = item.get('source')
            if isinstance(source, dict):
                return source.get('name', '')
            elif isinstance(source, str):
                return source
            else:
                return ''

        for news_list in [baidu_news, google_news, bing_news, duck_news]:
            for item in news_list:
                item['source'] = normalize_source(item)

        # 合并：使用所有搜索引擎的结果
        all_news = baidu_news + google_news + bing_news + duck_news

        # 去重（按title+link）
        unique = {}
        for item in all_news:
            key = (item.get('title', '').strip(), item.get('link', '').strip())
            if key not in unique:
                unique[key] = item
        deduped_news = list(unique.values())

        # 过滤黑名单
        filtered_news = []
        for item in deduped_news:
            title = item.get('title', '')
            if not any(bad in title for bad in blacklist_keywords):
                filtered_news.append(item)

        # 自动推断主关键词（如果未传--main_keyword）
        if not main_keyword:
            for mk, sk_list in SEARCH_KEYWORDS.items():
                if keyword in sk_list:
                    main_keyword = mk
                    break
            else:
                main_keyword = keyword  # fallback

        # 写入
        if output_path:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(filtered_news, f, ensure_ascii=False, indent=2)
            print(f"[INFO] 已保存 {len(filtered_news)} 条新闻到 {output_path}")
            # 统一风格写日志
            log_path = os.path.join("output", "run_log.txt")
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log = (
                f"\n[{now}]\n"
                f"执行程序: 新闻采集\n"
                f"[关键词] {main_keyword} / {search_keyword or keyword}\n"
                f"[来源] Google:{len(google_news)} Baidu:{len(baidu_news)} Bing:{len(bing_news)} DDG:{len(duck_news)}\n"
                f"[保存] 去重后: {len(filtered_news)}条\n"
                f"==============================\n"
            )
            with open(log_path, "a", encoding="utf-8") as logf:
                logf.write(log)
        else:
            print(f"[INFO] 共采集 {len(filtered_news)} 条新闻（未指定输出文件，不保存）")

        # 记录脚本完成
        if output_path and 'filtered_news' in locals():
            status_msg = f"采集完成，保存{len(filtered_news)}条新闻到{output_path}"
        else:
            status_msg = "采集完成"
        log_script_complete("fetch_and_filter.py", success=success, message=status_msg)
        return success
        
    except Exception as e:
        error_msg = f"fetch_and_filter.py 执行过程中发生异常: {str(e)}"
        print(f"[ERROR] {error_msg}")
        error_handler.log_error(
            error_type="MAIN_FUNCTION_ERROR",
            error_msg=error_msg,
            script_name="fetch_and_filter.py",
            keyword=keyword if 'keyword' in locals() else None
        )
        success = False
        log_script_complete("fetch_and_filter.py", success=success, message=error_msg)
        return success

# 命令行入口
if __name__ == "__main__":
    main()