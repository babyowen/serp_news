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

from db_utils import get_connection

LOG_PATH = os.path.join('output', 'run_log.txt')

def write_log(msg):
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')

# 建立数据库连接
conn = get_connection(autocommit=False)
cursor = conn.cursor()

# 写入 scored_news 表（新闻正文及评分）
# 数据获取：从json_path读取新闻列表
# 执行：查重（title+link），过滤空内容，不存在则插入scored_news表
# 结果：成功/跳过/失败数统计，写入日志
def insert_scored_news(json_path, keyword):
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    success, fail, skip = 0, 0, 0
    empty_content_skip = 0  # 新增：记录因内容为空而跳过的数量
    
    for item in data:
        # 新增：过滤空内容
        content = item.get('content', '')
        if not content or content.strip() == '':
            empty_content_skip += 1
            write_log(f"[跳过空内容] scored_news: {item.get('title', '')[:50]}... (内容为空)")
            continue
            
        # 查重：title+link
        cursor.execute(
            "SELECT id FROM scored_news WHERE title=%s AND link=%s",
            (item.get('title'), item.get('link'))
        )
        if cursor.fetchone():
            skip += 1
            continue
            
        sql = '''
        INSERT INTO scored_news (
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
                item.get('search_keyword')  # 新增：从JSON中获取search_keyword字段
            ))
            success += 1
        except Exception as e:
            fail += 1
            write_log(f"[导入异常] scored_news: {item.get('title', '')} 错误: {e}")
    conn.commit()
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    # 修改日志信息，添加空内容跳过统计
    log_msg = f"[{now}] 导入数据库\n  关键词: {keyword}\n  文件: {json_path}\n  表: scored_news\n  成功写入: {success} 条\n  跳过(已存在): {skip} 条\n  跳过(空内容): {empty_content_skip} 条\n  失败: {fail} 条\n------------------------------"
    write_log(log_msg)

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
    log_msg = f"[{now}] 导入数据库\n  关键词: {keyword}\n  文件: {json_path}\n  表: summary_news\n  成功写入: {success} 条\n  跳过: {skip} 条\n  失败: {fail} 条\n------------------------------"
    write_log(log_msg)

# 写入 news_websites 表（新闻源域名）
# 数据获取：从txt_path读取新闻源域名列表
# 执行：查重（website），不存在则插入news_websites表
# 结果：成功/跳过数统计，写入日志
def insert_news_websites(txt_path):
    with open(txt_path, 'r', encoding='utf-8') as f:
        websites = set(line.strip() for line in f if line.strip())
    success, skip = 0, 0
    duplicate_found = False  # 标记是否有查重
    for website in websites:
        cursor.execute(
            "SELECT website FROM news_websites WHERE website=%s",
            (website,)
        )
        if cursor.fetchone():
            duplicate_found = True
            skip += 1
            continue
        try:
            cursor.execute(
                "INSERT INTO news_websites (website, name) VALUES (%s, %s)",
                (website, None)
            )
            success += 1
            write_log(f"[写入] news_websites 新增: {website}")
        except Exception as e:
            write_log(f"[导入异常] news_websites: {website} 错误: {e}")
    # 只输出一条查重日志
    if duplicate_found:
        write_log(f"[查重] news_websites 已存在，跳过部分已存在网站（仅提示一次）")
    conn.commit()
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_msg = f"[{now}] 导入数据库\n  文件: {txt_path}\n  表: news_websites\n  成功写入: {success} 条\n  跳过: {skip} 条\n------------------------------"
    write_log(log_msg)

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
            write_log(f"[查重] news_source_stats 已存在，跳过: {item.get('date')} | {item.get('keyword')} | {item.get('domain')}")
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
    log_msg = f"[{now}] 导入数据库\n  文件: {json_path}\n  表: news_source_stats\n  成功写入: {success} 条\n  跳过: {skip} 条\n  失败: {fail} 条\n------------------------------"
    write_log(log_msg)

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
        temp_conn = pymysql.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=MYSQL_DB,
            charset='utf8mb4'
        )
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
        args = parser.parse_args()
        target_date = get_target_date(args.date)

        # 新增：先导入 news_websites
        insert_news_websites(os.path.join("output", "news_sources.txt"))

        # 新增：导入 news_source_stats
        stats_json_path = os.path.join("output", "news_source_stats.json")
        if os.path.exists(stats_json_path):
            print(f"正在导入: {stats_json_path} 到 news_source_stats ...")
            insert_news_source_stats(stats_json_path, target_date)
        else:
            print(f"文件不存在，跳过: {stats_json_path}")

        for keyword in DEFAULT_KEYWORDS:
            filename = f"{target_date}_{keyword}_scored.json"
            filepath = os.path.join("output", target_date, filename)
            if not os.path.exists(filepath):
                print(f"文件不存在，跳过: {filepath}")
                continue
            try:
                print(f"正在导入: {filepath} 到 scored_news ...")
                insert_scored_news(filepath, keyword)
            except Exception as e:
                    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    log_msg = f"[{now}] 导入数据库\n  关键词: {keyword}\n  文件: {filepath}\n  表: {suffix}_news\n  错误: {e}\n------------------------------"
                    write_log(log_msg)
                    print(f"导入 {filepath} 时出错: {e}")
                    error_handler.log_error(
                        error_type="DATABASE_IMPORT_ERROR",
                        error_msg=f"导入数据库失败: {filepath}, 错误: {e}",
                        script_name="write_to_mysql.py",
                        keyword=keyword,
                        context={"file_path": filepath, "table": f"{suffix}_news"}
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