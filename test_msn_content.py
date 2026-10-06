"""Shared MSN extraction, public payload validation, resume and date evidence."""
import json
import shlex

import pytest

DATE = '2026-10-04'
ID = 'AA2dwNMz'
URL = 'https://www.msn.cn/zh-cn/news/other/article/ar-' + ID
ENDPOINT = 'https://assets.msn.com/content/view/v2/Detail/zh-cn/' + ID
TITLE = '测试新闻标题：机构向社会开放公共服务，具体管理措施和服务信息公布' * 2
BODY = '机构公布了服务事项和开放时间，并安排人员负责日常管理、设施维护和意见反馈。' * 4


def payload(**changes):
    return {'id': ID, 'locale': 'zh-cn', 'type': 'article', 'title': TITLE,
            'body': '<p>' + BODY + '</p>', 'abstract': '这是摘要，不能代替正文。',
            'publishedDateTime': '2026-10-03T22:28:59Z',
            'createdDateTime': '2026-10-05T03:00:00Z', 'updatedDateTime': '2026-10-05T04:00:00Z',
            'provider': {'name': '新闻来源', 'isPremium': True},
            'sourceHref': 'https://publisher.example/article',
            'renderingRestriction': 0, 'subscriptionProductType': 0, **changes}


def http(monkeypatch, data=None, failure=None):
    import fetch_content
    calls = []
    class Response:
        def raise_for_status(self):
            if failure:
                raise failure
        def json(self):
            return payload() if data is None else data
    def get(url, **kwargs):
        calls.append((url, kwargs))
        assert url.startswith('https://assets.msn.com/content/view/v2/Detail/')
        return Response()
    monkeypatch.setattr(fetch_content.requests, 'get', get)
    monkeypatch.setattr(fetch_content.trafilatura, 'fetch_url',
                        lambda _: (_ for _ in ()).throw(AssertionError('MSN shell is not a body')))
    return calls


def input_file(tmp_path, monkeypatch, keyword, rows):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('RUN_LOG_PATH', str(tmp_path / 'run.log'))
    path = tmp_path / 'output' / DATE / f'{DATE}_{keyword}.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(rows), encoding='utf-8')
    return path


@pytest.mark.parametrize('url,locale', [
    (URL, 'zh-cn'),
    (URL.replace('msn.cn', 'msn.com') + '?ocid=BingNewsVerp#article', 'zh-cn'),
    ('https://msn.com/zh-tw/entertainment/other/test/ar-' + ID, 'zh-tw'),
    ('https://www.msn.com/en-us/news/other/test/ar-' + ID, 'en-us')])
def test_public_article_uses_one_bounded_anonymous_request(monkeypatch, url, locale):
    import fetch_content
    calls = http(monkeypatch, payload(locale=locale))
    monkeypatch.setattr(fetch_content, 'sync_playwright', lambda: pytest.fail('MSN browser launched'))
    monkeypatch.setattr(fetch_content, 'fetch_article_content_with_selenium_driver',
                        lambda _: pytest.fail('legacy MSN browser launched'))
    assert fetch_content.fetch_article_content(url, max_retries=1) == (BODY, len(BODY), True)
    assert calls == [('https://assets.msn.com/content/view/v2/Detail/' + locale + '/' + ID,
                      {'timeout': (5, 15)})]


def test_requests_entrypoint_reads_msn_body(monkeypatch):
    import fetch_content
    http(monkeypatch)
    assert fetch_content.fetch_article_content_with_requests(URL) == (BODY, len(BODY), True)


def test_publication_entrypoint_does_not_fabricate_html_metadata(monkeypatch):
    import fetch_content
    http(monkeypatch)
    assert fetch_content.fetch_publication_page(URL) == (BODY, '')


@pytest.mark.parametrize('changes', [
    {'id': 'different'}, {'locale': 'en-us'}, {'type': 'video'},
    {'renderingRestriction': 1}, {'subscriptionProductType': 1},
    {'body': None}, {'body': []}, {'body': ''}, {'body': TITLE}, {'body': TITLE + ' | MSN'}, {'body': '短标题'},
    {'body': '<p>????????????????????????????</p>'}])
def test_invalid_or_restricted_payload_is_not_successful_body(monkeypatch, changes):
    import fetch_content
    calls = http(monkeypatch, payload(**changes))
    assert fetch_content.fetch_article_content(URL, max_retries=1) == ('', 0, False)
    assert len(calls) == 1


@pytest.mark.parametrize('data', [[], 'invalid', {}])
def test_wrong_json_shape_is_not_success(monkeypatch, data):
    import fetch_content
    calls = http(monkeypatch, data)
    assert fetch_content.fetch_article_content(URL, max_retries=1) == ('', 0, False)
    assert len(calls) == 1


def test_markup_is_plain_text_without_script_or_styles(monkeypatch):
    import fetch_content
    http(monkeypatch, payload(body='<p>' + BODY + '</p><script>alert(1)</script>'
        '<style>secret</style><iframe>frame text</iframe><p>设施 &amp; 服务</p>'))
    assert fetch_content.fetch_article_content(URL, max_retries=1)[0] == BODY + '\n设施 & 服务'


def test_transport_failure_is_bounded_and_retry_can_recover(monkeypatch):
    import fetch_content
    calls = []
    class Response:
        def raise_for_status(self):
            if len(calls) == 1:
                raise fetch_content.requests.HTTPError('503')
        def json(self):
            return payload()
    def get(url, **kwargs):
        calls.append(url)
        assert url == ENDPOINT and kwargs == {'timeout': (5, 15)}
        return Response()
    monkeypatch.setattr(fetch_content.requests, 'get', get)
    assert fetch_content.fetch_article_content(URL, max_retries=2, retry_interval=0) == (BODY, len(BODY), True)
    assert calls == [ENDPOINT, ENDPOINT]


@pytest.mark.parametrize('url', [
    'https://www.msn.cn.evil.example/zh-cn/news/test/ar-' + ID,
    'https://www.msn.com/zh-cn/',
    'https://www.msn.com/zh-cn/news/test/ar-',
    'https://user:password@www.msn.com/zh-cn/news/test/ar-' + ID])
def test_invalid_msn_identity_does_not_request_an_endpoint(monkeypatch, url):
    import msn_news_content
    monkeypatch.setattr(msn_news_content.requests, 'get', lambda *a, **k: pytest.fail('invalid identity requested'))
    assert msn_news_content.fetch_msn_article(url) is None


@pytest.mark.parametrize('keyword', ['江苏机关事务', '养老', '公积金', '烟草服务银行', '任意未来关键词'])
@pytest.mark.parametrize('cached_body', ['', TITLE])
def test_pipeline_repairs_msn_for_any_keyword_and_preserves_completed_body(tmp_path, monkeypatch, keyword, cached_body):
    import fetch_content
    import main
    http(monkeypatch)
    path = input_file(tmp_path, monkeypatch, keyword,
        [{'title': TITLE, 'link': URL, 'content': cached_body, 'wordcount': len(cached_body)}])
    def child(command, *args, **kwargs):
        argv = shlex.split(command)
        assert argv[1:] == ['fetch_content.py', keyword, DATE]
        return fetch_content.process_json(argv[2], argv[3])
    monkeypatch.setattr(main, 'safe_subprocess_run', child)
    assert main.execute_content_fetching(DATE, keyword)
    row = json.loads(path.read_text())[0]
    assert row['content'] == BODY and row['wordcount'] == len(BODY)
    assert row['title'] == TITLE and row['link'] == URL
    assert row['content_source']['method'] == 'msn_public_detail'
    assert row['content_source']['source_url'] == 'https://publisher.example/article'
    assert row['content_source']['published_time'] == '2026-10-03T22:28:59Z'
    monkeypatch.setattr(fetch_content.requests, 'get', lambda *a, **k: pytest.fail('complete MSN body refetched'))
    assert main.execute_content_fetching(DATE, keyword)


@pytest.mark.parametrize('stamp,status', [
    ('2026-10-03T22:28:59Z', 'accepted'),
    ('2026-10-03T14:02:27Z', 'old'),
    ('2026-10-04T19:00:00Z', 'pending'),
    ('invalid', 'pending'), ('', 'pending')])
def test_date_check_uses_explicit_publication_in_beijing_time(tmp_path, monkeypatch, stamp, status):
    import fetch_content
    import news_freshness
    http(monkeypatch, payload(publishedDateTime=stamp))
    path = input_file(tmp_path, monkeypatch, '江苏机关事务', [{'title': TITLE, 'link': URL}])
    monkeypatch.setattr(fetch_content, 'fetch_article_content', lambda _: pytest.fail('date check should reuse MSN response'))
    assert fetch_content.process_json('江苏机关事务', DATE)
    row = json.loads(path.read_text())[0]
    check = row['publication_check']
    assert check['status'] == status
    assert news_freshness.eligible(row) is (status == 'accepted')
    if status == 'accepted':
        assert check['evidence'] == [{'source': 'msn:publishedDateTime', 'raw': stamp,
                                      'date': DATE, 'url': ENDPOINT}]
        assert check['source_url'] == 'https://publisher.example/article'
        assert row['content'] == BODY


def test_old_pending_msn_check_is_revisited_by_new_extractor(tmp_path, monkeypatch):
    import fetch_content
    http(monkeypatch)
    path = input_file(tmp_path, monkeypatch, '江苏机关事务', [{'title': TITLE, 'link': URL,
        'content': '', 'wordcount': 0, 'fetchdate': DATE,
        'publication_check': {'version': 1, 'target_date': DATE, 'status': 'pending',
            'reason': 'missing_publication_date', 'link': URL, 'evidence': []}}])
    assert fetch_content.process_json('江苏机关事务', DATE)
    row = json.loads(path.read_text())[0]
    assert row['publication_check']['status'] == 'accepted'
    assert row['content'] == BODY


def test_unavailable_date_is_quarantined_and_can_recover_on_resume(tmp_path, monkeypatch):
    import fetch_content
    calls = http(monkeypatch, failure=fetch_content.requests.HTTPError('503'))
    monkeypatch.setattr(fetch_content.time, 'sleep', lambda _: None)
    path = input_file(tmp_path, monkeypatch, '江苏机关事务', [{'title': TITLE, 'link': URL}])
    assert fetch_content.process_json('江苏机关事务', DATE)
    assert json.loads(path.read_text())[0]['publication_check']['status'] == 'pending'
    assert len(calls) == 3
    recovered_calls = http(monkeypatch)
    assert fetch_content.process_json('江苏机关事务', DATE)
    assert json.loads(path.read_text())[0]['publication_check']['status'] == 'pending'
    assert recovered_calls == []  # normal resume must not bypass the bounded queue
    rows = json.loads(path.read_text())
    fetch_content.check_topic_dates(rows, DATE, '江苏机关事务', force=True)
    assert rows[0]['publication_check']['status'] == 'accepted'


def test_msn_retry_keeps_other_sites_existing_body(tmp_path, monkeypatch):
    import fetch_content
    http(monkeypatch)
    path = input_file(tmp_path, monkeypatch, '新关键词', [
        {'title': '其他网站文章', 'link': 'https://example.com/article', 'content': BODY + '已有内容', 'wordcount': len(BODY) + 4},
        {'title': TITLE, 'link': URL, 'content': '', 'wordcount': 0}])
    assert fetch_content.process_json('新关键词', DATE)
    rows = json.loads(path.read_text())
    assert rows[0]['content'] == BODY + '已有内容'
    assert rows[1]['content'] == BODY


@pytest.mark.parametrize('title,content', [(TITLE + ' | MSN', TITLE), (TITLE, TITLE + ' | MSN')])
def test_msn_site_suffix_does_not_make_a_headline_a_body(tmp_path, monkeypatch, title, content):
    import main
    import fetch_content
    http(monkeypatch)
    path = input_file(tmp_path, monkeypatch, '未来关键词',
                      [{'title': title, 'link': URL, 'content': content, 'wordcount': len(content)}])
    assert not main.all_news_has_content(path)
    assert fetch_content.process_json('未来关键词', DATE)
    assert json.loads(path.read_text())[0]['content'] == BODY


def test_date_diagnostic_matches_final_body_retry_result(tmp_path, monkeypatch):
    import fetch_content
    responses = iter([payload(body=''), payload(publishedDateTime='2026-10-03T14:02:27Z')])
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return next(responses)
    monkeypatch.setattr(fetch_content.requests, 'get', lambda *a, **k: Response())
    path = input_file(tmp_path, monkeypatch, '江苏机关事务', [{'title': TITLE, 'link': URL}])
    assert fetch_content.process_json('江苏机关事务', DATE)
    row = json.loads(path.read_text())[0]
    assert row['publication_check']['status'] == 'old'
    diagnostic = json.loads((path.parent / 'diagnostics' / 'government_affairs_dates.json').read_text())
    assert diagnostic['counts'] == {'accepted': 0, 'old': 1, 'pending': 0}
    assert diagnostic['articles'][0]['publication_check'] == row['publication_check']


@pytest.mark.parametrize('failure', ['transport', 'json'])
def test_topic_transient_detail_failure_retries_before_quarantine(tmp_path, monkeypatch, failure):
    import fetch_content
    import news_freshness
    calls = []
    class Response:
        def raise_for_status(self):
            if failure == 'transport' and len(calls) == 1:
                raise fetch_content.requests.HTTPError('503')
        def json(self):
            if failure == 'json' and len(calls) == 1:
                raise ValueError('invalid JSON')
            return payload()
    def get(url, **kwargs):
        assert url == ENDPOINT and kwargs == {'timeout': (5, 15)}
        calls.append(url)
        return Response()
    monkeypatch.setattr(fetch_content.requests, 'get', get)
    monkeypatch.setattr(fetch_content.time, 'sleep', lambda _: None)
    path = input_file(tmp_path, monkeypatch, '江苏机关事务', [{'title': TITLE, 'link': URL}])
    assert fetch_content.process_json('江苏机关事务', DATE)
    row = json.loads(path.read_text())[0]
    assert news_freshness.eligible(row)
    assert row['content'] == BODY
    assert calls == [ENDPOINT, ENDPOINT]



def test_msn_resume_preserves_other_sites_short_successful_custom_body(tmp_path, monkeypatch):
    import fetch_content
    http(monkeypatch)
    short_body = '机构公布了公共服务开放安排，现场由工作人员提供咨询服务。'
    existing = {'title': '其他网站新闻', 'link': 'https://example.com/custom/article',
                'content': short_body, 'wordcount': len(short_body), 'custom_grab': True}
    path = input_file(tmp_path, monkeypatch, '新关键词', [existing,
                      {'title': TITLE, 'link': URL, 'content': '', 'wordcount': 0}])
    monkeypatch.setattr(fetch_content, 'fetch_article_content', lambda _: ('', 0, False))
    assert fetch_content.process_json('新关键词', DATE)
    rows = json.loads(path.read_text())
    assert {key:rows[0][key] for key in existing} == existing
    assert rows[0]['publication_check']['status'] == 'pending'
    assert rows[1]['content'] == BODY
