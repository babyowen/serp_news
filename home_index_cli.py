"""Inspect or explicitly add the date index used by the homepage; never runs at startup."""
import argparse
import json
import re

INDEX_NAME = "idx_scored_news_fetchdate"


def index_plan(table, columns, indexes):
    if not re.fullmatch(r"[A-Za-z0-9_]+", table):
        raise ValueError("无效的表名")
    field = next((row for row in columns if row['Field'] == 'fetchdate'), None)
    if field is None:
        raise ValueError("缺少 fetchdate 列")
    kind = field['Type'].lower()
    if re.fullmatch(r"(?:date|datetime|timestamp)(?:\(\d+\))?", kind):
        column = '`fetchdate`'
    elif re.fullmatch(r"(?:var)?char\(\d+\)", kind):
        length = int(re.search(r'\d+', kind).group())
        if length < 10:
            raise ValueError("fetchdate 字符列不足 10 位，请人工核对")
        column = '`fetchdate`' if length <= 64 else '`fetchdate`(10)'
    elif kind in ('text', 'tinytext', 'mediumtext', 'longtext'):
        column = '`fetchdate`(10)'
    else:
        raise ValueError("fetchdate 类型不支持自动规划，请人工核对")
    for row in indexes:
        if (row.get('Column_name') == 'fetchdate' and int(row['Seq_in_index']) == 1
                and row.get('Index_type', '').upper() == 'BTREE'
                and row.get('Visible', 'YES') == 'YES'
                and str(row.get('Ignored', 'NO')).upper() == 'NO'
                and (row.get('Sub_part') is None or int(row['Sub_part']) >= 10)):
            return None
    if any(row['Key_name'] == INDEX_NAME for row in indexes):
        raise ValueError("目标索引名称已被不同定义占用；不会删除或覆盖")
    return (f"ALTER TABLE `{table}` ADD INDEX `{INDEX_NAME}` ({column}), "
            "ALGORITHM=INPLACE, LOCK=NONE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='显式创建缺失索引；默认只检查')
    args = parser.parse_args()
    from db_utils import get_connection, get_table_name
    table = get_table_name()
    if not re.fullmatch(r'[A-Za-z0-9_]+', table):
        parser.error('无效表名')
    conn = get_connection(dict_cursor=True, retries=1, read_timeout=3600 if args.apply else 30)
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT VERSION() AS version')
            print(json.dumps(cursor.fetchone(), ensure_ascii=False), flush=True)
            cursor.execute(f'SHOW COLUMNS FROM `{table}`')
            columns = cursor.fetchall()
            cursor.execute(f'SHOW INDEX FROM `{table}`')
            indexes = cursor.fetchall()
            print(json.dumps({'table': table, 'date_column': [r for r in columns if r['Field'] == 'fetchdate'],
                              'indexes': indexes}, ensure_ascii=False, default=str), flush=True)
            sql = index_plan(table, columns, indexes)
            if sql is None:
                print('已有可用于日期范围的 BTREE 索引，无需创建。')
                return
            print('计划 SQL：' + sql, flush=True)
            if not args.apply:
                print('仅检查，未修改数据库。核对后使用 --apply 执行。')
                return
            # Bound metadata-lock waiting; online DDL can still require short exclusive locks.
            cursor.execute('SET SESSION lock_wait_timeout = 10')
            cursor.execute(sql)
            cursor.execute(f'SHOW INDEX FROM `{table}`')
            if index_plan(table, columns, cursor.fetchall()) is not None:
                raise RuntimeError('创建后未找到目标索引')
            print('索引创建并验证完成。')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
