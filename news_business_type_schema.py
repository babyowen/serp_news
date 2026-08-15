"""Explicit schema setup for Issue #10 business-type labeling."""

import argparse
import sys

from dotenv import load_dotenv

from db_utils import get_connection, get_table_name
from news_business_type_utils import ensure_business_type_schema, validate_business_type_table_name


load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Create business-type schema")
    parser.add_argument("--table", default=None, help="scored_news or scored_news_test")
    parser.add_argument("--all", action="store_true", help="Initialize both allowed news tables")
    args = parser.parse_args()

    if args.all and args.table:
        raise ValueError("Use either --table or --all")
    tables = ("scored_news", "scored_news_test") if args.all else (
        validate_business_type_table_name(args.table or get_table_name()),
    )
    conn = get_connection(autocommit=False)
    try:
        cur = conn.cursor()
        for table_name in tables:
            ensure_business_type_schema(cur, table_name)
            print(f"[schema ready] {table_name}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return True


if __name__ == "__main__":
    try:
        sys.exit(0 if main() else 1)
    except Exception as exc:
        print(f"[schema failed] {exc}", file=sys.stderr)
        sys.exit(1)
