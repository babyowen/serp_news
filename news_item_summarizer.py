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
from news_region_utils import (
    call_region_llm,
    call_summary_and_region_llm,
    table_has_region_column,
    validate_region_table_name,
)
from db_utils import get_connection, get_table_name
from llm_client_pool import get_pool

setup_global_exception_handler()
load_dotenv()

_pool = get_pool()

def parse_date(arg):
    if not arg:
        return (datetime.date.today() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    s = arg.strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s

def call_llm(title, content, max_retries=3):
    user_prompt = NEWS_ITEM_SUMMARY_USER_PROMPT_500.format(title=title.strip(), content=content.strip())
    backoffs = [5, 10, 20]
    for i in range(max_retries):
        client = _pool.get_client(DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL)
        try:
            resp = client.chat.completions.create(
                model='deepseek-v4-flash',
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
    return get_connection(autocommit=True)

def log_run(date, table_name, total, success, fail, skip):
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_path = os.environ.get("RUN_LOG_PATH", os.path.join('output', 'run_log.txt'))
    msg = f"[{now}] 单条摘要 {date}: 待处理{total}, 成功{success}, 失败{fail}, 跳过{skip}\n"
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(msg)


def update_summary_fields(cur, table_name, rid, summary, region):
    cur.execute(
        f"UPDATE {table_name} SET short_summary=%s, region=%s WHERE id=%s",
        (summary, region, rid),
    )


def update_summary_only(cur, table_name, rid, summary):
    cur.execute(
        f"UPDATE {table_name} SET short_summary=%s WHERE id=%s",
        (summary, rid),
    )


def update_region_only(cur, table_name, rid, region):
    cur.execute(
        f"UPDATE {table_name} SET region=%s WHERE id=%s",
        (region, rid),
    )

@with_error_handling("news_item_summarizer.py", "main")
def main():
    log_script_start("news_item_summarizer.py", sys.argv[1:])
    err = ErrorHandler()
    success_all = True
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument('date', nargs='?', default=None)
        parser.add_argument('--keyword', type=str, default=None, help='Filter by keyword')
        parser.add_argument('--table', type=str, default=None, help='Target table (default: MYSQL_TABLE env var or scored_news)')
        args = parser.parse_args()
        date = parse_date(args.date)
        table_name = validate_region_table_name(args.table or get_table_name())
        conn = get_conn()
        cur = conn.cursor()

        has_region_column = table_has_region_column(cur, table_name)
        if args.keyword in (None, "公积金") and not has_region_column:
            raise ValueError(f"Table {table_name} is missing region column")

        sql = f"SELECT id, title, content, keyword FROM {table_name} WHERE fetchdate=%s AND score>=3 AND (short_summary IS NULL OR short_summary='')"
        params = [date]
        
        if args.keyword:
            sql += " AND keyword=%s"
            params.append(args.keyword)
            
        cur.execute(sql, tuple(params))
        initial_rows = cur.fetchall()
        total = len(initial_rows)
        success = 0
        fail = 0
        skip = 0
        cycles = 0
        while True:
            conn.ping(reconnect=True)
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
            if not rows:
                break
            for rid, title, content, keyword in rows:
                if not content or not str(content).strip():
                    skip += 1
                    continue
                text = str(content).strip()
                is_gjj = keyword == "公积金"
                if len(text) <= 500:
                    backoffs = [1, 2, 4]
                    done = False
                    region = None
                    if is_gjj:
                        region = call_region_llm(title or '', text)
                    for i in range(len(backoffs) + 1):
                        try:
                            conn.ping(reconnect=True)
                            if is_gjj:
                                update_summary_fields(cur, table_name, rid, text, region)
                            else:
                                update_summary_only(cur, table_name, rid, text)
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
                if is_gjj:
                    result = call_summary_and_region_llm(title or '', content or '')
                    summary = result.get("short_summary") if result else None
                    region = result.get("region") if result else None
                else:
                    summary = call_llm(title or '', content or '')
                    region = None
                if summary:
                    backoffs = [1, 2, 4]
                    done = False
                    for i in range(len(backoffs) + 1):
                        try:
                            conn.ping(reconnect=True)
                            if is_gjj:
                                update_summary_fields(cur, table_name, rid, summary, region)
                            else:
                                update_summary_only(cur, table_name, rid, summary)
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
        log_run(date, table_name, total, success, fail, skip)
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
