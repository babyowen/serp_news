"""Read public Shangguan article data used by its own news-detail pages."""
from datetime import datetime, timezone
import re
import time
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from tencent_news_content import usable_article_text

NEWS_HOSTS = {'jfdaily.com', 'www.jfdaily.com', 'jfdaily.com.cn', 'www.jfdaily.com.cn',
              'shobserver.cn', 'www.shobserver.cn'}
ARTICLE_PATHS = {'/news/detail', '/news/detail.do', '/wx/detail', '/wx/detail.do',
                 '/staticsg/res/html/web/newsDetail.html'}
CHECK_VERSION = 1


def is_jfdaily_news_url(url):
    try:
        parsed = urlsplit(url or '')
        return (parsed.scheme in {'http', 'https'} and parsed.hostname in NEWS_HOSTS
                and not parsed.username and not parsed.password
                and parsed.port in (None, 80, 443))
    except (TypeError, ValueError):
        return False


def _identity(url):
    if not is_jfdaily_news_url(url):
        return None
    parsed = urlsplit(url)
    values = parse_qs(parsed.query, keep_blank_values=True).get('id', [])
    if parsed.path.rstrip('/') not in ARTICLE_PATHS or len(values) != 1:
        return None
    return values[0] if re.fullmatch(r'[1-9][0-9]{0,11}', values[0]) else None


def _publication_time(raw):
    # The service publishes Unix milliseconds; seconds and other timestamps
    # must remain unverified rather than becoming a false date in 1970.
    if isinstance(raw, bool) or not isinstance(raw, int) or not 10**12 <= raw < 10**13:
        return ''
    try:
        return datetime.fromtimestamp(raw / 1000, timezone.utc).astimezone(
            ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
    except (OverflowError, ValueError, OSError):
        return ''


def _article(data, article_id, endpoint, source_url):
    if not isinstance(data, dict) or data.get('success') is not True:
        return None
    obj = data.get('object')
    if (not isinstance(obj, dict) or str(obj.get('id')) != article_id
            or isinstance(obj.get('newstype'), bool) or obj.get('newstype') not in (0, '0')
            or obj.get('ismember') not in (False, 0, '0')):
        return None
    title = obj.get('title') if isinstance(obj.get('title'), str) else ''
    fragment = obj.get('detail')
    text = ''
    if isinstance(fragment, str):
        soup = BeautifulSoup(fragment, 'html.parser')
        for tag in soup.find_all(['script', 'style', 'iframe', 'noscript']):
            tag.decompose()
        text = soup.get_text('\n', strip=True)
        if not usable_article_text(text, title):
            text = ''
    return {'id': article_id, 'title': title, 'content': text,
            'published_time': _publication_time(obj.get('publishtime')),
            'raw_published_time': obj.get('publishtime'),
            'endpoint': endpoint, 'source_url': source_url}


def fetch_jfdaily_article(url, max_retries=1, retry_interval=10):
    """Fetch a matching public text article, without rendering a template shell."""
    article_id = _identity(url)
    if not article_id:
        return None
    endpoint = 'https://www.jfdaily.com/news/getNewsDetail?id=' + article_id
    for attempt in range(max_retries):
        try:
            response = requests.get(endpoint, timeout=(5, 15))
            response.raise_for_status()
            return _article(response.json(), article_id, endpoint, url)
        except (requests.RequestException, ValueError):
            if attempt == max_retries - 1:
                raise
            time.sleep(retry_interval)
    return None
