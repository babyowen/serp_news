"""Real post-release failures, with model/network/DB boundaries substituted."""
import json
from types import SimpleNamespace
from unittest.mock import patch
import pytest

FEEDBACK='Was this page helpful?\n Yes\n No\n Thank you for your feedback!'
ERROR='Operations too frequent.\nTry again later\nPage not found, please try again later.\nTake me home'
PAYWALL='政府が基金の見直しを強化することが分かった。\n会員限定\nこの記事は会員限定です\n（残り559文字／全文659文字）\n無料で登録すると月3本の鍵付き記事が読める'

def dated(content):
 return {'title':'业务新闻','link':'https://example.test/news','content':content,'keyword':'公积金','fetchdate':'2026-10-06','publication_check':{'version':1,'target_date':'2026-10-06','link':'https://example.test/news','status':'accepted','published_date':'2026-10-06','evidence':[{'date':'2026-10-06'}]}}

def test_compact_pubdate_agrees_with_iso_and_admits_article():
 from news_freshness import assess_html
 check=assess_html('<meta name="pubdate" content="20261006"><meta property="article:published_time" content="2026-10-06T16:00:37+08:00">','2026-10-06')
 assert check['status']=='accepted'
 assert len(check['evidence'])==2

@pytest.mark.parametrize('raw',['20260230','20261306','12320261006','202610060','编号20261006'])
def test_compact_pubdate_does_not_accept_invalid_or_embedded_numbers(raw):
 from news_freshness import parse_publication_date
 assert parse_publication_date(raw) is None

def test_compact_conflicting_pubdate_still_quarantines():
 from news_freshness import assess_html
 check=assess_html('<meta name="pubdate" content="20261005"><meta property="article:published_time" content="2026-10-06T16:00:37+08:00">','2026-10-06')
 assert check['status']=='pending' and check['reason']=='conflicting_publication_dates'

@pytest.mark.parametrize('body,status',[(FEEDBACK,'invalid_content'),(ERROR,'invalid_content'),(PAYWALL,'incomplete_content')])
def test_bad_body_never_calls_model_and_has_terminal_unscored_status(tmp_path,monkeypatch,body,status):
 from government_affairs_scoring import score_result,score_fields,scored_file_complete
 monkeypatch.setenv('RUN_LOG_PATH',str(tmp_path/'run.log'))
 pool=SimpleNamespace(get_client=lambda *a,**k:pytest.fail('bad body reached paid scoring'))
 result=score_result('业务新闻',body,'公积金','公积金',pool)
 assert (result.score,result.status,result.attempts)==(None,status,0)
 row=dated(body);row.update(score_fields(result));p=tmp_path/'scored.json';p.write_text(json.dumps([row]))
 assert scored_file_complete(p,'公积金')

@pytest.mark.parametrize('body',[FEEDBACK,ERROR,PAYWALL])
def test_raw_invalid_body_retains_publication_evidence_and_reason(tmp_path,monkeypatch,body):
 import fetch_content as fc
 monkeypatch.chdir(tmp_path);monkeypatch.setenv('RUN_LOG_PATH',str(tmp_path/'run.log'))
 p=tmp_path/'output/2026-10-06/2026-10-06_公积金.json';p.parent.mkdir(parents=True)
 row=dated(body);row['custom_grab']=True;row['wordcount']=len(body);p.write_text(json.dumps([row]))
 monkeypatch.setattr(fc,'fetch_article_content',lambda *_:pytest.fail('known rejected body must not loop in the fetch stage'))
 assert fc.process_json('公积金','2026-10-06')
 saved=json.loads(p.read_text())[0]
 assert saved['publication_check']==row['publication_check'] and saved['content']==body
 assert saved['content_quality']['status'] in {'invalid','incomplete'}
 assert saved['content_quality']['reason']

def test_error_page_never_enters_database_even_with_old_numeric_score(tmp_path,monkeypatch):
 from test_write_to_mysql_dedup import write_to_mysql,FakeConnection,FakeCursor
 c=FakeCursor();con=FakeConnection();monkeypatch.setattr(write_to_mysql,'cursor',c);monkeypatch.setattr(write_to_mysql,'conn',con)
 row=dated(ERROR);row.update(score=4,score_status='ok');p=tmp_path/'rows.json';p.write_text(json.dumps([row]))
 monkeypatch.setattr(write_to_mysql,'write_log',lambda *_:None)
 report=write_to_mysql.insert_scored_news(p,'公积金',report=True)
 assert report['completed_links']==[]
 assert not any(sql.startswith('INSERT') for sql,_ in c.executed)

@pytest.mark.parametrize('body',['公告：服务大厅10月8日上午暂停办理，下午恢复。','报道介绍网站故障：Page not found并不代表数据丢失，工作人员已修复系统。','本报推出订阅服务。今日政府基金公布投资项目名单，三个重点项目进入实施阶段。'])
def test_short_news_and_reporting_about_errors_remain_scorable(tmp_path,monkeypatch,body):
 from government_affairs_scoring import score_result
 monkeypatch.setenv('RUN_LOG_PATH',str(tmp_path/'run.log'))
 client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **k:SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='3'))]))))
 client.with_options=lambda **k:client
 pool=SimpleNamespace(get_client=lambda *a,**k:client)
 assert score_result('公告',body,'公积金','公积金',pool).score==3

@pytest.mark.parametrize('housing',[False,True])
def test_summary_does_not_accept_invented_interviewee_attribution(monkeypatch,housing):
 import news_item_summarizer as ns
 import news_region_utils as nr
 source='10月5日，市民到公积金大厅办理业务。服务负责人郭轶告诉记者，绝大部分业务可以在线办理。'
 summary='受访者认为，预约服务和商转公政策协同，有助于支持改善性住房需求。'
 if housing:
  monkeypatch.setattr(nr,'_call_llm',lambda *a,**k:json.dumps({'short_summary':summary,'region':'大连','business_types':[]}))
  result=nr.call_summary_and_region_llm('公积金办理不间断',source)
 else:
  client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **k:SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=summary))]))))
  monkeypatch.setattr(ns._pool,'get_client',lambda *a,**k:client);monkeypatch.setattr(ns.time,'sleep',lambda *_:None)
  result=ns.call_llm('公积金办理不间断',source,max_retries=1)
 assert result is None

def test_supported_interviewee_attribution_kept(monkeypatch):
 import news_region_utils as nr
 source='受访者表示，预约服务十分方便。'
 monkeypatch.setattr(nr,'_call_llm',lambda *a,**k:json.dumps({'short_summary':source,'region':'大连','business_types':[]}))
 assert nr.call_summary_and_region_llm('公积金办理',source)['short_summary']==source

@pytest.mark.parametrize('source,summary,want',[
 ('受访者在接受采访时表示，预约服务十分方便。','受访者表示，预约服务十分方便。',True),
 ('专家昨日指出，系统已经恢复。','专家指出，系统已经恢复。',True),
 ('记者联系受访者，但没有采访。负责人表示大厅开放。','受访者在接受采访时认为，需求增长。',False),
])
def test_anonymous_attribution_allows_explicit_interview_modifiers(source,summary,want):
 from summary_grounding import attribution_supported
 assert attribution_supported(summary,source) is want

def test_newly_fetched_error_is_preserved_but_not_counted_success(tmp_path,monkeypatch):
 import fetch_content as fc
 monkeypatch.chdir(tmp_path);monkeypatch.setenv('RUN_LOG_PATH',str(tmp_path/'run.log'))
 p=tmp_path/'output/2026-10-06/2026-10-06_公积金.json';p.parent.mkdir(parents=True)
 row=dated('');p.write_text(json.dumps([row]))
 monkeypatch.setattr(fc,'fetch_article_content',lambda *_:(ERROR,len(ERROR),False))
 assert fc.process_json('公积金','2026-10-06')
 saved=json.loads(p.read_text())[0]
 assert saved['content']==ERROR and saved['content_quality']['reason']=='error_page'
 assert json.loads((tmp_path/'output/news_source_stats.json').read_text())==[]
