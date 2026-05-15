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
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
    NEWS_SCORE_PROMPT, NEWS_RULE_BASED_SCORING, NEWS_SCORE_SYSTEM_MSG, DEFAULT_KEYWORD, DEFAULT_KEYWORDS, KEYWORD_SPECIFIC_SYSTEM_PROMPTS
)
import argparse
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

# 全局评分连接池实例
_scoring_client_pool = get_pool()

# 创建新闻日志记录器
scoring_logger = NewsLogger()

# 调用大模型对单条新闻进行评分
# title: 新闻标题
# content: 新闻正文  
# keyword: 用于AI评分的关键词（通常是search_keyword）
# main_keyword: 用于模型选择判断的主关键词（用于决定使用哪个模型）
# 返回分数（int）
def score_news(title: str, content: str, keyword: str, main_keyword: str = None, max_retries: int = 3, retry_interval: int = 5) -> int:
    prompt = NEWS_SCORE_PROMPT.format(keyword=keyword, title=title, content=content)
    
    # 根据主关键词选择合适的system prompt
    model_decision_keyword = main_keyword if main_keyword else keyword
    system_msg = KEYWORD_SPECIFIC_SYSTEM_PROMPTS.get(model_decision_keyword, NEWS_SCORE_SYSTEM_MSG)

    # 新增：token超限主动监控
    try:
        import tiktoken
        enc = tiktoken.get_encoding('cl100k_base')
        token_count = len(enc.encode(prompt))
        if token_count > 61000:
            content_len = len(content) if content else 0
            msg = f"[WARN] 评分token超限 | token数: {token_count} | 正文字数: {content_len} | 标题: {title[:40]}"
            safe_print(msg)
            with open("output/run_log.txt", "a", encoding="utf-8") as f:
                f.write(msg + "\n")
            return 0
    except Exception:
        pass

    model_to_use = 'deepseek-v4-flash'
    
    # 智能重试机制
    for attempt in range(1, max_retries + 1):
        # 从连接池获取客户端
        client = _scoring_client_pool.get_client(DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, max_uses=100)

        try:
            # 记录开始时间
            start_time = datetime.now()

            response = client.chat.completions.create(
                model=model_to_use,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt}
                ],
                stream=False,
                temperature=1,
                timeout=60  # 设置60秒超时
            )

            # 计算耗时
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()

            score_str = response.choices[0].message.content.strip()
            score = int(score_str[0])  # 只取第一个数字

            return score

        except Exception as e:
            # 计算失败耗时
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()

            err_str = str(e)
            # 分类错误类型
            is_ssl_error = any(x in err_str.lower() for x in ["ssl", "connection", "socket", "handshake", "certificate"])
            is_timeout = any(x in err_str.lower() for x in ["timeout", "timed out", "time out"])
            is_token_limit = any(x in err_str.lower() for x in ["token", "context length", "input length", "max input limit", "too long"])
            is_network_error = any(x in err_str.lower() for x in ["network", "dns", "resolve", "unreachable", "connection refused"])

            if is_token_limit:
                content_len = len(content) if content else 0
                msg = f"[WARN] 评分API token超限 | 正文字数: {content_len} | 标题: {title[:40]}"
                safe_print(msg)
                with open("output/run_log.txt", "a", encoding="utf-8") as f:
                    f.write(msg + "\n")
                # Token超限是不可重试的错误，直接返回0分
                return 0

            err_type = "SSL" if is_ssl_error else "超时" if is_timeout else "网络" if is_network_error else "其他"
            safe_print(f"[评分失败] 第{attempt}次({err_type}): {str(e)[:100]}")

            # 重试逻辑
            if attempt < max_retries:
                actual_retry_interval = retry_interval * 2 if (is_ssl_error or is_network_error) else retry_interval
                time.sleep(actual_retry_interval)
            else:
                content_len = len(content) if content else 0
                msg = f"[ERROR] 评分连续{max_retries}次失败({err_type}) | 关键词: {keyword} | 正文字数: {content_len} | 标题: {title[:40]} | 错误: {str(e)[:80]}"
                safe_print(msg)
                with open("output/run_log.txt", "a", encoding="utf-8") as f:
                    f.write(msg + "\n")

    # 所有重试都失败后返回0分
    return 0

# 规则打分函数
# 返回分数（int），未命中规则返回None
def rule_based_score(title: str, main_keyword: str) -> int:
    for rule in NEWS_RULE_BASED_SCORING:
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
    # 按link去重，保留第一条
    seen_links = set()
    unique_news_list = []
    for news in news_list:
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
        
        # 新增：如果 wordcount 为 0，直接打 0 分
        if wordcount == 0:
            score = 0
        else:
            # 先规则打分（规则打分仍使用主关键词）
            rule_score = rule_based_score(title, keyword)
            if rule_score is not None:
                score = rule_score
                rule_based_titles.append(title)  # 记录规则打分的标题
            else:
                # AI评分使用更精确的搜索关键词，但模型选择基于主关键词
                score = score_news(title, content, search_keyword, keyword)
        news_with_score = dict(news)
        news_with_score["score"] = score  # 用英文key
        results.append(news_with_score)
        score_counter[score] = score_counter.get(score, 0) + 1
    return results, score_counter, len(unique_news_list), rule_based_titles

# 保存带分数的新闻到新json文件，按分数降序排列
# results: 新闻列表
# json_path: 原始json路径
# 返回新文件路径
def write_scored_json(results, json_path):
    # 按评分降序排列
    results_sorted = sorted(results, key=lambda x: x.get("score", 0), reverse=True)
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
    log_path = os.path.join("output", "run_log.txt")
    score_line = " ".join([f"{i}分: {score_counter.get(i,0)}" for i in range(6)])

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
        args = parser.parse_args()

        if args.test_json:
            # 测试模式
            try:
                news = json.loads(args.test_json)
                title = news.get("title", "")
                content = news.get("content", "")
                keyword = news.get("keyword", DEFAULT_KEYWORD)
                safe_print(f"测试模式：\n新闻标题: {title}\n新闻正文: {content}\n关键词: {keyword}")
                score = score_news(title, content, keyword)
                safe_print(f"评分结果: {score}")
            except Exception as e:
                safe_print(f"测试模式解析失败: {e}")
                success = False
            log_script_complete("news_scorer.py", success=success, message="测试模式完成")
            return success

            # 批量模式：无参数时遍历 DEFAULT_KEYWORDS
        if args.keyword is None:
            date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            processed_count = 0
            total_count = len(DEFAULT_KEYWORDS)
            
            for keyword in DEFAULT_KEYWORDS:
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
                        processed_count += 1
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
                    if already_scored:
                        safe_print(f"已检测到 {json_path} 已经打分，跳过。")
                        processed_count += 1
                        continue
                    
                    results, score_counter, total, rule_based_titles = batch_score_news(json_path, keyword)
                    scored_json_path = write_scored_json(results, json_path)
                    append_log(keyword, json_path, total, score_counter, len(results), scored_json_path, results, rule_based_titles)
                    processed_count += 1
                    
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
                rescored_count = 0
                for news in results:
                    score = news.get("score")
                    # 只重评真正缺失分数的（score为None），不重评score=0（有效评分）
                    if score is not None:
                        continue
                    title = news.get("title", "")
                    content = news.get("content", "")
                    search_keyword = news.get("search_keyword", keyword)
                    if not content or content.strip() == "":
                        continue
                    new_score = score_news(title, content, search_keyword, keyword)
                    news["score"] = new_score
                    rescored_count += 1
                    safe_print(f"[RESCORE] {title[:40]}... → {new_score}分")
                # 写回
                results_sorted = sorted(results, key=lambda x: x.get("score", 0), reverse=True)
                with open(scored_json_path, "w", encoding="utf-8") as f:
                    json.dump(results_sorted, f, ensure_ascii=False, indent=2)
                safe_print(f"[RESCORE] 完成，重评了 {rescored_count} 条")
                log_script_complete("news_scorer.py", success=True, message=f"重评完成: {keyword}, 重评{rescored_count}条")
                return True

        # 跳过机制：如已存在_scored.json文件，说明已完成打分，无需重复处理
        if os.path.exists(scored_json_path):
            safe_print(f"[SKIP] {scored_json_path} 已存在，跳过 {keyword}")
            log_script_complete("news_scorer.py", success=True, message=f"跳过已处理: {keyword}")
            return True
            
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
        log_script_complete("news_scorer.py", success=True, message=f"关键词 {keyword} 评分完成")
        return True
        
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