import subprocess
import sys
import datetime
import os
import json
from config import DEFAULT_KEYWORDS, SEARCH_KEYWORDS

def run_step(cmd, step_name, script_name=None, desc=None):
    if script_name or desc:
        print(f"\n------ 即将执行: {script_name or ''} ------")
        if desc:
            print(f"功能说明: {desc}")
        print(f"-----------------------------------\n")
    print(f"\n==============================")
    print(f"🚩 开始执行步骤: {step_name}")
    print(f"==============================")
    try:
        # 实时输出子进程日志，遇到错误直接抛出异常
        result = subprocess.run(cmd, shell=True, check=True)
        print(f"==============================")
        print(f"✅ 步骤完成: {step_name}")
        print(f"==============================\n")
    except subprocess.CalledProcessError as e:
        print(f"[❌ 错误] {step_name} 执行失败: {e}")
        print(f"==============================\n")
        sys.exit(1)

def all_news_has_content(json_path):
    """判断json文件中所有新闻条目都已存在非空content字段"""
    if not os.path.exists(json_path):
        return False
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            news_list = json.load(f)
        if not isinstance(news_list, list) or not news_list:
            return False
        return all(item.get('content') and len(str(item.get('content')).strip()) > 0 for item in news_list)
    except Exception as e:
        print(f"[WARN] 检查content时读取失败: {json_path}, 错误: {e}")
        return False

def write_skip_log(keyword, reason, file_path):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_path = os.path.join("output", "run_log.txt")
    skip_log = (
        f"[🕒 {now}]\n[SKIP] 跳过关键词: {keyword}\n原因: {reason} {file_path}\n==============================\n"
    )
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(skip_log)

def main(date=None):
    # 如果未指定日期，自动赋值为昨天日期
    if not date:
        date = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"[INFO] 本次批量处理主关键词: {DEFAULT_KEYWORDS}")
    # 输出所有搜索关键词
    all_search_keywords = set()
    for main_kw in DEFAULT_KEYWORDS:
        all_search_keywords.update(SEARCH_KEYWORDS[main_kw])
    print(f"[INFO] 本次所有搜索关键词: {sorted(all_search_keywords)}")
    # 步骤1：抓取API
    for main_kw in DEFAULT_KEYWORDS:
        merged_file = f"output/{date}/{date}_{main_kw}.json"
        if os.path.exists(merged_file):
            print(f"[INFO] {merged_file} 已存在，跳过 {main_kw}")
            write_skip_log(main_kw, "已存在，新闻列表已抓取", merged_file)
            continue
        print(f"[INFO] [步骤1] 抓取API: {main_kw}")
        all_news = []
        for search_kw in SEARCH_KEYWORDS[main_kw]:
            # 调用采集脚本，采集结果临时存储
            tmp_file = f"output/{date}/tmp_{date}_{main_kw}_{search_kw}.json"
            os.system(f'python fetch_and_filter.py "{search_kw}" {date} --output "{tmp_file}"')
            # 读取采集结果，添加 search_keyword 字段
            if os.path.exists(tmp_file):
                try:
                    with open(tmp_file, 'r', encoding='utf-8') as f:
                        news_list = json.load(f)
                    for item in news_list:
                        item['search_keyword'] = search_kw
                        item['main_keyword'] = main_kw
                        item['keyword'] = main_kw  # 确保keyword字段为主关键词
                    all_news.extend(news_list)
                except Exception as e:
                    print(f"[WARN] 读取临时采集文件失败: {tmp_file}, 错误: {e}")
                os.remove(tmp_file)
        # 合并去重（按 title+link）
        unique = {}
        for item in all_news:
            key = (item.get('title', '').strip(), item.get('link', '').strip())
            if key not in unique:
                unique[key] = item
        deduped_news = list(unique.values())
        os.makedirs(f"output/{date}", exist_ok=True)
        with open(merged_file, 'w', encoding='utf-8') as f:
            json.dump(deduped_news, f, ensure_ascii=False, indent=2)
        print(f"[INFO] 合并去重后已保存: {merged_file}，数量：{len(deduped_news)}")
    # 步骤2：抓正文
    for kw in DEFAULT_KEYWORDS:
        kw = kw.strip()
        merged_file = f"output/{date}/{date}_{kw}.json"
        if not os.path.exists(merged_file):
            print(f"[WARN] {merged_file} 不存在，无法抓正文，跳过 {kw}")
            write_skip_log(kw, "新闻列表文件不存在，无法抓正文", merged_file)
            continue
        if all_news_has_content(merged_file):
            print(f"[INFO] {merged_file} 所有新闻正文已抓取，跳过 {kw}")
            write_skip_log(kw, "所有新闻正文已抓取", merged_file)
            continue
        print(f"[INFO] [步骤2] 抓正文: {kw}")
        os.system(f'python fetch_content.py {kw} {date}')
    # 步骤3：评分
    for kw in DEFAULT_KEYWORDS:
        kw = kw.strip()
        merged_file = f"output/{date}/{date}_{kw}.json"
        scored_file = f"output/{date}/{date}_{kw}_scored.json"
        if not os.path.exists(merged_file):
            print(f"[WARN] {merged_file} 不存在，无法评分，跳过 {kw}")
            write_skip_log(kw, "新闻列表文件不存在，无法评分", merged_file)
            continue
        if os.path.exists(scored_file):
            print(f"[INFO] {scored_file} 已存在，跳过 {kw}")
            write_skip_log(kw, "已存在，已完成打分", scored_file)
            continue
        print(f"[INFO] [步骤3] 评分: {kw}")
        os.system(f'python news_scorer.py {kw} {date}')
    print("[INFO] 全部关键词处理完成。开始自动总结主关键词...")
    # 步骤4：自动总结主关键词
    date_arg = f'--date {date}' if date else ''
    print("[INFO] [步骤4] 自动总结主关键词")
    run_step(f'python news_summarizer.py {date_arg}', '自动总结主关键词', 'news_summarizer.py', '对所有主关键词进行总结，自动合并搜索关键词新闻')
    # 步骤5：自动写入数据库
    print("[INFO] [步骤5] 自动写入数据库")
    run_step(f'python write_to_mysql.py {date_arg}', '自动写入数据库', 'write_to_mysql.py', '将scored和summary结果写入数据库')
    print("[INFO] 全部流程已自动完成！")

if __name__ == "__main__":
    import sys
    date = None
    if len(sys.argv) > 1:
        date = sys.argv[1]
    main(date) 