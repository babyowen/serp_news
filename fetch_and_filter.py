# -*- coding: utf-8 -*-
# =========================================
# 新闻采集过滤主程序
# 主要功能：从四大新闻API采集新闻，过滤昨天新闻，去重、黑名单过滤，保存结果
# =========================================
import os
import sys
import json
import argparse
import time
from datetime import datetime, timedelta
import re
from news_dates import reference_time, absolute_time, is_relative, parse_search_date
from news_fetcher import fetch_serpapi_google_news, fetch_serpapi_baidu_news, fetch_serpapi_bing_news, fetch_serpapi_duckduckgo_news
from news_fetcher import has_serpapi_error
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

def _reference_now(reference_now=None):
    return reference_time(reference_now)


def _target_date(target_date, reference_now=None):
    if target_date is None:
        return (_reference_now(reference_now) - timedelta(days=1)).date()
    if isinstance(target_date, datetime):
        return target_date.date()
    if hasattr(target_date, "year") and not isinstance(target_date, str):
        return target_date
    return datetime.strptime(target_date, "%Y-%m-%d").date()


def _is_relative_date(date_str):
    return is_relative(date_str)


def _is_on_target_date(date_str, target_date, parse_func, reference_now=None):
    now = _reference_now(reference_now)
    target = _target_date(target_date, now)
    if is_relative(date_str) and target != (now - timedelta(days=1)).date():
        return False
    return parse_func(date_str, reference_now=now) == target.isoformat()


def parse_baidu_news_date(date_str, reference_now=None):
    return parse_search_date(date_str, reference_now)


def is_baidu_news_on_date(date_str, target_date, reference_now=None):
    return _is_on_target_date(date_str, target_date, parse_baidu_news_date, reference_now)


def is_baidu_news_yesterday(date_str):
    return is_baidu_news_on_date(date_str, None)


def parse_google_news_date(date_str, reference_now=None):
    return parse_search_date(date_str, reference_now)


def is_google_news_on_date(date_str, target_date, reference_now=None):
    return _is_on_target_date(date_str, target_date, parse_google_news_date, reference_now)


def is_google_news_yesterday(date_str):
    return is_google_news_on_date(date_str, None)


def parse_bing_news_date(date_str, reference_now=None):
    return parse_search_date(date_str, reference_now)


def is_bing_news_on_date(date_str, target_date, reference_now=None):
    return _is_on_target_date(date_str, target_date, parse_bing_news_date, reference_now)


def is_bing_news_yesterday(date_str):
    return is_bing_news_on_date(date_str, None)


def parse_duckduckgo_news_date(date_str, reference_now=None):
    return parse_search_date(date_str, reference_now)


def is_duckduckgo_news_on_date(date_str, target_date, reference_now=None):
    return _is_on_target_date(date_str, target_date, parse_duckduckgo_news_date, reference_now)


def is_duckduckgo_news_yesterday(date_str):
    return is_duckduckgo_news_on_date(date_str, None)


def get_output_path(fetch_date, basename):
    return os.path.join("output", fetch_date, basename)


def filter_search_rows(rows, sourceapi, fetch_date, request_time):
    result = []
    for item in rows:
        # An API cache can be older than this process. Prefer its creation time.
        observed = absolute_time(item.get('search_fetched_at')) or request_time
        field = next((key for key in ('published_at', 'iso_date', 'date') if item.get(key)), 'date')
        raw = item.get(field, '')
        parsed = parse_search_date(raw, observed)
        if parsed != fetch_date:
            continue
        result.append({**item, 'search_date_raw':raw, 'search_date_field':field,
                       'search_fetched_at':observed.isoformat(), 'date':parsed,
                       'fetchdate':fetch_date, 'sourceapi':sourceapi})
    return result


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
        # Fail before making API calls when a historical backfill date is invalid.
        datetime.strptime(fetch_date, "%Y-%m-%d")
        if not keyword:
            print("[ERROR] 必须指定搜索用关键词")
            return False
        os.makedirs(os.path.join("output", fetch_date), exist_ok=True)
        log_lines = []
        run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_lines.append(f"=====[时间] 本次运行时间: {run_time}=====")
        
        # 采集四大新闻API
        print("[INFO] 正在采集 Google News ...")
        google_requested_at = reference_time()
        google_data = fetch_serpapi_google_news(keyword, fetch_date=fetch_date)
        print("[INFO] 正在采集 Baidu News ...")
        baidu_requested_at = reference_time()
        baidu_data = fetch_serpapi_baidu_news(keyword)
        print("[INFO] 正在采集 Bing News ...")
        bing_requested_at = reference_time()
        bing_data = fetch_serpapi_bing_news(keyword, fetch_date=fetch_date)
        print("[INFO] 正在采集 DuckDuckGo News ...")
        duck_requested_at = reference_time()
        duck_data = fetch_serpapi_duckduckgo_news(keyword, fetch_date=fetch_date)

        if main_keyword == "江苏机关事务" and any(
                data.get("_fetch_failed") or has_serpapi_error(data)
                for data in (google_data, baidu_data, bing_data, duck_data)):
            success = False
            print("[ERROR] 江苏机关事务有搜索引擎失败；保留部分结果，批次未完成")

        # Use one captured clock per result throughout filtering and later replay.
        baidu_news = filter_search_rows(baidu_data.get('organic_results', []), 'serp_baidunews', fetch_date, baidu_requested_at)
        bing_news = filter_search_rows(bing_data.get('organic_results', []), 'serp_bingnews', fetch_date, bing_requested_at)
        duck_news = filter_search_rows(duck_data.get('news_results', []), 'serp_duckduckgo_news', fetch_date, duck_requested_at)
        google_news = filter_search_rows(google_data.get('news_results', []), 'serp_googlenews', fetch_date, google_requested_at)

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
            log_path = os.environ.get("RUN_LOG_PATH", os.path.join("output", "run_log.txt"))
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
    sys.exit(0 if main() else 1)
