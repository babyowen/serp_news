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

# 导入图标管理系统
from icon_manager import safe_print, get_icon
from logger_utils import NewsLogger, get_news_logger

# 创建新闻日志记录器
scoring_logger = NewsLogger()

# 调用大模型对单条新闻进行评分
# title: 新闻标题
# content: 新闻正文  
# keyword: 用于AI评分的关键词（通常是search_keyword）
# main_keyword: 用于模型选择判断的主关键词（用于决定使用哪个模型）
# 返回分数（int）
def score_news(title: str, content: str, keyword: str, main_keyword: str = None) -> int:
    prompt = NEWS_SCORE_PROMPT.format(keyword=keyword, title=title, content=content)
    
    # 根据主关键词选择合适的system prompt
    model_decision_keyword = main_keyword if main_keyword else keyword
    system_msg = KEYWORD_SPECIFIC_SYSTEM_PROMPTS.get(model_decision_keyword, NEWS_SCORE_SYSTEM_MSG)
    
    safe_print("\n===== 送给大模型的内容 =====")
    safe_print(f"[评分关键词] {keyword}")
    safe_print(f"[主关键词] {model_decision_keyword}")
    safe_print(f"[system] {clean_unicode_for_console(system_msg)}")
    safe_print(f"[user] {clean_unicode_for_console(prompt)}")
    safe_print("==========================\n")
    # 新增：token超限主动监控
    try:
        import tiktoken
        enc = tiktoken.get_encoding('cl100k_base')
        token_count = len(enc.encode(prompt))
        if token_count > 61000:
            safe_print(f"[WARN] 评分prompt token数超限（主动判断），token数: {token_count}")
            safe_print(f"[WARN] prompt开头200字: {prompt[:200]}")
            safe_print(f"[WARN] prompt结尾200字: {prompt[-200:]}")
            with open("output/run_log.txt", "a", encoding="utf-8") as f:
                f.write(f"[WARN] 评分prompt token数超限（主动判断），token数: {token_count}\n")
                f.write(f"[WARN] prompt开头200字: {prompt[:200]}\n")
                f.write(f"[WARN] prompt结尾200字: {prompt[-200:]}\n")
    except Exception as e:
        safe_print(f"[WARN] tiktoken统计token失败: {e}")
    # 统一使用DeepSeek V3模型进行评分
    model_decision_keyword = main_keyword if main_keyword else keyword  # 用于模型选择判断的关键词
    model_to_use = 'deepseek-chat'  # 统一使用V3模型
    safe_print(f"[模型选择] 主关键词: {model_decision_keyword}, 评分关键词: {keyword}, 使用模型: {model_to_use} (统一使用V3)")
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    try:
        response = client.chat.completions.create(
            model=model_to_use,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt}
            ],
            stream=False,
            temperature=1
        )
        score_str = response.choices[0].message.content.strip()
        score = int(score_str[0])  # 只取第一个数字
    except Exception as e:
        # 新增：API返回token超限时记录prompt头尾
        err_str = str(e)
        is_token_limit = any(x in err_str.lower() for x in ["token", "context length", "input length", "max input limit", "too long"])
        if is_token_limit:
            safe_print(f"[WARN] 评分API返回token超限，prompt开头200字: {prompt[:200]}")
            safe_print(f"[WARN] 评分API返回token超限，prompt结尾200字: {prompt[-200:]}")
            with open("output/run_log.txt", "a", encoding="utf-8") as f:
                f.write(f"[WARN] 评分API返回token超限，prompt开头200字: {prompt[:200]}\n")
                f.write(f"[WARN] 评分API返回token超限，prompt结尾200字: {prompt[-200:]}\n")
        safe_print(f"[ERROR] 评分异常，关键词: {keyword}, 标题: {clean_unicode_for_console(title)}")
        safe_print(f"[ERROR] 异常类型: {type(e).__name__}, 内容: {e}")
        safe_print(f"[ERROR] prompt前200字: {clean_unicode_for_console(prompt[:200])}")
        score = 0
    return score

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
        safe_print(f"标题: {title}\n分数: {score}\n")
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
    safe_print(f"[DEBUG] 准备写入日志，关键词: {keyword}")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_path = os.path.join("output", "run_log.txt")
    score_line = " ".join([f"{i}分: {score_counter.get(i,0)}" for i in range(6)])
    emoji_map = ["0分", "1分", "2分", "3分", "4分", "5分"]
    emoji_score_line = " ".join([f"{emoji_map[i]}:{score_counter.get(i,0)}" for i in range(6)])
    total_wordcount_3plus = 0
    if results is not None:
        for news in results:
            if news.get("score", 0) >= 3:
                total_wordcount_3plus += len(news.get("content", ""))
    log = (
        f"\n[{now}]\n"
        f"执行程序: ai评分（统一使用DeepSeek V3模型）\n"
        f"[关键词] 主关键词: {keyword}\n"
        f"[文件] 原json文件: {json_path}\n"
        f"[结果] 评分结果文件: {scored_json_path}\n"
        f"[统计] 新闻总数: {total}\n"
        f"[完成] 完成评分: {scored_count}\n"
        f"{emoji_score_line}\n"
    )
    if results is not None:
        log += f"[字数] 3分及以上新闻正文总字数: {total_wordcount_3plus}\n"
    # 新增：规则打分统计
    if rule_based_titles is not None:
        log += f"[规则打分] 规则打分新闻数: {len(rule_based_titles)}\n"
        if rule_based_titles:
            log += "规则打分新闻标题：\n"
            for t in rule_based_titles:
                log += f"- {clean_unicode_for_console(t)}\n"
    log += f"==============================\n"
    safe_print(f"[DEBUG] 日志内容预览（前100字）：{clean_unicode_for_console(log[:100])}")
    
    # 对整个日志字符串进行Unicode清理，避免GBK编码错误
    cleaned_log = clean_unicode_for_console(log)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(cleaned_log)
    safe_print(f"[DEBUG] 日志写入完成，关键词: {keyword}")
    safe_print(f"评分完成，日志已写入: {log_path}")
    safe_print(f"评分结果文件: {scored_json_path}")

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
                        # 可选：写入日志
                        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        log_path = os.path.join("output", "run_log.txt")
                        skip_log = (
                            f"[{now}]\n[SKIP] 跳过关键词: {keyword}\n原因: 已存在 {scored_json_path}，已完成打分\n==============================\n"
                        )
                        # 对日志字符串进行Unicode清理，避免GBK编码错误
                        cleaned_skip_log = clean_unicode_for_console(skip_log)
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write(cleaned_skip_log)
                        processed_count += 1  # 跳过也算处理成功
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
        
        # 跳过机制：如已存在_scored.json文件，说明已完成打分，无需重复处理
        if os.path.exists(scored_json_path):
            safe_print(f"[SKIP] {scored_json_path} 已存在，跳过 {keyword}")
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_path = os.path.join("output", "run_log.txt")
            skip_log = (
                f"[{now}]\n[SKIP] 跳过关键词: {keyword}\n原因: 已存在 {scored_json_path}，已完成打分\n==============================\n"
            )
            # 对日志字符串进行Unicode清理，避免GBK编码错误
            cleaned_skip_log = clean_unicode_for_console(skip_log)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(cleaned_skip_log)
            log_script_complete("news_scorer.py", success=True, message=f"跳过已处理的关键词: {keyword}")
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

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1) 