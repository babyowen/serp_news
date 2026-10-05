"""Public Shangguan article extraction, cached bodies and publication gates."""
import json
import shlex

import pytest

DATE = '2026-10-04'
ARTICLE_ID = '1182118'
URL = 'https://www.jfdaily.com/news/detail?id=' + ARTICLE_ID
ENDPOINT = 'https://www.jfdaily.com/news/getNewsDetail?id=' + ARTICLE_ID
TITLE = '机构公布公共服务开放计划，明确设施共享与运行保障措施' * 3
BODY = '机构公布了公共服务事项、开放时间与配套保障措施，安排专人负责设施管理，并向社会公开了使用流程和意见反馈渠道。' * 3
STAMP = 1791066539000  # 2026-10-04 06:28:59 in Beijing.


def payload(**changes):
    return {'success': True, 'object': {'id': int(ARTICLE_ID), 'title': TITLE,
        'detail': '<p>' + BODY + '</p>', 'summary': '摘要不能代替正文',
        'newstype': '0', 'ismember': '0', 'publishtime': STAMP,
        'addtime': 1791036147000, 'edittime': 1791140400000,
        'originurl': None, 'status': '4', **changes}}


def api(monkeypatch, data=None, error=None):
    import fetch_content
    calls = []
    class Response:
        def raise_for_status(self):
            if error: raise error
        def json(self):
            return payload() if data is None else data
    def get(url, **kwargs):
        calls.append((url, kwargs))
        assert url == ENDPOINT
        return Response()
    monkeypatch.setattr(fetch_content.requests, 'get', get)
    monkeypatch.setattr(fetch_content, 'fetch_article_content_with_selenium_driver',
                        lambda _: pytest.fail('Shangguan should use its public data, not a browser'))
    monkeypatch.setattr(fetch_content.trafilatura, 'fetch_url',
                        lambda _: pytest.fail('Unrendered Shangguan shell is not an article'))
    return calls


@pytest.mark.parametrize('url', [URL, URL.replace('www.jfdaily.com', 'www.jfdaily.com.cn'),
    URL.replace('/news/detail', '/staticsg/res/html/web/newsDetail.html'),
    URL.replace('www.jfdaily.com', 'www.shobserver.cn'), URL + '&source=share#article'])
def test_jfdaily_shared_fetcher_reads_matching_public_body_without_browser(monkeypatch, url):
    import fetch_content
    calls = api(monkeypatch)
    assert fetch_content.fetch_article_content(url, max_retries=1) == (BODY, len(BODY), True)
    assert calls == [(ENDPOINT, {'timeout': (5, 15)})]


@pytest.mark.parametrize('changes', [{'id': 1182119}, {'newstype': '1'}, {'ismember': '1'},
    {'detail': None}, {'detail': []}, {'detail': ''}, {'detail': TITLE},
    {'detail': TITLE + ' | 上观新闻'}, {'detail': '{{title}} {{summary}} {{author}} ' * 8},
    {'detail': 'ERROR: ACCESS DENIED\n' + 'Request blocked. ' * 8}, {'detail': '?' * 100}])
def test_jfdaily_invalid_or_restricted_payload_never_becomes_body(monkeypatch, changes):
    import fetch_content
    calls = api(monkeypatch, payload(**changes))
    assert fetch_content.fetch_article_content(URL, max_retries=1) == ('', 0, False)
    assert len(calls) == 1


@pytest.mark.parametrize('data', [[], {}, {'success': False, 'object': payload()['object']},
    {'success': 'true', 'object': payload()['object']}, {'success': True, 'object': []}])
def test_jfdaily_wrong_response_shape_is_empty(monkeypatch, data):
    import fetch_content
    api(monkeypatch, data)
    assert fetch_content.fetch_article_content(URL, max_retries=1) == ('', 0, False)


@pytest.mark.parametrize('url', [
    'https://www.jfdaily.com.evil.example/news/detail?id=1182118',
    'https://user:password@www.jfdaily.com/news/detail?id=1182118',
    'https://www.jfdaily.com/news/detail?id=1182118&id=1182119',
    'https://www.jfdaily.com/news/detail?id=1182118&id=',
    'https://www.jfdaily.com/news/detail?id=-1',
    'https://www.jfdaily.com/news/detail?id=abc',
    'https://www.jfdaily.com/',
    'https://www.jfdaily.com/news/getNewsDetail?id=1182118'])
def test_jfdaily_invalid_identity_does_not_request(monkeypatch, url):
    import jfdaily_news_content
    monkeypatch.setattr(jfdaily_news_content.requests, 'get', lambda *a, **k: pytest.fail('invalid URL requested'))
    assert jfdaily_news_content.fetch_jfdaily_article(url) is None


def test_jfdaily_markup_is_plain_text(monkeypatch):
    import fetch_content
    api(monkeypatch, payload(detail='<p>' + BODY + '</p><script>secret</script><style>hidden</style><iframe>other</iframe><p>设施 &amp; 服务</p>'))
    assert fetch_content.fetch_article_content(URL, max_retries=1)[0] == BODY + '\n设施 & 服务'


def test_jfdaily_requests_and_publication_entrypoints_reuse_public_detail(monkeypatch):
    import fetch_content
    calls = api(monkeypatch)
    assert fetch_content.fetch_article_content_with_requests(URL) == (BODY, len(BODY), True)
    assert fetch_content.fetch_publication_page(URL) == (BODY, '')
    assert len(calls) == 2


def test_jfdaily_transport_failure_uses_bounded_retry(monkeypatch):
    import fetch_content
    calls = api(monkeypatch, error=fetch_content.requests.HTTPError('503'))
    monkeypatch.setattr(fetch_content.time, 'sleep', lambda _: None)
    assert fetch_content.fetch_article_content(URL, max_retries=3, retry_interval=0) == ('', 0, False)
    assert len(calls) == 3


def input_file(tmp_path, monkeypatch, keyword, rows):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('RUN_LOG_PATH', str(tmp_path/'run.log'))
    path = tmp_path/'output'/DATE/f'{DATE}_{keyword}.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(rows), encoding='utf-8')
    return path


@pytest.mark.parametrize('keyword', ['江苏机关事务', '养老', '数字政务', '任意未来关键词'])
@pytest.mark.parametrize('cached', ['', TITLE, '{{title}} {{summary}} {{author}} ' * 8])
def test_jfdaily_pipeline_repairs_empty_headline_and_template_for_any_keyword(tmp_path, monkeypatch, keyword, cached):
    import fetch_content
    import main
    api(monkeypatch)
    path = input_file(tmp_path, monkeypatch, keyword, [
        {'title': '已完成的其他文章', 'link': 'https://example.com/article', 'content': BODY + '保留', 'wordcount': len(BODY)+2, 'score': 4},
        {'title': TITLE, 'link': URL, 'content': cached, 'wordcount': len(cached), 'score': 0}])
    def child(command, *args, **kwargs):
        args = shlex.split(command)
        assert args[1:] == ['fetch_content.py', keyword, DATE]
        return fetch_content.process_json(keyword, DATE)
    monkeypatch.setattr(main, 'safe_subprocess_run', child)
    assert main.execute_content_fetching(DATE, keyword)
    old, row = json.loads(path.read_text())
    assert old['content'] == BODY + '保留' and old['score'] == 4
    assert row['content'] == BODY and row['wordcount'] == len(BODY) and row['score'] == 0
    assert row['content_source']['method'] == 'jfdaily_public_detail'
    assert row['content_source']['published_time'] == '2026-10-04T06:28:59+08:00'
    monkeypatch.setattr(fetch_content.requests, 'get', lambda *a, **k: pytest.fail('complete body refetched'))
    assert main.execute_content_fetching(DATE, keyword)


@pytest.mark.parametrize('stamp,status', [(STAMP, 'accepted'), (1791036147000, 'old'),
    (1791140400000, 'pending'), (None, 'pending'), ('invalid', 'pending'), (1791066539, 'pending')])
def test_jfdaily_publication_uses_only_publish_timestamp_and_beijing_date(tmp_path, monkeypatch, stamp, status):
    import fetch_content
    api(monkeypatch, payload(publishtime=stamp))
    path = input_file(tmp_path, monkeypatch, '江苏机关事务', [{'title': TITLE, 'link': URL}])
    assert fetch_content.process_json('江苏机关事务', DATE)
    row = json.loads(path.read_text())[0]
    assert row['publication_check']['status'] == status
    if status == 'accepted':
        assert row['publication_check']['evidence'] == [{'source': 'jfdaily:publishtime', 'raw': str(STAMP), 'date': DATE, 'url': ENDPOINT}]
        assert row['content'] == BODY


def test_jfdaily_old_pending_check_can_recover(tmp_path, monkeypatch):
    import fetch_content
    api(monkeypatch)
    path = input_file(tmp_path, monkeypatch, '江苏机关事务', [{'title': TITLE, 'link': URL, 'content': '',
        'publication_check': {'version': 1, 'target_date': DATE, 'status': 'pending',
            'reason': 'missing_publication_date', 'link': URL, 'evidence': []}}])
    assert fetch_content.process_json('江苏机关事务', DATE)
    assert json.loads(path.read_text())[0]['publication_check']['status'] == 'accepted'


@pytest.mark.parametrize('text', ['ERROR: ACCESS DENIED\n' + 'Request blocked. ' * 10,
    '{{title}} {{summary}} {{author}} ' * 8, '403 ERROR\n' + 'The request could not be satisfied. ' * 5])
def test_jfdaily_error_and_template_cache_are_not_complete(tmp_path, monkeypatch, text):
    import main
    path = input_file(tmp_path, monkeypatch, '未来关键词', [{'title': TITLE, 'link': URL, 'content': text}])
    assert not main.all_news_has_content(path)
