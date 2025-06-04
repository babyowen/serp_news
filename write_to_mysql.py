import os
import json
import pymysql
from dotenv import load_dotenv
import argparse
import datetime
from config import DEFAULT_KEYWORDS

# 加载.env文件
load_dotenv()

MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_PORT = int(os.getenv('MYSQL_PORT', 3306))
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')
MYSQL_DB = os.getenv('MYSQL_DB')

LOG_PATH = os.path.join('output', 'run_log.txt')

def write_log(msg):
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')

# 数据库连接
conn = pymysql.connect(
    host=MYSQL_HOST,
    port=MYSQL_PORT,
    user=MYSQL_USER,
    password=MYSQL_PASSWORD,
    database=MYSQL_DB,
    charset='utf8mb4'
)
cursor = conn.cursor()

# 写入 scored_news
def insert_scored_news(json_path, keyword):
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    success, fail, skip = 0, 0, 0
    for item in data:
        # 查重：title+link
        cursor.execute(
            "SELECT id FROM scored_news WHERE title=%s AND link=%s",
            (item.get('title'), item.get('link'))
        )
        if cursor.fetchone():
            write_log(f"[查重] scored_news 已存在，跳过: {item.get('title', '')} | {item.get('link', '')}")
            skip += 1
            continue
        sql = '''
        INSERT INTO scored_news (
            date, title, link, source, fetchdate, sourceapi, thumbnail, keyword, content, wordcount, custom_grab, score
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                item.get('content'),
                item.get('wordcount'),
                int(custom_grab),
                item.get('score')
            ))
            success += 1
        except Exception as e:
            fail += 1
            write_log(f"[导入异常] scored_news: {item.get('title', '')} 错误: {e}")
    conn.commit()
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_msg = f"[{now}] 导入数据库\n  关键词: {keyword}\n  文件: {json_path}\n  表: scored_news\n  成功写入: {success} 条\n  跳过: {skip} 条\n  失败: {fail} 条\n------------------------------"
    write_log(log_msg)

# 写入 summary_news
def insert_summary_news(json_path, keyword):
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    date = data.get('date')
    keyword = data.get('keyword', keyword)
    summaries = data.get('summaries', [])
    success, fail, skip = 0, 0, 0
    for s in summaries:
        # 查重：date+keyword+summary
        cursor.execute(
            "SELECT id FROM summary_news WHERE date=%s AND keyword=%s AND summary=%s",
            (date, keyword, s.get('summary'))
        )
        if cursor.fetchone():
            write_log(f"[查重] summary_news 已存在，跳过: {date} | {keyword} | {s.get('summary', '')[:30]}...")
            skip += 1
            continue
        sql = '''
        INSERT INTO summary_news (
            date, keyword, summary, platform, model
        ) VALUES (%s, %s, %s, %s, %s)
        '''
        try:
            cursor.execute(sql, (
                date,
                keyword,
                s.get('summary'),
                s.get('platform'),
                s.get('model')
            ))
            success += 1
        except Exception as e:
            fail += 1
            write_log(f"[导入异常] summary_news: {s.get('summary', '')[:30]}... 错误: {e}")
    conn.commit()
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_msg = f"[{now}] 导入数据库\n  关键词: {keyword}\n  文件: {json_path}\n  表: summary_news\n  成功写入: {success} 条\n  跳过: {skip} 条\n  失败: {fail} 条\n------------------------------"
    write_log(log_msg)

def get_target_date(date_arg):
    if date_arg:
        return date_arg
    else:
        return (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', type=str, help='指定日期，格式YYYY-MM-DD')
    args = parser.parse_args()
    target_date = get_target_date(args.date)

    for keyword in DEFAULT_KEYWORDS:
        for suffix in ['scored', 'summary']:
            filename = f"{target_date}_{keyword}_{suffix}.json"
            filepath = os.path.join("output", target_date, filename)
            if not os.path.exists(filepath):
                print(f"文件不存在，跳过: {filepath}")
                continue
            try:
                if suffix == 'scored':
                    print(f"正在导入: {filepath} 到 scored_news ...")
                    insert_scored_news(filepath, keyword)
                else:
                    print(f"正在导入: {filepath} 到 summary_news ...")
                    insert_summary_news(filepath, keyword)
            except Exception as e:
                now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                log_msg = f"[{now}] 导入数据库\n  关键词: {keyword}\n  文件: {filepath}\n  表: {suffix}_news\n  错误: {e}\n------------------------------"
                write_log(log_msg)
                print(f"导入 {filepath} 时出错: {e}")

    cursor.close()
    conn.close()

if __name__ == '__main__':
    main() 