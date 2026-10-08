"""Regression tests for recent search evidence and old relative timestamps."""
from datetime import datetime
import json
from pathlib import Path
from unittest.mock import Mock
import pytest

NOW = datetime.fromisoformat('2026-10-06T00:45:00+08:00')
DATE = '2026-10-05'

@pytest.mark.parametrize('engine', ['baidu', 'google', 'bing', 'duckduckgo'])
@pytest.mark.parametrize('raw,want', [
    ('5 years ago', '2021-10-06'), ('4 years ago', '2022-10-06'),
    ('2 months ago', '2026-08-06'), ('1 week ago', '2026-09-29'),
    ('7h', DATE), ('7 hours ago', DATE), ('7小时前', DATE),
    ('7 小時前', DATE), ('35m', '2026-10-06'), ('Yesterday', DATE),
    ('2026-10-04T18:30:00Z', DATE), ('10/04/2026, 06:30 PM, +0000 UTC', DATE),
    ('garbage 5', None), ('2026-02-30', None), ('2026', None),
])
def test_all_engines_parse_complete_dates_and_relative_units(engine, raw, want):
    import fetch_and_filter as f
    assert getattr(f, f'parse_{engine}_news_date')(raw, NOW) == want


def search_row(**updates):
    row=dict(title='节日公务用车监督', link='https://example.test/a', date=DATE,
             fetchdate=DATE, sourceapi='serp_bingnews', search_date_raw='7h',
             search_fetched_at=NOW.isoformat(), search_date_field='date',
             content='节日监督检查公务用车封存情况。'*20)
    row.update(updates)
    return row


def test_recent_search_date_without_page_date_reaches_scoring(tmp_path, monkeypatch):
    import fetch_content, news_scorer
    from topic_config import TOPIC
    from government_affairs_scoring import ScoreResult
    from news_freshness import eligible
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fetch_content, 'fetch_publication_page', lambda _: ('正文内容'*60, '<article>新闻正文</article>'))
    row=search_row()
    fetch_content.check_topic_dates([row], DATE)
    assert row['publication_check']['status']=='accepted'
    assert row['publication_check']['reason']=='search_date_fallback'
    assert row['publication_check']['published_date'] is None
    assert eligible(row)
    path=tmp_path/'rows.json';path.write_text(json.dumps([row]))
    calls=[]
    def score(*args, **kwargs):
        calls.append(args[0]);return ScoreResult(3,'ok')
    monkeypatch.setattr(news_scorer,'score_news_result',score)
    rows,_,count,_=news_scorer.batch_score_news(path,TOPIC)
    assert count==1 and rows[0]['score']==3 and calls==[row['title']]

@pytest.mark.parametrize('html,status', [
    ('<meta name="pubdate" content="2021-10-05">','old'),
    ('<meta name="pubdate" content="2026-10-04"><meta property="article:published_time" content="2026-10-05">','pending'),
    ('<meta name="pubdate" content="2026-10-06">','pending'),
])
def test_search_fallback_never_overrides_old_conflicting_or_future_page_date(tmp_path,monkeypatch,html,status):
    import fetch_content
    from news_freshness import eligible
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fetch_content,'fetch_publication_page',lambda _:('',html))
    row=search_row();fetch_content.check_topic_dates([row],DATE)
    assert row['publication_check']['status']==status
    assert not eligible(row)

@pytest.mark.parametrize('updates', [
    {'search_fetched_at':None}, {'search_fetched_at':'garbage'},
    {'search_date_raw':'5 years ago'}, {'search_date_raw':''},
    {'search_date_raw':'35m'}, {'date':'2026-10-04'},
    {'search_fetched_at':'2026-10-07T00:45:00+08:00'},
    {'sourceapi':'unrecognized'},
])
def test_incomplete_or_nonmatching_search_evidence_stays_pending(tmp_path,monkeypatch,updates):
    import fetch_content
    from news_freshness import eligible
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fetch_content,'fetch_publication_page',lambda _:('',''))
    row=search_row(**updates);fetch_content.check_topic_dates([row],DATE)
    assert row['publication_check']['status']=='pending' and not eligible(row)


def test_saved_fallback_cannot_be_reused_after_evidence_changes(tmp_path,monkeypatch):
    import fetch_content
    from news_freshness import eligible
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fetch_content,'fetch_publication_page',lambda _:('',''))
    row=search_row();fetch_content.check_topic_dates([row],DATE)
    assert eligible(row)
    row['search_date_raw']='5 years ago'
    assert not eligible(row)


def test_cached_pending_uses_saved_clock_without_refetching(tmp_path,monkeypatch):
    import fetch_content
    from news_freshness import assess_html,eligible
    monkeypatch.chdir(tmp_path)
    row=search_row();row['publication_check']={**assess_html('',DATE),'link':row['link']}
    monkeypatch.setattr(fetch_content,'fetch_publication_page',lambda _:pytest.fail('cached page re-fetched'))
    fetch_content.check_topic_dates([row],DATE)
    assert eligible(row)

@pytest.mark.parametrize('engine,key', [('google','news_results'),('baidu','organic_results'),('bing','organic_results'),('duckduckgo','news_results')])
def test_api_response_clock_is_preserved_for_cached_relative_dates(monkeypatch,engine,key):
    import news_fetcher as f
    payload={'search_metadata':{'status':'Success','created_at':'2026-10-05 16:45:00 UTC'},key:[{'title':'x','link':'https://example.test/x','date':'7h'}]}
    response=Mock();response.json.return_value=payload
    monkeypatch.setattr(f.requests,'get',lambda *a,**k:response)
    kwargs={'max_pages':1} if engine in {'google','duckduckgo'} else {}
    row=getattr(f,f'fetch_serpapi_{engine}_news')('term',**kwargs)[key][0]
    assert row['search_fetched_at']=='2026-10-06T00:45:00+08:00'


def test_google_uses_news_tab_that_supports_date_range_and_sort(monkeypatch):
    import news_fetcher as f
    calls=[]
    def get(url,**kw):
        calls.append(kw['params'].copy());r=Mock();r.json.return_value={'news_results':[]};return r
    monkeypatch.setattr(f.requests,'get',get)
    f.fetch_serpapi_google_news('公物仓',fetch_date=DATE)
    assert calls[0]['engine']=='google' and calls[0]['tbm']=='nws'
    assert calls[0]['tbs']=='cdr:1,cd_min:10/5/2026,cd_max:10/5/2026,sbd:1'

@pytest.mark.parametrize('target,window',[('2026-10-06','d'),('2026-10-05','w'),('2026-10-01','w'),('2026-09-20','m'),('2025-01-01',None)])
def test_duckduckgo_uses_supported_window_for_requested_day(monkeypatch,target,window):
    import news_fetcher as f
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return NOW
    monkeypatch.setattr(f,'datetime',Clock)
    calls=[]
    def get(url,**kw):
        calls.append(kw['params'].copy());r=Mock();r.json.return_value={'news_results':[]};return r
    monkeypatch.setattr(f.requests,'get',get)
    f.fetch_serpapi_duckduckgo_news('公物仓',fetch_date=target)
    assert calls[0].get('df')==window


def test_bing_filters_recent_results_and_sorts_newest(monkeypatch):
    import news_fetcher as f
    request=Mock();request.return_value.json.return_value={'organic_results':[]}
    monkeypatch.setattr(f.requests,'get',request)
    f.fetch_serpapi_bing_news('公物仓')
    assert request.call_args.kwargs['params']['qft']=='interval="7" sortbydate="1"'


def test_collection_retains_capture_clock_and_prefers_absolute_api_timestamp():
    from fetch_and_filter import filter_search_rows
    rows=[
        {'title':'old', 'date':'5 years ago'},
        {'title':'recent', 'date':'7h', 'search_fetched_at':NOW.isoformat()},
        {'title':'precise', 'date':'1 day ago','published_at':'2026-10-04 18:30:00 UTC'},
        {'title':'too new','date':'35m'},
    ]
    result=filter_search_rows(rows,'serp_googlenews',DATE,NOW)
    assert [r['title'] for r in result]==['recent','precise']
    assert result[0]['search_fetched_at']==NOW.isoformat()
    assert result[1]['date']==DATE and result[1]['search_date_field']=='published_at'


def test_fallback_survives_json_and_reaches_database_insert(tmp_path,monkeypatch):
    import importlib.util
    from unittest.mock import patch
    from topic_config import TOPIC
    from news_freshness import assess_html,apply_search_fallback
    row=search_row(keyword=TOPIC,score=3,wordcount=200,score_status='ok')
    row['publication_check']={**apply_search_fallback(assess_html('',DATE),row,DATE),'link':row['link']}
    path=tmp_path/'scored.json';path.write_text(json.dumps([row]))
    conn=Mock();conn.cursor.return_value.fetchall.return_value=[]
    spec=importlib.util.spec_from_file_location('fallback_import_test',Path(__file__).with_name('write_to_mysql.py'))
    writer=importlib.util.module_from_spec(spec)
    with patch('db_utils.get_connection',return_value=conn):spec.loader.exec_module(writer)
    monkeypatch.setattr(writer,'write_log',lambda _:None)
    writer.insert_scored_news(str(path),TOPIC)
    inserts=[call.args for call in conn.cursor.return_value.execute.call_args_list if 'INSERT INTO' in call.args[0]]
    assert len(inserts)==1
    assert inserts[0][1][0]==DATE and inserts[0][1][11]==3


def test_changed_evidence_invalidates_cached_check(tmp_path,monkeypatch):
    import fetch_content
    from news_freshness import check_current
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fetch_content,'fetch_publication_page',lambda _:('',''))
    row=search_row();fetch_content.check_topic_dates([row],DATE)
    assert check_current(row)
    row['search_fetched_at']='2026-10-07T00:45:00+08:00'
    assert not check_current(row)


@pytest.mark.parametrize('target,interval',[('2026-10-05','8'),('2026-10-01','8'),('2026-09-20','9'),('2025-01-01',None)])
def test_bing_window_covers_the_start_of_requested_calendar_day(monkeypatch,target,interval):
    import news_fetcher as f
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return NOW
    monkeypatch.setattr(f,'datetime',Clock)
    request=Mock();request.return_value.json.return_value={'organic_results':[]}
    monkeypatch.setattr(f.requests,'get',request)
    f.fetch_serpapi_bing_news('公物仓',fetch_date=target)
    qft=request.call_args.kwargs['params']['qft']
    assert 'sortbydate="1"' in qft
    assert (f'interval="{interval}"' in qft) if interval else 'interval=' not in qft


@pytest.mark.parametrize('raw',['2021.10.05','October 5, 2021','20211005','旧版日期格式'])
def test_unrecognized_explicit_publication_is_not_missing_and_never_uses_search(tmp_path,monkeypatch,raw):
    import fetch_content
    from news_freshness import eligible
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fetch_content,'fetch_publication_page',lambda _:('',f'<meta name="pubdate" content="{raw}">'))
    row=search_row();fetch_content.check_topic_dates([row],DATE)
    assert row['publication_check']['status'] in {'old','pending'}
    assert row['publication_check']['reason']!='search_date_fallback'
    assert not eligible(row)

@pytest.mark.parametrize('site',['msn','jfdaily'])
def test_unparseable_service_publication_does_not_use_search(site):
    from fetch_content import msn_publication_check,jfdaily_publication_check
    from news_freshness import apply_search_fallback
    if site=='msn':
        check=msn_publication_check({'published_time':'unparseable','endpoint':'https://example.test/api','id':'123','source_url':'https://example.test/original'},DATE)
    else:
        check=jfdaily_publication_check({'published_time':'unparseable','endpoint':'https://example.test/api','id':'123','source_url':'https://example.test/original'},DATE)
    assert apply_search_fallback(check,search_row(),DATE)['status']=='pending'
    assert check['reason']=='unparseable_publication_date'


@pytest.mark.parametrize('site',['msn','jfdaily'])
@pytest.mark.parametrize('published,want',[('',True),('2021-10-05',False),('invalid',False)])
def test_body_recovery_reapplies_search_fallback_without_overriding_old_dates(tmp_path,monkeypatch,site,published,want):
    import fetch_content as f
    from topic_config import TOPIC
    from news_freshness import eligible
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('RUN_LOG_PATH',str(tmp_path/'run.log'))
    monkeypatch.setattr(f.time,'sleep',lambda _:None)
    url='https://www.msn.com/zh-cn/news/other/story/ar-AA1TEST' if site=='msn' else 'https://www.jfdaily.com/staticsg/res/html/web/newsDetail.html?id=123'
    row=search_row(link=url,content='')
    folder=tmp_path/'output'/DATE;folder.mkdir(parents=True)
    path=folder/f'{DATE}_{TOPIC}.json';path.write_text(json.dumps([row]))
    article={'id':'AA1TEST' if site=='msn' else '123','content':'公共机构开展节能改造取得实效。'*30,
             'published_time':published,'raw_published_time':published,'endpoint':'https://example.test/api',
             'source_url':'https://example.test/original','locale':'zh-cn','provider':'媒体'}
    responses=iter([None,article])
    monkeypatch.setattr(f,f'fetch_{site}_article',lambda *a,**k:next(responses))
    assert f.process_json(TOPIC,DATE)
    saved=json.loads(path.read_text())[0]
    assert len(saved['content'])>100
    assert eligible(saved)==want
    diagnostic=json.loads((folder/'diagnostics/government_affairs_dates.json').read_text())
    assert diagnostic['search_fallback_count']==int(want)


@pytest.mark.parametrize('site,raw',[
    ('jfdaily',1601856000),('jfdaily',978307200000),('jfdaily','unparseable'),('jfdaily',0),
    ('msn',1601856000),('msn',{'year':2021}),('msn',0),
])
def test_real_service_reader_preserves_unparseable_raw_dates(site,raw):
    from fetch_content import msn_publication_check,jfdaily_publication_check
    from news_freshness import apply_search_fallback
    if site=='jfdaily':
        from jfdaily_news_content import _article
        from test_jfdaily_content import payload,ARTICLE_ID,ENDPOINT,URL
        article=_article(payload(publishtime=raw),ARTICLE_ID,ENDPOINT,URL)
        check=jfdaily_publication_check(article,DATE)
    else:
        from msn_news_content import _article
        from test_msn_content import payload,ID,ENDPOINT
        article=_article(payload(publishedDateTime=raw),'zh-cn',ID,ENDPOINT)
        check=msn_publication_check(article,DATE)
    assert article['raw_published_time']==raw
    assert check['reason']=='unparseable_publication_date'
    assert apply_search_fallback(check,search_row(),DATE)['status']=='pending'
