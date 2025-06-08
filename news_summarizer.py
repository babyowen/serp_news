# -*- coding: utf-8 -*-
# =========================================
# 新闻摘要主程序
# 主要功能：加载新闻、过滤、去重、构建prompt、调用大模型总结、保存结果、记录日志
# =========================================
import os
import sys
# 设置tokenizer并行环境变量，避免警告
os.environ["TOKENIZERS_PARALLELISM"] = "false"
import json
from datetime import datetime, timedelta
from config import (
    NEWS_SUMMARY_MODELS,  # 支持的模型配置
    NEWS_SUMMARY_SYSTEM_PROMPT,  # 系统提示词
    NEWS_SUMMARY_USER_PROMPT,    # 用户提示词模板
    NEWS_SUMMARY_RESULT_FILENAME, # 结果文件名模板
    NEWS_SUMMARY_PLATFORM,       # 默认平台
    NEWS_SUMMARY_MODEL,          # 默认模型
    DEFAULT_KEYWORDS,            # 默认关键词列表
    NEWS_SUMMARY_JUDGE_SYSTEM_PROMPT, # 新增：评判官system prompt
    NEWS_SUMMARY_JUDGE_USER_PROMPT,   # 新增：评判官user prompt
    NEWS_SUMMARY_OPTIMIZE_SYSTEM_PROMPT, # 新增：优化轮system prompt
    NEWS_SUMMARY_OPTIMIZE_USER_PROMPT,    # 新增：优化轮user prompt
    NEWS_SUMMARY_HOTSPOT_SYSTEM_PROMPT, # 新增：热点追踪system prompt
    NEWS_SUMMARY_HOTSPOT_USER_PROMPT,   # 新增：热点追踪user prompt
)
from openai import OpenAI
import tiktoken
import requests
import importlib

# ========== deepseek官方tokenizer加载（仅deepseek平台用） ==========
deepseek_tokenizer = None
# 获取deepseek tokenizer实例
# 若未加载则尝试加载本地tokenizer，否则fallback到tiktoken
# 返回tokenizer对象或None

def get_deepseek_tokenizer():
    global deepseek_tokenizer
    if deepseek_tokenizer is None:
        try:
            from transformers import AutoTokenizer
            chat_tokenizer_dir = os.path.join(os.path.dirname(__file__), 'deepseek_v3_tokenizer')
            deepseek_tokenizer = AutoTokenizer.from_pretrained(chat_tokenizer_dir, trust_remote_code=True)
        except Exception as e:
            print(f"[WARN] deepseek官方tokenizer加载失败: {e}")
            deepseek_tokenizer = None
    return deepseek_tokenizer

# 统计文本token数，支持deepseek和openai平台
# text: 输入文本
# platform: 平台名
# model_name: 模型名
# 返回token数量

def count_tokens(text, platform=None, model_name=None):
    platform = platform or NEWS_SUMMARY_PLATFORM
    model_name = model_name or NEWS_SUMMARY_MODEL
    if platform == 'deepseek':
        tokenizer = get_deepseek_tokenizer()
        if tokenizer:
            return len(tokenizer.encode(text))
        else:
            # fallback
            enc = tiktoken.get_encoding('cl100k_base')
            return len(enc.encode(text))
    else:
        enc = tiktoken.get_encoding('cl100k_base')
        return len(enc.encode(text))

# 加载打分后的新闻，过滤低分新闻
# json_path: 新闻json文件路径
# min_score: 最低分数阈值
# 返回过滤后的新闻列表

def load_scored_news(json_path, min_score=3):
    print(f"[INFO] 读取打分新闻文件: {json_path}")
    with open(json_path, 'r', encoding='utf-8') as f:
        news_list = json.load(f)
    filtered = [n for n in news_list if n.get('score', 0) >= min_score]
    print(f"[INFO] 3分及以上新闻数量: {len(filtered)}")
    return filtered

# 构建新闻列表prompt字符串
# news_list: 新闻列表
# 返回拼接后的prompt字符串

def build_news_list_prompt(news_list):
    lines = []
    for idx, news in enumerate(news_list, 1):
        title = news.get('title', '').strip().replace('\n', ' ')
        content = news.get('content', '').strip()
        score = news.get('score', '')
        lines.append(f"标题{idx}: {title}\n分数{idx}: {score}\n新闻{idx}: {content}")
    return '\n\n'.join(lines)

# 调用大模型对新闻列表进行总结
# news_list: 新闻列表
# platform: 平台名
# model_name: 模型名
# keyword: 关键词
# 返回prompt、总结、token统计等

def summarize_news(news_list, platform=None, model_name=None, keyword=None):
    platform = platform or NEWS_SUMMARY_PLATFORM
    model_name = model_name or NEWS_SUMMARY_MODEL
    model_cfg = NEWS_SUMMARY_MODELS[platform][model_name]
    print(f"[INFO] 当前平台: {platform}")
    print(f"[INFO] 当前模型: {model_name}")
    print(f"[INFO] API地址: {model_cfg.get('base_url')}")
    news_list_str = build_news_list_prompt(news_list)
    user_prompt = NEWS_SUMMARY_USER_PROMPT.format(news_list=news_list_str, keyword=keyword)
    system_tokens = count_tokens(NEWS_SUMMARY_SYSTEM_PROMPT, platform, model_name)
    user_tokens = count_tokens(user_prompt, platform, model_name)
    print(f"[INFO] 新闻关键词: {keyword}")
    print("\n===== 送给大模型的内容 =====")
    print(f"[system] {NEWS_SUMMARY_SYSTEM_PROMPT.strip()}")
    print(f"[user] {user_prompt[:1000]}{'...（已截断）' if len(user_prompt)>1000 else ''}")
    print(f"[INFO] system prompt tokens: {system_tokens}")
    print(f"[INFO] user prompt tokens: {user_tokens}")
    print("==========================\n")
    # 统一OpenAI SDK调用
    client = OpenAI(api_key=model_cfg['api_key'], base_url=model_cfg['base_url'])
    print(f"[DEBUG] OpenAI SDK调用模型: {model_cfg['model']}")
    print(f"[DEBUG] OpenAI SDK地址: {model_cfg['base_url']}")
    print(f"[DEBUG] API Key: {'已配置' if model_cfg['api_key'] else '未配置'}")
    # 判断是否需要流式（如qwq-plus等）
    stream_mode = model_cfg['model'] in ['qwq-plus']  # 可扩展其它流式模型
    print(f"[DEBUG] stream参数: {stream_mode}")
    response = client.chat.completions.create(
        model=model_cfg['model'],
        messages=[
            {"role": "system", "content": NEWS_SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        stream=stream_mode
    )
    if stream_mode:
        result = ""
        for chunk in response:
            if hasattr(chunk.choices[0].delta, "content") and chunk.choices[0].delta.content:
                result += chunk.choices[0].delta.content
    else:
        result = response.choices[0].message.content.strip()
    result_tokens = count_tokens(result, platform, model_name)
    print(f"[INFO] 返回内容tokens: {result_tokens}")
    return user_prompt, result, system_tokens, user_tokens, result_tokens, platform, model_name

# 保存总结结果到json文件
# date: 日期
# keyword: 关键词
# summary: 总结内容
# output_dir: 输出目录
# model_name/platform/model_str: 模型信息
# 返回保存路径

def save_summary(date, keyword, summary, output_dir, model_name=None, platform=None, model_str=None):
    filename = NEWS_SUMMARY_RESULT_FILENAME.format(date=date, keyword=keyword)
    out_path = os.path.join(output_dir, filename)
    # 读取旧文件，兼容旧格式
    if os.path.exists(out_path):
        with open(out_path, 'r', encoding='utf-8') as f:
            result = json.load(f)
        # 兼容旧格式，迁移顶层 summary 字段
        if 'summary' in result:
            old_entry = {
                "summary": result.pop('summary'),
                "platform": result.pop('platform', 'unknown'),
                "model": result.pop('model', 'unknown')
            }
            result.setdefault('summaries', []).insert(0, old_entry)
    else:
        result = {"date": date, "keyword": keyword}
    if 'summaries' not in result:
        result['summaries'] = []
    # 只保留platform和model字段
    result['summaries'].append({
        "summary": summary,
        "platform": platform if platform else "unknown",
        "model": model_str if model_str else "unknown"
    })
    # 只保留 date, keyword, summaries
    result = {k: result[k] for k in ['date', 'keyword', 'summaries'] if k in result}
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[INFO] 总结已保存: {out_path}")
    return out_path

# 追加运行日志到log文件
# 记录运行参数、token统计、合并关键词等信息

def append_log(date, keyword, model_name, prompt, summary_path, news_count, success=True, error_msg=None, system_tokens=None, user_tokens=None, result_tokens=None, platform=None, model_str=None, extra_info=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_path = os.path.join("output", "run_log.txt")
    log = (
        f"\n[🕒 {now}]\n"
        f"执行程序: news_summarizer\n"
        f"🔑 主关键词: {keyword}\n"
    )
    # 新增：记录涉及的搜索关键词
    if extra_info and 'search_keywords' in extra_info and extra_info['search_keywords']:
        log += f"涉及搜索关键词: {', '.join([str(s) for s in extra_info['search_keywords'] if s])}\n"
    log += (
        f"📅 日期: {date}\n"
        f"🤖 平台: {platform if platform else 'unknown'}\n"
        f"🤖 模型: {model_str if model_str else model_name}\n"
        f"📄 3分及以上新闻数量: {news_count}\n"
        f"📝 送给大模型的prompt前300字: {prompt[:300].replace(chr(10),' ')}\n"
        f"💾 总结结果文件: {summary_path if summary_path else '无'}\n"
        f"[Token统计] 平台: {platform if platform else 'unknown'}，模型: {model_str if model_str else model_name}，system: {system_tokens}, user: {user_tokens}, result: {result_tokens}\n"
        f"✅ 运行结果: {'成功' if success else '失败'}\n"
    )
    # 新增：记录合并的二级关键词
    if extra_info and 'secondary_keywords' in extra_info:
        log += f"合并的二级关键词: {extra_info['secondary_keywords']}\n"
    if error_msg:
        log += f"❌ 错误信息: {error_msg}\n"
    if extra_info and 'merge_status' in extra_info:
        log += f"合并情况: {extra_info['merge_status']}\n"
    log += f"==============================\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(log)
    print(f"[INFO] 日志已写入: {log_path}")

def call_llm(system_prompt, user_prompt, platform, model_name, stream_mode=False):
    model_cfg = NEWS_SUMMARY_MODELS[platform][model_name]
    client = OpenAI(api_key=model_cfg['api_key'], base_url=model_cfg['base_url'])
    response = client.chat.completions.create(
        model=model_cfg['model'],
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        stream=stream_mode
    )
    if stream_mode:
        result = ""
        for chunk in response:
            if hasattr(chunk.choices[0].delta, "content") and chunk.choices[0].delta.content:
                result += chunk.choices[0].delta.content
    else:
        result = response.choices[0].message.content.strip()
    return result

# 主流程入口
# date: 日期
# keyword: 关键词
# model_name: 指定模型名
# output_dir: 输出目录

def main(date=None, keyword=None, model_name=None, output_dir=None):
    if date is None:
        date = datetime.now().strftime('%Y-%m-%d')
    if keyword is None:
        raise ValueError('必须指定关键词')
    if output_dir is None:
        output_dir = os.path.join('output', date)
    scored_json = os.path.join(output_dir, f"{date}_{keyword}_scored.json")
    news_list = []
    search_keywords_set = set()
    # 只加载主关键词新闻
    if os.path.exists(scored_json):
        news_items = load_scored_news(scored_json, min_score=3)
        news_list += news_items
        for n in news_items:
            if 'search_keyword' in n:
                search_keywords_set.add(n['search_keyword'])
    # 合并去重（按link去重）
    unique_links = set()
    deduped_news = []
    for news in news_list:
        link = news.get('link')
        if link and link not in unique_links:
            deduped_news.append(news)
            unique_links.add(link)
    news_list = deduped_news
    # 新增：打印涉及的搜索关键词
    if search_keywords_set:
        print(f"[INFO] 本批次涉及的搜索关键词: {', '.join([str(s) for s in search_keywords_set if s])}")
    if not news_list:
        print("[INFO] 无3分及以上新闻，无需总结。")
        append_log(date, keyword, model_name, '', '', 0, success=True, extra_info={'search_keywords': list(search_keywords_set)})
        return
    # 后续流程保持不变
    # token超限多级预判
    platform = NEWS_SUMMARY_PLATFORM
    model = NEWS_SUMMARY_MODEL
    news_list_str = build_news_list_prompt(news_list)
    user_prompt = NEWS_SUMMARY_USER_PROMPT.format(news_list=news_list_str, keyword=keyword)
    user_tokens = count_tokens(user_prompt, platform=platform, model_name=model)
    switched = False
    # 1. deepseek超限，切换到qwen-max
    if platform == 'deepseek' and user_tokens > 61000:
        print(f"[WARN] token数{user_tokens}超出deepseek 64k限制，自动切换到bailian平台qwen-plus-latest模型")
        platform = 'bailian'
        model = 'qwen-plus-latest'
        news_list_str = build_news_list_prompt(news_list)
        user_prompt = NEWS_SUMMARY_USER_PROMPT.format(news_list=news_list_str, keyword=keyword)
        user_tokens = count_tokens(user_prompt, platform=platform, model_name=model)
        switched = True
        with open(os.path.join("output", "run_log.txt"), "a", encoding="utf-8") as f:
            f.write(f"[WARN] token数超限，已切换到bailian平台qwen-plus-latest模型，token数: {user_tokens}\n")
    prompt, summary, system_tokens, user_tokens, result_tokens, used_platform, used_model = summarize_news(news_list, platform=platform, model_name=model, keyword=keyword)
    # ========== 第一轮摘要结果保存 ==========
    print("\n===== 第1轮-初稿摘要 =====")
    print("[system prompt]")
    print(NEWS_SUMMARY_SYSTEM_PROMPT.strip())
    print("\n[user prompt]")
    print(prompt.strip())
    print("\n[大模型输出]")
    print(summary.strip())

    round1_entry = {
        "summary": summary,
        "platform": used_platform,
        "model": used_model,
        "round": 1
    }
    # ========== 第一轮优化：评判官建议 ==========
    judge_user_prompt = NEWS_SUMMARY_JUDGE_USER_PROMPT.format(
        system_prompt=NEWS_SUMMARY_SYSTEM_PROMPT.strip(),
        user_prompt=prompt.strip(),
        summary=summary.strip()
    )
    judge_system_prompt = NEWS_SUMMARY_JUDGE_SYSTEM_PROMPT.strip()
    # token计数与模型切换
    judge_user_tokens = count_tokens(judge_user_prompt, platform=used_platform, model_name=used_model)
    judge_system_tokens = count_tokens(judge_system_prompt, platform=used_platform, model_name=used_model)
    judge_platform, judge_model = used_platform, used_model
    if judge_platform == 'deepseek' and (judge_user_tokens + judge_system_tokens) > 61000:
        print(f"[WARN] 评判官token数超限，切换到bailian平台qwen-plus-latest模型")
        judge_platform = 'bailian'
        judge_model = 'qwen-plus-latest'
        judge_user_tokens = count_tokens(judge_user_prompt, platform=judge_platform, model_name=judge_model)
        judge_system_tokens = count_tokens(judge_system_prompt, platform=judge_platform, model_name=judge_model)
    print("\n===== 第2-1轮-评判官意见 =====")
    print("[system prompt]")
    print(judge_system_prompt.strip())
    print("\n[user prompt]")
    print(judge_user_prompt.strip())
    judge_suggestion = call_llm(judge_system_prompt, judge_user_prompt, judge_platform, judge_model)
    print("\n[大模型输出]")
    print(judge_suggestion.strip())
    # ========== 第二轮优化摘要 ==========
    optimize_user_prompt = NEWS_SUMMARY_OPTIMIZE_USER_PROMPT.format(
        system_prompt=NEWS_SUMMARY_SYSTEM_PROMPT.strip(),
        user_prompt=prompt.strip(),
        summary=summary.strip(),
        judge_suggestion=judge_suggestion.strip()
    )
    optimize_system_prompt = NEWS_SUMMARY_OPTIMIZE_SYSTEM_PROMPT.strip()
    optimize_platform, optimize_model = used_platform, used_model
    optimize_user_tokens = count_tokens(optimize_user_prompt, platform=optimize_platform, model_name=optimize_model)
    optimize_system_tokens = count_tokens(optimize_system_prompt, platform=optimize_platform, model_name=optimize_model)
    if optimize_platform == 'deepseek' and (optimize_user_tokens + optimize_system_tokens) > 61000:
        print(f"[WARN] 优化摘要token数超限，切换到bailian平台qwen-plus-latest模型")
        optimize_platform = 'bailian'
        optimize_model = 'qwen-plus-latest'
        optimize_user_tokens = count_tokens(optimize_user_prompt, platform=optimize_platform, model_name=optimize_model)
        optimize_system_tokens = count_tokens(optimize_system_prompt, platform=optimize_platform, model_name=optimize_model)
    print("\n===== 第2-2轮-优化后摘要 =====")
    print("[system prompt]")
    print(optimize_system_prompt.strip())
    print("\n[user prompt]")
    print(optimize_user_prompt.strip())
    improved_summary = call_llm(optimize_system_prompt, optimize_user_prompt, optimize_platform, optimize_model)
    print("\n[大模型输出]")
    print(improved_summary.strip())
    round2_entry = {
        "summary": improved_summary,
        "platform": optimize_platform,
        "model": optimize_model,
        "round": 2,
        "judge_suggestion": judge_suggestion
    }
    # ========== 第3轮热点追踪 ==========
    # 动态import write_to_mysql，避免循环依赖
    write_to_mysql = importlib.import_module('write_to_mysql')
    prev_date = (datetime.strptime(date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    prev_summary, prev_round = write_to_mysql.fetch_latest_summary(prev_date, keyword)
    round3_entry = None
    if prev_summary:
        hotspot_system_prompt = NEWS_SUMMARY_HOTSPOT_SYSTEM_PROMPT.strip()
        hotspot_user_prompt = NEWS_SUMMARY_HOTSPOT_USER_PROMPT.format(prev_summary=prev_summary.strip(), today_summary=improved_summary.strip())
        print("\n===== 第3轮-热点追踪总结 =====")
        print("[system prompt]")
        print(hotspot_system_prompt)
        print("\n[user prompt]")
        print(hotspot_user_prompt)
        hotspot_summary = call_llm(hotspot_system_prompt, hotspot_user_prompt, optimize_platform, optimize_model)
        print("\n[大模型输出]")
        print(hotspot_summary.strip())
        round3_entry = {
            "summary": hotspot_summary,
            "platform": optimize_platform,
            "model": optimize_model,
            "round": 3,
            "prev_date": prev_date
        }
    # ========== 保存所有轮次摘要 ==========
    all_rounds = [round1_entry, round2_entry]
    if round3_entry:
        all_rounds.append(round3_entry)
    summary_path = save_summary_multi_round(date, keyword, all_rounds, output_dir)
    # ========== 新增：保存所有轮次相关prompt到txt ==========
    optimization_prompt_path = os.path.join(output_dir, f"{date}_{keyword}_optimization_prompts.txt")
    with open(optimization_prompt_path, "w", encoding="utf-8") as f:
        import json as _json
        # 第1轮初稿
        round1_messages = [
            {"role": "system", "content": NEWS_SUMMARY_SYSTEM_PROMPT.strip()},
            {"role": "user", "content": prompt.strip()}
        ]
        f.write("【1-1 初稿 messages】\n")
        f.write(_json.dumps(round1_messages, ensure_ascii=False, indent=2) + "\n\n")
        f.write("【1-1 初稿输出（摘要）】\n")
        f.write(summary.strip() + "\n\n")
        # 第2-1轮 评判官
        judge_messages = [
            {"role": "system", "content": judge_system_prompt.strip()},
            {"role": "user", "content": judge_user_prompt.strip()}
        ]
        f.write("【2-1 评判官 messages】\n")
        f.write(_json.dumps(judge_messages, ensure_ascii=False, indent=2) + "\n\n")
        f.write("【2-1 评判官输出（建议）】\n")
        f.write(judge_suggestion.strip() + "\n\n")
        # 第2-2轮 优化
        optimize_messages = [
            {"role": "system", "content": optimize_system_prompt.strip()},
            {"role": "user", "content": optimize_user_prompt.strip()}
        ]
        f.write("【2-2 优化 messages】\n")
        f.write(_json.dumps(optimize_messages, ensure_ascii=False, indent=2) + "\n\n")
        f.write("【2-2 优化输出（最终摘要）】\n")
        f.write(improved_summary.strip() + "\n\n")
        # 第3轮热点追踪
        if round3_entry:
            hotspot_messages = [
                {"role": "system", "content": hotspot_system_prompt},
                {"role": "user", "content": hotspot_user_prompt}
            ]
            f.write("【3-1 热点追踪 messages】\n")
            f.write(_json.dumps(hotspot_messages, ensure_ascii=False, indent=2) + "\n\n")
            f.write("【3-1 热点追踪输出】\n")
            f.write(hotspot_summary.strip() + "\n")
    print(f"[INFO] 优化流程所有prompt及messages已保存: {optimization_prompt_path}")
    # 记录合并二级关键词情况
    merge_info = {
        'search_keywords': list(search_keywords_set)
    }
    append_log(date, keyword, optimize_model, prompt, summary_path, len(news_list), success=True, system_tokens=system_tokens, user_tokens=user_tokens, result_tokens=result_tokens, platform=optimize_platform, model_str=optimize_model, extra_info=merge_info)

# 新增：多轮摘要保存，保留所有轮次和相关信息
def save_summary_multi_round(date, keyword, round_entries, output_dir):
    filename = NEWS_SUMMARY_RESULT_FILENAME.format(date=date, keyword=keyword)
    out_path = os.path.join(output_dir, filename)
    # 读取旧文件，兼容旧格式
    if os.path.exists(out_path):
        with open(out_path, 'r', encoding='utf-8') as f:
            result = json.load(f)
    else:
        result = {"date": date, "keyword": keyword}
    if 'summaries' not in result:
        result['summaries'] = []
    # 追加所有新轮次
    result['summaries'].extend(round_entries)
    # 只保留 date, keyword, summaries
    result = {k: result[k] for k in ['date', 'keyword', 'summaries'] if k in result}
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[INFO] 多轮摘要已保存: {out_path}")
    return out_path

# 命令行入口
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', type=str, help='日期, 格式YYYY-MM-DD')
    parser.add_argument('--keyword', type=str, help='关键词')
    parser.add_argument('--model', type=str, default=None, help='模型名（deepseek/bailian等）')
    args = parser.parse_args()

    # 新增：无参数时自动批量处理昨天所有关键词
    if args.keyword is None:
        date_str = args.date or (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        for keyword in DEFAULT_KEYWORDS:
            print(f"\n{'='*40}\n=== 开始总结主关键词: {keyword} ===\n{'='*40}")
            try:
                main(date=date_str, keyword=keyword, model_name=args.model)
                print(f"{'='*40}\n=== 总结主关键词: {keyword} 完成 ===\n{'='*40}")
            except Exception as e:
                print(f"[ERROR] 总结关键词 {keyword} 失败: {e}")
        sys.exit(0)

    main(date=args.date, keyword=args.keyword, model_name=args.model) 