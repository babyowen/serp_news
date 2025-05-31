import os
import sys
import json
from datetime import datetime
import trafilatura
import time
import random
from newspaper import Article
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import urllib.parse
import requests
from selenium.webdriver.common.by import By
from config_grab_rules import CUSTOM_GRAB_RULES
from config_grab_rules import grab_msn_cn_playwright
from playwright.sync_api import sync_playwright
from readability import Document
from bs4 import BeautifulSoup


def get_today_str():
    return datetime.now().strftime('%Y-%m-%d')

def get_json_path(keyword, date_str=None):
    if date_str is None:
        date_str = get_today_str()
    filename = f"{date_str}_{keyword}.json"
    return os.path.join('output', date_str, filename)

def get_log_path(date_str=None):
    # 日志统一放在output目录下
    return os.path.join('output', 'run_log.txt')

def get_chromedriver_path():
    # 只需这样即可，chromedriver 会自动下载到默认缓存目录
    driver_path = ChromeDriverManager().install()
    return driver_path

def fetch_article_content_with_selenium(url):
    print(f"[调试] fetch_article_content_with_selenium 启动, url={url}")
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--ignore-certificate-errors')
    options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
    driver_path = get_chromedriver_path()
    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=options)
    custom_grab = False
    try:
        driver.get(url)
        time.sleep(5)
        print(f"[调试] 开始遍历定制化规则, CUSTOM_GRAB_RULES 长度: {len(CUSTOM_GRAB_RULES)}")
        for match_func, grab_func in CUSTOM_GRAB_RULES:
            print(f"[调试] 检查规则: {grab_func.__name__}, url={url.lower()}")
            if match_func(url.lower()):
                print(f"[调试] 命中定制化规则: {grab_func.__name__}, url={url}")
                text, grab_type = grab_func(driver)
                # 只要命中定制化规则就直接返回，无论是否抓到正文
                return text, len(text), True
        print("[调试] 未命中任何定制化规则，进入通用抓取")
        candidates = [
            'article', '.article-content', '#main-content', '.content', '.news_content', '.content-article', '.news-content'
        ]
        text = ''
        for selector in candidates:
            elems = driver.find_elements('css selector', selector)
            for elem in elems:
                t = elem.text.strip()
                if len(t) > len(text):
                    text = t
        return text, len(text), False
    except Exception as e:
        print(f"[调试] fetch_article_content_with_selenium 异常: {e}")
        return '', 0, custom_grab
    finally:
        driver.quit()

def fetch_article_content_with_requests(url):
    try:
        resp = requests.get(url, timeout=10)
        encoding = resp.apparent_encoding
        html = resp.content.decode(encoding, errors='replace')
        # 先用trafilatura
        result = trafilatura.extract(html, output_format='json')
        if result:
            data = json.loads(result)
            text = data.get('text', '')
            if text and len(text) > 50:
                return text, len(text), True
        # 再用newspaper3k
        try:
            article = Article(url, language='zh')
            article.set_html(html)
            article.parse()
            text = article.text
            if text and len(text) > 50:
                return text, len(text), True
        except Exception:
            pass
    except Exception:
        pass
    return '', 0, False

def fetch_article_content_with_playwright(url):
    print(f"[调试] fetch_article_content_with_playwright 启动, url={url}")
    custom_grab = False
    try:
        print(f"[调试] 当前已注册定制化规则: {[f.__name__ for _, f in CUSTOM_GRAB_RULES]}")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=30000)
            # 遍历定制化规则
            for match_func, grab_func in CUSTOM_GRAB_RULES:
                print(f"[调试] [Playwright] 检查规则: {grab_func.__name__}, url={url.lower()}")
                if match_func(url.lower()):
                    print(f"[调试] [Playwright] 命中定制化规则: {grab_func.__name__}, url={url}")
                    grab_func_name = grab_func.__name__ + '_playwright'
                    print(f"[调试] [Playwright] 期望调用: {grab_func_name}")
                    if grab_func_name in globals():
                        print(f"[调试] [Playwright] 找到 {grab_func_name}，即将调用")
                        text, grab_type = globals()[grab_func_name](page)
                        browser.close()
                        print(f"[调试] [Playwright] {grab_func_name} 返回字数: {len(text)}")
                        return text, len(text), True
                    else:
                        print(f"[调试] [Playwright] 未实现 {grab_func_name}，跳过")
                        print(f"[调试] [Playwright] 当前globals()中可用函数: {[k for k in globals().keys() if 'msn' in k or 'playwright' in k]}")
                        # 输出渲染后HTML片段
                        html_full = page.content()
                        with open('output/msn_playwright_debug.html', 'w', encoding='utf-8') as f:
                            f.write(html_full)
                        print(f"[调试] [Playwright] 渲染后完整HTML已写入 output/msn_playwright_debug.html")
                        break
            browser.close()
        print("[调试] [Playwright] 未命中任何定制化规则")
        return '', 0, False
    except Exception as e:
        print(f"[调试] fetch_article_content_with_playwright 异常: {e}")
        return '', 0, False

def fetch_article_content(url, max_retries=3):
    print(f"[调试] fetch_article_content 启动, url={url}")
    # 1. msn.cn 直接跳过抓取
    if 'msn.cn' in url:
        print("[调试] msn.cn 暂不支持正文抓取，已跳过")
        return '', 0, False
    # 2. 其它站点走原有流程
    for match_func, grab_func in CUSTOM_GRAB_RULES:
        if match_func(url.lower()):
            print(f"[调试] 优先命中定制化规则: {grab_func.__name__}, url={url}")
            text, grab_type = grab_func(fetch_article_content_with_selenium_driver(url))
            return text, len(text), True
    # 针对GBK/GB2312等特殊站点优先用requests自动编码识别
    if any(domain in url for domain in ['jxnews.com.cn']):
        print("[调试] 命中特殊编码站点，优先 requests 抓取")
        text, wc, used_custom = fetch_article_content_with_requests(url)
        if text and wc > 50:
            print("[调试] requests 抓取成功，提前 return")
            return text, wc, used_custom
    # 2. trafilatura
    for attempt in range(max_retries):
        try:
            print(f"[调试] trafilatura 抓取 attempt {attempt+1}")
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                result = trafilatura.extract(downloaded, output_format='json')
                if result:
                    data = json.loads(result)
                    text = data.get('text', '')
                    if text and len(text) > 50:
                        print("[调试] trafilatura 抓取成功，提前 return")
                        return text, len(text), False
        except Exception as e:
            print(f"[调试] trafilatura 异常: {e}")
        if attempt < max_retries - 1:
            time.sleep(random.uniform(1, 3))
    # 3. newspaper3k
    try:
        print("[调试] newspaper3k 抓取")
        article = Article(url, language='zh')
        article.download()
        article.parse()
        text = article.text
        if text and len(text) > 50:
            print("[调试] newspaper3k 抓取成功，提前 return")
            return text, len(text), False
    except Exception as e:
        print(f"[调试] newspaper3k 异常: {e}")
    # 4. Playwright渲染+正文提取
    try:
        print("[调试] Playwright 渲染+正文提取")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=30000)
            page.wait_for_load_state('networkidle')
            html_content = page.content()
            browser.close()
        # 4.1 Newspaper3k 提取
        try:
            article = Article(url="dummy_url_for_newspaper", language='zh')
            article.download(input_html=html_content)
            article.parse()
            text = article.text
            if text and len(text) > 50:
                print("[调试] Playwright+Newspaper3k 抓取成功，提前 return")
                return text, len(text), False
        except Exception as e:
            print(f"[调试] Playwright+Newspaper3k 异常: {e}")
        # 4.2 Readability 提取
        try:
            doc = Document(html_content)
            text = doc.summary()
            soup = BeautifulSoup(text, 'html.parser')
            pure_text = soup.get_text(separator='\n').strip()
            if pure_text and len(pure_text) > 50:
                print("[调试] Playwright+Readability 抓取成功，提前 return")
                return pure_text, len(pure_text), False
        except Exception as e:
            print(f"[调试] Playwright+Readability 异常: {e}")
    except Exception as e:
        print(f"[调试] Playwright 渲染异常: {e}")
    # 5. Selenium定制化兜底（如未命中定制化规则时的通用抓取）
    try:
        print("[调试] selenium 定制化抓取")
        text, wc, custom_grab = fetch_article_content_with_selenium(url)
        if custom_grab or (text and wc > 50):
            print("[调试] selenium 定制化抓取命中，提前 return")
            return text, wc, custom_grab
    except Exception as e:
        print(f"[调试] selenium 定制化异常: {e}")
    print("[调试] 全部抓取失败，返回空")
    return '', 0, False

def fetch_article_content_with_selenium_driver(url):
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--ignore-certificate-errors')
    options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
    driver_path = get_chromedriver_path()
    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=options)
    try:
        driver.get(url)
        time.sleep(5)
        return driver
    except Exception as e:
        print(f"[调试] fetch_article_content_with_selenium_driver 异常: {e}")
        driver.quit()
        raise

def get_domain(url):
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.netloc
    except Exception:
        return ''

def process_json(keyword, date_str=None, mode='正式'):
    json_path = get_json_path(keyword, date_str)
    log_path = get_log_path(date_str)
    if not os.path.exists(json_path):
        print(f"File not found: {json_path}")
        return
    with open(json_path, 'r', encoding='utf-8') as f:
        news_list = json.load(f)
    # 新增：将 https://people.com.cn 及其所有子域名替换为 http
    for item in news_list:
        url = item.get('link')
        if url and url.startswith('https://') and '.people.com.cn' in url:
            # 只替换协议部分
            new_url = 'http://' + url[len('https://'):]
            item['link'] = new_url
    # 1. 先抓取正文，写入item，并加同一网站间隔
    prev_domain = None
    filtered_news_list = []
    skipped_video_items = []
    for item in news_list:
        url = item.get('link')
        curr_domain = get_domain(url) if url else None
        # 只在抓取正文时加间隔
        if prev_domain and curr_domain and prev_domain == curr_domain:
            sleep_time = random.uniform(1, 4)
            print(f"同一网站({curr_domain})，等待 {sleep_time:.1f} 秒防反爬...")
            time.sleep(sleep_time)
        prev_domain = curr_domain
        # 跳过tv.cctv.com，仅日志记录
        if url and 'tv.cctv.com' in url:
            print(f"[跳过] {url} 为视频新闻，未写入json，仅日志记录")
            skipped_video_items.append({'title': item.get('title', ''), 'link': url})
            continue
        if not url:
            item['content'] = ''
            item['wordcount'] = 0
            item['custom_grab'] = False
            filtered_news_list.append(item)
            continue
        print(f"Fetching: {url}")
        content, wordcount, custom_grab = fetch_article_content(url)
        item['content'] = content
        item['wordcount'] = wordcount
        item['custom_grab'] = custom_grab
        filtered_news_list.append(item)
    # 2. 写回json（只写入非tv.cctv.com）
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(filtered_news_list, f, ensure_ascii=False, indent=2)
    # 3. 重新统计，确保日志和json一致
    success_count = 0
    fail_items = []
    custom_used_items = []
    custom_grab_domain_count = {}
    custom_grab_domain_items = {}
    all_domains = set()
    domain_news_count = {}
    for item in filtered_news_list:
        url = item.get('link')
        curr_domain = get_domain(url) if url else None
        if curr_domain:
            all_domains.add(curr_domain)
            if curr_domain not in domain_news_count:
                domain_news_count[curr_domain] = 0
            domain_news_count[curr_domain] += 1
        if not url or item.get('wordcount', 0) == 0:
            fail_items.append({'title': item.get('title', ''), 'link': url, 'custom': item.get('custom_grab', False)})
            continue
        success_count += 1
        if item.get('custom_grab'):
            custom_used_items.append({'title': item.get('title', ''), 'link': url})
            if curr_domain not in custom_grab_domain_count:
                custom_grab_domain_count[curr_domain] = 0
                custom_grab_domain_items[curr_domain] = []
            custom_grab_domain_count[curr_domain] += 1
            custom_grab_domain_items[curr_domain].append({'title': item.get('title', ''), 'link': url})
    # 新增：写入所有新闻来源域名到output/news_sources.txt（累积历史域名）
    sources_path = os.path.join('output', 'news_sources.txt')
    # 读取历史域名
    if os.path.exists(sources_path):
        with open(sources_path, 'r', encoding='utf-8') as sf:
            for line in sf:
                all_domains.add(line.strip())
    # 本次采集到的域名已在 all_domains set 中
    with open(sources_path, 'w', encoding='utf-8') as sf:
        for domain in sorted(all_domains):
            sf.write(domain + '\n')
    # 新增：写入每天每个关键词每个新闻源采集条数到output/news_source_stats.json（追加模式，含日期）
    stats_path = os.path.join('output', 'news_source_stats.json')
    today_str = date_str or get_today_str()
    stats_records = []
    if os.path.exists(stats_path):
        with open(stats_path, 'r', encoding='utf-8') as sf:
            try:
                stats_records = json.load(sf)
            except Exception:
                stats_records = []
    for domain, count in domain_news_count.items():
        stats_records.append({
            'date': today_str,
            'keyword': keyword,
            'domain': domain,
            'count': count
        })
    with open(stats_path, 'w', encoding='utf-8') as sf:
        json.dump(stats_records, sf, ensure_ascii=False, indent=2)
    # 写日志
    with open(log_path, 'a', encoding='utf-8') as logf:
        logf.write(f"\n==============================\n")
        logf.write(f"📰 [抓取新闻正文] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        logf.write(f"🔑 关键词: {keyword}\n")
        logf.write(f"🗂️ 模式: {mode}\n")
        logf.write(f"📄 处理的json文件: {json_path}\n")
        logf.write(f"\n📊 抓取统计：\n")
        logf.write(f"  ✅ 成功抓取正文: {success_count} 篇\n")
        logf.write(f"  ✨ 其中定制化抓取: {len(custom_used_items)} 篇\n")
        logf.write(f"  🌐 本次采集新闻源数量: {len(all_domains)}\n")
        logf.write(f"  ❌ 抓取失败: {len(fail_items)} 篇\n")
        if custom_grab_domain_count:
            logf.write("\n🎯 定制化抓取命中统计：\n")
            for domain, count in custom_grab_domain_count.items():
                logf.write(f"  - {domain}: {count} 篇\n")
                for item in custom_grab_domain_items[domain]:
                    logf.write(f"      • {item['title']} | {item['link']}\n")
        if fail_items:
            logf.write("\n⚠️ 未能抓取的新闻：\n")
            for fail in fail_items:
                mark = "(定制化)" if fail.get('custom') else ""
                logf.write(f"  - {fail['title']} | {fail['link']} {mark}\n")
        # 在日志中单独记录tv.cctv.com跳过情况
        if skipped_video_items:
            logf.write("\n📺 跳过仅含视频的新闻（tv.cctv.com）：\n")
            for item in skipped_video_items:
                logf.write(f"  - {item['title']} | {item['link']}\n")
        logf.write("==============================\n\n")
    print(f"Updated: {json_path}")
    print(f"日志已写入 {log_path}")
    print(f"已写入新闻来源统计: {sources_path}")
    print(f"已写入新闻源分布统计: {stats_path}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("用法: python fetch_content.py 关键词 [日期, 格式:YYYY-MM-DD] [--test] [--url 新闻链接]")
        sys.exit(1)
    keyword = sys.argv[1]
    date_str = None
    mode = '正式'
    test_url = None
    for arg in sys.argv[2:]:
        if arg in ('--test', '-test'):
            mode = '测试'
        elif arg.startswith('--url'):
            # 支持 --url=xxx 或 --url xxx
            if '=' in arg:
                test_url = arg.split('=', 1)[1]
            else:
                idx = sys.argv.index(arg)
                if idx + 1 < len(sys.argv):
                    test_url = sys.argv[idx + 1]
        elif date_str is None and not arg.startswith('--'):
            date_str = arg
    if test_url:
        print(f"测试抓取单个新闻链接: {test_url}")
        content, wordcount, used_custom = fetch_article_content(test_url)
        print(f"\n【抓取结果】\n字数: {wordcount}\n定制化: {used_custom}\n正文预览:\n{content[:500]}{'...' if len(content) > 500 else ''}")
    else:
        process_json(keyword, date_str, mode) 