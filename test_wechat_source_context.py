"""Verified publisher geography reaches model requests without rewriting articles."""
import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

import news_item_summarizer as summary
import news_scorer as scorer
from config_store import read_document
from topic_config import TOPIC, build_topic_candidate
from wechat_collector import load_registry


def article(region="南京", **changes):
    account = next(a for a in load_registry()["accounts"] if a["region"] == region)
    row = dict(title="市局开展节能工作", content="市局组织开展公共机构节能工作。" * 50,
               keyword=TOPIC, main_keyword=TOPIC, search_keyword=account["name"],
               source=account["name"], sourceapi="极致了api", wechat_ghid=account["ghid"],
               link="https://mp.weixin.qq.com/s?" + urlencode({"__biz": account["biz"], "mid": "100", "idx": "1"}),
               wechat_article_id=account["biz"] + "/100/1", fetchdate="2026-11-13")
    row["publication_check"] = dict(version=1, target_date="2026-11-13", link=row["link"],
                                    status="accepted", published_date="2026-11-13",
                                    evidence=[{"source": "fixture", "date": "2026-11-13"}])
    row.update(changes)
    return row


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch, tmp_path):
    document = build_topic_candidate(read_document(Path(__file__).with_name("config_defaults.json")))["document"]
    monkeypatch.setattr("runtime_config.get_snapshot", lambda: SimpleNamespace(document=document, token="test:1"))
    monkeypatch.setenv("RUN_LOG_PATH", str(tmp_path / "run.log"))
    monkeypatch.setattr(summary.time, "sleep", lambda *_: None)


def model_transport(monkeypatch, answer="4"):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=answer))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client.with_options = lambda **_: client
    monkeypatch.setattr(scorer._scoring_client_pool, "get_client", lambda *a, **k: client)
    monkeypatch.setattr(summary._pool, "get_client", lambda *a, **k: client)
    return calls


def source_context(request):
    user = request["messages"][1]["content"]
    prefix = "官微来源上下文："
    assert prefix in user, "verified publisher geography is missing from the model request"
    return json.loads(user.split(prefix, 1)[1].split("\n", 1)[0])


@pytest.mark.parametrize("region,expected", [
    ("南京", {"发布账号": "勤惟宁", "账号辖区": "江苏省南京市", "账号层级": "市级"}),
    ("安徽", {"发布账号": "皖美管家", "账号辖区": "安徽省", "账号层级": "省级"}),
    ("上海", {"发布账号": "上海机关事务", "账号辖区": "上海市", "账号层级": "省级（直辖市）"}),
    ("内蒙古", {"发布账号": "内蒙古自治区机关事务管理局", "账号辖区": "内蒙古自治区", "账号层级": "省级（自治区）"}),
    ("宁夏", {"发布账号": "宁夏机关事务", "账号辖区": "宁夏回族自治区", "账号层级": "省级（自治区）"}),
    ("国管局", {"发布账号": "国管局", "账号辖区": "中央机关", "账号层级": "国家级"}),
])
def test_scoring_request_has_verified_publisher_geography(monkeypatch, region, expected):
    calls = model_transport(monkeypatch)
    row = article(region)
    before = copy.deepcopy(row)
    result = scorer._score_row(row, TOPIC)
    assert result["score"] == 4
    assert source_context(calls[0]) == expected
    assert row == before
    assert all(result[key] == row[key] for key in ("title", "content", "keyword", "search_keyword"))
    assert row["content"] in calls[0]["messages"][1]["content"]


def test_all_enabled_accounts_get_context_and_disabled_account_does_not(monkeypatch):
    calls = model_transport(monkeypatch)
    for account in load_registry()["accounts"]:
        scorer._score_row(article(account["region"]), TOPIC)
        if account["enabled"]:
            context = source_context(calls[-1])
            assert context["发布账号"] == account["name"]
            assert context["账号辖区"] and context["账号层级"]
        else:
            assert "官微来源上下文：" not in calls[-1]["messages"][1]["content"]


@pytest.mark.parametrize("changes", [
    {"sourceapi": "serp_bingnews"},
    {"link": "https://example.test/s?__biz=MzkxOTYwMDcyMg==&mid=100&idx=1"},
    {"link": "https://mp.weixin.qq.com.evil.test/s?__biz=MzkxOTYwMDcyMg==&mid=100&idx=1"},
    {"link": "https://mp.weixin.qq.com/s/short-link"},
    {"link": "https://mp.weixin.qq.com/s?__biz=unknown&mid=100&idx=1"},
    {"link": "https://mp.weixin.qq.com/s?__biz=MzkxOTYwMDcyMg==&__biz=MzkxOTYwMDcyMg==&mid=100&idx=1"},
    {"wechat_ghid": "gh_other"},
    {"wechat_ghid": ""},
    {"wechat_ghid": None},
    {"wechat_article_id": "MzkxOTYwMDcyMg==/101/1"},
    {"wechat_article_id": ""},
    {"keyword": "未来关键词"},
    {"main_keyword": "未来关键词"},
])
def test_unverified_identity_cannot_gain_geography_from_nickname(monkeypatch, changes):
    calls = model_transport(monkeypatch)
    row = article(**changes)
    scorer._score_row(row, TOPIC)
    assert "官微来源上下文：" not in calls[0]["messages"][1]["content"]


def test_other_main_keyword_keeps_original_scoring_messages(monkeypatch):
    calls = model_transport(monkeypatch)
    row = article()
    scorer._score_row(row, "未来关键词")
    with_identity = calls[-1]["messages"]
    scorer._score_row({k: v for k, v in row.items() if k not in {"source", "sourceapi", "link", "wechat_ghid", "wechat_article_id"}}, "未来关键词")
    assert calls[-1]["messages"] == with_identity


def test_source_context_does_not_treat_reprint_publisher_as_event_location(monkeypatch):
    calls = model_transport(monkeypatch)
    row = article(content="转载：上海市机关事务部门在上海开展调研。")
    scorer._score_row(row, TOPIC)
    assert source_context(calls[0])["账号辖区"] == "江苏省南京市"
    system = calls[0]["messages"][0]["content"]
    assert "正文明确地点优先" in system
    assert "来源辖区不等于事件发生地" in system
    assert "不得猜测" in system
    assert "已核验来源上下文也属于提供的资料" in system
    assert row["content"] in calls[0]["messages"][1]["content"]


def test_incremental_cache_rescores_legacy_and_changed_identity_only(monkeypatch, tmp_path):
    calls = model_transport(monkeypatch)
    row = article(wechat_content_status="ok")
    path = tmp_path / "articles.json"
    saved = tmp_path / "articles_scored.json"
    path.write_text(json.dumps([row]))
    saved.write_text(json.dumps([{**row, "score": 3, "score_status": "ok"}]))
    monkeypatch.setattr("wechat_pipeline_run.write_stage", lambda *a, **k: None)
    run = {"date": "2026-11-13"}
    assert scorer.incremental_score_news(path, TOPIC, run)
    assert len(calls) == 1, "legacy scores without publisher context must be refreshed"
    assert scorer.incremental_score_news(path, TOPIC, run)
    assert len(calls) == 1, "unchanged publisher context must reuse its successful score"
    row["wechat_ghid"] = "gh_conflict"
    path.write_text(json.dumps([row]))
    assert scorer.incremental_score_news(path, TOPIC, run)
    assert len(calls) == 2, "removing trusted context must invalidate a contextual score"
    assert "官微来源上下文：" not in calls[-1]["messages"][1]["content"]


@pytest.mark.parametrize("topic,changes", [(TOPIC, {"sourceapi": "serp_bingnews"}), ("未来关键词", {})])
def test_unrelated_incremental_caches_keep_existing_behavior(monkeypatch, tmp_path, topic, changes):
    calls = model_transport(monkeypatch)
    row = article(keyword=topic, main_keyword=topic, wechat_content_status="ok", **changes)
    path = tmp_path / "articles.json"
    path.write_text(json.dumps([row]))
    path.with_name("articles_scored.json").write_text(json.dumps([{**row, "score": 3, "score_status": "ok"}]))
    monkeypatch.setattr("wechat_pipeline_run.write_stage", lambda *a, **k: None)
    assert scorer.incremental_score_news(path, topic, {"date": "2026-11-13"})
    assert calls == []


def scoring_cli_files(monkeypatch, tmp_path, mode, rows, topic=TOPIC, *, cached=True):
    monkeypatch.chdir(tmp_path)
    day = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d") if mode == "batch" else "2026-11-13"
    folder = tmp_path / "output" / day
    folder.mkdir(parents=True)
    for row in rows:
        row["fetchdate"] = day
        row["publication_check"].update(target_date=day, published_date=day, evidence=[{"source": "fixture", "date": day}])
    raw = folder / f"{day}_{topic}.json"
    raw.write_text(json.dumps(rows))
    saved = raw.with_name(raw.stem + "_scored.json")
    if cached:
        saved.write_text(json.dumps(rows))
    monkeypatch.setattr(scorer, "prepare_batch", lambda *a, **k: (None, [topic]))
    args = [] if mode == "batch" else [topic, day]
    if mode == "rescore":
        args.append("--rescore")
    monkeypatch.setattr(sys, "argv", ["news_scorer.py", *args])
    return saved


@pytest.mark.parametrize("mode", ["single", "batch", "rescore"])
@pytest.mark.parametrize("marker", [None, "obsolete-source-context"])
def test_scoring_cli_refreshes_only_stale_wechat_context(monkeypatch, tmp_path, mode, marker):
    calls = model_transport(monkeypatch, "5")
    stale = article(score=4, score_status="ok")
    if marker is not None:
        stale["score_source_context"] = marker
    ordinary = article("安徽", sourceapi="serp_bingnews", score=0, score_status="ok")
    saved = scoring_cli_files(monkeypatch, tmp_path, mode, [stale, ordinary])
    assert scorer.main()
    assert len(calls) == 1, "every scoring entry must refresh only the stale contextual score"
    rows = {row["source"]: row for row in json.loads(saved.read_text())}
    assert rows["勤惟宁"]["score"] == 5
    assert rows["勤惟宁"]["score_source_context"] not in (None, marker)
    assert rows["皖美管家"] == ordinary
    assert scorer.scored_file_complete(saved, TOPIC)
    assert scorer.main()
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["single", "batch", "rescore"])
@pytest.mark.parametrize("topic", [TOPIC, "未来关键词"])
def test_scoring_cli_keeps_unrelated_successful_cache(monkeypatch, tmp_path, mode, topic):
    calls = model_transport(monkeypatch)
    row = article(keyword=topic, main_keyword=topic, sourceapi="serp_bingnews" if topic == TOPIC else "极致了api",
                  score=0, score_status="ok")
    saved = scoring_cli_files(monkeypatch, tmp_path, mode, [row], topic)
    assert scorer.main()
    assert calls == []
    assert json.loads(saved.read_text()) == [row]


@pytest.mark.parametrize("mode", ["single", "batch"])
def test_fresh_scoring_cli_stamps_context_for_later_reuse(monkeypatch, tmp_path, mode):
    calls = model_transport(monkeypatch)
    saved = scoring_cli_files(monkeypatch, tmp_path, mode, [article()], cached=False)
    assert scorer.main()
    assert len(calls) == 1
    assert json.loads(saved.read_text())[0]["score_source_context"]
    assert scorer.scored_file_complete(saved, TOPIC)
    assert scorer.main()
    assert len(calls) == 1


def test_scored_file_rejects_context_that_has_not_reached_model(monkeypatch, tmp_path):
    calls = model_transport(monkeypatch)
    path = tmp_path / "scored.json"
    row = article(score=4, score_status="ok")
    path.write_text(json.dumps([row]))
    assert not scorer.scored_file_complete(path, TOPIC)
    scored = scorer._score_row(row, TOPIC)
    path.write_text(json.dumps([scored]))
    assert scorer.scored_file_complete(path, TOPIC)
    scored["score_source_context"] = "obsolete-source-context"
    path.write_text(json.dumps([scored]))
    assert not scorer.scored_file_complete(path, TOPIC)
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["single", "batch", "rescore"])
def test_scoring_cli_clears_marker_when_publisher_identity_becomes_untrusted(monkeypatch, tmp_path, mode):
    calls = model_transport(monkeypatch)
    contextual = scorer._score_row(article(), TOPIC)
    contextual["wechat_ghid"] = "gh_conflict"
    saved = scoring_cli_files(monkeypatch, tmp_path, mode, [contextual])
    assert scorer.main()
    assert len(calls) == 2
    updated = json.loads(saved.read_text())[0]
    assert "score_source_context" not in updated
    assert "官微来源上下文：" not in calls[-1]["messages"][1]["content"]
    assert scorer.main()
    assert len(calls) == 2
    updated["wechat_ghid"] = "gh_b658f8c01c4a"
    assert not scorer._cached_score(updated, updated, TOPIC), "restored identity must not revive its old contextual score"


def test_reused_plain_score_does_not_inherit_a_marker_from_input_row(monkeypatch):
    model_transport(monkeypatch)
    row = scorer._score_row(article(), TOPIC)
    row["wechat_ghid"] = "gh_conflict"
    cached = {key: value for key, value in row.items() if key != "score_source_context"}
    assert scorer._cached_score(cached, row, TOPIC)
    reused = scorer._reuse_score(cached, row)
    assert "score_source_context" not in reused
    reused["wechat_ghid"] = "gh_b658f8c01c4a"
    assert not scorer._cached_score(reused, reused, TOPIC)


@pytest.fixture
def stored_database(monkeypatch):
    import sqlite3
    from test_pr27_comprehensive import SQLiteConnection

    db = sqlite3.connect(":memory:", isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE scored_news_test (
        id INTEGER PRIMARY KEY, title TEXT, content TEXT, keyword TEXT, source TEXT,
        link TEXT, sourceapi TEXT, search_keyword TEXT, score INTEGER, fetchdate TEXT, short_summary TEXT)""")
    monkeypatch.setattr(summary, "get_conn", lambda: SQLiteConnection(db, [], []))
    monkeypatch.setattr(summary, "get_table_name", lambda: "scored_news_test")
    monkeypatch.setattr(summary, "prepare_batch", lambda *a, **k: (None, [TOPIC, "未来关键词"]))
    monkeypatch.setattr(summary, "table_has_region_column", lambda *a: False)
    monkeypatch.setattr(summary, "table_has_business_types_column", lambda *a: False)
    monkeypatch.setattr(summary, "stage_run", lambda *_: None)
    monkeypatch.setattr(sys, "argv", ["news_item_summarizer.py", "2026-11-13"])
    yield db
    db.close()


def store_row(db, row, existing_summary=None):
    db.execute("""INSERT INTO scored_news_test
        (title,content,keyword,source,link,sourceapi,search_keyword,score,fetchdate,short_summary)
        VALUES (?,?,?,?,?,?,?,?,?,?)""", (*[row[key] for key in
        ("title", "content", "keyword", "source", "link", "sourceapi", "search_keyword")],
        4, "2026-11-13", existing_summary))


def test_summary_uses_actual_stored_publisher_and_keeps_short_and_historical_rows(monkeypatch, stored_database):
    calls = model_transport(monkeypatch, "市局组织开展公共机构节能工作。")
    # The retained row's biz is authoritative even if a candidate nickname differs.
    row = article("安徽", source="勤惟宁", search_keyword="勤惟宁")
    store_row(stored_database, row)
    store_row(stored_database, article(content="文" * 500, title="短文"))
    store_row(stored_database, article(title="历史"), "已存在的历史摘要")
    before = [tuple(r) for r in stored_database.execute("SELECT title,content,keyword,source,link,sourceapi,search_keyword FROM scored_news_test")]
    assert summary.main()
    assert len(calls) == 1
    assert source_context(calls[0]) == {"发布账号": "皖美管家", "账号辖区": "安徽省", "账号层级": "省级"}
    assert "正文明确地点优先" in calls[0]["messages"][0]["content"]
    assert "来源辖区不等于事件发生地" in calls[0]["messages"][0]["content"]
    assert "不得猜测" in calls[0]["messages"][0]["content"]
    assert row["content"] in calls[0]["messages"][1]["content"]
    after = [tuple(r) for r in stored_database.execute("SELECT title,content,keyword,source,link,sourceapi,search_keyword FROM scored_news_test")]
    assert before == after
    assert dict(stored_database.execute("SELECT title,short_summary FROM scored_news_test"))["短文"] == "文" * 500
    assert dict(stored_database.execute("SELECT title,short_summary FROM scored_news_test"))["历史"] == "已存在的历史摘要"


def test_other_keywords_and_nonwechat_summary_messages_are_unchanged(monkeypatch, stored_database):
    calls = model_transport(monkeypatch, "忠实摘要")
    first = article(sourceapi="serp_bingnews")
    second = article(keyword="未来关键词", main_keyword="未来关键词")
    store_row(stored_database, first)
    store_row(stored_database, second)
    assert summary.main()
    summary.call_llm(first["title"], first["content"])
    assert len(calls) == 3
    assert calls[0]["messages"] == calls[1]["messages"] == calls[2]["messages"]
