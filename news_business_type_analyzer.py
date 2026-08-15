"""Backfill business types for high-score housing-fund news."""

import argparse
import datetime
import json
import os
import sys
import time

from dotenv import load_dotenv

from db_utils import get_connection, get_table_name
from error_handler import ErrorHandler, log_script_complete, log_script_start, setup_global_exception_handler, with_error_handling
from icon_manager import safe_print
from news_business_type_utils import (
    build_business_type_catalog,
    load_business_type_aliases,
    table_has_business_types_column,
    validate_business_type_table_name,
)
from news_region_utils import call_business_type_llm


setup_global_exception_handler()
load_dotenv()


def parse_date(value):
    if not value:
        return None
    value = value.strip()
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    return value


def build_query(table_name, date=None, date_from=None, date_to=None, limit=None, force=False):
    sql = [
        f"SELECT id, title, content, short_summary, fetchdate FROM {table_name}",
        "WHERE keyword=%s",
        "AND score>=3",
    ]
    params = ["公积金"]
    if not force:
        sql.append("AND business_types IS NULL")
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


def log_run(table_name, total, success, fail, skip):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_path = os.environ.get("RUN_LOG_PATH", os.path.join("output", "run_log.txt"))
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{now}] 公积金业务类型补标 {table_name}: 待处理{total}, 成功{success}, 失败{fail}, 跳过{skip}\n")


@with_error_handling("news_business_type_analyzer.py", "main")
def main():
    log_script_start("news_business_type_analyzer.py", sys.argv[1:])
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", default=None, help="Target table (default: MYSQL_TABLE)")
    parser.add_argument("--date", default=None, help="Single fetch date")
    parser.add_argument("--date-from", dest="date_from", default=None, help="Start fetch date")
    parser.add_argument("--date-to", dest="date_to", default=None, help="End fetch date")
    parser.add_argument("--limit", type=int, default=None, help="Max rows to process")
    parser.add_argument("--force", action="store_true", help="Reclassify records that already have business_types")
    args = parser.parse_args()

    table_name = validate_business_type_table_name(args.table or get_table_name())
    target_date = parse_date(args.date)
    date_from = parse_date(args.date_from)
    date_to = parse_date(args.date_to)
    if target_date and (date_from or date_to):
        raise ValueError("Use either --date or --date-from/--date-to")
    if args.limit is not None and args.limit <= 0:
        raise ValueError("--limit must be positive")

    conn = get_connection(autocommit=True)
    cur = conn.cursor()
    if not table_has_business_types_column(cur, table_name):
        raise ValueError(f"Table {table_name} is missing business_types column; run news_business_type_schema.py first")

    aliases = load_business_type_aliases(cur, table_name)
    catalog = build_business_type_catalog(cur, table_name, aliases)
    sql, params = build_query(table_name, target_date, date_from, date_to, args.limit, args.force)
    cur.execute(sql, tuple(params))
    rows = cur.fetchall()
    success = fail = skip = 0
    err = ErrorHandler()

    for news_id, title, content, short_summary, fetchdate in rows:
        source_text = (short_summary or "").strip() or (content or "").strip()
        if not source_text:
            skip += 1
            safe_print(f"[跳过空内容] id={news_id}")
            continue
        business_types = call_business_type_llm(title or "", source_text, catalog, aliases)
        if business_types is None:
            fail += 1
            safe_print(f"[标注生成失败] id={news_id}")
            continue

        done = False
        for attempt, delay in enumerate([1, 2, 4, None]):
            try:
                conn.ping(reconnect=True)
                cur.execute(
                    f"UPDATE {table_name} SET business_types=%s WHERE id=%s",
                    (json.dumps(business_types, ensure_ascii=False), news_id),
                )
                success += 1
                for item in business_types:
                    labels = catalog.setdefault(item["level1"], [])
                    if item["level2"] not in labels:
                        labels.append(item["level2"])
                        labels.sort()
                safe_print(f"[业务类型更新成功] id={news_id} fetchdate={fetchdate}")
                done = True
                break
            except Exception as exc:
                err.log_error(
                    "DB_UPDATE_ERROR",
                    str(exc),
                    script_name="news_business_type_analyzer.py",
                    context={"id": news_id},
                )
                if delay is not None:
                    time.sleep(delay)
                    conn = get_connection(autocommit=True)
                    cur = conn.cursor()
        if not done:
            fail += 1
        time.sleep(0.2)

    log_run(table_name, len(rows), success, fail, skip)
    log_script_complete(
        "news_business_type_analyzer.py",
        success=(fail == 0),
        message=f"{table_name} 公积金业务类型补录完成: 成功{success} 失败{fail} 跳过{skip}",
    )
    return fail == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
