import os
import sys
import time
import datetime
import argparse
import pymysql
from dotenv import load_dotenv
from openai import OpenAI
from error_handler import (
    setup_global_exception_handler,
    with_error_handling,
    log_script_start,
    log_script_complete,
    ErrorHandler
)
from icon_manager import safe_print
from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500,
    NEWS_ITEM_SUMMARY_USER_PROMPT_500
)

setup_global_exception_handler()
load_dotenv()

MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_PORT = int(os.getenv('MYSQL_PORT', 3306))
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')
MYSQL_DB = os.getenv('MYSQL_DB')

class DSClientPool:
    def __init__(self):
        self.client = None
        self.usage = 0
        self.max_usage = 200
    def get(self):
        if self.client is None or self.usage >= self.max_usage:
            self.client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
            self.usage = 0
        self.usage += 1
        return self.client

_pool = DSClientPool()

def parse_date(arg):
    if not arg:
        return (datetime.date.today() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    s = arg.strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s

def call_llm(title, content, max_retries=3):
    user_prompt = NEWS_ITEM_SUMMARY_USER_PROMPT_500.format(title=title.strip(), content=content.strip())
    safe_print(f"[模型] deepseek-chat @ {DEEPSEEK_BASE_URL}")
    safe_print(f"【LLM system前120字】 {NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500.strip()[:120].replace(chr(10),' ')}")
    safe_print(f"【LLM user前200字】 {user_prompt.strip()[:200].replace(chr(10),' ')}")
    backoffs = [5, 10, 20]
    for i in range(max_retries):
        client = _pool.get()
        try:
            resp = client.chat.completions.create(
                model='deepseek-chat',
                messages=[
                    {"role": "system", "content": NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500},
                    {"role": "user", "content": user_prompt}
                ],
                stream=False,
                timeout=60
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            safe_print(f"[LLM错误] {str(e)}")
            if i < max_retries - 1:
                time.sleep(backoffs[i])
    return None

def get_conn():
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        charset='utf8mb4',
        autocommit=True
    )

def log_run(date, total, success, fail, skip):
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_path = os.path.join('output', 'run_log.txt')
    msg = (
        f"\n[{now}]\n"
        f"执行程序: news_item_summarizer\n"
        f"[抓取日期] {date}\n"
        f"[统计] 待处理: {total} 成功: {success} 失败: {fail} 跳过: {skip}\n"
        f"==============================\n"
    )
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(msg)

@with_error_handling("news_item_summarizer.py", "main")
def main():
    log_script_start("news_item_summarizer.py", sys.argv[1:])
    err = ErrorHandler()
    success_all = True
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument('date', nargs='?', default=None)
        args = parser.parse_args()
        date = parse_date(args.date)
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, title, content FROM scored_news WHERE fetchdate=%s AND score>=3 AND (short_summary IS NULL OR short_summary='')",
            (date,)
        )
        initial_rows = cur.fetchall()
        total = len(initial_rows)
        success = 0
        fail = 0
        skip = 0
        cycles = 0
        while True:
            conn.ping(reconnect=True)
            cur.execute(
                "SELECT id, title, content FROM scored_news WHERE fetchdate=%s AND score>=3 AND (short_summary IS NULL OR short_summary='')",
                (date,)
            )
            rows = cur.fetchall()
            if not rows:
                break
            for rid, title, content in rows:
                if not content or not str(content).strip():
                    skip += 1
                    continue
                text = str(content).strip()
                if len(text) <= 500:
                    backoffs = [1, 2, 4]
                    done = False
                    for i in range(len(backoffs) + 1):
                        try:
                            conn.ping(reconnect=True)
                            cur.execute(
                                "UPDATE scored_news SET short_summary=%s WHERE id=%s",
                                (text, rid)
                            )
                            success += 1
                            safe_print(f"[直接写原文] id={rid} 字数={len(text)}")
                            done = True
                            break
                        except Exception as e:
                            msg = f"id={rid} 错误={type(e).__name__}: {str(e)}"
                            safe_print(f"[更新失败] {msg}")
                            err.log_step_failure(step_name="DB_UPDATE", error_msg=msg)
                            err.log_error(
                                error_type="DB_UPDATE_ERROR",
                                error_msg=str(e),
                                script_name="news_item_summarizer.py",
                                context={"id": rid}
                            )
                            if i < len(backoffs):
                                time.sleep(backoffs[i])
                                conn = get_conn()
                                cur = conn.cursor()
                    if not done:
                        fail += 1
                    continue
                summary = call_llm(title or '', content or '')
                if summary:
                    backoffs = [1, 2, 4]
                    done = False
                    for i in range(len(backoffs) + 1):
                        try:
                            conn.ping(reconnect=True)
                            cur.execute(
                                "UPDATE scored_news SET short_summary=%s WHERE id=%s",
                                (summary, rid)
                            )
                            success += 1
                            safe_print(f"[更新成功] id={rid}")
                            done = True
                            break
                        except Exception as e:
                            msg = f"id={rid} 错误={type(e).__name__}: {str(e)}"
                            safe_print(f"[更新失败] {msg}")
                            err.log_step_failure(step_name="DB_UPDATE", error_msg=msg)
                            err.log_error(
                                error_type="DB_UPDATE_ERROR",
                                error_msg=str(e),
                                script_name="news_item_summarizer.py",
                                context={"id": rid}
                            )
                            if i < len(backoffs):
                                time.sleep(backoffs[i])
                                conn = get_conn()
                                cur = conn.cursor()
                    if not done:
                        fail += 1
                else:
                    fail += 1
                    safe_print(f"[生成失败] id={rid}")
                time.sleep(0.2)
            cycles += 1
            if cycles >= 3:
                break
        log_run(date, total, success, fail, skip)
        log_script_complete("news_item_summarizer.py", success=True, message=f"抓取日期 {date} 完成: 成功{success} 失败{fail} 跳过{skip}")
        return True
    except Exception as e:
        success_all = False
        err.log_error(
            error_type="MAIN_FUNCTION_ERROR",
            error_msg=str(e),
            script_name="news_item_summarizer.py"
        )
        log_script_complete("news_item_summarizer.py", success=False, message=str(e))
        return False

if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
