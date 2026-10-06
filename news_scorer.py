# -*- coding: utf-8 -*-
# =========================================
# 新闻评分程序
# 主要功能：对新闻进行AI评分，同时支持基于规则的评分
# 使用DeepSeek V3模型进行智能评分
# =========================================

import json
import os
import sys
import time
from datetime import datetime, timedelta
from collections import Counter
from openai import OpenAI
from config import (
    NEWS_SCORE_PROMPT, NEWS_RULE_BASED_SCORING, NEWS_SCORE_SYSTEM_MSG, DEFAULT_KEYWORD, DEFAULT_KEYWORDS, KEYWORD_SPECIFIC_SYSTEM_PROMPTS
)
import argparse
from config_schema import ConfigError
from news_freshness import eligible, check_current
from government_affairs_scoring import (ScoreResult, parse_government_affairs_score,
    score_result, score_fields, scored_file_complete, context_limit_skipped)
from error_handler import (
    setup_global_exception_handler,
    with_error_handling,
    log_script_start,
    log_script_complete,
    ErrorHandler
)

from icon_manager import safe_print, get_icon
from logger_utils import NewsLogger, get_news_logger
from llm_client_pool import get_pool
from runtime_config import value, model_credentials, model_arguments
from batch_config import prepare_batch

# 全局评分连接池实例
_scoring_client_pool = get_pool()

# 创建新闻日志记录器
scoring_logger = NewsLogger()


def get_system_message(main_keyword: str = None, keyword: str = None) -> str:
    """Select the scoring prompt by main business keyword."""
    model_decision_keyword = main_keyword if main_keyword else keyword
    prompts = value("KEYWORD_SPECIFIC_SYSTEM_PROMPTS")
    return prompts.get(model_decision_keyword, value("NEWS_SCORE_SYSTEM_MSG"))

# 调用大模型对单条新闻进行评分
# title: 新闻标题
# content: 新闻正文  
# keyword: 用于AI评分的关键词（通常是search_keyword）
# main_keyword: 用于模型选择判断的主关键词（用于决定使用哪个模型）
# 返回分数；任何关键词评分失败时返回 None
def score_news(title: str, content: str, keyword: str, main_keyword: str = None, max_retries: int = 3, retry_interval: int = 5, context=None) -> int | None:
    return score_news_result(title, content, keyword, main_keyword or keyword,
                             max_retries, retry_interval, context=context).score


def score_news_result(title, content, keyword, main_keyword, max_retries=3, retry_interval=5, context=None):
    return score_result(title, content, keyword, main_keyword, _scoring_client_pool,
                        max_retries=max_retries, retry_interval=retry_interval, context=context)

# 规则打分函数（仅兼容配置及测试；生产评分必须经过公共时效规则）
# 返回分数（int），未命中规则返回None
def rule_based_score(title: str, main_keyword: str) -> int:
    for rule in value("NEWS_RULE_BASED_SCORING"):
        if rule.get('main_keyword') == main_keyword and rule.get('title_contains') in title:
            return rule.get('score', 0)
    return None

# 批量对新闻进行评分，去重、统计分布
# json_path: 新闻json文件路径
# keyword: 关键词
# 返回：带分数的新闻列表、分数统计、新闻总数、规则打分标题列表
def batch_score_news(json_path, keyword):
    with open(json_path, "r", encoding="utf-8") as f:
        news_list = json.load(f)
    if any(not check_current(row) for row in news_list):
        raise ValueError("publication date check missing or stale; run content stage first")
    # 按link去重，保留第一条
    seen_links = set()
    unique_news_list = []
    for news in news_list:
        if not eligible(news):
            continue
        link = news.get("link", None)
        if link and link not in seen_links:
            unique_news_list.append(news)
            seen_links.add(link)
    results = []
    score_counter = {i: 0 for i in range(6)}
    rule_based_titles = []  # 新增：记录规则打分的标题
    for news in unique_news_list:
        title = news.get("title", "")
        content = news.get("content", "")
        wordcount = news.get("wordcount", None)
        # 新增：优先使用更精确的搜索关键词进行AI评分
        search_keyword = news.get("search_keyword", keyword)  # 如果没有search_keyword则回退到主关键词
        
        # Every keyword gets the same evidence-aware model path. Title-only rules
        # cannot decide whether an event is stale or has substantive new progress.
        result = score_news_result(title, content, search_keyword, keyword, context=news)
        score = result.score
        news_with_score = dict(news)
        news_with_score.update(score_fields(result))
        from scoring_policy import VERSION
        news_with_score["scoring_policy_version"] = VERSION
        results.append(news_with_score)
        if score is not None:
            score_counter[score] = score_counter.get(score, 0) + 1
    return results, score_counter, len(unique_news_list), rule_based_titles


def migrate_legacy_scores(results, json_path, date_str):
    """Attach verified batch evidence to unchanged legacy scores without rescoring."""
    if not os.path.exists(json_path):
        return
    with open(json_path, encoding="utf-8") as handle:
        rows = json.load(handle)
    by_link = {}
    for row in rows:
        by_link.setdefault(row.get("link"), []).append(row)
    evidence_fields = ("fetchdate", "publication_check", "date", "sourceapi",
                       "search_date_raw", "search_fetched_at", "search_date_field")
    for news in results:
        score = news.get("score")
        if ("score_status" in news or type(score) is not int or not 0 <= score <= 5
                or not news.get("link") or news.get("fetchdate") not in (None, date_str)):
            continue
        matches = by_link.get(news["link"], [])
        if len(matches) != 1:
            continue
        source = matches[0]
        if (source.get("fetchdate") != date_str or not eligible(source)
                or any(source.get(key) != news.get(key)
                       for key in ("title", "content"))):
            continue
        candidate = dict(news)
        for key in evidence_fields:
            candidate.pop(key, None)
            if key in source:
                candidate[key] = source[key]
        if eligible(candidate):
            candidate["score_status"] = "ok"
            news.update(candidate)


def refresh_review_scores(json_path, keyword, links):
    """Score newly admitted rows, keeping already successful results across retries."""
    from pathlib import Path
    from government_affairs_pipeline import atomic_json
    from scoring_policy import VERSION
    path = Path(json_path)
    saved = path.with_name(path.stem + "_scored.json")
    rows = json.loads(path.read_text(encoding="utf-8"))
    previous = json.loads(saved.read_text(encoding="utf-8")) if saved.exists() else []
    by_link = {n.get("link"): n for n in previous}
    selected = []
    for row in rows:
        if row.get("link") not in links or not eligible(row):
            continue
        cached = by_link.get(row["link"], {})
        valid = ((context_limit_skipped(cached) or
                  (type(cached.get("score")) is int and 0 <= cached["score"] <= 5
                   and cached.get("score_status") == "ok"))
                 and cached.get("title") == row.get("title")
                 and cached.get("content") == row.get("content"))
        if valid:
            result = {**cached, **row, **{k:v for k,v in cached.items() if k.startswith("score")}}
        else:
            scored = score_news_result(row.get("title", ""), row.get("content", ""),
                                       row.get("search_keyword", keyword), keyword, context=row)
            result = {**row, **score_fields(scored), "scoring_policy_version":VERSION}
        by_link[row["link"]] = result
        # Persist each paid success before the next request or a DB operation.
        atomic_json(saved, list(by_link.values()))
        selected.append(result)
    return selected


# 保存带分数的新闻到新json文件，按分数降序排列
# results: 新闻列表
# json_path: 原始json路径
# 返回新文件路径
def write_scored_json(results, json_path):
    # 按评分降序排列
    results_sorted = sorted(results, key=lambda x: x.get("score") if x.get("score") is not None else -1, reverse=True)
    base, ext = os.path.splitext(json_path)
    new_path = base + "_scored" + ext
    with open(new_path, "w", encoding="utf-8") as f:
        json.dump(results_sorted, f, ensure_ascii=False, indent=2)
    return new_path

# 追加运行日志到log文件，记录评分分布、文件路径等
# keyword: 关键词
# json_path: 原始json路径
# total: 新闻总数
# score_counter: 分数统计
# scored_count: 已评分数量
# scored_json_path: 评分结果文件路径
# results: 可选，带分数的新闻列表
# rule_based_titles: 可选，规则打分标题列表
def append_log(keyword, json_path, total, score_counter, scored_count, scored_json_path, results=None, rule_based_titles=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_path = os.environ.get("RUN_LOG_PATH", os.path.join("output", "run_log.txt"))
    score_line = " ".join([f"{i}分: {score_counter.get(i,0)}" for i in range(6)])
    if results is not None:
        failures = sum(n.get("score") is None and not context_limit_skipped(n) for n in results)
        skipped = sum(context_limit_skipped(n) for n in results)
        empty = sum(n.get("score_status") == "empty_content" for n in results)
        scored_count = sum(n.get("score_status") == "ok" for n in results)
        score_line += f" | 有效评分: {scored_count} 空正文: {empty} 失败: {failures} 上下文超限跳过: {skipped}"

    log = (
        f"\n[{now}]\n"
        f"执行程序: AI评分\n"
        f"[关键词] {keyword}\n"
        f"[统计] 总数: {total}，完成: {scored_count}\n"
        f"[分布] {score_line}\n"
    )
    if rule_based_titles is not None and len(rule_based_titles) > 0:
        log += f"[规则打分] {len(rule_based_titles)}条\n"
    log += f"==============================\n"

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(log)

# 获取昨天日期字符串，格式YYYY-MM-DD
def get_yesterday_str():
    yesterday = datetime.now() - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")

def clean_unicode_for_console(text):
    """
    清理文本中的特殊Unicode字符，避免Windows GBK编码错误
    """
    if not text:
        return text
    
    # 常见的需要替换的Unicode字符
    replacements = {
        '\xa9': '(C)',      # 版权符号
        '\u2714': '[OK]',   # 勾选符号
        '\u261e': '[->]',   # 手指符号
        '\U0001f552': '[TIME]',  # 时钟emoji
        '\U0001f4f0': '[NEWS]',  # 新闻emoji
        '\U0001f4ca': '[CHART]', # 图表emoji
        '\U0001f4f1': '[PHONE]', # 手机emoji
        '\U0001f4bb': '[PC]',    # 电脑emoji
        '\U0001f310': '[GLOBE]', # 地球emoji
        '\U0001f4c8': '[TREND]', # 趋势图emoji
        # 添加更多需要替换的字符...
    }
    
    cleaned_text = text
    for unicode_char, replacement in replacements.items():
        cleaned_text = cleaned_text.replace(unicode_char, replacement)
    
    # 移除其他可能导致GBK编码错误的字符
    # 保留中文、英文、数字和常用标点符号
    try:
        # 尝试编码为GBK，如果失败则移除有问题的字符
        cleaned_text.encode('gbk')
    except UnicodeEncodeError as e:
        # 逐字符检查，移除无法编码的字符
        safe_chars = []
        for char in cleaned_text:
            try:
                char.encode('gbk')
                safe_chars.append(char)
            except UnicodeEncodeError:
                safe_chars.append('?')  # 用?替代无法编码的字符
        cleaned_text = ''.join(safe_chars)
    
    return cleaned_text

# 主流程入口，支持批量/单条/测试模式
@with_error_handling("news_scorer.py", "main")
def main():
    """主函数：处理新闻评分任务"""
    # 记录脚本开始
    log_script_start("news_scorer.py", sys.argv[1:])
    
    error_handler = ErrorHandler()
    success = True
    
    try:
        # 用法: python news_scorer.py [keyword] [date] 或 --test_json '{...}'
        parser = argparse.ArgumentParser()
        parser.add_argument('keyword', nargs='?', default=None)
        parser.add_argument('date', nargs='?', default=None)
        parser.add_argument('--test_json', type=str, help='测试模式，输入一条json字符串')
        parser.add_argument('--rescore', action='store_true', help='重评模式：只对无分数或0分的条目重新评分')
        parser.add_argument('--adopt-existing-config', action='store_true')
        args = parser.parse_args()

        if args.test_json:
            # 测试模式
            try:
                news = json.loads(args.test_json)
                title = news.get("title", "")
                content = news.get("content", "")
                keyword = news.get("keyword", DEFAULT_KEYWORD)
                safe_print(f"测试模式：\n新闻标题: {title}\n新闻正文: {content}\n关键词: {keyword}")
                score = score_news(title, content, news.get("search_keyword", keyword), news.get("main_keyword", keyword), context=news)
                success = score is not None
                safe_print(f"评分结果: {score}")
            except Exception as e:
                safe_print(f"测试模式解析失败: {e}")
                success = False
            log_script_complete("news_scorer.py", success=success, message="测试模式完成")
            return success

            # 批量模式：无参数时遍历 DEFAULT_KEYWORDS
        if args.keyword is None:
            date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            _, batch_keywords = prepare_batch(date_str, adopt_existing=args.adopt_existing_config)
            processed_count = 0
            total_count = len(batch_keywords)

            for keyword in batch_keywords:
                json_path = os.path.join("output", date_str, f"{date_str}_{keyword}.json")
                scored_json_path = os.path.splitext(json_path)[0] + "_scored.json"
                safe_print(f"\n=== 开始处理关键词: {keyword} ===")
                
                try:
                    if not os.path.exists(json_path):
                        safe_print(f"未找到文件: {json_path}")
                        continue
                    # 跳过机制：如已存在_scored.json文件，说明已完成打分，无需重复处理
                    if os.path.exists(scored_json_path):
                        safe_print(f"[SKIP] {scored_json_path} 已存在，跳过 {keyword}")
                        if scored_file_complete(scored_json_path, keyword):
                            processed_count += 1
                        else:
                            success = False
                        continue
                    # 检查是否已打分（兼容旧流程）
                    try:
                        with open(json_path, "r", encoding="utf-8") as f:
                            news_list = json.load(f)
                        already_scored = any("score" in news for news in news_list)
                    except Exception as e:
                        safe_print(f"读取文件失败: {json_path}, 错误: {e}")
                        error_handler.log_error(
                            error_type="FILE_READ_ERROR",
                            error_msg=f"读取文件失败: {json_path}, 错误: {e}",
                            script_name="news_scorer.py",
                            keyword=keyword
                        )
                        continue
                    results, score_counter, total, rule_based_titles = batch_score_news(json_path, keyword)
                    scored_json_path = write_scored_json(results, json_path)
                    append_log(keyword, json_path, total, score_counter, len(results), scored_json_path, results, rule_based_titles)
                    if scored_file_complete(scored_json_path, keyword):
                        processed_count += 1
                    else:
                        success = False
                    
                except Exception as e:
                    safe_print(f"处理关键词 {keyword} 时发生异常: {e}")
                    error_handler.log_error(
                        error_type="SCORING_ERROR",
                        error_msg=f"处理关键词 {keyword} 时发生异常: {e}",
                        script_name="news_scorer.py",
                        keyword=keyword
                    )
                    success = False
            
            safe_print(f"\n[统计] 批量评分完成：{processed_count}/{total_count} 个关键词处理成功")
            log_script_complete("news_scorer.py", success=success, message=f"批量评分完成：{processed_count}/{total_count}")
            return success

        # 单关键词模式
        keyword = args.keyword or DEFAULT_KEYWORD
        date_str = args.date
        if not date_str or date_str.lower() == 'none':
            date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        prepare_batch(date_str, keyword, args.adopt_existing_config)
        json_path = os.path.join("output", date_str, f"{date_str}_{keyword}.json")
        scored_json_path = os.path.splitext(json_path)[0] + "_scored.json"

        # 重评模式：从已有的_scored.json中找无分/0分条目重新评分
        if args.rescore:
            if not os.path.exists(scored_json_path):
                safe_print(f"[RESCORE] {scored_json_path} 不存在，无法重评，走正常评分流程")
            else:
                safe_print(f"[RESCORE] 开始重评: {keyword} {date_str}")
                with open(scored_json_path, "r", encoding="utf-8") as f:
                    results = json.load(f)
                migrate_legacy_scores(results, json_path, date_str)
                rescored_count = 0
                for news in results:
                    if not eligible(news):
                        continue
                    score = news.get("score")
                    # 只重评真正缺失分数的（score为None），不重评score=0（有效评分）
                    if score is not None:
                        continue
                    title = news.get("title", "")
                    content = news.get("content", "")
                    search_keyword = news.get("search_keyword", keyword)
                    if not content or content.strip() == "":
                        continue
                    result = score_news_result(title, content, search_keyword, keyword, context=news)
                    news.update(score_fields(result))
                    new_score = result.score
                    rescored_count += 1
                    safe_print(f"[RESCORE] {title[:40]}... → {new_score}分")
                # 写回
                results_sorted = sorted(results, key=lambda x: x.get("score") if x.get("score") is not None else -1, reverse=True)
                with open(scored_json_path, "w", encoding="utf-8") as f:
                    json.dump(results_sorted, f, ensure_ascii=False, indent=2)
                safe_print(f"[RESCORE] 完成，重评了 {rescored_count} 条")
                complete = scored_file_complete(scored_json_path, keyword)
                log_script_complete("news_scorer.py", success=complete, message=f"重评完成: {keyword}, 重评{rescored_count}条")
                return complete

        # 跳过机制：如已存在_scored.json文件，说明已完成打分，无需重复处理
        if os.path.exists(scored_json_path):
            safe_print(f"[SKIP] {scored_json_path} 已存在，跳过 {keyword}")
            complete = scored_file_complete(scored_json_path, keyword)
            log_script_complete("news_scorer.py", success=complete, message=f"检查已有评分: {keyword}；失败项需显式 --rescore")
            return complete
            
        if not os.path.exists(json_path):
            safe_print(f"未找到文件: {json_path}")
            error_handler.log_error(
                error_type="FILE_NOT_FOUND",
                error_msg=f"未找到文件: {json_path}",
                script_name="news_scorer.py",
                keyword=keyword
            )
            log_script_complete("news_scorer.py", success=False, message=f"文件不存在: {json_path}")
            return False
            
        results, score_counter, total, rule_based_titles = batch_score_news(json_path, keyword)
        scored_json_path = write_scored_json(results, json_path)
        append_log(keyword, json_path, total, score_counter, len(results), scored_json_path, results, rule_based_titles)
        safe_print(f"[完成] 关键词 {keyword} 评分完成")
        complete = scored_file_complete(scored_json_path, keyword)
        log_script_complete("news_scorer.py", success=complete, message=f"关键词 {keyword} 评分完成")
        return complete
        
    except Exception as e:
        error_msg = f"news_scorer.py 执行过程中发生异常: {str(e)}"
        safe_print(f"[ERROR] {error_msg}")
        error_handler.log_error(
            error_type="MAIN_FUNCTION_ERROR",
            error_msg=error_msg,
            script_name="news_scorer.py"
        )
        success = False
        log_script_complete("news_scorer.py", success=False, message=error_msg)
        return False
    
    finally:
        try:
            _scoring_client_pool.close_all()
        except Exception:
            pass

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
