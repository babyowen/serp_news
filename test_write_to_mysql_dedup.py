"""Regression coverage for application-level batch deduplication."""

import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import pymysql


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
        self.rollbacks = 0
        self.closed = False
        self.cursor_instance = FakeCursor()

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


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

    def test_connection_loss_reconnects_and_retries_keyword_once(self):
        previous_cursor, previous_connection = write_to_mysql.cursor, write_to_mysql.conn
        failed_cursor = FakeCursor()
        failed_connection = FakeConnection()
        replacement_connection = FakeConnection()
        error = pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query (timed out)")
        reconnected_connection = None
        reconnected_cursor = None
        try:
            write_to_mysql.cursor = failed_cursor
            write_to_mysql.conn = failed_connection
            with patch.object(write_to_mysql, "insert_scored_news", side_effect=[error, None]) as insert_mock, \
                    patch.object(write_to_mysql, "get_connection", return_value=replacement_connection) as connection_mock, \
                    patch.object(write_to_mysql, "write_log"):
                write_to_mysql.import_scored_news_with_retry("output/date/news.json", "养老")
                reconnected_connection = write_to_mysql.conn
                reconnected_cursor = write_to_mysql.cursor
        finally:
            write_to_mysql.cursor, write_to_mysql.conn = previous_cursor, previous_connection

        self.assertEqual(insert_mock.call_count, 2)
        connection_mock.assert_called_once_with(autocommit=False)
        self.assertTrue(failed_connection.closed)
        self.assertIs(reconnected_connection, replacement_connection)
        self.assertIs(reconnected_cursor, replacement_connection.cursor_instance)

    def test_connection_retry_wrapper_can_protect_source_metadata_imports(self):
        previous_cursor, previous_connection = write_to_mysql.cursor, write_to_mysql.conn
        failed_connection = FakeConnection()
        replacement_connection = FakeConnection()
        error = pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query (timed out)")
        action_mock = Mock(side_effect=[error, "ok"])
        result = None
        try:
            write_to_mysql.cursor = failed_connection.cursor_instance
            write_to_mysql.conn = failed_connection
            with patch.object(write_to_mysql, "get_connection", return_value=replacement_connection), \
                    patch.object(write_to_mysql, "write_log"):
                result = write_to_mysql.run_with_connection_retry(
                    "导入网站源",
                    action_mock,
                    "output/news_sources.txt",
                )
        finally:
            write_to_mysql.cursor, write_to_mysql.conn = previous_cursor, previous_connection

        self.assertEqual(result, "ok")
        self.assertEqual(action_mock.call_count, 2)
        self.assertTrue(failed_connection.closed)


if __name__ == "__main__":
    unittest.main()
