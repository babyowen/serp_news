import subprocess
import sys
import datetime
import os
from config import DEFAULT_KEYWORDS, SECONDARY_KEYWORDS

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

def get_all_keywords():
    all_keywords = set(DEFAULT_KEYWORDS)
    for sublist in SECONDARY_KEYWORDS.values():
        all_keywords.update(sublist)
    return list(all_keywords)

def main(date=None):
    all_keywords = get_all_keywords()
    print(f"[INFO] 本次批量处理关键词: {all_keywords}")
    # 步骤1：抓取API
    for kw in all_keywords:
        print(f"[INFO] [步骤1] 抓取API: {kw}")
        os.system(f'python fetch_and_filter.py {kw} {date}')
    # 步骤2：抓正文
    for kw in all_keywords:
        print(f"[INFO] [步骤2] 抓正文: {kw}")
        os.system(f'python fetch_content.py {kw} {date}')
    # 步骤3：评分
    for kw in all_keywords:
        print(f"[INFO] [步骤3] 评分: {kw}")
        os.system(f'python news_scorer.py {kw} {date}')
    print("[INFO] 全部关键词处理完成。开始自动总结主关键词...")
    # 步骤4：自动总结主关键词
    date_arg = f'--date {date}' if date else ''
    print("[INFO] [步骤4] 自动总结主关键词（合并二级关键词）")
    run_step(f'python news_summarizer.py {date_arg}', '自动总结主关键词', 'news_summarizer.py', '对所有主关键词进行总结，自动合并二级关键词新闻')
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