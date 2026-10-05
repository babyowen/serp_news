"""Extraction-layer errors must allow the following fallback to run."""
import json
import types

import pytest

URL = 'https://example.com/news/article'
BODY = '新闻正文说明了服务事项、时间安排、保障机制和办理流程。' * 5


@pytest.mark.parametrize('failed_layer', ['trafilatura', 'newspaper'])
def test_content_fallback_exception_does_not_prevent_later_extractor(monkeypatch, failed_layer):
    import fetch_content as fc
    monkeypatch.setattr(fc, 'CUSTOM_GRAB_RULES', [])
    monkeypatch.setattr(fc, 'fetch_article_content_with_requests', lambda _: ('', 0, False))
    def download(_):
        if failed_layer == 'trafilatura': raise RuntimeError('earlier fetch failed')
        return None
    monkeypatch.setattr(fc.trafilatura, 'fetch_url', download)
    class Article:
        def __init__(self, *a, **k): pass
        def download(self): raise RuntimeError('earlier download failed')
    monkeypatch.setattr(fc, 'Article', Article)
    monkeypatch.setattr(fc, 'sync_playwright', lambda: (_ for _ in ()).throw(RuntimeError('renderer unavailable')))
    monkeypatch.setattr(fc, 'fetch_article_content_with_selenium', lambda _: (BODY, len(BODY), False))
    assert fc.fetch_article_content(URL, max_retries=1) == (BODY, len(BODY), False)


def test_content_fallback_garbled_extraction_allows_next_method(monkeypatch):
    import fetch_content as fc
    monkeypatch.setattr(fc, 'CUSTOM_GRAB_RULES', [])
    monkeypatch.setattr(fc, 'fetch_article_content_with_requests', lambda _: ('', 0, False))
    monkeypatch.setattr(fc.trafilatura, 'fetch_url', lambda _: '<html>bad</html>')
    monkeypatch.setattr(fc.trafilatura, 'extract', lambda *a, **k: json.dumps({'text': '?' * 100}))
    class Article:
        title = '有效新闻'
        text = BODY
        def __init__(self, *a, **k): pass
        def download(self): pass
        def parse(self): pass
    monkeypatch.setattr(fc, 'Article', Article)
    assert fc.fetch_article_content(URL, max_retries=1) == (BODY, len(BODY), False)


def test_content_fallback_lightweight_result_avoids_browser(monkeypatch):
    import fetch_content as fc
    monkeypatch.setattr(fc, 'CUSTOM_GRAB_RULES', [])
    monkeypatch.setattr(fc, 'fetch_article_content_with_requests', lambda _: (BODY, len(BODY), False))
    monkeypatch.setattr(fc.trafilatura, 'fetch_url', lambda _: pytest.fail('valid lightweight body ignored'))
    assert fc.fetch_article_content(URL, max_retries=1) == (BODY, len(BODY), False)


def test_content_fallback_browser_is_closed_on_navigation_error(monkeypatch):
    import fetch_content as fc
    monkeypatch.setattr(fc, 'CUSTOM_GRAB_RULES', [])
    monkeypatch.setattr(fc, 'fetch_article_content_with_requests', lambda _: ('', 0, False))
    monkeypatch.setattr(fc.trafilatura, 'fetch_url', lambda _: None)
    class Article:
        text = ''
        def __init__(self, *a, **k): pass
        def download(self): pass
        def parse(self): pass
    monkeypatch.setattr(fc, 'Article', Article)
    class Browser:
        closed = False
        def new_page(self): return types.SimpleNamespace(goto=lambda *a, **k: (_ for _ in ()).throw(RuntimeError('navigation failed')))
        def close(self): self.closed = True
    browser = Browser()
    class Playwright:
        def __enter__(self): return types.SimpleNamespace(chromium=types.SimpleNamespace(launch=lambda **k: browser))
        def __exit__(self, *a): pass
    monkeypatch.setattr(fc, 'sync_playwright', Playwright)
    monkeypatch.setattr(fc, 'fetch_article_content_with_selenium', lambda _: (BODY, len(BODY), False))
    assert fc.fetch_article_content(URL, max_retries=1)[0] == BODY
    assert browser.closed


@pytest.mark.parametrize('text', ['ERROR: ACCESS DENIED\n' + 'blocked ' * 12,
    '{{title}} {{summary}} {{author}} ' * 8])
def test_content_fallback_http_error_or_template_text_is_rejected(monkeypatch, text):
    import fetch_content as fc
    class Response:
        content = ('<html><title>页面</title><body>' + text + '</body></html>').encode()
        apparent_encoding = 'utf-8'
        def raise_for_status(self): pass
    monkeypatch.setattr(fc.requests, 'get', lambda *a, **k: Response())
    monkeypatch.setattr(fc.trafilatura, 'extract', lambda *a, **k: json.dumps({'text': text}))
    class Article:
        def __init__(self, *a, **k): pass
        def set_html(self, *a): pass
        def parse(self): pass
    Article.text = text
    monkeypatch.setattr(fc, 'Article', Article)
    assert fc.fetch_article_content_with_requests(URL) == ('', 0, False)


@pytest.mark.parametrize('mixed', [False, True])
def test_topic_resume_preserves_verified_short_custom_body(tmp_path, monkeypatch, mixed):
    import fetch_content as fc
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('RUN_LOG_PATH', str(tmp_path/'run.log'))
    date = '2026-10-04'
    body = '公共服务大厅新增周末开放时段。'
    url = 'https://www.yicai.com/news/short-article.html'
    existing = {'title': '公共服务公告', 'link': url, 'content': body,
                'wordcount': len(body), 'custom_grab': True, 'score': 2,
                'publication_check': {'version': 1, 'target_date': date, 'status': 'accepted',
                    'reason': 'target_date', 'published_date': date, 'link': url, 'evidence': []}}
    rows = [existing]
    if mixed:
        other_url = 'https://example.com/new-article'
        rows.append({'title': '另一篇新闻', 'link': other_url, 'content': '', 'wordcount': 0,
            'publication_check': {'version': 1, 'target_date': date, 'status': 'accepted',
                'reason': 'target_date', 'published_date': date, 'link': other_url, 'evidence': []}})
    path = tmp_path/'output'/date/f'{date}_江苏机关事务.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(rows,ensure_ascii=False))
    monkeypatch.setattr(fc, 'fetch_article_content', lambda _: ('',0,False))
    assert fc.process_json('江苏机关事务',date)
    assert json.loads(path.read_text())[0] == existing


@pytest.mark.parametrize('previous_check', [None, {'version': 0, 'target_date': '2026-10-03', 'status': 'accepted'}])
def test_topic_date_recheck_failure_preserves_valid_short_custom_body(tmp_path, monkeypatch, previous_check):
    import fetch_content as fc
    from news_freshness import eligible
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('RUN_LOG_PATH', str(tmp_path/'run.log'))
    date = '2026-10-04';body = '公共服务大厅新增周末开放时段。'
    row = {'title': '公共服务公告', 'link': 'https://www.yicai.com/news/short-article.html',
           'content': body, 'wordcount': len(body), 'custom_grab': True, 'score': 2}
    if previous_check is not None: row['publication_check'] = previous_check
    path = tmp_path/'output'/date/f'{date}_江苏机关事务.json'
    path.parent.mkdir(parents=True);path.write_text(json.dumps([row],ensure_ascii=False))
    monkeypatch.setattr(fc, 'fetch_publication_page', lambda _: ('',''))
    monkeypatch.setattr(fc, 'fetch_article_content', lambda _: pytest.fail('cached body must not be fetched again'))
    assert fc.process_json('江苏机关事务',date)
    result = json.loads(path.read_text())[0]
    assert result['content'] == body and result['wordcount'] == len(body)
    assert result['custom_grab'] is True and result['score'] == 2
    assert result['publication_check']['status'] == 'pending'
    assert not eligible(result)
