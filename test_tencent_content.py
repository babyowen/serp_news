"""Tencent extraction is shared by all keywords and preserves date quarantine."""
import json

import pytest

DATE = '2026-10-04'
ARTICLE_ID = '20261004A0123400'
URL = 'https://news.qq.com/rain/a/' + ARTICLE_ID
TITLE = '这是一条腾讯新闻测试标题用于验证长标题也不会误判为正文' * 3
BODY = '图书馆向市民开放服务，工作人员落实场地维护和车辆引导。' * 4


def page(data=None, date=DATE, prefix=''):
    if data is None:
        data = {'article_id': ARTICLE_ID, 'title': TITLE, 'article_is_pay': 0,
                'originContent': {'text': '<p>' + BODY + '</p>'},
                'pubtime': DATE + ' 08:30:00'}
    return (f'<meta name="pubdate" content="{date}"><title>{TITLE}_腾讯新闻</title>'
            f'<article>{TITLE}</article><script>{prefix}window.DATA = '
            + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + '; window.other = 1;</script>')


def http(monkeypatch, html):
    import fetch_content
    requests = []

    class Response:
        apparent_encoding = 'utf-8'
        text = html
        def raise_for_status(self):
            pass

    def get(url, **kwargs):
        requests.append((url, kwargs))
        return Response()

    monkeypatch.setattr(fetch_content.requests, 'get', get)
    # Ordinary HTML extraction reproduces the production headline-only result.
    monkeypatch.setattr(fetch_content.trafilatura, 'extract',
                        lambda *a, **k: json.dumps({'text': TITLE, 'title': TITLE}))
    monkeypatch.setattr(fetch_content.trafilatura, 'fetch_url', lambda _: html)
    return requests


@pytest.mark.parametrize('url', [URL, 'https://view.inews.qq.com/a/' + ARTICLE_ID,
    'https://new.qq.com/omn/20261004/' + ARTICLE_ID + '.html',
    'https://news.qq.com/rain/a/' + ARTICLE_ID + '?from=search#detail'])
def test_publication_fetch_reads_embedded_body_for_tencent_entries(monkeypatch, url):
    import fetch_content
    html = page()
    calls = http(monkeypatch, html)
    text, original_html = fetch_content.fetch_publication_page(url)
    assert text == BODY
    assert original_html == html
    assert calls == [(url, {'timeout': (5, 15)})]


def test_shared_article_fetch_uses_one_http_request_without_browser(monkeypatch):
    import fetch_content
    calls = http(monkeypatch, page())
    monkeypatch.setattr(fetch_content, 'sync_playwright',
                        lambda: pytest.fail('browser launched for embedded body'))
    monkeypatch.setattr(fetch_content, 'fetch_article_content_with_selenium',
                        lambda _: pytest.fail('browser launched for embedded body'))
    assert fetch_content.fetch_article_content(URL, max_retries=1) == (BODY, len(BODY), True)
    assert len(calls) == 1


def test_requests_entrypoint_also_uses_embedded_body(monkeypatch):
    import fetch_content
    http(monkeypatch, page())
    assert fetch_content.fetch_article_content_with_requests(URL) == (BODY, len(BODY), True)


@pytest.mark.parametrize('data', [None, [], 'bad', {'article_id': ARTICLE_ID},
    {'article_id': ARTICLE_ID, 'originContent': []},
    {'article_id': ARTICLE_ID, 'originContent': {'text': None}},
    {'article_id': 'other', 'originContent': {'text': '<p>' + BODY + '</p>'}},
    {'article_id': ARTICLE_ID, 'article_is_pay': 1, 'originContent': {'text': BODY}},
    {'article_id': ARTICLE_ID, 'title': TITLE, 'originContent': {'text': TITLE}}])
def test_unusable_tencent_data_never_becomes_headline_body(monkeypatch, data):
    import fetch_content
    html = page(data) if data is not None else '<title>' + TITLE + '</title><script>window.DATA = {broken</script>'
    http(monkeypatch, html)
    text, original_html = fetch_content.fetch_publication_page(URL)
    assert text == ''
    assert original_html == html


def test_embedded_html_is_plain_text_and_ignores_executable_tags(monkeypatch):
    import fetch_content
    data = {'article_id': ARTICLE_ID, 'title': TITLE, 'originContent': {'text':
        '<p>' + BODY + '</p><script>alert(1)</script><style>secret-style</style>'
        '<iframe>foreign-frame</iframe><p>名单 &amp; 服务</p>'}}
    http(monkeypatch, page(data))
    assert fetch_content.fetch_publication_page(URL)[0] == BODY + '\n名单 & 服务'


def test_malformed_assignment_before_valid_data_can_recover(monkeypatch):
    import fetch_content
    http(monkeypatch, page(prefix='window.DATA = {broken; '))
    assert fetch_content.fetch_publication_page(URL)[0] == BODY


def test_non_tencent_page_keeps_ordinary_extraction(monkeypatch):
    import fetch_content
    http(monkeypatch, page())
    # The embedded Tencent-shaped data is untrusted on another host.
    assert fetch_content.fetch_publication_page('https://news.qq.com.evil.example/a/' + ARTICLE_ID)[0] == TITLE


def input_file(tmp_path, monkeypatch, keyword, rows):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('RUN_LOG_PATH', str(tmp_path / 'run.log'))
    path = tmp_path / 'output' / DATE / f'{DATE}_{keyword}.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(rows), encoding='utf-8')
    return path


@pytest.mark.parametrize('keyword', ['江苏机关事务', '养老', '公积金', '中国烟草', '烟草服务银行', '以后新增关键词'])
def test_any_keyword_repairs_headline_and_resumes_complete_body(tmp_path, monkeypatch, keyword):
    import fetch_content
    http(monkeypatch, page())
    path = input_file(tmp_path, monkeypatch, keyword,
        [{'title': TITLE + '_腾讯新闻', 'link': URL, 'content': TITLE, 'wordcount': len(TITLE)}])
    assert fetch_content.process_json(keyword, DATE)
    row = json.loads(path.read_text())[0]
    assert row['content'] == BODY and row['wordcount'] == len(BODY)
    assert row['title'] == TITLE + '_腾讯新闻' and row['link'] == URL
    monkeypatch.setattr(fetch_content.requests, 'get', lambda *a, **k: pytest.fail('completed body refetched'))
    assert fetch_content.process_json(keyword, DATE)


def test_mixed_keyword_file_preserves_complete_body_and_repairs_empty(tmp_path, monkeypatch):
    import fetch_content
    http(monkeypatch, page())
    path = input_file(tmp_path, monkeypatch, '未来关键词', [
        {'title': '原始完整内容', 'link': URL, 'content': BODY + '已存在的正文', 'wordcount': len(BODY) + 7},
        {'title': TITLE, 'link': URL, 'content': '', 'wordcount': 0}])
    monkeypatch.setattr(fetch_content.time, 'sleep', lambda _: None)
    assert fetch_content.process_json('未来关键词', DATE)
    rows = json.loads(path.read_text())
    assert rows[0]['content'] == BODY + '已存在的正文'
    assert rows[1]['content'] == BODY


@pytest.mark.parametrize('date,want', [('2025-10-04', 'old'), ('', 'pending')])
def test_date_quarantine_prevents_browser_fallback(tmp_path, monkeypatch, date, want):
    import fetch_content
    http(monkeypatch, page(date=date))
    path = input_file(tmp_path, monkeypatch, '江苏机关事务', [{'title': TITLE, 'link': URL}])
    monkeypatch.setattr(fetch_content, 'fetch_article_content', lambda _: pytest.fail('quarantined page refetched'))
    assert fetch_content.process_json('江苏机关事务', DATE)
    assert json.loads(path.read_text())[0]['publication_check']['status'] == want


def test_repair_rechecks_date_before_using_newly_fetched_body(tmp_path, monkeypatch):
    import fetch_content
    http(monkeypatch, page(date='2026-10-05'))
    path = input_file(tmp_path, monkeypatch, '江苏机关事务', [{'title': TITLE, 'link': URL,
        'content': TITLE, 'wordcount': len(TITLE), 'fetchdate': DATE,
        'publication_check': {'version': 1, 'target_date': DATE, 'status': 'accepted',
            'published_date': DATE, 'link': URL, 'evidence': [{'date': DATE}]}}])
    assert fetch_content.process_json('江苏机关事务', DATE)
    row = json.loads(path.read_text())[0]
    assert row['publication_check']['status'] == 'pending'
    assert row['publication_check']['reason'] == 'after_target_date'


def test_accepted_non_tencent_headline_uses_body_fallback(tmp_path, monkeypatch):
    import fetch_content
    http(monkeypatch, page())
    path = input_file(tmp_path, monkeypatch, '江苏机关事务', [{'title': TITLE, 'link': 'https://example.com/news'}])
    monkeypatch.setattr(fetch_content, 'fetch_article_content', lambda _: (BODY, len(BODY), False))
    assert fetch_content.process_json('江苏机关事务', DATE)
    assert json.loads(path.read_text())[0]['content'] == BODY


def test_legacy_non_tencent_empty_content_resume_is_unchanged(tmp_path, monkeypatch):
    import fetch_content
    path = input_file(tmp_path, monkeypatch, '养老', [{'title': TITLE, 'link': 'https://example.com/news', 'content': ''}])
    monkeypatch.setattr(fetch_content, 'fetch_article_content', lambda _: pytest.fail('legacy row unexpectedly refetched'))
    assert fetch_content.process_json('养老', DATE)
    assert json.loads(path.read_text())[0]['content'] == ''


@pytest.mark.parametrize('keyword', ['养老', '公积金', '中国烟草', '烟草服务银行', '数字政务', '任意未来关键词'])
def test_main_pipeline_reaches_tencent_repair_for_any_keyword(tmp_path, monkeypatch, keyword):
    import shlex
    import fetch_content
    import main
    http(monkeypatch, page())
    path = input_file(tmp_path, monkeypatch, keyword,
        [{'title': TITLE, 'link': URL, 'content': TITLE, 'wordcount': len(TITLE)}])
    def child(command, *args, **kwargs):
        argv = shlex.split(command)
        assert argv[1:] == ['fetch_content.py', keyword, DATE]
        return fetch_content.process_json(argv[2], argv[3])
    monkeypatch.setattr(main, 'safe_subprocess_run', child)
    assert main.execute_content_fetching(DATE, keyword)
    assert json.loads(path.read_text())[0]['content'] == BODY
    monkeypatch.setattr(main, 'safe_subprocess_run', lambda *a, **k: pytest.fail('complete Tencent body refetched'))
    assert main.execute_content_fetching(DATE, keyword)


def test_tencent_repair_preserves_other_sites_complete_body(tmp_path, monkeypatch):
    import fetch_content
    http(monkeypatch, page())
    original_fetch = fetch_content.fetch_article_content
    def fetch(url):
        if url == 'https://example.com/news':
            return '', 0, False  # A transient failure must not erase cached evidence.
        return original_fetch(url, max_retries=1)
    monkeypatch.setattr(fetch_content, 'fetch_article_content', fetch)
    path = input_file(tmp_path, monkeypatch, '未来关键词', [
        {'title': '其他网站已经抓好的文章', 'link': 'https://example.com/news',
         'content': BODY + '其他网站的内容', 'wordcount': len(BODY) + 8},
        {'title': TITLE, 'link': URL, 'content': '', 'wordcount': 0}])
    assert fetch_content.process_json('未来关键词', DATE)
    rows = json.loads(path.read_text())
    assert rows[0]['content'] == BODY + '其他网站的内容'
    assert rows[1]['content'] == BODY


def test_missing_embedded_data_uses_successful_generic_html_fallback(monkeypatch):
    import fetch_content
    http(monkeypatch, '<title>' + TITLE + '_腾讯新闻</title><article>' + BODY + '</article>')
    monkeypatch.setattr(fetch_content.trafilatura, 'extract',
        lambda *a, **k: json.dumps({'title': TITLE, 'text': BODY}))
    assert fetch_content.fetch_article_content(URL, max_retries=1) == (BODY, len(BODY), True)


def test_http_failure_during_cached_title_recheck_keeps_date_but_not_bad_body(tmp_path, monkeypatch):
    import fetch_content
    import news_freshness
    path = input_file(tmp_path, monkeypatch, '江苏机关事务', [{'title': TITLE, 'link': URL,
        'content': TITLE, 'wordcount': len(TITLE), 'fetchdate': DATE,
        'publication_check': {'version': 1, 'target_date': DATE, 'status': 'accepted',
            'published_date': DATE, 'link': URL, 'evidence': [{'date': DATE}]}}])
    def unavailable(*args, **kwargs):
        raise fetch_content.requests.HTTPError('HTTP 503')
    monkeypatch.setattr(fetch_content.requests, 'get', unavailable)
    monkeypatch.setattr(fetch_content, 'fetch_article_content', lambda _: ('', 0, False))
    assert fetch_content.process_json('江苏机关事务', DATE)
    row = json.loads(path.read_text())[0]
    assert row['publication_check']['status'] == 'accepted'
    assert row['publication_check']['published_date'] == DATE
    assert news_freshness.eligible(row)
    assert row['content'] == '' and row['wordcount'] == 0
