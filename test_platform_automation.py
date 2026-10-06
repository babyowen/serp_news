"""Platform parity and bounded review; only external I/O is substituted."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

DAY = "2026-10-05"
KEYWORDS = ["江苏机关事务", "公积金", "养老", "以后新增的关键词"]

def row(status="pending"):
    return {"title":"新闻", "link":"https://example.test/news", "content":"正文内容" * 80,
            "date":DAY,"fetchdate":DAY, "keyword":"公积金", "search_keyword":"住房",
            "publication_check":{"version":1,"target_date":DAY,"link":"https://example.test/news",
                "status":status,"reason":"missing_publication_date" if status=="pending" else "target_date",
                "published_date": DAY if status=="accepted" else None,
                "evidence":[{"source":"meta:pubdate","date":DAY}] if status=="accepted" else []}}

@pytest.mark.parametrize("keyword", KEYWORDS)
def test_each_keyword_runs_original_date_gate(tmp_path, monkeypatch, keyword):
    import fetch_content as fc
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_LOG_PATH", str(tmp_path/"run.log"))
    folder=Path("output")/DAY; folder.mkdir(parents=True)
    path=folder/f"{DAY}_{keyword}.json"
    item=row(); item.pop("publication_check"); item["keyword"]=keyword
    path.write_text(json.dumps([item]))
    monkeypatch.setattr(fc,"fetch_publication_page",lambda _: (item["content"], '<meta name="pubdate" content="2021-01-01">'))
    assert fc.process_json(keyword, DAY)
    result=json.loads(path.read_text())[0]
    assert result.get("publication_check",{}).get("status")=="old"

@pytest.mark.parametrize("keyword", KEYWORDS)
def test_each_keyword_sends_time_rule_and_original_batch_date(monkeypatch, keyword):
    import news_scorer as ns
    calls=[]
    def create(**kw):
        calls.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="1"))])
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client.with_options=lambda **kw:client
    monkeypatch.setattr(ns._scoring_client_pool,"get_client",lambda *a,**kw:client)
    monkeypatch.setattr("government_affairs_scoring.value",lambda key: {
        "KEYWORD_SPECIFIC_SYSTEM_PROMPTS":{keyword:"本关键词业务规则"},
        "NEWS_SCORE_SYSTEM_MSG":"默认业务规则", "NEWS_SCORE_PROMPT":"{keyword} {title} {content}"}[key])
    assert "context" in __import__('inspect').signature(ns.score_news_result).parameters, "date evidence is missing from scoring interface"
    result=ns.score_news_result("新闻", "引用1998年历史政策，今天有新进展", "住房", keyword, context=row("accepted"))
    assert result.score==1
    system=calls[0]["messages"][0]["content"]
    user=calls[0]["messages"][1]["content"]
    assert "本关键词业务规则" in system and "2" in system and "七天" in system
    assert DAY in user and "meta:pubdate" in user

@pytest.mark.parametrize("keyword", KEYWORDS)
def test_every_keyword_quarantines_pending_and_old(tmp_path, keyword):
    import news_scorer as ns
    old=row("old"); old["publication_check"]["published_date"]="2021-01-01"
    path=tmp_path/"raw.json"; path.write_text(json.dumps([row(),old]))
    results,counts,total,_=ns.batch_score_news(path, keyword)
    assert results==[] and total==0


def review_module():
    import importlib.util
    assert importlib.util.find_spec("date_review") is not None, "automatic date review has not been implemented"
    import date_review
    return date_review


def test_review_is_bounded_and_does_not_spend_two_attempts_same_day():
    dr=review_module(); item=row()
    dr.track_review(item,"2026-10-06")
    assert not dr.review_due(item,"2026-10-06")
    assert dr.review_due(item,"2026-10-07")
    dr.start_review(item,"2026-10-07"); dr.track_review(item,"2026-10-07")
    assert not dr.review_due(item,"2026-10-07")
    assert dr.review_due(item,"2026-10-08")
    dr.start_review(item,"2026-10-08"); dr.track_review(item,"2026-10-08")
    assert item["date_review"]["status"]=="archived"
    assert item["date_review"]["attempts"]==2
    assert not dr.review_due(item,"2026-10-09")
    assert item["publication_check"]["status"]=="pending"
    assert item["content"]


def test_review_success_is_queued_for_delivery_and_old_is_not():
    dr=review_module()
    for status,want in [("accepted","ready"),("old","excluded")]:
        item=row();dr.track_review(item,"2026-10-06")
        dr.start_review(item,"2026-10-07")
        item["publication_check"]=row(status)["publication_check"]
        dr.track_review(item,"2026-10-07")
        assert item["date_review"]["status"]==want
        assert item["fetchdate"]==DAY


def test_old_pending_without_saved_clock_not_silently_activated():
    dr=review_module()
    assert not dr.review_due(row(),"2026-10-07")


def test_review_missed_days_do_not_make_unlimited_backlog():
    dr=review_module(); item=row(); dr.track_review(item,"2026-10-06")
    assert not dr.review_due(item,"2026-11-06")
    dr.expire_review(item,"2026-11-06")
    assert item["date_review"]["status"]=="archived"


def test_recovered_rows_merge_scores_without_rescoring_success(tmp_path, monkeypatch):
    import news_scorer as ns
    from government_affairs_scoring import ScoreResult
    assert hasattr(ns,"refresh_review_scores"), "recovery does not continue into scoring"
    path=tmp_path/"batch.json"
    first=row("accepted"); first.update(score=0,score_status="ok")
    second=row("accepted"); second.update(title="新增",link="https://example.test/new")
    second["publication_check"]["link"]=second["link"]
    path.write_text(json.dumps([first, second]))
    scored=tmp_path/"batch_scored.json"; scored.write_text(json.dumps([first]))
    calls=[]
    def score(*args,**kwargs):
        calls.append((args[0],kwargs["context"]["fetchdate"]))
        return ScoreResult(4,"ok")
    monkeypatch.setattr(ns,"score_news_result",score)
    selected=ns.refresh_review_scores(path,"公积金",{second["link"]})
    assert [(n["title"],n["score"]) for n in selected]==[("新增",4)]
    assert calls==[("新增",DAY)]
    assert {n["title"]:n["score"] for n in json.loads(scored.read_text())}=={"新闻":0,"新增":4}
    ns.refresh_review_scores(path,"公积金",{second["link"]})
    assert len(calls)==1


def test_worker_rechecks_original_date_and_delivers_once(tmp_path, monkeypatch):
    import date_review as dr
    import fetch_content as fc
    import batch_config
    assert hasattr(dr,"review_batch"), "review runner missing"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_LOG_PATH",str(tmp_path/"log"))
    folder=Path("output")/DAY;folder.mkdir(parents=True)
    item=row(); dr.track_review(item,"2026-10-06")
    path=folder/f"{DAY}_公积金.json";path.write_text(json.dumps([item]))
    pins=[]
    monkeypatch.setattr(batch_config,"prepare_batch",lambda day,kw: pins.append((day,kw)))
    pages=[]
    def page(url):
        pages.append(url)
        return item["content"], '<meta name="pubdate" content="2026-10-05">'
    monkeypatch.setattr(fc,"fetch_publication_page",page)
    deliveries=[]
    def deliver(path,keyword,links):
        deliveries.append((keyword,links));return True
    assert dr.review_batch(DAY,"公积金","2026-10-07",deliver=deliver)
    assert pins==[(DAY,"公积金")]
    saved=json.loads(path.read_text())[0]
    assert saved["date_review"]["status"]=="delivered"
    assert saved["publication_check"]["published_date"]==DAY
    assert saved["fetchdate"]==DAY
    assert len(pages)==len(deliveries)==1
    dr.review_batch(DAY,"公积金","2026-10-07",deliver=deliver)
    assert len(pages)==len(deliveries)==1


def test_delivery_failures_retry_on_later_days_then_archive(tmp_path, monkeypatch):
    import date_review as dr
    import batch_config
    assert hasattr(dr,"review_batch"), "review runner missing"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_LOG_PATH",str(tmp_path/"log"))
    monkeypatch.setattr(batch_config,"prepare_batch",lambda *a:None)
    folder=Path("output")/DAY;folder.mkdir(parents=True)
    item=row();dr.track_review(item,"2026-10-06")
    item["publication_check"]=row("accepted")["publication_check"]
    dr.track_review(item,"2026-10-07")
    path=folder/f"{DAY}_公积金.json";path.write_text(json.dumps([item]))
    calls=[]
    def deliver(*a):calls.append(1);raise OSError("database unavailable")
    for day in ["2026-10-07","2026-10-07","2026-10-08","2026-10-09","2026-10-10"]:
        dr.review_batch(DAY,"公积金",day,deliver=deliver)
    assert len(calls)==3
    saved=json.loads(path.read_text())[0]
    assert saved["date_review"]["status"]=="delivery_failed"
    assert saved["publication_check"]["status"]=="accepted"


@pytest.mark.parametrize("score,want",[(0,0),(1,1),(2,2),(3,2),(4,2),(5,2)])
def test_old_score_cap_never_raises_low_score(score,want):
    from scoring_policy import cap_score
    item=row("old")
    item["publication_check"].update(published_date="2026-09-27",evidence=[{"date":"2026-09-27"}])
    assert cap_score(score,item)==want

@pytest.mark.parametrize("days,want",[("2026-09-28",5),("2026-09-27",2)])
def test_seven_day_boundary(days,want):
    from scoring_policy import cap_score
    item=row("old")
    item["publication_check"].update(published_date=days,evidence=[{"date":days}])
    assert cap_score(5,item)==want


def test_background_year_is_not_programmatically_treated_as_publication():
    from scoring_policy import cap_score
    item=row("accepted");item["content"]="2026年10月5日落实1998年出台的政策，新增公共服务。"
    assert cap_score(5,item)==5


def test_partial_delivery_does_not_mark_other_rows_delivered(tmp_path, monkeypatch):
    import date_review as dr
    import batch_config
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(batch_config,"prepare_batch",lambda *a:None)
    folder=Path("output")/DAY;folder.mkdir(parents=True)
    rows=[]
    for link in ["https://example.test/good", "https://example.test/bad"]:
        item=row();item["link"]=link
        dr.track_review(item,"2026-10-06")
        item["publication_check"]=row("accepted")["publication_check"]
        item["publication_check"]["link"]=link
        dr.track_review(item,"2026-10-07");rows.append(item)
    path=folder/f"{DAY}_公积金.json";path.write_text(json.dumps(rows))
    dr.review_batch(DAY,"公积金","2026-10-07",deliver=lambda *a:{rows[0]["link"]})
    saved=json.loads(path.read_text())
    assert [r["date_review"]["status"] for r in saved]==["delivered","ready"]


def test_delivery_after_crash_cannot_exceed_three_attempts(tmp_path, monkeypatch):
    import date_review as dr
    import batch_config
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(batch_config,"prepare_batch",lambda *a:None)
    folder=Path("output")/DAY;folder.mkdir(parents=True)
    item=row();dr.track_review(item,"2026-10-06")
    item["publication_check"]=row("accepted")["publication_check"]
    dr.track_review(item,"2026-10-07")
    item["date_review"].update(delivery_attempts=3)
    path=folder/f"{DAY}_公积金.json";path.write_text(json.dumps([item]))
    dr.review_batch(DAY,"公积金","2026-10-10",deliver=lambda *a:pytest.fail("extra paid attempt"))
    assert json.loads(path.read_text())[0]["date_review"]["status"]=="delivery_failed"


def test_scheduler_does_not_inherit_current_batch_revision(tmp_path, monkeypatch):
    import date_review as dr
    import subprocess
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SERP_CONFIG_REVISION","current:9")
    item=row();dr.track_review(item,"2026-10-06");dr.sync_job([item],DAY,"公积金")
    requests=[]
    def run(args,**kwargs):
        requests.append((args,kwargs));return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess,"run",run)
    outcomes=dr.run_due("2026-10-07",["公积金"])
    assert len(outcomes)==1 and outcomes[0]["ok"]
    args,kwargs=requests[0]
    assert args[args.index("--batch-date")+1]==DAY
    assert "SERP_CONFIG_REVISION" not in kwargs["env"]
    import os
    assert os.environ["SERP_CONFIG_REVISION"]=="current:9"


def test_resume_does_not_reset_failed_delivery_schedule():
    import date_review as dr
    item=row();dr.track_review(item,"2026-10-06")
    item["publication_check"]=row("accepted")["publication_check"]
    dr.track_review(item,"2026-10-07")
    item["date_review"].update(delivery_attempts=1,next_date="2026-10-08")
    dr.track_review(item,"2026-10-07")
    assert item["date_review"]["next_date"]=="2026-10-08"


@pytest.mark.parametrize("keyword",KEYWORDS)
def test_scoring_failure_is_nullable_for_every_keyword(monkeypatch, keyword):
    import news_scorer as ns
    import government_affairs_scoring as gs
    calls=[]
    def create(**kw):
        calls.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="评分为5"))])
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client.with_options=lambda **kw:client
    monkeypatch.setattr(ns._scoring_client_pool,"get_client",lambda *a,**kw:client)
    monkeypatch.setattr(gs.time,"sleep",lambda _:None)
    result=ns.score_news_result("新闻","正文","住房",keyword,context=row("accepted"))
    assert (result.score,result.status,result.attempts,result.error_code)==(None,"failed",3,"invalid_output")
    assert len(calls)==3


def test_real_recovery_delivers_good_body_without_waiting_for_empty_one(tmp_path,monkeypatch):
    import date_review as dr
    import fetch_content as fc
    import news_scorer as ns
    import write_to_mysql as db
    from government_affairs_scoring import ScoreResult
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_LOG_PATH",str(tmp_path/"log"))
    good=row("accepted")
    bad=row("accepted");bad.update(link="https://example.test/empty",content="")
    bad["publication_check"]["link"]=bad["link"]
    folder=Path("output")/DAY;folder.mkdir(parents=True)
    path=folder/f"{DAY}_公积金.json";path.write_text(json.dumps([good,bad]))
    monkeypatch.setattr(fc,"fetch_publication_page",lambda _: ("",'<meta name="pubdate" content="2026-10-05">'))
    monkeypatch.setattr(fc,"fetch_article_content",lambda _: ("",0,False))
    calls=[]
    def score(*a,**kw):calls.append(a[0]);return ScoreResult(4,"ok")
    monkeypatch.setattr(ns,"score_news_result",score)
    imported=[]
    def write(file,keyword,**kwargs):
        rows=json.loads(Path(file).read_text());imported.extend(rows)
        return {"completed_links":[r["link"] for r in rows]}
    monkeypatch.setattr(db,"import_scored_news_with_retry",write)
    delivered=dr.deliver_rows(path,"公积金",{good["link"],bad["link"]})
    assert delivered=={good["link"]}
    assert [r["link"] for r in imported]==[good["link"]]
    assert calls==[good["title"]]
    dr.deliver_rows(path,"公积金",{good["link"]})
    assert len(calls)==1


def test_review_body_revealing_old_date_excludes_row(tmp_path,monkeypatch):
    import date_review as dr
    import batch_config
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(batch_config,"prepare_batch",lambda *a:None)
    item=row();dr.track_review(item,"2026-10-06")
    item["publication_check"]=row("accepted")["publication_check"]
    dr.track_review(item,"2026-10-07")
    folder=Path("output")/DAY;folder.mkdir(parents=True)
    path=folder/f"{DAY}_公积金.json";path.write_text(json.dumps([item]))
    def deliver(path,*a):
        rows=json.loads(path.read_text());rows[0]["publication_check"].update(status="old",published_date="2020-01-01",evidence=[{"date":"2020-01-01"}])
        path.write_text(json.dumps(rows));return set()
    assert dr.review_batch(DAY,"公积金","2026-10-07",deliver=deliver)
    assert json.loads(path.read_text())[0]["date_review"]["status"]=="excluded"


@pytest.mark.parametrize("kind",["conflict","unparseable"])
def test_refetch_failure_never_discards_prior_publication_evidence(tmp_path,monkeypatch,kind):
    import fetch_content as fc
    import news_freshness as nf
    monkeypatch.chdir(tmp_path)
    item=row()
    item.update(sourceapi="serp_bingnews",search_date_raw="7h",search_fetched_at="2026-10-06T00:30:00+08:00")
    html=('<meta name="pubdate" content="2021-01-01"><meta property="article:published_time" content="2026-10-05">'
          if kind=="conflict" else '<meta name="pubdate" content="unknown-format">')
    previous=nf.assess_html(html,DAY);previous["link"]=item["link"]
    item["publication_check"]=previous
    monkeypatch.setattr(fc,"fetch_publication_page",lambda _: ("",""))
    fc.check_topic_dates([item],DAY,"公积金",force=True,today="2026-10-07")
    assert item["publication_check"]["status"]=="pending"
    assert item["publication_check"]["reason"]==previous["reason"]
    assert not nf.eligible(item)
    assert item["publication_check"].get("evidence")==previous.get("evidence")
    assert item["publication_check"].get("unparsed_evidence")==previous.get("unparsed_evidence")


def test_review_delivery_preserves_short_successful_custom_body(tmp_path,monkeypatch):
    import date_review as dr
    import news_scorer as ns
    import write_to_mysql as db
    from government_affairs_scoring import ScoreResult
    monkeypatch.chdir(tmp_path);monkeypatch.setenv("RUN_LOG_PATH",str(tmp_path/"log"))
    item=row("accepted")
    item.update(content="机构公布了公共服务开放安排，现场由工作人员提供咨询服务。",custom_grab=True)
    folder=Path("output")/DAY;folder.mkdir(parents=True)
    path=folder/f"{DAY}_公积金.json";path.write_text(json.dumps([item]))
    calls=[]
    def score(*a,**kw):calls.append(a[0]);return ScoreResult(4,"ok")
    monkeypatch.setattr(ns,"score_news_result",score)
    monkeypatch.setattr(db,"import_scored_news_with_retry",lambda *a,**kw:{"completed_links":[item["link"]]})
    assert dr.deliver_rows(path,"公积金",{item["link"]})=={item["link"]}
    assert calls==[item["title"]]



def test_database_partial_import_reports_successful_rows(tmp_path,monkeypatch):
    import write_to_mysql as db
    import inspect
    assert "report" in inspect.signature(db.import_scored_news_with_retry).parameters, "DB importer does not report per-row outcomes"
    class Cursor:
        rowcount=0
        def execute(self,sql,params):
            if sql.lstrip().startswith("INSERT") and params[1]=="bad":
                raise __import__("pymysql").err.DataError(1406,"permanent row error")
        def fetchall(self):return []
    monkeypatch.setattr(db,"cursor",Cursor())
    monkeypatch.setattr(db,"conn",SimpleNamespace(commit=lambda:None))
    monkeypatch.setattr(db,"write_log",lambda _:None)
    good=row("accepted");good.update(score=4,score_status="ok")
    bad=row("accepted");bad.update(title="bad",link="https://example.test/bad",score=3,score_status="ok")
    bad["publication_check"]["link"]=bad["link"]
    path=tmp_path/"scored.json";path.write_text(json.dumps([good,bad]))
    report=db.import_scored_news_with_retry(str(path),"公积金",report=True)
    assert report["completed_links"]==[good["link"]]
    assert report["failed_links"]==[bad["link"]]
    assert report["ok"] is False



def test_content_persistence_never_truncates_raw_batch(tmp_path,monkeypatch):
    import fetch_content as fc
    import builtins
    monkeypatch.chdir(tmp_path);monkeypatch.setenv("RUN_LOG_PATH",str(tmp_path/"log"))
    item=row("accepted");item.update(content="",wordcount=0)
    folder=Path("output")/DAY;folder.mkdir(parents=True)
    path=folder/f"{DAY}_公积金.json";path.write_text(json.dumps([item]))
    monkeypatch.setattr(fc,"fetch_article_content",lambda _: ("有效正文"*80,320,False))
    original=builtins.open
    def guard(file,mode="r",*a,**kw):
        if Path(file)==path and "w" in mode:
            pytest.fail("raw batch was opened with truncation instead of atomic replacement")
        return original(file,mode,*a,**kw)
    monkeypatch.setattr(builtins,"open",guard)
    assert fc.process_json("公积金",DAY)
    assert json.loads(path.read_text())[0]["content"]=="有效正文"*80


@pytest.mark.parametrize("stage",["insert","update"])
@pytest.mark.parametrize("code",[1213,1205])
def test_transaction_error_never_reports_uncommitted_rows(tmp_path,monkeypatch,stage,code):
    import write_to_mysql as db
    import pymysql
    good=row("accepted");good.update(score=4,score_status="ok")
    bad=row("accepted");bad.update(title="bad",link="https://example.test/bad",score=3,score_status="ok")
    bad["publication_check"]["link"]=bad["link"]
    pending=[];committed=[];rollbacks=[]
    class Cursor:
        rowcount=1
        def execute(self,sql,params):
            sql=sql.lstrip()
            relevant=sql.startswith("INSERT" if stage=="insert" else "UPDATE")
            if relevant and params[1]=="bad":
                pending.clear()
                raise pymysql.err.OperationalError(code,"transaction aborted")
            if relevant:pending.append(params[1])
        def fetchall(self):return [] if stage=="insert" else [(good["title"],good["link"]),(bad["title"],bad["link"])]
    def rollback():rollbacks.append(True);pending.clear()
    monkeypatch.setattr(db,"cursor",Cursor())
    monkeypatch.setattr(db,"conn",SimpleNamespace(commit=lambda:committed.extend(pending),rollback=rollback))
    monkeypatch.setattr(db,"write_log",lambda _:None)
    path=tmp_path/"rows.json";path.write_text(json.dumps([good,bad]))
    with pytest.raises(pymysql.err.OperationalError):
        db.import_scored_news_with_retry(str(path),"公积金",report=True)
    assert committed==[] and rollbacks
