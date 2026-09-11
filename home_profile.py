"""Read-only timing and EXPLAIN of the real homepage route; emits no news or credentials."""
import argparse
import datetime
import json
import time
from urllib.parse import urlencode
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True)
    parser.add_argument('--keyword')
    parser.add_argument('--page', type=int, default=1)
    args = parser.parse_args()
    datetime.date.fromisoformat(args.date)
    import app
    from routes import views
    original_connect = views.get_connection
    timings = []

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def execute(self, sql, params=None):
            if not sql.lstrip().upper().startswith('SELECT '):
                raise RuntimeError('检查只允许 SELECT')
            self.cursor.execute('EXPLAIN ' + sql, params)
            print(json.dumps({'query': len(timings) + 1, 'plan': self.cursor.fetchall()},
                             ensure_ascii=False, default=str), flush=True)
            start = time.monotonic()
            result = self.cursor.execute(sql, params)
            elapsed = time.monotonic() - start
            timings.append(elapsed)
            print(f'查询 {len(timings)} 执行及接收：{elapsed:.3f} 秒', flush=True)
            return result

        def fetchall(self):
            rows = self.cursor.fetchall()
            print(f'返回行数：{len(rows)}', flush=True)
            return rows

        def close(self):
            self.cursor.close()

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def cursor(self):
            return Cursor(self.connection.cursor())

        def close(self):
            self.connection.close()

    def connect(**kwargs):
        start = time.monotonic()
        conn = original_connect(**kwargs)
        try:
            with conn.cursor() as cursor:
                cursor.execute('SET SESSION TRANSACTION READ ONLY')
        except Exception:
            conn.close()
            raise
        print(f'连接及只读会话初始化：{time.monotonic() - start:.3f} 秒', flush=True)
        return Connection(conn)

    query = {'date_from': args.date, 'date_to': args.date, 'page': args.page}
    if args.keyword is not None:
        query['keyword'] = args.keyword
    start = time.monotonic()
    with patch.object(views, 'get_connection', side_effect=connect), app.app.test_client() as client:
        response = client.get('/?' + urlencode(query))
    print(json.dumps({'status': response.status_code, 'bytes': len(response.data),
                      'seconds_including_explain': round(time.monotonic() - start, 3),
                      'query_seconds': round(sum(timings), 3)}))
    if response.status_code != 200:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
