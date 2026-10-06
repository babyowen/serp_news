"""Publication evidence, quarantine and host-only crawler regression tests."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

DATE = '2026-10-02'

@pytest.mark.parametrize('html,want', [
    ('<meta property="article:published_time" content="2025-03-06T11:10:00+08:00">', 'old'),
    ('<meta property="article:published_time" content="2026-10-02T11:10:00+08:00"><p>依据2012年条例</p>', 'accepted'),
    ('<meta property="article:modified_time" content="2026-10-02">', 'pending'),
    ('<footer>Copyright 2026-10-02</footer><p>2026-10-02曾出台政策</p>', 'pending'),
    ('<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-10-02","dateModified":"2026-10-03"}</script>', 'accepted'),
    ('<script type="application/ld+json">{"@type":"Event","datePublished":"2026-10-02"}</script>', 'pending'),
    ('<meta name="pubdate" content="2026-10-02"><meta property="article:published_time" content="2024-10-02">', 'pending'),
    ('<meta name="pubdate" content="2026-10-03">', 'pending'),
    ('<meta name="pubdate" content="2026-02-30">', 'pending'),
    ('<meta name="pubdate" content="2026-10-01T18:00:00Z">', 'accepted'),
    ('<div class="publish-time">发布时间：2026年10月02日 12:00</div>', 'accepted'),
    ('<time datetime="2026-10-02">更新</time>', 'pending'),
])
def test_publication_evidence(html, want):
    import news_freshness as freshness
    result = freshness.assess_html(html, DATE)
    assert result['status'] == want
    assert result['target_date'] == DATE


def test_url_year_is_not_publication_evidence():
    import news_freshness as freshness
    assert freshness.assess_html('<p>依据2012年条例</p>', DATE)['status'] == 'pending'


def test_score_batch_quarantines_without_paid_requests(tmp_path, monkeypatch):
    import news_scorer
    from topic_config import TOPIC
    from government_affairs_scoring import ScoreResult
    calls = []
    def score(*args, **kwargs):
        calls.append(args[0])
        return ScoreResult(4, 'ok')
    monkeypatch.setattr(news_scorer, 'score_news_result', score)
    rows = [dict(title='old', link='https://test/old', content='旧文', fetchdate=DATE, publication_check={'version':1,'target_date':DATE,'link':'https://test/old','status':'old','published_date':'2025-03-06','evidence':[{'date':'2025-03-06'}]}),
            dict(title='fresh', link='https://test/new', content='引用2012年政策', fetchdate=DATE,
                 publication_check={'version':1, 'target_date':DATE, 'status':'accepted', 'published_date':DATE, 'link':'https://test/new', 'evidence':[{'source':'meta:pubdate','date':DATE}]} )]
    path = tmp_path/'rows.json'
    path.write_text(json.dumps(rows))
    result, _, _, _ = news_scorer.batch_score_news(path, TOPIC)
    assert [row['title'] for row in result] == ['fresh']
    assert calls == ['fresh']


def test_content_stage_records_and_resumes_date_check(tmp_path, monkeypatch):
    import fetch_content
    from topic_config import TOPIC
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('RUN_LOG_PATH', str(tmp_path/'run.log'))
    folder=tmp_path/'output'/DATE
    folder.mkdir(parents=True)
    path=folder/f'{DATE}_{TOPIC}.json'
    path.write_text(json.dumps([dict(title='旧文',link='https://test/old',content='已有正文',fetchdate=DATE)]))
    def fetch(url):
        return '已有正文', '<meta name="pubdate" content="2025-03-06">'
    monkeypatch.setattr(fetch_content, 'fetch_publication_page', fetch, raising=False)
    assert fetch_content.process_json(TOPIC, DATE)
    rows=json.loads(path.read_text())
    assert rows[0]['publication_check']['status']=='old'
    diagnostic=folder/'diagnostics'/'government_affairs_dates.json'
    assert json.loads(diagnostic.read_text())['counts']=={'accepted':0,'old':1,'pending':0}
    monkeypatch.setattr(fetch_content, 'fetch_publication_page', lambda _: pytest.fail('resume refetched'))
    assert fetch_content.process_json(TOPIC, DATE)


def test_disabled_tobacco_not_executed_or_counted_as_failed(tmp_path, monkeypatch):
    import main
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('RUN_LOG_PATH', str(tmp_path/'run.log'))
    monkeypatch.setenv('ENABLE_TOBACCO_CRAWLER','0')
    monkeypatch.setenv('ENABLE_ITEM_SUMMARIZER','1')
    monkeypatch.setattr(main,'execute_news_fetching',lambda *a: True)
    monkeypatch.setattr(main,'execute_content_fetching',lambda *a: True)
    monkeypatch.setattr(main,'execute_scoring_concurrent',lambda *a,**k:(1,0))
    monkeypatch.setattr(main,'run_step',lambda *a:True)
    commands=[]
    def run(cmd,*a,**k):
        commands.append(cmd)
        return 'tobacco_gov_crawler.py' not in cmd
    monkeypatch.setattr(main,'safe_subprocess_run',run)
    monkeypatch.setattr(main,'run_volume_alert',lambda *a,**k:None)
    results=[]
    monkeypatch.setattr(main,'log_script_complete',lambda *a,**k:results.append(k))
    assert main.main(DATE)
    assert not any('tobacco_gov_crawler.py' in cmd for cmd in commands)
    assert '6/6' in results[-1]['message']

@pytest.mark.parametrize('mutation', ['missing','link','date','conflict'])
def test_import_rejects_unverified_or_stale_evidence(tmp_path, monkeypatch, mutation):
    import importlib.util
    from unittest.mock import Mock, patch
    from topic_config import TOPIC
    row=dict(title='旧稿',link='https://test/old',fetchdate=DATE,content='正文',score=5,score_status='ok',keyword=TOPIC,
             publication_check={'version':1,'target_date':DATE,'status':'accepted','published_date':DATE,
                 'link':'https://test/old','evidence':[{'date':DATE}]})
    if mutation=='missing':row.pop('publication_check')
    if mutation=='link':row['publication_check']['link']='https://test/other'
    if mutation=='date':row['publication_check']['target_date']='2026-10-01'
    if mutation=='conflict':row['publication_check']['evidence'].append({'date':'2024-10-02'})
    path=tmp_path/'scored.json';path.write_text(json.dumps([row]))
    conn=Mock()
    conn.cursor.return_value.execute.side_effect=AssertionError('unverified row reached database')
    spec=importlib.util.spec_from_file_location('freshness_import_test',Path(__file__).with_name('write_to_mysql.py'))
    writer=importlib.util.module_from_spec(spec)
    with patch('db_utils.get_connection',return_value=conn):spec.loader.exec_module(writer)
    monkeypatch.setattr(writer,'write_log',lambda _:None)
    writer.import_scored_news_with_retry(str(path),TOPIC)
    assert writer.update_scores_from_json(str(path),TOPIC)==0


def test_publication_survives_content_extractor_error(monkeypatch):
    import fetch_content
    class Response:
        apparent_encoding='utf-8'
        text='<meta name="pubdate" content="2026-10-02">'
        def raise_for_status(self):pass
    monkeypatch.setattr(fetch_content.requests,'get',lambda *a,**k:Response())
    def fail(*a,**k):raise ValueError('text extraction failure')
    monkeypatch.setattr(fetch_content.trafilatura,'extract',fail)
    text,html=fetch_content.fetch_publication_page('https://test/')
    assert html==Response.text and text==''


def test_malformed_jsonld_type_is_pending():
    import news_freshness
    assert news_freshness.assess_html('<script type="application/ld+json">{"@type":[{}],"datePublished":"2026-10-02"}</script>',DATE)['status']=='pending'


def test_missing_check_cannot_create_false_completed_empty_score_file(tmp_path, monkeypatch):
    import news_scorer
    from topic_config import TOPIC
    row={'title':'未经核验','link':'https://test/','fetchdate':DATE,'content':'正文'}
    path=tmp_path/'input.json';path.write_text(json.dumps([row]))
    with pytest.raises(ValueError, match='publication'):
        news_scorer.batch_score_news(path,TOPIC)


def test_stale_template_meta_conflicts_with_printed_newspaper_date():
    import news_freshness
    html='<meta name="publishdate" content="2013-07-17"><span class="newstime">2026年10月02日</span>'
    result=news_freshness.assess_html(html, DATE)
    assert result['status']=='pending' and result['reason']=='conflicting_publication_dates'
