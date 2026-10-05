"""Read the public detail data used by MSN article pages, without a browser."""
import re
import time
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from tencent_news_content import usable_article_text

NEWS_HOSTS = {'msn.cn', 'www.msn.cn', 'msn.com', 'www.msn.com'}
CHECK_VERSION = 1


def is_msn_news_url(url):
    try:
        parsed = urlsplit(url or '')
        return (parsed.scheme in {'http', 'https'} and parsed.hostname in NEWS_HOSTS
                and not parsed.username and not parsed.password)
    except (TypeError, ValueError):
        return False


def _identity(url):
    if not is_msn_news_url(url):
        return None
    match = re.fullmatch(r'/([a-zA-Z]{2,3}-[a-zA-Z]{2})/.*/ar-([a-zA-Z0-9]{6,40})/?', urlsplit(url).path)
    return (match.group(1).lower(), match.group(2)) if match else None


def _article(data, locale, article_id, endpoint):
    if (not isinstance(data, dict) or data.get('id') != article_id
            or data.get('type') != 'article' or data.get('locale') != locale
            or data.get('renderingRestriction') != 0
            or data.get('subscriptionProductType') != 0):
        return None
    title = data.get('title') if isinstance(data.get('title'), str) else ''
    fragment = data.get('body')
    text = ''
    if isinstance(fragment, str):
        soup = BeautifulSoup(fragment, 'html.parser')
        for tag in soup.find_all(['script', 'style', 'iframe', 'noscript']):
            tag.decompose()
        text = soup.get_text('\n', strip=True)
        if not usable_article_text(text, title):
            text = ''
    provider = data.get('provider')
    return {'id': article_id, 'locale': locale, 'title': title, 'content': text,
            'published_time': data.get('publishedDateTime') if isinstance(data.get('publishedDateTime'), str) else '',
            'source_url': data.get('sourceHref') if isinstance(data.get('sourceHref'), str) else '',
            'provider': provider.get('name', '') if isinstance(provider, dict) else '',
            'endpoint': endpoint}


def fetch_msn_article(url, max_retries=1, retry_interval=10):
    """One normal request returns matching public body and publication metadata.

    Transport/JSON errors propagate to the shared retry or quarantine caller.
    Invalid identities and restricted/non-article payloads never become bodies.
    """
    identity = _identity(url)
    if not identity:
        return None
    locale, article_id = identity
    endpoint = f'https://assets.msn.com/content/view/v2/Detail/{locale}/{article_id}'
    for attempt in range(max_retries):
        try:
            response = requests.get(endpoint, timeout=(5, 15))
            response.raise_for_status()
            return _article(response.json(), locale, article_id, endpoint)
        except (requests.RequestException, ValueError):
            if attempt == max_retries - 1:
                raise
            time.sleep(retry_interval)
    return None
