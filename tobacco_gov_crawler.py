import os
import sys
import re
import argparse
import datetime
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from db_utils import get_connection
from fetch_content import fetch_article_content
import trafilatura
import json
import time
import random

BASE_DOMAIN = "http://www.tobacco.gov.cn"
SECTIONS = {
    "行业要闻": "http://www.tobacco.gov.cn/gjyc/hyyw/list.shtml",
    "各地新闻": "http://www.tobacco.gov.cn/gjyc/gdxw/list.shtml",
    "基层工作": "http://www.tobacco.gov.cn/gjyc/jcgz/list.shtml",
}

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)

def today_str():
    return datetime.date.today().strftime("%Y-%m-%d")

def yesterday_str():
    return (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

def build_page_url(base_url: str, page: int) -> str:
    if page <= 1:
        return base_url
    if "list.shtml" in base_url:
        return base_url.replace("list.shtml", f"list_{page}.shtml")
    return base_url

def normalize_date(text: str) -> str:
    if not text:
        return None
    t = text.strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", t)
    if m:
        return t
    m = re.match(r"^(\d{4})/(\d{2})/(\d{2})$", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日$", t)
    if m:
        y = int(m.group(1))
        mm = int(m.group(2))
        dd = int(m.group(3))
        return f"{y:04d}-{mm:02d}-{dd:02d}"
    return None

def parse_list_page(url: str):
    items = []
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": random.choice([
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
            "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0"
        ])})
        if resp.status_code != 200:
            return items
        html = resp.content.decode("utf-8", errors="replace")
        if len(re.findall(r"[\u4e00-\u9fff]", html)) < 5:
            html = resp.content.decode("gb18030", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        container = soup.select_one("div.tyList ul.inTyList")
        lis = container.find_all("li") if container else soup.find_all("li")
        for li in lis:
            a = li.find("a")
            if not a or not a.get("href"):
                continue
            title = a.get_text(strip=True)
            href = a.get("href")
            link = href if href.startswith("http") else urljoin(BASE_DOMAIN, href)
            date_span = li.find("span", class_=re.compile(r"\bdate\b"))
            date_text = date_span.get_text(strip=True) if date_span else None
            date_norm = normalize_date(date_text)
            items.append({
                "title": title,
                "link": link,
                "date": date_norm
            })
        return items
    except Exception:
        return items

def filter_items(items, days: int, exact_yesterday: bool):
    kept = []
    if exact_yesterday:
        target = datetime.datetime.strptime(yesterday_str(), "%Y-%m-%d").date()
        for it in items:
            if not it.get("date"):
                continue
            try:
                d = datetime.datetime.strptime(it["date"], "%Y-%m-%d").date()
                if d == target:
                    kept.append(it)
            except Exception:
                continue
        return kept
    threshold = datetime.date.today() - datetime.timedelta(days=days)
    for it in items:
        if not it.get("date"):
            continue
        try:
            d = datetime.datetime.strptime(it["date"], "%Y-%m-%d").date()
            if d >= threshold:
                kept.append(it)
        except Exception:
            continue
    return kept

def log_paths():
    ensure_dir(os.path.join("output", "logs"))
    run_log = os.path.join("output", "logs", f"tobacco_gov_crawler_{today_str()}.log")
    err_log = os.path.join("output", "logs", f"tobacco_gov_crawler_error_{today_str()}.log")
    return run_log, err_log

def log_info(msg: str):
    run_log, _ = log_paths()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(run_log, "a", encoding="utf-8") as f:
        f.write(f"[{now}] {msg}\n")
    print(msg)

def log_error(msg: str):
    _, err_log = log_paths()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(err_log, "a", encoding="utf-8") as f:
        f.write(f"[{now}] {msg}\n")
    print(msg)

def get_conn():
    return get_connection(autocommit=True)

def has_chinese(text: str) -> bool:
    if not text:
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", text))

def fix_title_via_trafilatura(url: str, title: str) -> str:
    try:
        if has_chinese(title):
            return title
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return title
        data = trafilatura.extract(downloaded, output_format='json')
        if not data:
            return title
        j = json.loads(data)
        new_title = j.get('title')
        if new_title and has_chinese(new_title):
            return new_title.strip()
        return title
    except Exception:
        return title

def insert_item(conn, item: dict):
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM scored_news WHERE title=%s AND link=%s",
                (item.get("title"), item.get("link"))
            )
            if cursor.fetchone():
                log_info(f"SkippedDuplicate | {item.get('title', '')[:50]} | {item.get('link', '')}")
                return False
            if not item.get("content") or int(item.get("wordcount") or 0) == 0:
                log_info(f"SkippedEmptyContent | {item.get('title', '')[:50]} | {item.get('link', '')}")
                return False
            sql = (
                "INSERT INTO scored_news (date, title, link, source, fetchdate, sourceapi, thumbnail, keyword, content, wordcount, custom_grab, score, search_keyword) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            )
            cursor.execute(sql, (
                item.get("date"),
                item.get("title"),
                item.get("link"),
                item.get("source"),
                item.get("fetchdate"),
                item.get("sourceapi"),
                item.get("thumbnail"),
                item.get("keyword"),
                item.get("content"),
                int(item.get("wordcount") or 0),
                int(1 if item.get("custom_grab") else 0),
                int(item.get("score") or 0),
                item.get("search_keyword")
            ))
            log_info(f"Inserted | {item.get('title', '')[:50]} | {item.get('link', '')} | wc={item.get('wordcount')} | score={item.get('score')}")
            return True
    except Exception as e:
        log_error(f"InsertError | {item.get('title', '')} | {e}")
        return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", type=int, default=None)
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-delay", type=float, default=0.6)
    parser.add_argument("--max-delay", type=float, default=1.8)
    parser.add_argument("--no-throttle", action="store_true")
    args = parser.parse_args()

    exact_yesterday = (args.page is None and args.days is None)
    page = args.page if args.page is not None else 1
    days = args.days if args.days is not None else 1

    run_log, err_log = log_paths()
    mode = "昨天" if exact_yesterday else f"近{days}天"
    log_info(f"Start | page={page} | mode={mode} | exact_yesterday={exact_yesterday} | dry_run={args.dry_run} | throttle={not args.no_throttle} | delay={args.min_delay}-{args.max_delay}s | table=scored_news")

    conn = None
    if not args.dry_run:
        try:
            conn = get_conn()
        except Exception as e:
            log_error(f"DBConnectError | {e}")
            return 1

    total_parsed = 0
    total_kept = 0
    total_inserted = 0
    total_skipped_dup = 0
    total_skipped_empty = 0
    total_fetch_success = 0
    total_fetch_fail = 0

    for section_name, base_url in SECTIONS.items():
        try:
            section_parsed = 0
            section_kept = 0
            for p in range(1, page + 1):
                if not args.no_throttle:
                    d = random.uniform(args.min_delay, args.max_delay)
                    time.sleep(d)
                    log_info(f"Sleep {round(d,2)}s before {section_name} page {p}")
                page_url = build_page_url(base_url, p)
                log_info(f"FetchList | {section_name} | {page_url}")
                parsed = parse_list_page(page_url)
                section_parsed += len(parsed)
                total_parsed += len(parsed)
                for it in parsed:
                    log_info(f"Parsed | {section_name} | {it.get('title','')[:50]} | {it.get('date')} | {it.get('link')}")
                kept = filter_items(parsed, days, exact_yesterday)
                section_kept += len(kept)
                total_kept += len(kept)
                for it in kept:
                    it["source"] = "官网抓取"
                    it["sourceapi"] = "官网抓取"
                    it["score"] = 4
                    it["keyword"] = "中国烟草"
                    it["search_keyword"] = "中国烟草"
                    it["fetchdate"] = it.get("date")
                    it["thumbnail"] = None
                    it["title"] = fix_title_via_trafilatura(it.get("link"), it.get("title"))
                    if args.dry_run:
                        log_info(f"Filtered | Keep | {section_name} | {it.get('title','')[:50]} | {it.get('date')} | {it.get('link')}")
                        print(f"[{section_name}] {it.get('date')} | {it.get('title')} | {it.get('link')}")
                        continue
                    try:
                        if not args.no_throttle:
                            d2 = random.uniform(args.min_delay, args.max_delay)
                            time.sleep(d2)
                            log_info(f"Sleep {round(d2,2)}s before fetch content")
                        content, wordcount, custom_grab = fetch_article_content(it.get("link"))
                        it["content"] = content
                        it["wordcount"] = wordcount
                        it["custom_grab"] = custom_grab
                        if wordcount and wordcount > 0:
                            total_fetch_success += 1
                            log_info(f"FetchSuccess | {section_name} | wc={wordcount} | custom={custom_grab} | {it.get('title','')[:50]}")
                        else:
                            total_fetch_fail += 1
                            log_info(f"FetchFail | {section_name} | {it.get('title','')[:50]}")
                    except Exception as e:
                        total_fetch_fail += 1
                        log_error(f"FetchError | {section_name} | {it.get('title','')} | {e}")
                        it["content"] = ""
                        it["wordcount"] = 0
                        it["custom_grab"] = False
                    ok = insert_item(conn, it) if conn else False
                    if ok:
                        total_inserted += 1
                    else:
                        if not it.get("content") or int(it.get("wordcount") or 0) == 0:
                            total_skipped_empty += 1
                        else:
                            total_skipped_dup += 1
            print(f"Section={section_name} Parsed={section_parsed} Kept={section_kept}")
        except Exception as e:
            log_error(f"ListError | {section_name} | {e}")

    log_info(f"Summary | parsed={total_parsed} | kept={total_kept} | fetch_success={total_fetch_success} | fetch_fail={total_fetch_fail} | inserted={total_inserted} | skipped_dup={total_skipped_dup} | skipped_empty={total_skipped_empty}")
    run_log, _ = log_paths()
    print(f"LogFile={run_log}")
    return 0

if __name__ == "__main__":
    sys.exit(main())