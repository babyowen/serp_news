# -*- coding: utf-8 -*-
# =========================================
# 新闻正文抓取主程序
# 主要功能：多方式抓取新闻正文，支持定制化规则、trafilatura、newspaper3k、Selenium、Playwright等，抓取结果写入json并记录日志
# =========================================
import os
import sys
import json
from datetime import datetime, timedelta
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
from config import DEFAULT_KEYWORDS
import re
from error_handler import (
    setup_global_exception_handler,
    with_error_handling,
    log_script_start,
    log_script_complete,
    ErrorHandler
)

# 导入图标管理系统
from icon_manager import safe_print, get_icon
from logger_utils import NewsLogger

# 设置全局异常处理器
setup_global_exception_handler()

# 创建新闻日志记录器
news_logger = NewsLogger()

# 判断文本是否为乱码
def is_garbled(text):
    # 1. 乱码特征：大量非中文、非英文字符
    if not text:
        return False
    # 2. 统计可见字符比例
    visible_chars = re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9]', text)
    if len(visible_chars) / max(len(text), 1) < 0.2:
        return True
    # 3. 连续问号/乱码符号
    if re.search(r'[?]{10,}', text):
        return True
    # 4. 长度异常
    if len(text) > 20000:
        return True
    return False

# 获取今天日期字符串
def get_today_str():
    return datetime.now().strftime('%Y-%m-%d')

# 获取昨天日期字符串
def get_yesterday_str():
    yesterday = datetime.now() - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")

# 获取指定关键词和日期的json路径
def get_json_path(keyword, date_str=None):
    if date_str is None:
        date_str = get_today_str()
    filename = f"{date_str}_{keyword}.json"
    return os.path.join('output', date_str, filename)

# 获取日志文件路径
def get_log_path(date_str=None):
    return os.environ.get("RUN_LOG_PATH", os.path.join('output', 'run_log.txt'))

# 获取chromedriver路径（自动下载）
def get_chromedriver_path():
    # 只需这样即可，chromedriver 会自动下载到默认缓存目录
    driver_path = ChromeDriverManager().install()
    return driver_path

# 获取针对Windows优化的Chrome选项
def get_chrome_options_for_windows():
    """
    获取针对Windows环境优化的Chrome选项，减少SSL错误和日志输出
    """
    options = Options()
    
    # 基础无头模式配置
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    
    # Windows特有的SSL和安全配置
    options.add_argument('--ignore-certificate-errors')
    options.add_argument('--ignore-ssl-errors')
    options.add_argument('--ignore-certificate-errors-spki-list')
    options.add_argument('--ignore-urlfetcher-cert-requests')
    options.add_argument('--disable-web-security')
    options.add_argument('--allow-running-insecure-content')
    options.add_argument('--disable-features=VizDisplayCompositor')
    options.add_argument('--disable-site-isolation-trials')
    
    # Windows下的日志和输出控制（最重要的部分）
    options.add_argument('--disable-logging')
    options.add_argument('--log-level=3')  # 只显示致命错误
    options.add_argument('--silent')
    options.add_argument('--disable-dev-tools')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-plugins')
    options.add_argument('--disable-background-networking')
    options.add_argument('--disable-background-timer-throttling')
    options.add_argument('--disable-backgrounding-occluded-windows')
    options.add_argument('--disable-renderer-backgrounding')
    options.add_argument('--disable-device-discovery-notifications')
    options.add_argument('--disable-infobars')
    options.add_argument('--disable-notifications')
    options.add_argument('--disable-desktop-notifications')
    
    # 性能优化（Windows特有）
    options.add_argument('--disable-images')
    options.add_argument('--disable-javascript')  # 大多数新闻网站不需要JS
    options.add_argument('--disable-plugins')
    options.add_argument('--disable-java')
    options.add_argument('--disable-flash')
    options.add_argument('--disable-popup-blocking')
    options.add_argument('--disable-translate')
    options.add_argument('--disable-features=TranslateUI')
    options.add_argument('--disable-ipc-flooding-protection')
    
    # 网络和连接优化
    options.add_argument('--aggressive-cache-discard')
    options.add_argument('--disable-background-sync')
    options.add_argument('--disable-sync')
    options.add_argument('--disable-default-apps')
    options.add_argument('--disable-component-update')
    
    # Windows特有的Chrome进程管理
    options.add_argument('--disable-hang-monitor')
    options.add_argument('--disable-prompt-on-repost')
    options.add_argument('--disable-domain-reliability')
    options.add_argument('--disable-component-extensions-with-background-pages')
    
    # User-Agent伪装
    options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
    
    # 设置页面加载策略
    options.page_load_strategy = 'eager'  # 不等待所有资源加载完成
    
    # Windows专用实验性选项
    options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_experimental_option('detach', True)
    
    # Windows环境下的首选项设置
    prefs = {
        'profile.default_content_setting_values': {
            'notifications': 2,  # 阻止通知
            'media_stream': 2,   # 阻止媒体流
            'geolocation': 2,    # 阻止地理位置
            'desktop_notifications': 2,  # 阻止桌面通知
        },
        'profile.default_content_settings.popups': 0,  # 阻止弹窗
        'profile.managed_default_content_settings.images': 2,  # 阻止图片
    }
    options.add_experimental_option('prefs', prefs)
    
    return options

# 获取针对Windows优化的Chrome服务配置
def get_chrome_service_for_windows():
    """
    获取针对Windows环境优化的Chrome服务配置
    """
    driver_path = get_chromedriver_path()
    service = Service(driver_path)
    
    # Windows环境下的服务配置
    service.log_level = 'ERROR'  # 只输出错误级别的日志
    
    # 在Windows环境下设置service参数
    if sys.platform.startswith('win'):
        try:
            # Windows下尝试隐藏控制台窗口
            service.creation_flags = 0x08000000  # CREATE_NO_WINDOW
        except:
            # 如果设置失败，继续使用默认配置
            pass
    
    return service

# 用Selenium抓取新闻正文，支持定制化规则和通用抓取
def fetch_article_content_with_selenium(url):
    options = get_chrome_options_for_windows()
    service = get_chrome_service_for_windows()

    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(30)
    driver.implicitly_wait(10)

    custom_grab = False
    try:
        driver.get(url)
        time.sleep(5)
        for match_func, grab_func in CUSTOM_GRAB_RULES:
            if match_func(url.lower()):
                text, grab_type = grab_func(driver)
                return text, len(text), True
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
        return '', 0, custom_grab
    finally:
        try:
            driver.quit()
        except:
            pass

# 用requests+trafilatura/newspaper3k抓取正文，适合特殊编码站点
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

# 用Playwright渲染页面并抓取正文，支持定制化规则
def fetch_article_content_with_playwright(url):
    custom_grab = False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=30000)
            # 遍历定制化规则
            for match_func, grab_func in CUSTOM_GRAB_RULES:
                if match_func(url.lower()):
                    grab_func_name = grab_func.__name__ + '_playwright'
                    if grab_func_name in globals():
                        text, grab_type = globals()[grab_func_name](page)
                        browser.close()
                        return text, len(text), True
                    else:
                        browser.close()
                        return '', 0, False
            browser.close()
        return '', 0, False
    except Exception as e:
        return '', 0, False

# 综合抓取正文的核心实现（不包含重试逻辑）
def _fetch_article_content_core(url):
    # 1. msn.cn 直接跳过抓取
    if 'msn.cn' in url:
        return '', 0, False
    # 2. 其它站点走原有流程
    for match_func, grab_func in CUSTOM_GRAB_RULES:
        if match_func(url.lower()):
            try:
                text, grab_type = grab_func(fetch_article_content_with_selenium_driver(url))
                if is_garbled(text):
                    return '', 0, False
                if text and len(text) > 0:
                    return text, len(text), True
            except Exception:
                raise
    # 针对GBK/GB2312等特殊站点优先用requests自动编码识别
    if any(domain in url for domain in ['jxnews.com.cn']):
        text, wc, used_custom = fetch_article_content_with_requests(url)
        if is_garbled(text):
            return '', 0, False
        if text and wc > 50:
            return text, wc, used_custom
    # 2. trafilatura
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            result = trafilatura.extract(downloaded, output_format='json')
            if result:
                data = json.loads(result)
                text = data.get('text', '')
                if is_garbled(text):
                    return '', 0, False
                if text and len(text) > 50:
                    return text, len(text), False
    except Exception:
        raise
    # 3. newspaper3k
    try:
        article = Article(url, language='zh')
        article.download()
        article.parse()
        text = article.text
        if is_garbled(text):
            return '', 0, False
        if text and len(text) > 50:
            return text, len(text), False
    except Exception:
        raise
    # 4. Playwright渲染+正文提取
    try:
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
            if is_garbled(text):
                return '', 0, False
            if text and len(text) > 50:
                return text, len(text), False
        except Exception:
            pass
        # 4.2 Readability 提取
        try:
            doc = Document(html_content)
            text = doc.summary()
            soup = BeautifulSoup(text, 'html.parser')
            pure_text = soup.get_text(separator='\n').strip()
            if is_garbled(pure_text):
                return '', 0, False
            if pure_text and len(pure_text) > 50:
                return pure_text, len(pure_text), False
        except Exception:
            pass
    except Exception:
        raise
    # 5. Selenium定制化兜底
    try:
        text, wc, custom_grab = fetch_article_content_with_selenium(url)
        if is_garbled(text):
            return '', 0, False
        if custom_grab or (text and wc > 50):
            return text, wc, custom_grab
    except Exception:
        raise
    return '', 0, False

# 带重试机制的正文抓取包装函数
def fetch_article_content(url, max_retries=3, retry_interval=10):
    last_exception = None

    for attempt in range(max_retries):
        try:
            content, wordcount, custom_grab = _fetch_article_content_core(url)
            if wordcount > 0:
                return content, wordcount, custom_grab
            if attempt < max_retries - 1:
                time.sleep(retry_interval)
        except Exception as e:
            last_exception = e
            if attempt < max_retries - 1:
                time.sleep(retry_interval)

    return '', 0, False

# 用Selenium驱动返回driver对象，供定制化规则使用
def fetch_article_content_with_selenium_driver(url):
    options = get_chrome_options_for_windows()
    service = get_chrome_service_for_windows()

    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(30)
    driver.implicitly_wait(10)

    try:
        driver.get(url)
        time.sleep(5)
        return driver
    except Exception:
        try:
            driver.quit()
        except:
            pass
        raise

# 获取url的主域名
def get_domain(url):
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.netloc
    except Exception:
        return ''

# 处理指定关键词和日期的json，抓取正文并写入，统计日志
@with_error_handling("fetch_content.py", "正文抓取处理")
def process_json(keyword, date_str=None, mode='正式'):
    json_path = get_json_path(keyword, date_str)
    log_path = get_log_path(date_str)
    if not os.path.exists(json_path):
        print(f"File not found: {json_path}")
        return False  # 文件不存在算失败
    with open(json_path, 'r', encoding='utf-8') as f:
        news_list = json.load(f)
    # 跳过机制：如所有新闻条目都已包含content字段（不论内容是否为空），说明已跑过正文抓取，无需重复处理
    # 但是如果新闻列表为空，则不应该跳过
    all_has_content_field = len(news_list) > 0 and all('content' in item for item in news_list)
    if all_has_content_field:
        print(f"[SKIP] {json_path} 所有新闻已包含content字段，跳过")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        skip_log = f"[{now}] [SKIP] 正文抓取: {keyword} 已完成\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(skip_log)
        return True  # 跳过也算成功
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
    for item in news_list:
        url = item.get('link')
        curr_domain = get_domain(url) if url else None
        # 只在抓取正文时加间隔
        if prev_domain and curr_domain and prev_domain == curr_domain:
            time.sleep(random.uniform(1, 4))
        prev_domain = curr_domain
        # 跳过tv.cctv.com，仅日志记录
        if url and 'tv.cctv.com' in url:
            continue
        if not url:
            item['content'] = ''
            item['wordcount'] = 0
            item['custom_grab'] = False
            filtered_news_list.append(item)
            continue
        
        # 增强日志记录：添加关键词和新闻标题信息
        title = item.get('title', '无标题')
        news_logger.content_fetch_start(keyword, title, url)
        
        try:
            content, wordcount, custom_grab = fetch_article_content(url)
            item['content'] = content
            item['wordcount'] = wordcount
            item['custom_grab'] = custom_grab
            
            # 记录抓取结果
            if wordcount > 0:
                grab_type = "定制化" if custom_grab else "通用"
                news_logger.content_fetch(keyword, wordcount, grab_type, "success")
            else:
                news_logger.content_fetch(keyword, 0, "通用", "failed")
                
        except Exception as e:
            news_logger.content_fetch(keyword, 0, "通用", "error", str(e))
            safe_print(f"[{keyword}] 标题: {title}")
            safe_print(f"[{keyword}] URL: {url}")
            
            # 记录到错误日志
            error_handler = ErrorHandler()
            error_handler.log_error(
                error_type="CONTENT_FETCH_ERROR",
                error_msg=f"抓取正文时发生异常: {str(e)}",
                script_name="fetch_content.py",
                keyword=keyword,
                context={"url": url, "title": title}
            )
            
            # 设置默认值，继续处理
            item['content'] = ''
            item['wordcount'] = 0
            item['custom_grab'] = False
            
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
    current_domains = set()  # 只统计本次成功抓取正文的新闻源域名
    domain_news_count = {}
    for item in filtered_news_list:
        url = item.get('link')
        curr_domain = get_domain(url) if url else None
        # 只统计成功抓取正文的新闻源
        if curr_domain and item.get('wordcount', 0) > 0:
            current_domains.add(curr_domain)
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
    # 读取历史域名并合并
    all_domains = set(current_domains)
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
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_content = f"\n[{now}]\n"
    log_content += f"执行程序: 正文抓取\n"
    log_content += f"[关键词] {keyword}\n"
    log_content += f"[统计] 成功: {success_count}, 定制: {len(custom_used_items)}, 失败: {len(fail_items)}\n"
    if fail_items:
        log_content += f"[失败] 未抓取正文: {len(fail_items)}篇\n"
        for fail in fail_items[:5]:  # 最多列出5条
            log_content += f"  - {fail['title'][:40]} | {fail['link']}\n"
        if len(fail_items) > 5:
            log_content += f"  ... 还有{len(fail_items)-5}条\n"
    log_content += f"==============================\n"

    with open(log_path, 'a', encoding='utf-8') as logf:
        logf.write(log_content)
    print(f"Updated: {json_path}")
    print(f"日志已写入 {log_path}")
    print(f"已写入新闻来源统计: {sources_path}")
    print(f"已写入新闻源分布统计: {stats_path}")
    
    # 返回True表示执行成功
    return True

# 命令行入口，支持批量/单条/测试模式
@with_error_handling("fetch_content.py", "main")
def main():
    """主函数：处理命令行参数和执行正文抓取"""
    # 记录脚本开始
    log_script_start("fetch_content.py", sys.argv[1:])
    
    error_handler = ErrorHandler()
    success = True
    
    try:
        if len(sys.argv) > 2:
            keyword = sys.argv[1]
            date_str = sys.argv[2]
            if not date_str or date_str.lower() == 'none':
                date_str = get_yesterday_str()
            mode = '正式'
            test_url = None
            for arg in sys.argv[3:]:
                if arg in ('--test', '-test'):
                    mode = '测试'
                elif arg.startswith('--url'):
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
                result = process_json(keyword, date_str, mode)
                if result is None or result is False:
                    success = False
        elif len(sys.argv) > 1:
            keyword = sys.argv[1]
            date_str = get_yesterday_str()
            mode = '正式'
            result = process_json(keyword, date_str, mode)
            if result is None or result is False:
                success = False
        else:
            # 无参数，自动批量处理昨天所有关键词
            date_str = get_yesterday_str()
            mode = '正式'
            processed_count = 0
            total_count = len(DEFAULT_KEYWORDS)
            
            for keyword in DEFAULT_KEYWORDS:
                json_path = os.path.join("output", date_str, f"{date_str}_{keyword}.json")
                print(f"自动抓取: {json_path}")
                result = process_json(keyword, date_str, mode)
                if result is not None and result is not False:
                    processed_count += 1
                else:
                    success = False
            
            print(f"\n[统计] 批量处理完成：{processed_count}/{total_count} 个关键词处理成功")
    
    except Exception as e:
        error_msg = f"fetch_content.py 执行过程中发生异常: {str(e)}"
        print(f"[ERROR] {error_msg}")
        error_handler.log_error(
            error_type="MAIN_FUNCTION_ERROR",
            error_msg=error_msg,
            script_name="fetch_content.py"
        )
        success = False
    
    # 记录脚本完成
    status_msg = "执行成功" if success else "执行过程中出现错误"
    log_script_complete("fetch_content.py", success=success, message=status_msg)
    
    return success

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1) 