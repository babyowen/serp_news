import os
import sys
import time
import datetime
import argparse
import json
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
    call_region_and_business_type_llm,
    call_summary_and_region_llm,
    table_has_region_column,
    validate_region_table_name,
)
from db_utils import get_connection, get_table_name
from llm_client_pool import get_pool
from runtime_config import value, model_credentials, model_arguments
from batch_config import prepare_batch
from news_business_type_utils import (
    build_business_type_catalog,
    load_business_type_aliases,
    table_has_business_types_column,
)

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
    user_prompt = value("NEWS_ITEM_SUMMARY_USER_PROMPT_500").format(title=title.strip(), content=content.strip())
    backoffs = [5, 10, 20]
    for i in range(max_retries):
        client = _pool.get_client(*model_credentials("item_summarizer"))
        try:
            resp = client.chat.completions.create(
                **model_arguments("item_summarizer"),
                messages=[
                    {"role": "system", "content": value("NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500")},
                    {"role": "user", "content": user_prompt}
                ]
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


def update_summary_fields(cur, table_name, rid, summary, region, business_types):
    cur.execute(
        f"UPDATE {table_name} SET short_summary=%s, region=%s, business_types=%s WHERE id=%s",
        (summary, region, json.dumps(business_types, ensure_ascii=False), rid),
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


def add_business_types_to_catalog(catalog, business_types):
    for item in business_types:
        labels = catalog.setdefault(item["level1"], [])
        if item["level2"] not in labels:
            labels.append(item["level2"])
            labels.sort()

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
        parser.add_argument('--adopt-existing-config', action='store_true')
        args = parser.parse_args()
        date = parse_date(args.date)
        _, batch_keywords = prepare_batch(date, args.keyword, args.adopt_existing_config)
        table_name = validate_region_table_name(args.table or get_table_name())
        conn = get_conn()
        cur = conn.cursor()

        has_region_column = table_has_region_column(cur, table_name)
        if args.keyword == "公积金" and not has_region_column:
            raise ValueError(f"Table {table_name} is missing region column")
        has_business_types_column = table_has_business_types_column(cur, table_name)
        if args.keyword == "公积金" and not has_business_types_column:
            raise ValueError(f"Table {table_name} is missing business_types column; run news_business_type_schema.py first")

        gjj_annotation_ready = has_region_column and has_business_types_column
        aliases = load_business_type_aliases(cur, table_name) if gjj_annotation_ready else {}
        business_type_catalog = build_business_type_catalog(cur, table_name, aliases) if gjj_annotation_ready else {}

        sql = f"SELECT id, title, content, keyword FROM {table_name} WHERE fetchdate=%s AND score>=3 AND (short_summary IS NULL OR short_summary='')"
        # Scope the database work to the same topics whose revision was pinned.
        # Historical rows for removed/unbound topics require an explicit run.
        # Using another revision in this directory requires per-topic resumes;
        # use a separate output directory when a full-date rerun is needed.
        sql += " AND keyword IN (" + ",".join(["%s"] * len(batch_keywords)) + ")"
        params = [date, *batch_keywords]
            
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
                annotate_gjj = is_gjj and gjj_annotation_ready
                if len(text) <= 500:
                    backoffs = [1, 2, 4]
                    done = False
                    region = None
                    business_types = []
                    if annotate_gjj:
                        result = call_region_and_business_type_llm(
                            title or '', text, business_type_catalog, aliases
                        )
                        if not result:
                            fail += 1
                            safe_print(f"[标注生成失败] id={rid}")
                            time.sleep(0.2)
                            continue
                        region = result.get("region")
                        business_types = result.get("business_types", [])
                    for i in range(len(backoffs) + 1):
                        try:
                            conn.ping(reconnect=True)
                            if annotate_gjj:
                                update_summary_fields(cur, table_name, rid, text, region, business_types)
                            else:
                                update_summary_only(cur, table_name, rid, text)
                            success += 1
                            if annotate_gjj:
                                add_business_types_to_catalog(business_type_catalog, business_types)
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
                if annotate_gjj:
                    result = call_summary_and_region_llm(
                        title or '', content or '', business_type_catalog, aliases
                    )
                    summary = result.get("short_summary") if result else None
                    region = result.get("region") if result else None
                    business_types = result.get("business_types", []) if result else []
                else:
                    summary = call_llm(title or '', content or '')
                    region = None
                    business_types = []
                if summary:
                    backoffs = [1, 2, 4]
                    done = False
                    for i in range(len(backoffs) + 1):
                        try:
                            conn.ping(reconnect=True)
                            if annotate_gjj:
                                update_summary_fields(cur, table_name, rid, summary, region, business_types)
                            else:
                                update_summary_only(cur, table_name, rid, summary)
                            success += 1
                            if annotate_gjj:
                                add_business_types_to_catalog(business_type_catalog, business_types)
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
