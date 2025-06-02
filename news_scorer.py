import json
import sys
import os
from datetime import datetime, timedelta
from openai import OpenAI
from config import NEWS_SCORE_PROMPT, DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, DEFAULT_KEYWORD, NEWS_SCORE_SYSTEM_MSG, DEFAULT_KEYWORDS
import argparse

def score_news(title: str, content: str, keyword: str) -> int:
    prompt = NEWS_SCORE_PROMPT.format(keyword=keyword, title=title, content=content)
    print("\n===== 送给大模型的内容 =====")
    print(f"[system] {NEWS_SCORE_SYSTEM_MSG}")
    print(f"[user] {prompt}")
    print("==========================\n")
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    response = client.chat.completions.create(
        model='deepseek-reasoner',
        messages=[
            {"role": "system", "content": NEWS_SCORE_SYSTEM_MSG},
            {"role": "user", "content": prompt}
        ],
        stream=False,
        temperature=1
    )
    score_str = response.choices[0].message.content.strip()
    try:
        score = int(score_str[0])  # 只取第一个数字
    except Exception:
        score = 0
    return score

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
    for news in unique_news_list:
        title = news.get("title", "")
        content = news.get("content", "")
        wordcount = news.get("wordcount", None)
        # 新增：如果 wordcount 为 0，直接打 0 分
        if wordcount == 0:
            score = 0
        else:
            score = score_news(title, content, keyword)
        news_with_score = dict(news)
        news_with_score["score"] = score  # 用英文key
        results.append(news_with_score)
        score_counter[score] = score_counter.get(score, 0) + 1
        print(f"标题: {title}\n分数: {score}\n")
    return results, score_counter, len(unique_news_list)

def write_scored_json(results, json_path):
    # 按评分降序排列
    results_sorted = sorted(results, key=lambda x: x.get("score", 0), reverse=True)
    base, ext = os.path.splitext(json_path)
    new_path = base + "_scored" + ext
    with open(new_path, "w", encoding="utf-8") as f:
        json.dump(results_sorted, f, ensure_ascii=False, indent=2)
    return new_path

def append_log(keyword, json_path, total, score_counter, scored_count, scored_json_path, results=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_path = os.path.join("output", "run_log.txt")
    score_line = " ".join([f"{i}分: {score_counter.get(i,0)}" for i in range(6)])
    # emoji_map = ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    emoji_map = ["0分", "1分", "2分", "3分", "4分", "5分"]
    emoji_score_line = " ".join([f"{emoji_map[i]}:{score_counter.get(i,0)}" for i in range(6)])
    # 新增：统计3分及以上新闻正文总字数
    total_wordcount_3plus = 0
    if results is not None:
        for news in results:
            if news.get("score", 0) >= 3:
                total_wordcount_3plus += len(news.get("content", ""))
    log = (
        f"\n[🕒 {now}]\n"
        f"执行程序: ai评分\n"
        f"🔑 关键词: {keyword}\n"
        f"📄 原json文件: {json_path}\n"
        f"🆕 评分结果文件: {scored_json_path}\n"
        f"📊 新闻总数: {total}\n"
        f"✅ 完成评分: {scored_count}\n"
        f"{emoji_score_line}\n"
    )
    if results is not None:
        log += f"📝 3分及以上新闻正文总字数: {total_wordcount_3plus}\n"
    log += f"==============================\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(log)
    # 新增：屏幕输出日志文件位置
    print(f"评分完成，日志已写入: {log_path}")
    print(f"评分结果文件: {scored_json_path}")

def get_yesterday_str():
    yesterday = datetime.now() - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")

def main():
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
            print(f"测试模式：\n新闻标题: {title}\n新闻正文: {content}\n关键词: {keyword}")
            score = score_news(title, content, keyword)
            print(f"评分结果: {score}")
        except Exception as e:
            print(f"测试模式解析失败: {e}")
        return

    # 批量模式：无参数时遍历 DEFAULT_KEYWORDS
    if args.keyword is None:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        for keyword in DEFAULT_KEYWORDS:
            json_path = os.path.join("output", date_str, f"{date_str}_{keyword}.json")
            scored_json_path = os.path.splitext(json_path)[0] + "_scored.json"
            print(f"\n=== 开始处理关键词: {keyword} ===")
            if not os.path.exists(json_path):
                print(f"未找到文件: {json_path}")
                continue
            # 优先判断_scored.json是否已存在
            if os.path.exists(scored_json_path):
                print(f"已检测到 {scored_json_path} 已存在，跳过。")
                continue
            # 检查是否已打分（兼容旧流程）
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    news_list = json.load(f)
                already_scored = any("score" in news for news in news_list)
            except Exception as e:
                print(f"读取文件失败: {json_path}, 错误: {e}")
                continue
            if already_scored:
                print(f"已检测到 {json_path} 已经打分，跳过。")
                continue
            results, score_counter, total = batch_score_news(json_path, keyword)
            scored_json_path = write_scored_json(results, json_path)
            append_log(keyword, json_path, total, score_counter, len(results), scored_json_path, results)
        return

    # 单关键词模式
    keyword = args.keyword or DEFAULT_KEYWORD
    date_str = args.date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    json_path = os.path.join("output", date_str, f"{date_str}_{keyword}.json")
    if not os.path.exists(json_path):
        print(f"未找到文件: {json_path}")
        return
    results, score_counter, total = batch_score_news(json_path, keyword)
    scored_json_path = write_scored_json(results, json_path)
    append_log(keyword, json_path, total, score_counter, len(results), scored_json_path, results)

if __name__ == "__main__":
    main() 