import requests
from config import GNEWS_API_KEY, SERPAPI_KEY, DEFAULT_KEYWORDS
from datetime import datetime, timedelta
import json
from bs4 import BeautifulSoup
import re
import time
import os

def fetch_gnews(keyword, date=None, sortby="publishedAt"):
    if date is None:
        # 默认查找昨天
        date = (datetime.now() - timedelta(days=1)).date()
    from_date = f"{date}T00:00:00Z"
    to_date = f"{date}T23:59:59Z"
    url = "https://gnews.io/api/v4/search"
    params = {
        "q": keyword,
        # "lang": "zh",  # 放宽条件，去掉语言限制
        "from": from_date,
        "to": to_date,
        "max": 50,
        "sortby": sortby,
        "apikey": GNEWS_API_KEY
    }
    resp = requests.get(url, params=params)
    # 保存完整原始响应内容
    with open("gnews_raw_response.json", "w", encoding="utf-8") as f:
        f.write(resp.text)
    resp.raise_for_status()
    return resp.json()

def fetch_serpapi_google_news(keyword, max_pages=2):
    url = "https://serpapi.com/search.json"
    params = {
        "engine": "google_news",
        "q": keyword,
        "api_key": SERPAPI_KEY,
        "gl": "cn",
        "hl": "zh-CN",
        "tbs": "qdr:d",  # 新增：只看过去一天的新闻
        "num": "100"      # 新增：每页返回100条结果
    }
    all_results = []
    for page in range(max_pages):
        if page > 0:
            params["start"] = page * 10
        for attempt in range(3):
            try:
                resp = requests.get(url, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                results = data.get("news_results", [])
                if not results:
                    break
                all_results.extend(results)
                break  # 成功则跳出重试
            except Exception as e:
                if attempt == 2:
                    log_error(f"google_news", keyword, str(e))
                else:
                    time.sleep(3)
        else:
            # 3次都失败
            return {"news_results": []}
    return {"news_results": all_results}

def fetch_serpapi_baidu_news(keyword):
    url = "https://serpapi.com/search.json"
    params = {
        "engine": "baidu_news",
        "q": keyword,
        "api_key": SERPAPI_KEY,
        "rtt": 4,  # 最终确认：使用 4 按时间排序，为后续筛选提供最全面的数据
        "rn": 50   # 一次性获取更多结果，以减少分页，节约API调用次数
    }
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()  # 成功时直接返回JSON数据
        except Exception as e:
            if attempt == 2:
                log_error(f"baidu_news", keyword, str(e))
            else:
                time.sleep(3)
    # 3次都失败
    return {"organic_results": []}

def fetch_baidu_news_web(keyword="养老", max_pages=3):
    """
    通过网页方式抓取百度新闻搜索结果，支持关键词和分页。
    返回结构：[{'title':..., 'link':..., 'source':..., 'date':...}, ...]
    """
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    }
    for page in range(max_pages):
        pn = page * 20  # 百度新闻每页20条
        url = f"https://www.baidu.com/s?tn=news&rtt=4&bsst=1&cl=2&wd={keyword}&pn={pn}&medium=0"
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for item in soup.select(".result"):
            title_tag = item.select_one("h3")
            link = title_tag.a["href"] if title_tag and title_tag.a else ""
            title = title_tag.get_text(strip=True) if title_tag else ""
            source_tag = item.select_one(".c-author")
            date_str = ""
            source = ""
            if source_tag:
                # 例："澎湃新闻客户端 2024-05-25 18:47"
                text = source_tag.get_text(strip=True)
                m = re.match(r"(.+?)\\s+(\\d{4}-\\d{2}-\\d{2}.*)", text)
                if m:
                    source, date_str = m.groups()
                else:
                    source = text
            results.append({
                "title": title,
                "link": link,
                "source": source,
                "date": date_str
            })
    return results

def fetch_serpapi_bing_news(keyword, max_pages=2):
    url = "https://serpapi.com/search.json"
    params = {
        "engine": "bing_news",
        "q": keyword,
        "api_key": SERPAPI_KEY,
        "mkt": "zh-hk",
        "qft": 'interval="7"',  # 修改：从8(一周)改为7(一天)
        "count": "50"           # 新增：每页返回50条结果
    }
    all_results = []
    for page in range(max_pages):
        if page > 0:
            params["first"] = page * 10
        for attempt in range(3):
            try:
                resp = requests.get(url, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                results = data.get("organic_results", [])
                if not results:
                    break
                all_results.extend(results)
                break
            except Exception as e:
                if attempt == 2:
                    log_error(f"bing_news", keyword, str(e))
                else:
                    time.sleep(3)
        else:
            return {"organic_results": []}
    return {"organic_results": all_results}

def fetch_serpapi_duckduckgo_news(keyword, max_pages=2):
    """
    通过SerpApi DuckDuckGo News API获取新闻，拉取一天内新闻，分页，自动重试，错误日志。
    返回结构：{'news_results': [...]}，内容为原始news_results
    """
    url = "https://serpapi.com/search.json"
    params = {
        "engine": "duckduckgo_news",
        "q": keyword,
        "api_key": SERPAPI_KEY,
        "kl": "cn-zh",
        "df": "d"  # 修改：从w(一周)改为d(一天)
    }
    all_results = []
    for page in range(max_pages):
        if page > 0:
            params["start"] = page * 30  # duckduckgo_news每页最多30条
        for attempt in range(3):
            try:
                resp = requests.get(url, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                results = data.get("news_results", [])
                if not results:
                    break
                all_results.extend(results)
                break
            except Exception as e:
                if attempt == 2:
                    log_error(f"duckduckgo_news", keyword, str(e))
                else:
                    time.sleep(3)
        else:
            return {"news_results": []}
    return {"news_results": all_results}

def log_error(source, keyword, error_msg):
    # 记录到 output/error_log.txt
    log_dir = "output"
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "error_log.txt")
    with open(log_path, "a", encoding="utf-8") as f:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{now}] {source} | {keyword} | {error_msg}\n")

if __name__ == "__main__":
    import sys
    import os
    
    def run_for_keyword(keyword, date_str=None):
        if date_str is None:
            date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"[INFO] 抓取关键词: {keyword} 日期: {date_str}")
        all_news = []
        # 各采集源
        baidu = fetch_serpapi_baidu_news(keyword).get("organic_results", [])
        print(f"  Baidu News: {len(baidu)} 条")
        all_news.extend(baidu)
        google = fetch_serpapi_google_news(keyword).get("news_results", [])
        print(f"  Google News: {len(google)} 条")
        all_news.extend(google)
        bing = fetch_serpapi_bing_news(keyword).get("organic_results", [])
        print(f"  Bing News: {len(bing)} 条")
        all_news.extend(bing)
        duck = fetch_serpapi_duckduckgo_news(keyword).get("news_results", [])
        print(f"  DuckDuckGo News: {len(duck)} 条")
        all_news.extend(duck)
        # 保存
        outdir = os.path.join("output", date_str)
        os.makedirs(outdir, exist_ok=True)
        outpath = os.path.join(outdir, f"news_fetcher_{keyword}_{date_str}.json")
        with open(outpath, "w", encoding="utf-8") as f:
            json.dump(all_news, f, ensure_ascii=False, indent=2)
        print(f"[INFO] 共采集 {len(all_news)} 条，已保存到 {outpath}")

    if len(sys.argv) > 1:
        # 指定关键词
        run_for_keyword(sys.argv[1])
    else:
        # 批量模式
        for kw in DEFAULT_KEYWORDS:
            run_for_keyword(kw) 