"""Read public Tencent article data without executing the page's JavaScript."""
import json
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

NEWS_HOSTS = {'news.qq.com', 'new.qq.com', 'view.inews.qq.com', 'www.inews.qq.com'}


def is_tencent_news_url(url):
    try:
        parsed = urlsplit(url or '')
        return parsed.scheme in {'http', 'https'} and parsed.hostname in NEWS_HOSTS
    except (TypeError, ValueError):
        return False


def article_text_is_placeholder(text):
    """Reject response errors and unrendered templates, including HTTP-200 shells."""
    if not isinstance(text, str) or not text.strip():
        return True
    text = text.strip()
    if re.match(r'^(?:[45]\d\d\s+(?:ERROR|Bad Gateway|Forbidden|Not Found)|ERROR:\s*ACCESS DENIED|Access Denied|Request blocked|Just a moment|Verify you are human|页面不存在|您访问的页面不存在)', text, re.I):
        return True
    if '{{title}}' in text and len(re.findall(r'\{\{[^{}]+\}\}', text)) >= 3:
        return True
    return '$article.EXT_CLOB' in text


def usable_article_text(text, title=''):
    """A long headline, including a Tencent/MSN site suffix, is still not a body."""
    if not isinstance(text, str) or not 50 < len(text.strip()) <= 20000:
        return False
    if article_text_is_placeholder(text):
        return False
    if len(re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9]', text)) / len(text) < 0.2 or re.search(r'[?]{10,}', text):
        return False
    def normalize(value):
        value = re.sub(r'\s*[_\-|｜]\s*(?:腾讯新闻|MSN|上观新闻)\s*$', '', str(value or '').strip(), flags=re.I)
        return re.sub(r'\s+', '', value)
    return bool(normalize(text)) and normalize(text) != normalize(title)


def html_title(html):
    tag = BeautifulSoup(html or '', 'html.parser').find('title')
    return tag.get_text(' ', strip=True) if tag else ''


def extract_tencent_content(url, html):
    """Bind window.DATA to the URL's article ID and extract its public body."""
    if not is_tencent_news_url(url):
        return ''
    path = urlsplit(url).path
    match = re.fullmatch(r'/(?:rain/a|a|omn/\d{8})/([A-Za-z0-9]+)(?:\.html)?/?', path)
    if not match:
        return ''
    article_id = match.group(1)
    soup = BeautifulSoup(html or '', 'html.parser')
    for script in soup.find_all('script'):
        raw = script.string or script.get_text()
        for assignment in re.finditer(r'\bwindow\.DATA\s*=\s*', raw):
            try:
                data, _ = json.JSONDecoder().raw_decode(raw[assignment.end():].lstrip())
            except (ValueError, RecursionError):
                continue
            if not isinstance(data, dict) or data.get('article_id') != article_id:
                continue
            if data.get('article_is_pay') in (True, 1, '1'):
                continue
            origin = data.get('originContent')
            fragment = origin.get('text') if isinstance(origin, dict) else None
            if not isinstance(fragment, str) or not fragment.strip():
                continue
            body = BeautifulSoup(fragment, 'html.parser')
            for tag in body.find_all(['script', 'style', 'iframe', 'noscript']):
                tag.decompose()
            text = body.get_text('\n', strip=True)
            if usable_article_text(text, data.get('title') or html_title(html)):
                return text
    return ''
