import requests
from config import GNEWS_API_KEY, SERPAPI_KEY
from datetime import datetime, timedelta
import json
from bs4 import BeautifulSoup
import re

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

def fetch_serpapi_google_news(keyword, max_pages=5):
    url = "https://serpapi.com/search.json"
    params = {
        "engine": "google_news",
        "q": keyword,
        "api_key": SERPAPI_KEY,
        "gl": "cn",
        "hl": "zh-CN"
    }
    all_results = []
    for page in range(max_pages):
        if page > 0:
            params["start"] = page * 10
        resp = requests.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("news_results", [])
        if not results:
            break
        all_results.extend(results)
    return {"news_results": all_results}

def fetch_serpapi_baidu_news(keyword, max_pages=5):
    url = "https://serpapi.com/search.json"
    params = {
        "engine": "baidu_news",
        "q": keyword,
        "api_key": SERPAPI_KEY,
        "rtt": 4  # 按时间排序
    }
    all_results = []
    for page in range(max_pages):
        params["pn"] = page * 10
        resp = requests.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("organic_results", [])
        if not results:
            break
        all_results.extend(results)
    return {"organic_results": all_results}

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

def fetch_serpapi_bing_news(keyword, max_pages=5):
    url = "https://serpapi.com/search.json"
    params = {
        "engine": "bing_news",
        "q": keyword,
        "api_key": SERPAPI_KEY,
        "mkt": "zh-hk",
        "qft": 'interval="8"'
    }
    all_results = []
    for page in range(max_pages):
        if page > 0:
            params["first"] = page * 10
        resp = requests.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("organic_results", [])
        if not results:
            break
        all_results.extend(results)
    return {"organic_results": all_results} 