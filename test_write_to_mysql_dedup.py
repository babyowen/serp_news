"""Regression coverage for application-level batch deduplication."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch


class BootstrapCursor:
    pass


class BootstrapConnection:
    def cursor(self):
        return BootstrapCursor()


# write_to_mysql currently opens a connection at import time. Keep this unit
# test independent from both production and test database configuration.
with patch("db_utils.get_connection", return_value=BootstrapConnection()):
    import write_to_mysql


class FakeCursor:
    def __init__(self):
        self.executed = []
        self.rows = [("已存在", "https://example.test/existing")]

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


class BatchDedupTests(unittest.TestCase):
    def test_existing_and_incoming_duplicates_use_one_lookup_per_keyword(self):
        fake_cursor = FakeCursor()
        fake_connection = FakeConnection()
        items = [
            {"title": "已存在", "link": "https://example.test/existing", "content": "正文"},
            {"title": "同标题", "link": "https://example.test/a", "content": "正文"},
            {"title": "同标题", "link": "https://example.test/b", "content": "正文"},
            {"title": "空正文", "link": "https://example.test/empty", "content": ""},
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8", delete=False) as file:
            json.dump(items, file, ensure_ascii=False)
            path = file.name
        previous_cursor, previous_connection = write_to_mysql.cursor, write_to_mysql.conn
        try:
            write_to_mysql.cursor = fake_cursor
            write_to_mysql.conn = fake_connection
            write_to_mysql.insert_scored_news(path, "烟草服务银行")
        finally:
            write_to_mysql.cursor, write_to_mysql.conn = previous_cursor, previous_connection
            os.unlink(path)

        selects = [sql for sql, _ in fake_cursor.executed if sql.startswith("SELECT title, link")]
        inserts = [sql for sql, _ in fake_cursor.executed if sql.startswith("INSERT INTO")]
        self.assertEqual(len(selects), 1)
        self.assertEqual(len(inserts), 1)
        self.assertEqual(fake_connection.commits, 1)


if __name__ == "__main__":
    unittest.main()
