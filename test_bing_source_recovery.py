"""Regressions from the 2026-10-08 production Bing failure, offline only."""
from datetime import datetime
from unittest.mock import Mock

import pytest
import news_fetcher as fetcher
import fetch_and_filter as filtering


@pytest.mark.parametrize('keyword', ['养老', '公积金', '机关事务管理', '未来新增主题'])
def test_bing_uses_one_time_filter_and_supported_market(monkeypatch, keyword):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromisoformat('2026-10-08T00:30:00+08:00')
    monkeypatch.setattr(fetcher, 'datetime', Clock)
    response = Mock()
    response.json.return_value = {'search_metadata': {'status': 'Success'}, 'organic_results': []}
    request = Mock(return_value=response)
    monkeypatch.setattr(fetcher.requests, 'get', request)

    result = fetcher.fetch_serpapi_bing_news(keyword, fetch_date='2026-10-07')
    params = request.call_args.kwargs['params']
    assert params['qft'] == 'interval="8"'
    assert params['mkt'] == 'en-US'
    assert not result.get('_fetch_failed')


def test_old_backfill_has_no_time_or_sort_filter(monkeypatch):
    response = Mock()
    response.json.return_value = {'organic_results': []}
    request = Mock(return_value=response)
    monkeypatch.setattr(fetcher.requests, 'get', request)
    fetcher.fetch_serpapi_bing_news('养老', fetch_date='2025-01-01')
    assert 'qft' not in request.call_args.kwargs['params']


def test_bing_keeps_publisher_and_capture_time_for_local_date_gate(monkeypatch):
    response = Mock()
    response.json.return_value = {
        'search_metadata': {'status': 'Success', 'created_at': '2026-10-07 16:40:00 UTC'},
        'organic_results': [
            {'title': '目标日新闻', 'link': 'https://example.test/target', 'date': '2h', 'source': '原发布机构'},
            {'title': '当日新闻', 'link': 'https://example.test/today', 'date': '20m', 'source': '另一机构'},
            {'title': '旧闻', 'link': 'https://example.test/old', 'date': '3d', 'source': '历史机构'},
        ],
    }
    monkeypatch.setattr(fetcher.requests, 'get', Mock(return_value=response))
    result = fetcher.fetch_serpapi_bing_news('养老', fetch_date='2026-10-07')
    rows = result['organic_results']
    assert rows[0]['source'] == '原发布机构'
    accepted = filtering.filter_search_rows(rows, 'serp_bingnews', '2026-10-07',
                                           datetime.fromisoformat('2026-10-08T01:00:00+08:00'))
    assert [row['title'] for row in accepted] == ['目标日新闻']
    assert accepted[0]['search_fetched_at'] == '2026-10-08T00:40:00+08:00'


@pytest.mark.parametrize('payload,expected', [
    ({'organic_results': []}, 'empty'),
    ({'_fetch_failed': True, 'organic_results': []}, 'failed'),
    ({'search_metadata': {'status': 'Success'}, 'error': 'No results', 'organic_results': []}, 'empty'),
    ({'organic_results': [{'title': '新闻'}]}, 'ok'),
])
def test_source_status_separates_provider_failure_from_empty_results(payload, expected):
    assert filtering.source_status(payload, 'organic_results') == expected


@pytest.mark.parametrize('keyword', ['养老', '公积金', '江苏机关事务', '未来新增主题'])
def test_partial_source_warning_is_persisted_for_every_topic(tmp_path, monkeypatch, keyword):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('RUN_LOG_PATH', str(tmp_path / 'run.log'))
    monkeypatch.setattr('sys.argv', ['fetch_and_filter.py', keyword, '2026-10-07',
                                   '--main_keyword', keyword, '--output', str(tmp_path / 'rows.json')])
    for name in ('baidu', 'duckduckgo'):
        monkeypatch.setattr(filtering, f'fetch_serpapi_{name}_news', lambda *a, **k: {})
    monkeypatch.setattr(filtering, 'fetch_serpapi_bing_news',
                        lambda *a, **k: {'_fetch_failed': True, 'organic_results': []})
    monkeypatch.setattr(filtering, 'fetch_serpapi_google_news', lambda *a, **k: {
        'news_results': [{'title': '目标日新闻', 'link': 'https://example.test/target', 'date': '2026-10-07'}]})
    complete = Mock()
    monkeypatch.setattr(filtering, 'log_script_complete', complete)
    filtering.main()
    import json
    from pathlib import Path
    assert len(json.loads(Path('rows.json').read_text())) == 1
    text = Path('run.log').read_text()
    assert 'Bing:failed' in text and 'Baidu:empty' in text and 'DDG:empty' in text
    assert '来源不完整: Bing' in complete.call_args.kwargs['message']


def test_bing_http_failure_never_logs_the_authenticated_request_url(monkeypatch, capsys):
    secret = 'sentinel-search-key'
    error = fetcher.requests.HTTPError(f'503 for url: https://serpapi.com/search.json?api_key={secret}')
    error.response = Mock(status_code=503)
    monkeypatch.setattr(fetcher.requests, 'get', Mock(side_effect=error))
    monkeypatch.setattr(fetcher.time, 'sleep', lambda _: None)
    logs = Mock()
    monkeypatch.setattr(fetcher, 'log_error', logs)
    result = fetcher.fetch_serpapi_bing_news('养老')
    assert result['_fetch_failed'] is True
    assert secret not in capsys.readouterr().out
    assert all(secret not in str(call.args) for call in logs.call_args_list)
    assert '503' in logs.call_args.args[-1]
