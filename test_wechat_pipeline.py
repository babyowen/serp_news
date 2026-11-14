"""Exercise the real pipeline entry with offline provider/model/database transports."""
from datetime import datetime
import json
import os
from pathlib import Path
import shlex
import sys
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pymysql
import pytest

from news_freshness import assess_publication_evidence
from test_wechat_collector import DAY, BODY, Provider, article, page
from topic_config import TOPIC


def read(path):
    return json.loads(Path(path).read_text())


def news(title, link, keyword=TOPIC):
    return dict(title=title, link=link, keyword=keyword, main_keyword=keyword,
                content=BODY, date=DAY, fetchdate=DAY,
                publication_check={**assess_publication_evidence(
                    [{"source": "fixture", "date": DAY}], DAY), "link": link})


class Database:
    """A transactional loopback with statement errors and commit acknowledgement loss."""
    def __init__(self):
        self.rows = []
        self.uncommitted = []
        self.result = []
        self.rowcount = 0
        self.reject = None
        self.fail_score_update = False
        self.lose_commit_ack = False

    def cursor(self):
        return self

    def close(self):
        pass

    def rollback(self):
        self.uncommitted.clear()

    def commit(self):
        self.rows.extend(self.uncommitted)
        had_writes = bool(self.uncommitted)
        self.uncommitted.clear()
        if had_writes and self.lose_commit_ack:
            raise pymysql.OperationalError(2013, "lost commit acknowledgement")

    def ping(self, **_):
        pass

    def fetchall(self):
        return list(self.result)

    def fetchone(self):
        return self.result[0] if self.result else None

    def execute(self, sql, params):
        self.rowcount = 0
        if "news_websites" in sql or "news_source_stats" in sql:
            self.result = []
        elif sql.startswith("SELECT title, link"):
            self.result = [(r["title"], r["link"]) for r in self.rows if r["keyword"] == params[0]]
        elif sql.lstrip().startswith("INSERT INTO"):
            day, title, link, _, fetchdate, _, _, keyword, content, _, _, score, _ = params
            if title == self.reject:
                raise pymysql.DataError(1406, "invalid row")
            self.uncommitted.append(dict(id=len(self.rows) + len(self.uncommitted) + 1, title=title,
                                         link=link, fetchdate=fetchdate, keyword=keyword, content=content,
                                         score=score, short_summary=None))
            self.rowcount = 1
        elif sql.startswith("UPDATE") and "SET score=" in sql:
            if self.fail_score_update:
                raise pymysql.OperationalError(1213, "update rolled back")
            score, title, keyword, link, *identity = params
            for row in self.rows:
                if (row["title"], row["keyword"], row["link"], row["score"]) != (title, keyword, link, None):
                    continue
                if identity:
                    assert all(predicate in sql for predicate in (
                        "fetchdate=%s", "CAST(content AS BINARY)=CAST(%s AS BINARY)",
                        "CAST(title AS BINARY)=CAST(%s AS BINARY)", "CAST(link AS BINARY)=CAST(%s AS BINARY)",
                    ))
                    if [row["fetchdate"], row["content"], row["title"], row["link"]] != identity:
                        continue
                row["score"] = score
                self.rowcount += 1
        elif sql.startswith("SELECT id, title, content, keyword"):
            self.result = [(r["id"], r["title"], r["content"], r["keyword"]) for r in self.rows
                           if r["fetchdate"] == params[0] and r["keyword"] in params[1:]
                           and r["score"] is not None and r["score"] >= 3 and not r["short_summary"]]
        elif sql.startswith("SELECT id, title, link, fetchdate, score, short_summary, content"):
            self.result = [(r["id"], r["title"], r["link"], r["fetchdate"], r["score"],
                            r["short_summary"], r["content"]) for r in self.rows
                           if r["keyword"] == params[0] and r["title"] in params[1:]]
        elif sql.lstrip().startswith("UPDATE") and "short_summary" in sql:
            summary, rid = params
            for row in self.rows:
                if row["id"] == rid and not row["short_summary"]:
                    row["short_summary"] = summary
                    self.rowcount = 1
        else:
            raise AssertionError(sql)


@pytest.fixture
def integration(monkeypatch, tmp_path):
    import main
    import fetch_content
    import news_item_summarizer
    import news_scorer
    import wechat_collector
    import wechat_notifications
    import news_volume_alert
    from government_affairs_scoring import ScoreResult

    from config_store import ConfigStore, read_document
    from topic_config import build_topic_candidate

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_LOG_PATH", str(tmp_path / "main.log"))
    for name in ("SERP_WECHAT_STAGE_RUN_ID", "SERP_WECHAT_STAGE_DATE"):
        monkeypatch.delenv(name, raising=False)
    store = ConfigStore(tmp_path / "integration.sqlite")
    document = read_document(Path(__file__).with_name("config_defaults.json"))
    document = build_topic_candidate(document)["document"]
    document["settings"]["SEARCH_KEYWORDS"]["未来关键词"] = ["未来词"]
    store.initialize(document, {"kind": "test"})
    monkeypatch.setenv("SERP_CONFIG_STORE", str(store.path))
    monkeypatch.setenv("SERP_CONFIG_REVISION", "")
    monkeypatch.setenv("ENABLE_GOVERNMENT_WECHAT", "1")
    monkeypatch.setenv("ENABLE_ITEM_SUMMARIZER", "1")
    monkeypatch.setenv("ENABLE_TOBACCO_CRAWLER", "0")
    monkeypatch.setattr(wechat_notifications, "_now", lambda: datetime.fromisoformat("2026-11-14T08:00:00+08:00"))
    monkeypatch.setattr(main, "run_volume_alert", lambda **_: None)
    monkeypatch.setattr(news_item_summarizer, "table_has_region_column", lambda *_: False)
    monkeypatch.setattr(news_item_summarizer, "table_has_business_types_column", lambda *_: False)
    monkeypatch.setattr(news_item_summarizer.time, "sleep", lambda *_: None)
    db, scored, cards, commands = Database(), [], [], []
    with patch("db_utils.get_connection", return_value=db):
        import write_to_mysql
    monkeypatch.setattr(write_to_mysql, "DEFAULT_KEYWORDS", list(store.read().document["settings"]["SEARCH_KEYWORDS"]))
    monkeypatch.setattr(write_to_mysql, "conn", db)
    monkeypatch.setattr(write_to_mysql, "cursor", db)
    monkeypatch.setattr(write_to_mysql, "get_connection", lambda **_: db)
    monkeypatch.setattr(write_to_mysql, "write_log", lambda *_: None)
    monkeypatch.setattr(news_item_summarizer, "get_conn", lambda: db)
    monkeypatch.setattr(news_volume_alert, "send_feishu",
                        lambda card, **kw: cards.append(card) or True)

    def score(title, *_args, **_kwargs):
        scored.append(title)
        return ScoreResult(2 if title == "无需摘要" else 4, "ok")

    monkeypatch.setattr(news_scorer, "score_news_result", score)
    monkeypatch.setattr(fetch_content, "fetch_publication_page",
                        lambda *_: pytest.fail("unexpected generic date request"))
    monkeypatch.setattr(fetch_content, "fetch_article_content",
                        lambda *_: pytest.fail("unexpected generic browser request"))
    accounts = [a for a in wechat_collector.load_registry()["accounts"] if a["enabled"]]
    pages = {(a["ghid"], ""): [page(ghid=a["ghid"])] for a in accounts}
    provider = Provider(pages)
    collect = wechat_collector.collect_wechat
    monkeypatch.setattr(wechat_collector, "collect_wechat", lambda day: collect(day, client=provider))

    search_rows = {}
    review = [lambda: True]

    def child(cmd, _step, **options):
        _, script, *args = shlex.split(cmd)
        commands.append((script, args, options))
        if script == "fetch_and_filter.py":
            p = Path(args[args.index("--output") + 1])
            result = search_rows.get(args[0], [])
            if isinstance(result, Exception):
                return False
            p.write_text(json.dumps(result))
            return True
        if script == "date_review.py":
            return review[0]()
        module = {"fetch_content.py": fetch_content, "news_item_summarizer.py": news_item_summarizer,
                  "news_scorer.py": news_scorer, "write_to_mysql.py": write_to_mysql}[script]
        with patch.object(sys, "argv", [script, *args]):
            return module.main()

    monkeypatch.setattr(main, "safe_subprocess_run", child)
    provider.search_rows = search_rows
    provider.review = review
    return main, accounts, provider, db, scored, cards, commands


def ledger(index=-1):
    records = sorted(
        (read(path) for path in Path("output/wechat_runs").glob(f"{DAY}/*.json")
         if path.stem != "overdue"),
        key=lambda row: Path("output/wechat_runs", DAY, row["run_id"] + ".json").stat().st_mtime_ns,
    )
    assert records
    return records[index]


def test_wechat_main_reuses_completed_sources_and_scores_new_articles(integration):
    main, accounts, provider, db, scored, cards, commands = integration
    a, b = accounts[:2]
    provider.pages[a["ghid"], ""] = [page(article(101, biz=a["biz"]), ghid=a["ghid"])]
    provider.pages[b["ghid"], ""] = [
        dict(code=20001, msg="insufficient balance"),
        page(article(102, biz=b["biz"]), ghid=b["ghid"]),
    ]
    assert main.main(DAY, keyword=TOPIC) is False
    first = ledger()
    assert first["status"] == "partial"
    assert scored == ["文章101-1"]
    assert len(db.rows) == len(cards) == 1
    search_count = sum(script == "fetch_and_filter.py" for script, *_ in commands)

    assert main.main(DAY, keyword=TOPIC) is True
    second = ledger()
    assert second["status"] == "success"
    assert scored == ["文章101-1", "文章102-1"]
    assert len(db.rows) == len(cards) == 2
    history = [ghid for name, ghid, *rest in provider.calls if name == "history"]
    assert len(set(history)) == 38
    assert history.count(a["ghid"]) == 1
    assert history.count(b["ghid"]) == 2
    assert sum(script == "fetch_and_filter.py" for script, *_ in commands) == search_count
    receipts = Path("output/wechat_runs") / DAY / "stages" / second["run_id"]
    assert read(receipts / "scoring.json")["counts"] == {
        "candidates": 2, "succeeded": 1, "failed": 0, "skipped": 0, "already_done": 1
    }
    assert read(receipts / "database.json")["counts"] == {
        "inserted": 1, "duplicate": 1, "failed": 0, "skipped": 0
    }
    assert read(receipts / "summary.json")["counts"] == {
        "succeeded": 1, "failed": 0, "already_done": 1, "not_needed": 0, "pending": 0
    }
    assert all(options.get("retries") == 1 for script, _, options in commands
               if script == "write_to_mysql.py")


def test_wechat_main_zero_and_beijing_check(integration):
    from wechat_notifications import check_missing

    main, accounts, provider, db, scored, cards, commands = integration
    assert main.main(DAY, keyword=TOPIC) is True
    run = ledger()
    assert run["status"] == "success"
    report = read(Path("output") / DAY / "diagnostics/wechat_collection.json")
    assert len(report["accounts"]) == 38
    assert {account["status"] for account in report["accounts"]} == {"empty"}
    for stage in ("scoring", "database", "summary"):
        receipt = read(Path("output/wechat_runs") / DAY / "stages" / run["run_id"] / f"{stage}.json")
        assert receipt["counts"] and set(receipt["counts"].values()) == {0}
    assert read(Path("output") / DAY / f"{DAY}_{TOPIC}_scored.json") == []
    check_missing(now=datetime.fromisoformat(run["started_at"]).replace(
        hour=9, minute=0, tzinfo=ZoneInfo("Asia/Shanghai")), send_fn=lambda card: cards.append(card) or True)
    assert len(cards) == 1


def test_wechat_main_finalizes_on_exception(integration, monkeypatch):
    main, accounts, provider, db, scored, cards, commands = integration

    def broken(*_):
        raise RuntimeError("content crashed before scoring")

    monkeypatch.setattr(main, "execute_content_fetching", broken)
    assert main.main(DAY, keyword=TOPIC) is False
    run = ledger()
    assert run["status"] == "failed"
    assert run["notification"]["state"] == "sent"
    assert len(cards) == 1
    assert not any(k.startswith("SERP_WECHAT_STAGE") for k in os.environ)


def test_wechat_body_and_publication_conflict_cannot_enter_generic_fetch(integration):
    import fetch_content

    row = news("来源冲突", "https://mp.weixin.qq.com/s?__biz=example")
    row.update(wechat_article_id="source/100/1", wechat_content_status="failed", content="")
    check = assess_publication_evidence(
        [{"source": "wechat_history", "date": DAY}, {"source": "wechat_http", "date": "2026-11-12"}],
        DAY)
    row["publication_check"] = {**check, "link": row["link"]}
    fetch_content.check_topic_dates([row], DAY, TOPIC, force=True)
    assert row["publication_check"] == {**check, "link": row["link"]}
    assert not row.get("date_review")
    assert fetch_content.content_fetch_complete(row, TOPIC)


def test_wechat_committed_insert_counts_survive_failed_score_sync(integration):
    main, accounts, provider, db, scored, cards, commands = integration
    a = accounts[0]
    provider.pages[a["ghid"], ""] = [page(article(100, biz=a["biz"]), ghid=a["ghid"])]
    db.fail_score_update = True
    assert main.main(DAY, keyword=TOPIC) is False
    run = ledger()
    receipt = read(Path("output/wechat_runs") / DAY / "stages" / run["run_id"] / "database.json")
    assert receipt["counts"]["inserted"] == 1
    assert receipt["status"] == "failed"
    assert len(db.rows) == 1
    assert len(cards) == 1


def test_wechat_import_lost_commit_ack_does_not_report_an_insert(integration):
    main, accounts, provider, db, scored, cards, commands = integration
    a = accounts[0]
    provider.pages[a["ghid"], ""] = [page(article(100, biz=a["biz"]), ghid=a["ghid"])]
    db.lose_commit_ack = True
    assert main.main(DAY, keyword=TOPIC) is False
    receipt = Path("output/wechat_runs") / DAY / "stages" / ledger()["run_id"] / "database.json"
    assert read(receipt)["counts"].get("inserted") is None
    assert len(db.rows) == 1


def test_wechat_summary_maps_same_title_to_actual_database_row(integration):
    main, accounts, provider, db, scored, cards, commands = integration
    a = accounts[0]
    duplicate = "跨来源同一标题"
    shared = news(duplicate, "https://example.test/search")
    provider.search_rows["机关事务管理"] = [shared]
    provider.pages[a["ghid"], ""] = [
        page(article(201, 1, biz=a["biz"], title=duplicate),
             article(201, 2, biz=a["biz"], title=duplicate),
             article(202, biz=a["biz"], title="无需摘要"), ghid=a["ghid"])
    ]
    assert main.main(DAY, keyword=TOPIC) is True
    run = ledger()
    receipts = Path("output/wechat_runs") / DAY / "stages" / run["run_id"]
    assert len(db.rows) == 2
    imported = read(receipts / "database.json")
    assert imported["counts"] == {"inserted": 1, "duplicate": 2, "failed": 0, "skipped": 0}
    assert [row["stored_link"] for row in imported["articles"] if row["status"] == "duplicate"] == [shared["link"]] * 2
    summarized = read(receipts / "summary.json")
    assert summarized["counts"] == {
        "succeeded": 1, "failed": 0, "already_done": 0, "not_needed": 1, "pending": 0
    }
    assert len(next(row["candidates"] for row in summarized["articles"] if row["status"] == "succeeded")) == 2


def test_wechat_row_error_keeps_other_committed_counts(integration):
    main, accounts, provider, db, scored, cards, commands = integration
    a = accounts[0]
    provider.pages[a["ghid"], ""] = [
        page(article(201, biz=a["biz"]), article(202, biz=a["biz"]), ghid=a["ghid"])
    ]
    db.reject = "文章201-1"
    assert main.main(DAY, keyword=TOPIC) is False
    run = ledger()
    receipts = Path("output/wechat_runs") / DAY / "stages" / run["run_id"]
    assert read(receipts / "database.json")["counts"] == {
        "inserted": 1, "duplicate": 0, "failed": 1, "skipped": 0,
    }
    assert read(receipts / "summary.json")["counts"] == {
        "succeeded": 1, "failed": 0, "already_done": 0, "not_needed": 0, "pending": 1,
    }


def test_wechat_search_match_cannot_bypass_collector_conflict(integration):
    main, accounts, provider, db, scored, cards, commands = integration
    a = accounts[0]
    captured = article(201, biz=a["biz"])
    search_url = captured["url"].replace("#rd", "&tracking=fixture")
    provider.search_rows["机关事务管理"] = [news(captured["title"], search_url)]
    provider.pages[a["ghid"], ""] = [page(captured, ghid=a["ghid"])]
    provider.bodies[captured["url"]] = '<script>var ct = "1794499199";</script><div id="js_content">内容</div>'
    assert main.main(DAY, keyword=TOPIC) is False
    rows = read(Path("output", DAY, f"{DAY}_{TOPIC}.json"))
    assert len(rows) == 1
    assert rows[0]["publication_check"]["reason"] == "conflicting_publication_dates"
    assert rows[0]["wechat_search_matches"][0]["link"] == search_url
    assert not scored and not db.rows


def test_wechat_search_recovery_reuses_successful_terms(integration):
    main, accounts, provider, db, scored, cards, commands = integration
    term = "机关事务管理"
    provider.search_rows[term] = RuntimeError("search provider unavailable")
    assert main.main(DAY, keyword=TOPIC) is False
    commands.clear()
    provider.search_rows[term] = [news("补采的搜索结果", "https://example.test/restored")]
    assert main.main(DAY, keyword=TOPIC) is True
    assert [args[0] for script, args, _ in commands if script == "fetch_and_filter.py"] == [term]
    assert [row["title"] for row in db.rows] == ["补采的搜索结果"]


@pytest.mark.parametrize("keyword", ["养老", "烟草服务银行", "未来关键词"])
def test_wechat_opt_in_preserves_shared_date_and_scoring_for_other_keywords(integration, keyword):
    main, accounts, provider, db, scored, cards, commands = integration
    link = "https://example.test/article"
    accepted = news("采集日活动", link, keyword)
    old = news("旧文章", link + "/old", keyword)
    old["publication_check"] = {**assess_publication_evidence(
        [{"source": "fixture", "date": "2026-11-12"}], DAY), "link": old["link"]}
    path = Path("output", DAY, f"{DAY}_{keyword}.json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps([accepted, old]))
    assert main.main(DAY, keyword=keyword, adopt_existing_config=True) is True
    assert [row["title"] for row in db.rows] == ["采集日活动"]
    assert scored == ["采集日活动"]
    assert not provider.calls and not cards


def test_wechat_review_postlude_can_acquire_the_same_day_lock(integration):
    import subprocess
    from date_review import job_path

    main, accounts, provider, db, scored, cards, commands = integration
    script = Path(__file__).with_name("date_review.py")

    def review():
        result = subprocess.run([sys.executable, "-B", str(script), "--batch-date", DAY,
                                 "--keyword", TOPIC, "--today", "2026-11-14"],
                                capture_output=True, text=True, timeout=8)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "[DATE_REVIEW]" in result.stdout
        directory = job_path(DAY, TOPIC).parent
        directory.mkdir(exist_ok=True)
        directory.joinpath("postlude_completed").touch()
        return True

    provider.review[0] = review
    assert main.main(DAY, keyword=TOPIC)
    assert job_path(DAY, TOPIC).parent.joinpath("postlude_completed").exists()


def test_wechat_merge_recovers_after_daily_diagnostic_write_was_interrupted(integration, monkeypatch):
    import government_affairs_pipeline as pipeline

    main, accounts, provider, db, scored, cards, commands = integration
    a = accounts[0]
    provider.pages[a["ghid"], ""] = [page(article(301, biz=a["biz"]), ghid=a["ghid"])]
    atomic_json = pipeline.atomic_json

    def interrupt(path, data):
        if Path(path).name == "government_affairs_fetch.json":
            raise OSError("interrupted after merged row write")
        atomic_json(path, data)

    monkeypatch.setattr(pipeline, "atomic_json", interrupt)
    assert main.main(DAY, keyword=TOPIC) is False
    assert not db.rows
    monkeypatch.setattr(pipeline, "atomic_json", atomic_json)
    commands.clear()
    assert main.main(DAY, keyword=TOPIC) is True
    assert scored == ["文章301-1"]
    assert not any(script == "fetch_and_filter.py" for script, *_ in commands)


def test_wechat_pipeline_does_not_rescore_or_write_on_invalid_source_fingerprint(integration):
    main, accounts, provider, db, scored, cards, commands = integration
    a = accounts[0]
    provider.pages[a["ghid"], ""] = [page(article(301, biz=a["biz"]), ghid=a["ghid"])]
    assert main.main(DAY, keyword=TOPIC) is True
    raw = Path("output", DAY, f"{DAY}_{TOPIC}.json")
    rows = read(raw)
    rows[0]["title"] = "未经验证的改动"
    raw.write_text(json.dumps(rows))
    commands.clear()
    assert main.main(DAY, keyword=TOPIC) is False
    assert not any(script in {"fetch_and_filter.py", "news_scorer.py"} for script, *_ in commands)
    assert len(db.rows) == 1
    assert ledger()["outcome"]["database"]["inserted"] is None


def test_wechat_main_explicit_default_is_previous_beijing_day(integration, monkeypatch):
    main, accounts, provider, db, scored, cards, commands = integration
    monkeypatch.setattr(main, "reference_time", lambda: datetime.fromisoformat("2026-11-14T00:30:00+08:00"))
    assert main.main(keyword=TOPIC) is True
    assert ledger()["date"] == DAY


def receipts_for(run):
    return Path("output/wechat_runs", DAY, "stages", run["run_id"])


def stored_null_score(title, *, day=DAY, content=BODY):
    return dict(id=1, title=title, link="https://example.test/original-search", fetchdate=day,
                keyword=TOPIC, content=content, score=None, short_summary=None)


def test_wechat_recovers_null_score_on_the_matching_duplicate_and_converges(integration):
    main, accounts, provider, db, scored, cards, commands = integration
    a = accounts[0]
    title = "完全相同的跨渠道报道"
    db.rows.append(stored_null_score(title))
    provider.pages[a["ghid"], ""] = [page(article(401, biz=a["biz"], title=title), ghid=a["ghid"])]
    assert main.main(DAY, keyword=TOPIC) is True
    receipts = receipts_for(ledger())
    assert read(receipts / "database.json")["counts"]["duplicate"] == 1
    assert read(receipts / "database.json")["counts"]["inserted"] == 0
    assert read(receipts / "summary.json")["counts"]["succeeded"] == 1
    assert db.rows[0]["score"] == 4
    assert db.rows[0]["short_summary"] == BODY

    assert main.main(DAY, keyword=TOPIC) is True
    second = ledger()
    assert second["status"] == "success"
    assert read(receipts_for(second) / "summary.json")["counts"]["already_done"] == 1
    assert scored == [title]


@pytest.mark.parametrize("day,content,summary_status,summary_count,success",
                         [(DAY, BODY + "这是不同的正文。", "failed", "pending", False),
                          ("2026-11-12", BODY, "success", "not_needed", True)])
def test_wechat_does_not_transfer_score_to_different_or_historical_stored_rows(
        integration, day, content, summary_status, summary_count, success):
    main, accounts, provider, db, scored, cards, commands = integration
    a = accounts[0]
    title = "同标题但应保留存档正文"
    db.rows.append(stored_null_score(title, day=day, content=content))
    provider.pages[a["ghid"], ""] = [page(article(402, biz=a["biz"], title=title), ghid=a["ghid"])]
    assert main.main(DAY, keyword=TOPIC) is success
    run = ledger()
    receipt = read(receipts_for(run) / "summary.json")
    assert receipt["status"] == summary_status
    assert receipt["counts"][summary_count] == 1
    assert run["status"] == ("success" if success else "partial")
    assert db.rows[0]["score"] is None
    assert db.rows[0]["short_summary"] is None


@pytest.mark.parametrize("summary_complete", [True, False])
def test_wechat_mixed_keyword_exception_preserves_its_own_summary_outcome(integration, monkeypatch,
                                                                        summary_complete):
    import news_item_summarizer

    main, accounts, provider, db, scored, cards, commands = integration
    a = accounts[0]
    title = "已有官微摘要" if summary_complete else "正文不同的官微候选"
    existing = stored_null_score(title, content=BODY if summary_complete else BODY + "不同的正文。")
    if summary_complete:
        existing.update(score=4, short_summary="现有摘要")
    db.rows.extend([existing, dict(id=2, title="养老专题文章", link="https://example.test/other",
                                   fetchdate=DAY, keyword="养老", content="这是养老项目的完整报道。" * 100,
                                   score=4, short_summary=None)])
    provider.pages[a["ghid"], ""] = [page(article(403, biz=a["biz"], title=title), ghid=a["ghid"])]

    def broken(*_):
        raise ValueError("unrelated row error")

    monkeypatch.setattr(news_item_summarizer, "call_llm", broken)
    assert main.main(DAY) is False
    run = ledger()
    report = read(receipts_for(run) / "summary.json")
    assert report["counts"] == {
        "succeeded": 0, "failed": 0, "already_done": int(summary_complete),
        "not_needed": 0, "pending": int(not summary_complete),
    }
    assert report["status"] == ("success" if summary_complete else "failed")
    assert run["status"] == ("success" if summary_complete else "partial")
