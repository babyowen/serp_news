import os
import sys
os.environ["TOKENIZERS_PARALLELISM"] = "false"
import json
from datetime import datetime, timedelta
from config import (
    NEWS_SUMMARY_MODELS,
    NEWS_SUMMARY_SYSTEM_PROMPT,
    NEWS_SUMMARY_USER_PROMPT,
    NEWS_SUMMARY_RESULT_FILENAME,
    NEWS_SUMMARY_PLATFORM,
    NEWS_SUMMARY_MODEL,
    DEFAULT_KEYWORDS,
    SECONDARY_KEYWORDS
)
from openai import OpenAI
import tiktoken
import requests

# 加载deepseek官方tokenizer（仅deepseek平台时用）
deepseek_tokenizer = None
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

def load_scored_news(json_path, min_score=3):
    print(f"[INFO] 读取打分新闻文件: {json_path}")
    with open(json_path, 'r', encoding='utf-8') as f:
        news_list = json.load(f)
    filtered = [n for n in news_list if n.get('score', 0) >= min_score]
    print(f"[INFO] 3分及以上新闻数量: {len(filtered)}")
    return filtered

def build_news_list_prompt(news_list):
    lines = []
    for idx, news in enumerate(news_list, 1):
        title = news.get('title', '').strip().replace('\n', ' ')
        content = news.get('content', '').strip()
        score = news.get('score', '')
        lines.append(f"标题{idx}: {title}\n分数{idx}: {score}\n新闻{idx}: {content}")
    return '\n\n'.join(lines)

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

def save_summary(date, keyword, summary, output_dir, model_name=None, platform=None, model_str=None):
    filename = NEWS_SUMMARY_RESULT_FILENAME.format(date=date, keyword=keyword)
    out_path = os.path.join(output_dir, filename)
    # 读取旧文件
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

def append_log(date, keyword, model_name, prompt, summary_path, news_count, success=True, error_msg=None, system_tokens=None, user_tokens=None, result_tokens=None, platform=None, model_str=None, extra_info=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_path = os.path.join("output", "run_log.txt")
    log = (
        f"\n[🕒 {now}]\n"
        f"执行程序: news_summarizer\n"
        f"🔑 关键词: {keyword}\n"
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

def main(date=None, keyword=None, model_name=None, output_dir=None):
    if date is None:
        date = datetime.now().strftime('%Y-%m-%d')
    if keyword is None:
        raise ValueError('必须指定关键词')
    if output_dir is None:
        output_dir = os.path.join('output', date)
    scored_json = os.path.join(output_dir, f"{date}_{keyword}_scored.json")
    news_list = []
    # 1. 先加载主关键词新闻
    if os.path.exists(scored_json):
        news_list += load_scored_news(scored_json, min_score=3)
    # 2. 加载所有二级关键词新闻
    secondary_keywords_used = []
    for sub_kw in SECONDARY_KEYWORDS.get(keyword, []):
        sub_json = os.path.join(output_dir, f"{date}_{sub_kw}_scored.json")
        if os.path.exists(sub_json):
            news_list += load_scored_news(sub_json, min_score=3)
            secondary_keywords_used.append(sub_kw)
    # 3. 合并去重（按link去重）
    unique_links = set()
    deduped_news = []
    for news in news_list:
        link = news.get('link')
        if link and link not in unique_links:
            deduped_news.append(news)
            unique_links.add(link)
    news_list = deduped_news
    # 打印合并的二级关键词
    if secondary_keywords_used:
        print(f"[INFO] 本次合并的二级关键词: {secondary_keywords_used}")
    else:
        print("[INFO] 本次未合并任何二级关键词。")
    if not news_list:
        print("[INFO] 无3分及以上新闻，无需总结。")
        append_log(date, keyword, model_name, '', '', 0, success=True, extra_info={'secondary_keywords': secondary_keywords_used})
        return
    # 后续流程保持不变
    # token超限多级预判
    platform = NEWS_SUMMARY_PLATFORM
    model = NEWS_SUMMARY_MODEL
    news_list_str = build_news_list_prompt(news_list)
    user_prompt = NEWS_SUMMARY_USER_PROMPT.format(news_list=news_list_str, keyword=keyword)
    user_tokens = count_tokens(user_prompt, platform=platform, model_name=model)
    switched = False
    # 新增：如有二级关键词，保存完整prompt到txt
    if secondary_keywords_used:
        prompt_txt_path = os.path.join('output', f'{date}_{keyword}_summary_prompt.txt')
        with open(prompt_txt_path, 'w', encoding='utf-8') as f:
            f.write(user_prompt)
        print(f"[INFO] 已保存完整prompt到: {prompt_txt_path}")
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
    summary_path = save_summary(date, keyword, summary, output_dir, model_name=used_model, platform=used_platform, model_str=used_model)
    # 记录合并二级关键词情况
    merge_info = {
        'secondary_keywords': secondary_keywords_used,
        'merge_status': f"合并了{len(secondary_keywords_used)}个二级关键词: {secondary_keywords_used}" if secondary_keywords_used else "未合并二级关键词"
    }
    append_log(date, keyword, used_model, prompt, summary_path, len(news_list), success=True, system_tokens=system_tokens, user_tokens=user_tokens, result_tokens=result_tokens, platform=used_platform, model_str=used_model, extra_info=merge_info)

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