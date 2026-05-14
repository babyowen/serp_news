import argparse
import datetime
import os
import sys
import time

from dotenv import load_dotenv

from error_handler import (
    ErrorHandler,
    log_script_complete,
    log_script_start,
    setup_global_exception_handler,
    with_error_handling,
)
from icon_manager import safe_print
from news_region_utils import (
    call_region_llm,
    table_has_region_column,
    validate_region_table_name,
)
from db_utils import get_connection, get_table_name


setup_global_exception_handler()
load_dotenv()


def parse_date(value):
    if not value:
        return None
    value = value.strip()
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    return value


def get_conn():
    return get_connection(autocommit=True)


def build_query(table_name, keyword, date=None, date_from=None, date_to=None, limit=None):
    sql = [
        f"SELECT id, title, content, short_summary, fetchdate FROM {table_name}",
        "WHERE keyword=%s",
        "AND score>=3",
        "AND (region IS NULL OR region='')",
    ]
    params = [keyword]

    if date:
        sql.append("AND fetchdate=%s")
        params.append(date)
    else:
        if date_from:
            sql.append("AND fetchdate >= %s")
            params.append(date_from)
        if date_to:
            sql.append("AND fetchdate <= %s")
            params.append(date_to)

    sql.append("ORDER BY id ASC")
    if limit:
        sql.append("LIMIT %s")
        params.append(limit)

    return " ".join(sql), params


def log_run(table_name, keyword, total, success, fail, skip):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_path = os.path.join("output", "run_log.txt")
    msg = (
        f"\n[{now}]\n"
        f"执行程序: news_region_analyzer\n"
        f"[数据表] {table_name}\n"
        f"[关键词] {keyword}\n"
        f"[统计] 待处理: {total} 成功: {success} 失败: {fail} 跳过: {skip}\n"
        f"==============================\n"
    )
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(msg)


@with_error_handling("news_region_analyzer.py", "main")
def main():
    log_script_start("news_region_analyzer.py", sys.argv[1:])
    err = ErrorHandler()

    parser = argparse.ArgumentParser()
    parser.add_argument("--table", default=None, help="Target table name (default: MYSQL_TABLE env var)")
    parser.add_argument("--keyword", default="公积金", help="Only 公积金 is supported")
    parser.add_argument("--date", default=None, help="Single fetch date")
    parser.add_argument("--date-from", dest="date_from", default=None, help="Start fetch date")
    parser.add_argument("--date-to", dest="date_to", default=None, help="End fetch date")
    parser.add_argument("--limit", type=int, default=None, help="Max rows to process")
    args = parser.parse_args()

    if args.keyword != "公积金":
        raise ValueError("news_region_analyzer.py only supports keyword=公积金")

    table_name = validate_region_table_name(args.table or get_table_name())
    target_date = parse_date(args.date)
    date_from = parse_date(args.date_from)
    date_to = parse_date(args.date_to)

    if target_date and (date_from or date_to):
        raise ValueError("Use either --date or --date-from/--date-to")

    conn = get_conn()
    cur = conn.cursor()

    if not table_has_region_column(cur, table_name):
        raise ValueError(f"Table {table_name} is missing region column")

    sql, params = build_query(
        table_name,
        args.keyword,
        date=target_date,
        date_from=date_from,
        date_to=date_to,
        limit=args.limit,
    )
    cur.execute(sql, tuple(params))
    rows = cur.fetchall()

    total = len(rows)
    success = 0
    fail = 0
    skip = 0

    for rid, title, content, short_summary, fetchdate in rows:
        source_text = (short_summary or "").strip() or (content or "").strip()
        if not source_text:
            skip += 1
            safe_print(f"[跳过空内容] id={rid}")
            continue

        region = call_region_llm(title or "", source_text)
        if not region:
            skip += 1
            safe_print(f"[未识别地域] id={rid}")
            continue

        backoffs = [1, 2, 4]
        done = False
        for i in range(len(backoffs) + 1):
            try:
                conn.ping(reconnect=True)
                cur.execute(
                    f"UPDATE {table_name} SET region=%s WHERE id=%s",
                    (region, rid),
                )
                success += 1
                safe_print(f"[地域更新成功] id={rid} fetchdate={fetchdate} region={region}")
                done = True
                break
            except Exception as exc:
                msg = f"id={rid} 错误={type(exc).__name__}: {str(exc)}"
                safe_print(f"[更新失败] {msg}")
                err.log_step_failure(step_name="DB_UPDATE", error_msg=msg)
                err.log_error(
                    error_type="DB_UPDATE_ERROR",
                    error_msg=str(exc),
                    script_name="news_region_analyzer.py",
                    context={"id": rid, "table": table_name},
                )
                if i < len(backoffs):
                    time.sleep(backoffs[i])
                    conn = get_conn()
                    cur = conn.cursor()
        if not done:
            fail += 1
        time.sleep(0.2)

    log_run(table_name, args.keyword, total, success, fail, skip)
    log_script_complete(
        "news_region_analyzer.py",
        success=(fail == 0),
        message=f"{table_name} 公积金地域补录完成: 成功{success} 失败{fail} 跳过{skip}",
    )
    return fail == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
