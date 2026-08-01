# -*- coding: utf-8 -*-
# =========================================
# 新闻数据写入MySQL主程序
# 主要功能：将抓取和处理后的新闻、摘要、新闻源等数据写入MySQL数据库，支持查重、日志记录
# =========================================
import os
import sys
import json
import pymysql
from dotenv import load_dotenv
import argparse
import datetime
from config import DEFAULT_KEYWORDS
from error_handler import (
    setup_global_exception_handler,
    with_error_handling,
    log_script_start,
    log_script_complete,
    ErrorHandler
)

# 设置全局异常处理器
setup_global_exception_handler()

# 加载.env文件
load_dotenv()

from db_utils import get_connection, get_table_name
from news_dedup_schema import ensure_keyword_scoped_dedup_index

LOG_PATH = os.environ.get("RUN_LOG_PATH", os.path.join('output', 'run_log.txt'))
TABLE_NAME = get_table_name()
MYSQL_CONNECTION_ERROR_CODES = {0, 2006, 2013}

def write_log(msg):
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')

# 建立数据库连接
conn = get_connection(autocommit=False)
cursor = conn.cursor()


def is_mysql_connection_error(error):
    """判断是否为连接已不可继续复用的 MySQL 错误。"""
    if not isinstance(error, pymysql.MySQLError):
        return False
    code = error.args[0] if error.args else None
    if code in MYSQL_CONNECTION_ERROR_CODES:
        return True
    message = str(error).lower()
    return "lost connection" in message or "server has gone away" in message


def reconnect_database():
    """重建全局连接和游标，避免后续关键词复用已失效的连接。"""
    global conn, cursor

    for resource in (cursor, conn):
        try:
            resource.close()
        except Exception:
            pass

    conn = get_connection(autocommit=False)
    cursor = conn.cursor()


def rollback_or_reconnect_after_error(error):
    if is_mysql_connection_error(error):
        reconnect_database()
        return

    try:
        conn.rollback()
    except pymysql.MySQLError:
        reconnect_database()


def run_with_connection_retry(action_name, func, *args, max_attempts=2):
    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args)
        except Exception as e:
            rollback_or_reconnect_after_error(e)
            if attempt < max_attempts and is_mysql_connection_error(e):
                warning = f"{action_name} 时数据库连接异常，已重连并重试: {e}"
                now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                write_log(f"[{now}] [WARN] {warning}")
                print(f"[WARN] {warning}")
                continue
            raise


def import_scored_news_with_retry(filepath, keyword, max_attempts=2):
    return run_with_connection_retry(
        f"导入 {keyword}",
        insert_scored_news,
        filepath,
        keyword,
        max_attempts=max_attempts,
    )

# 写入 scored_news 表（新闻正文及评分）
# 数据获取：从json_path读取新闻列表
# 执行：同一主关键词内查重（title+link），过滤空内容，不存在则插入scored_news表
# 结果：成功/跳过/失败数统计，写入日志
def insert_scored_news(json_path, keyword):
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    success, fail, skip = 0, 0, 0
    empty_content_skip = 0
    dup_title_skip = 0

    # On legacy production tables without the keyword/title/link index, doing two
    # SELECTs for every item turns one batch into many full-table scans. Load the
    # existing keys once per main keyword and keep newly inserted keys in memory.
    candidate_items = []
    for item in data:
        content = item.get('content', '')
        if not content or content.strip() == '':
            empty_content_skip += 1
            continue
        candidate_items.append((item, content))

    existing_title_links = set()
    existing_titles = set()
    if candidate_items:
        cursor.execute(
            f"SELECT title, link FROM {TABLE_NAME} WHERE keyword=%s",
            (keyword,)
        )
        for title, link in cursor.fetchall():
            if title is not None:
                existing_titles.add(title)
                if link is not None:
                    existing_title_links.add((title, link))

    for item, content in candidate_items:
        title = item.get('title')
        link = item.get('link')

        # 查重：同一主关键词内 title+link。不同业务关键词允许各保留一条。
        if title is not None and link is not None and (title, link) in existing_title_links:
            skip += 1
            continue

        # 兜底查重：同一关键词下 title 完全相同（URL可能不同）
        if title is not None and title in existing_titles:
            dup_title_skip += 1
            continue

        sql = f'''
        INSERT INTO {TABLE_NAME} (
            date, title, link, source, fetchdate, sourceapi, thumbnail, keyword, content, wordcount, custom_grab, score, search_keyword
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        '''
        date = item.get('date', None)
        fetchdate = item.get('fetchdate', None)
        custom_grab = item.get('custom_grab')
        if custom_grab is None:
            custom_grab = False
        try:
            cursor.execute(sql, (
                date,
                item.get('title'),
                item.get('link'),
                item.get('source'),
                fetchdate,
                item.get('sourceapi'),
                item.get('thumbnail'),
                item.get('keyword'),
                content,  # 使用已验证的content变量
                item.get('wordcount'),
                int(custom_grab),
                item.get('score'),
                item.get('search_keyword')
            ))
            success += 1
            if title is not None:
                existing_titles.add(title)
                if link is not None:
                    existing_title_links.add((title, link))
        except Exception as e:
            fail += 1
            write_log(f"[导入异常] {item.get('title', '')[:30]}... 错误: {e}")
    conn.commit()
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    parts = [f"成功: {success}", f"跳过: {skip}", f"空内容: {empty_content_skip}"]
    if dup_title_skip:
        parts.append(f"标题重复: {dup_title_skip}")
    if fail:
        parts.append(f"失败: {fail}")
    write_log(f"[{now}] 导入数据库 {keyword}: {', '.join(parts)}")


def update_scores_from_json(json_path, keyword):
    """重评后更新数据库中已有记录的分数"""
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    updated = 0
    for item in data:
        score = item.get('score')
        title = item.get('title', '')
        if not score or not title:
            continue
        cursor.execute(
            f"UPDATE {TABLE_NAME} SET score=%s WHERE title=%s AND keyword=%s AND score IS NULL",
            (score, title, item.get('keyword', keyword))
        )
        if cursor.rowcount > 0:
            updated += cursor.rowcount
    conn.commit()
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    write_log(f"[{now}] 更新评分 {keyword}: 更新{updated}条")
    print(f"[更新评分] {keyword}: 更新了 {updated} 条记录的分数")
    return updated

# 写入 summary_news 表（新闻摘要）
# 数据获取：从json_path读取摘要数据
# 执行：查重（date+keyword+summary+round），不存在则插入summary_news表
# 结果：成功/跳过/失败数统计，写入日志
# 支持多轮摘要（如round=1,2,3...）自动写入
def insert_summary_news(json_path, keyword):
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    date = data.get('date')
    keyword = data.get('keyword', keyword)
    summaries = data.get('summaries', [])
    success, fail, skip = 0, 0, 0
    for s in summaries:
        # 查重：date+keyword+summary+round
        round_num = s.get('round', 1)
        judge_suggestion = s.get('judge_suggestion', None)
        cursor.execute(
            "SELECT id FROM summary_news WHERE date=%s AND keyword=%s AND summary=%s AND round=%s",
            (date, keyword, s.get('summary'), round_num)
        )
        if cursor.fetchone():
            skip += 1
            continue
        # 新增：支持round和judge_suggestion字段
        sql = '''
        INSERT INTO summary_news (
            date, keyword, summary, platform, model, round, judge_suggestion
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        '''
        try:
            cursor.execute(sql, (
                date,
                keyword,
                s.get('summary'),
                s.get('platform'),
                s.get('model'),
                round_num,
                judge_suggestion
            ))
            success += 1
        except Exception as e:
            fail += 1
            write_log(f"[导入异常] summary_news: {s.get('summary', '')[:30]}... 错误: {e}")
    conn.commit()
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    write_log(f"[{now}] 导入摘要 {keyword}: 成功{success}, 跳过{skip}, 失败{fail}")

# 写入 news_websites 表（新闻源域名）
# 数据获取：从txt_path读取新闻源域名列表
# 执行：查重（website），不存在则插入news_websites表
# 结果：成功/跳过数统计，写入日志
def insert_news_websites(txt_path):
    with open(txt_path, 'r', encoding='utf-8') as f:
        websites = set(line.strip() for line in f if line.strip())
    success, skip = 0, 0
    for website in websites:
        cursor.execute(
            "SELECT website FROM news_websites WHERE website=%s",
            (website,)
        )
        if cursor.fetchone():
            skip += 1
            continue
        try:
            cursor.execute(
                "INSERT INTO news_websites (website, name) VALUES (%s, %s)",
                (website, None)
            )
            success += 1
        except Exception:
            skip += 1
    conn.commit()
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    write_log(f"[{now}] 导入网站源: 成功{success}, 跳过{skip}")

# 写入 news_source_stats 表（新闻源分布统计）
# 数据获取：从json_path读取新闻源统计数据
# 执行：查重（date+keyword+domain），不存在则插入news_source_stats表
# 结果：成功/跳过/失败数统计，写入日志
def insert_news_source_stats(json_path, target_date):
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    success, fail, skip = 0, 0, 0
    for item in data:
        if item.get('date') != target_date:
            continue
        # 查重：date+keyword+domain
        cursor.execute(
            "SELECT id FROM news_source_stats WHERE date=%s AND keyword=%s AND domain=%s",
            (item.get('date'), item.get('keyword'), item.get('domain'))
        )
        if cursor.fetchone():
            skip += 1
            continue
        sql = '''
        INSERT INTO news_source_stats (
            date, keyword, domain, count
        ) VALUES (%s, %s, %s, %s)
        '''
        try:
            cursor.execute(sql, (
                item.get('date'),
                item.get('keyword'),
                item.get('domain'),
                item.get('count')
            ))
            success += 1
        except Exception as e:
            fail += 1
            write_log(f"[导入异常] news_source_stats: {item.get('date')} | {item.get('keyword')} | {item.get('domain')} 错误: {e}")
    conn.commit()
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    write_log(f"[{now}] 导入来源统计: 成功{success}, 跳过{skip}, 失败{fail}")

# 获取目标日期（无参数则为昨天）
# 数据获取：命令行参数或默认昨天
# 执行：判断参数，返回日期字符串
# 结果：返回目标日期字符串
def get_target_date(date_arg):
    if date_arg:
        return date_arg
    else:
        return (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

# 新增：获取指定日期和关键词的最大轮次摘要
def fetch_latest_summary(date, keyword):
    """
    获取指定日期和关键词的最新摘要
    注意：此函数创建独立的数据库连接，避免依赖全局cursor
    """
    # 创建独立的数据库连接
    try:
        temp_conn = get_connection(autocommit=False)
        temp_cursor = temp_conn.cursor()
        
        sql = "SELECT summary, round FROM summary_news WHERE date=%s AND keyword=%s ORDER BY round DESC LIMIT 1"
        temp_cursor.execute(sql, (date, keyword))
        row = temp_cursor.fetchone()
        
        result_summary, result_round = None, None
        if row:
            result_summary, result_round = row[0], row[1]
            
        # 关闭临时连接
        temp_cursor.close()
        temp_conn.close()
        
        return result_summary, result_round
        
    except Exception as e:
        print(f"[ERROR] fetch_latest_summary查询失败: {e}")
        return None, None

# 主流程入口，按顺序导入新闻源、新闻源统计、新闻正文及评分、新闻摘要
# 数据获取：命令行参数、配置文件、各类json/txt文件
# 执行：依次调用各类insert函数，完成数据导入
# 结果：所有数据写入MySQL，日志记录导入情况
@with_error_handling("write_to_mysql.py", "main")
def main():
    """主函数：处理数据库导入任务"""
    # 记录脚本开始
    log_script_start("write_to_mysql.py", sys.argv[1:])
    
    error_handler = ErrorHandler()
    success = True
    
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument('--date', type=str, help='指定日期，格式YYYY-MM-DD')
        parser.add_argument('--keyword', choices=DEFAULT_KEYWORDS, help='只导入指定主关键词')
        args = parser.parse_args()
        target_date = get_target_date(args.date)
        keywords = [args.keyword] if args.keyword else DEFAULT_KEYWORDS

        # Schema changes must be an explicit maintenance action, never the
        # default behavior of a scheduled production batch.
        if os.getenv("AUTO_MIGRATE_DEDUP_INDEX", "0") == "1":
            try:
                if ensure_keyword_scoped_dedup_index(cursor, TABLE_NAME):
                    write_log(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 已迁移唯一索引: {TABLE_NAME} 使用 keyword+title+link 去重")
                    print(f"[INFO] 已迁移唯一索引: {TABLE_NAME} 使用主关键词级去重")
            except pymysql.MySQLError as e:
                try:
                    conn.rollback()
                except pymysql.MySQLError:
                    pass
                warning = f"唯一索引迁移未完成，继续使用应用层查重: {e}"
                write_log(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [WARN] {warning}")
                print(f"[WARN] {warning}")
        else:
            print("[INFO] 已通过 AUTO_MIGRATE_DEDUP_INDEX=0 跳过唯一索引迁移，使用应用层查重")

        # 新增：先导入 news_websites
        run_with_connection_retry(
            "导入网站源",
            insert_news_websites,
            os.path.join("output", "news_sources.txt"),
        )

        # 新增：导入 news_source_stats
        stats_json_path = os.path.join("output", "news_source_stats.json")
        if os.path.exists(stats_json_path):
            print(f"正在导入: {stats_json_path} 到 news_source_stats ...")
            run_with_connection_retry("导入来源统计", insert_news_source_stats, stats_json_path, target_date)
        else:
            print(f"文件不存在，跳过: {stats_json_path}")

        for keyword in keywords:
            filename = f"{target_date}_{keyword}_scored.json"
            filepath = os.path.join("output", target_date, filename)
            if not os.path.exists(filepath):
                print(f"文件不存在，跳过: {filepath}")
                continue
            try:
                print(f"正在导入: {filepath} 到 scored_news ...")
                import_scored_news_with_retry(filepath, keyword)
            except Exception as e:
                    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    log_msg = f"[{now}] 导入数据库\n  关键词: {keyword}\n  文件: {filepath}\n  表: {TABLE_NAME}\n  错误: {e}\n------------------------------"
                    write_log(log_msg)
                    print(f"导入 {filepath} 时出错: {e}")
                    error_handler.log_error(
                        error_type="DATABASE_IMPORT_ERROR",
                        error_msg=f"导入数据库失败: {filepath}, 错误: {e}",
                        script_name="write_to_mysql.py",
                        keyword=keyword,
                        context={"file_path": filepath, "table": TABLE_NAME}
                    )
                    success = False

        print(f"[统计] 数据库导入完成，日期: {target_date}")
        
    except Exception as e:
        error_msg = f"write_to_mysql.py 执行过程中发生异常: {str(e)}"
        print(f"[ERROR] {error_msg}")
        error_handler.log_error(
            error_type="MAIN_FUNCTION_ERROR", 
            error_msg=error_msg,
            script_name="write_to_mysql.py"
        )
        success = False
    finally:
        # 确保数据库连接正确关闭
        try:
            if 'cursor' in locals():
                cursor.close()
            if 'conn' in locals():
                conn.close()
        except Exception as e:
            print(f"[WARN] 关闭数据库连接时出错: {e}")
    
    # 记录脚本完成
    status_msg = "数据库导入成功" if success else "数据库导入过程中出现错误"
    log_script_complete("write_to_mysql.py", success=success, message=status_msg)
    
    return success

# 命令行入口
def __main__():
    main()

if __name__ == '__main__':
    main()
