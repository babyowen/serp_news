"""统一数据库连接工具模块"""
import os
from dotenv import load_dotenv

load_dotenv()

MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", 3306))
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
MYSQL_DB = os.getenv("MYSQL_DB")


def get_connection(autocommit=True, dict_cursor=False):
    """获取MySQL连接

    Args:
        autocommit: 是否自动提交（默认True）
        dict_cursor: 是否使用字典游标（默认False）
    """
    import pymysql
    cursorclass = pymysql.cursors.DictCursor if dict_cursor else None
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        charset="utf8mb4",
        autocommit=autocommit,
        cursorclass=cursorclass,
    )


def get_table_name(default="scored_news"):
    """获取主表名，通过MYSQL_TABLE环境变量控制（用于测试切换）"""
    return os.getenv("MYSQL_TABLE", default)
