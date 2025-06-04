import subprocess
import sys
import datetime
import os
from config import DEFAULT_KEYWORDS

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

def main():
    # 计算昨天日期
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    print("\n==============================")
    print(f"🟢 新闻自动化主流程启动，处理日期: {yesterday}")
    print("==============================\n")
    # 步骤1：抓取API并筛选
    run_step(
        f"python fetch_and_filter.py {yesterday}",
        "抓取API并筛选",
        script_name="fetch_and_filter.py",
        desc="抓取各新闻API并筛选、去重，保存为json文件"
    )
    # 步骤2：抓取正文（对每个关键词循环）
    for keyword in DEFAULT_KEYWORDS:
        run_step(
            f"python fetch_content.py {keyword} {yesterday}",
            f"抓取新闻正文: {keyword}",
            script_name="fetch_content.py",
            desc=f"为关键词 {keyword} 的每条新闻抓取正文内容，写入json"
        )
    # 步骤3：正文打分
    run_step(
        f"python news_scorer.py",
        "正文打分",
        script_name="news_scorer.py",
        desc="对每条新闻正文进行AI打分，生成_scored.json"
    )
    # 步骤4：总结
    run_step(
        f"python news_summarizer.py",
        "新闻总结",
        script_name="news_summarizer.py",
        desc="对每个关键词的高分新闻进行AI总结，生成_summary.json"
    )
    # 步骤5：写入数据库
    run_step(
        f"python write_to_mysql.py --date {yesterday}",
        "写入数据库",
        script_name="write_to_mysql.py",
        desc="将_scored.json和_summary.json数据批量写入MySQL数据库"
    )
    print("\n==============================")
    print("🎉 全部流程执行完毕！请检查 output/run_log.txt 或数据库查看结果。")
    print("==============================\n")

if __name__ == "__main__":
    main() 