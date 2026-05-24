"""统一数据库连接工具模块"""
import os
import time
import logging
import pymysql
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", 3306))
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
MYSQL_DB = os.getenv("MYSQL_DB")


def get_connection(autocommit=True, dict_cursor=False, retries=3, delay=2):
    """获取MySQL连接，带重试

    Args:
        autocommit: 是否自动提交（默认True）
        dict_cursor: 是否使用字典游标（默认False）
        retries: 连接重试次数（默认3次）
        delay: 重试间隔秒数（默认2秒）
    """
    kwargs = dict(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        charset="utf8mb4",
        autocommit=autocommit,
        read_timeout=30,
        write_timeout=30,
    )
    if dict_cursor:
        kwargs["cursorclass"] = pymysql.cursors.DictCursor
    last_err = None
    for i in range(retries):
        try:
            return pymysql.connect(**kwargs)
        except pymysql.err.OperationalError as e:
            last_err = e
            logger.warning(f"[db_utils] 连接MySQL失败({i+1}/{retries}): {e}")
            if i < retries - 1:
                time.sleep(delay)
    raise last_err


def ping_connection(conn):
    """检查连接是否存活，断开则重连"""
    try:
        conn.ping(reconnect=True)
    except Exception:
        return get_connection(
            autocommit=conn.get_autocommit(),
            dict_cursor=isinstance(conn.cursorclass, type) and issubclass(conn.cursorclass, pymysql.cursors.DictCursor),
        )
    return conn


def get_table_name(default="scored_news"):
    """获取主表名，通过MYSQL_TABLE环境变量控制（用于测试切换）"""
    return os.getenv("MYSQL_TABLE", default)
