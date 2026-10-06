"""Strict topic scoring and failed-only recovery; external SDK/DB are substituted."""
import copy
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import news_scorer as scorer
from config_store import read_document
from topic_config import build_topic_candidate, TOPIC

def dated(row, day="2099-01-01"):
    row["fetchdate"] = day
    row["publication_check"] = {"version":1,"target_date":day,"link":row["link"],
        "status":"accepted","published_date":day,"evidence":[{"source":"meta:pubdate","date":day}]}
    return row

@pytest.fixture(autouse=True)
def topic_runtime(monkeypatch, tmp_path):
    doc = build_topic_candidate(read_document(Path(__file__).with_name("config_defaults.json")))["document"]
    doc["settings"]["NEWS_RULE_BASED_SCORING"].append(
        {"main_keyword": TOPIC, "title_contains": "通知", "score": 5})
    monkeypatch.setattr("runtime_config.get_snapshot", lambda: SimpleNamespace(document=doc, token="test:1"))
    monkeypatch.setenv("RUN_LOG_PATH", str(tmp_path / "run.log"))
    monkeypatch.setattr(scorer.time, "sleep", lambda _: None)
    return doc

def fake_client(monkeypatch, answers):
    calls = []
    remaining = iter(answers)
    def create(**kwargs):
        assert client.options_used, "SDK retries must be disabled for bounded attempts"
        calls.append(kwargs)
        answer = next(remaining)
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=answer))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    def with_options(**kwargs):
        assert kwargs == {"max_retries": 0}
        client.options_used = True
        return client
    client.with_options = with_options
    client.options_used = False
    monkeypatch.setattr(scorer._scoring_client_pool, "get_client", lambda *a, **k: client)
    return calls

@pytest.mark.parametrize("raw,want", [("0",0),("5",5),(" 3\n",3)])
def test_parse_valid(raw, want):
    assert hasattr(scorer, "parse_government_affairs_score"), "missing strict full-response parser"
    assert scorer.parse_government_affairs_score(raw) == want

@pytest.mark.parametrize("raw", ["5分", "10", "6", "-1", "3.0", "５", "", None, '{"score":3}'])
def test_parse_invalid(raw):
    assert hasattr(scorer, "parse_government_affairs_score"), "missing strict full-response parser"
    with pytest.raises(ValueError):
        scorer.parse_government_affairs_score(raw)

def test_failure_not_zero_and_retry_is_bounded(monkeypatch):
    calls = fake_client(monkeypatch, ["5分", "10", "评分为5"])
    assert scorer.score_news("通知", "居民积分活动", "碳普惠", TOPIC) is None
    assert len(calls) == 3

def test_success_after_retry_and_dedicated_prompt(monkeypatch):
    calls = fake_client(monkeypatch, [TimeoutError(), "3"])
    assert hasattr(scorer, "score_news_result"), "missing structured scoring result"
    result = scorer.score_news_result("调剂", "机关车辆调剂", "车辆", TOPIC)
    assert (result.score, result.status, result.attempts) == (3, "ok", 2)
    assert calls[-1]["messages"][0]["role"] == "system"
    assert "全国采集" in calls[-1]["messages"][0]["content"]

def test_batch_ignores_title_rules_and_bad_wordcount(monkeypatch, tmp_path):
    calls = fake_client(monkeypatch, ["1"])
    path = tmp_path / "news.json"
    path.write_text(json.dumps([dated(row) for row in [
        {"title":"通知", "content":"居民积分活动", "wordcount":0, "link":"https://a.test/1"},
        {"title":"政策", "content":"  ", "wordcount":123, "link":"https://a.test/2"}]]))
    results, counts, _, rules = scorer.batch_score_news(path, TOPIC)
    assert [n["score"] for n in results] == [1, 0]
    assert [n["score_status"] for n in results] == ["ok", "empty_content"]
    assert rules == [] and len(calls) == 1

def test_failed_json_sorts_last_and_is_not_complete(monkeypatch, tmp_path):
    fake_client(monkeypatch, ["bad"] * 3)
    path = tmp_path / "news.json"
    path.write_text(json.dumps([dated(row) for row in [{"title":"x","content":"正文","link":"https://a.test"}]]))
    results, counts, _, _ = scorer.batch_score_news(path, TOPIC)
    assert results[0]["score"] is None
    assert sum(counts.values()) == 0
    results.append({"score": 0, "score_status":"ok"})
    saved = scorer.write_scored_json(results, str(path))
    assert json.loads(Path(saved).read_text())[0]["score"] == 0
    assert hasattr(scorer, "scored_file_complete")
    assert scorer.scored_file_complete(saved, TOPIC) is False

def test_main_pipeline_does_not_skip_partial_score_file(tmp_path, monkeypatch):
    import main
    monkeypatch.chdir(tmp_path)
    folder=Path("output/2099-01-01")
    folder.mkdir(parents=True)
    (folder / f"2099-01-01_{TOPIC}.json").write_text("[]")
    (folder / f"2099-01-01_{TOPIC}_scored.json").write_text('[{"score":null,"score_status":"failed"}]')
    assert main.execute_scoring("2099-01-01", TOPIC) is False

def test_rescore_updates_only_failed_and_keeps_valid_zero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = Path("output/2099-01-01"); folder.mkdir(parents=True)
    path = folder / f"2099-01-01_{TOPIC}_scored.json"
    path.write_text(json.dumps([dated(row) for row in [
        {"title":"failed", "content":"有效正文", "link":"a", "score":None, "score_status":"failed"},
        {"title":"zero", "content":"无关正文", "link":"b", "score":0, "score_status":"ok"}]]))
    calls = fake_client(monkeypatch, ["0"])
    monkeypatch.setattr(scorer, "prepare_batch", lambda *a, **k: (None,[TOPIC]))
    monkeypatch.setattr(sys, "argv", ["news_scorer.py", TOPIC, "2099-01-01", "--rescore"])
    assert scorer.main() is True
    rows=json.loads(path.read_text())
    assert len(calls)==1 and all(n["score"]==0 and n["score_status"]=="ok" for n in rows)

def test_missing_dedicated_mapping_uses_platform_default(topic_runtime, monkeypatch):
    topic_runtime["keyword_prompt_ids"].pop(TOPIC)
    calls = fake_client(monkeypatch, ["5"])
    from config_schema import ConfigError
    assert scorer.get_system_message(TOPIC, "碳普惠") == topic_runtime["prompts"]["NEWS_SCORE_SYSTEM_MSG"]["text"]
    assert calls == []

def test_db_import_path_updates_null_to_valid_zero(tmp_path, monkeypatch):
    conn=Mock(); conn.cursor.return_value.fetchall.return_value=[("x","https://a.test")]
    conn.cursor.return_value.rowcount = 1
    with patch("db_utils.get_connection",return_value=conn):
        sys.modules.pop("write_to_mysql",None)
        db=importlib.import_module("write_to_mysql")
    monkeypatch.setattr(db,"write_log",lambda _:None)
    path=tmp_path/"rows.json"
    path.write_text(json.dumps([dated(row) for row in [{"keyword":TOPIC,"title":"x","link":"https://a.test",
        "content":"正文","score":0,"score_status":"ok"}]]))
    db.import_scored_news_with_retry(str(path),TOPIC)
    updates=[c for c in conn.cursor.return_value.execute.call_args_list if "UPDATE" in c.args[0]]
    assert len(updates)==1
    assert "score IS NULL" in updates[0].args[0] and "link=%s" in updates[0].args[0]
    assert updates[0].args[1] == (0,"x",TOPIC,"https://a.test")

@pytest.mark.parametrize("score", [0, 5])
def test_rescore_migrates_verified_legacy_score_without_model(tmp_path, monkeypatch, score):
    import main
    monkeypatch.chdir(tmp_path)
    folder = Path("output/2099-01-01")
    folder.mkdir(parents=True)
    raw = folder / "2099-01-01_碳普惠.json"
    saved = folder / "2099-01-01_碳普惠_scored.json"
    legacy = {"title":"政策", "content":"原有正文", "link":"https://a.test/1", "score":score}
    raw.write_text(json.dumps([dated({k:v for k,v in legacy.items() if k != "score"})]))
    saved.write_text(json.dumps([legacy]))
    calls = fake_client(monkeypatch, [])
    monkeypatch.setattr(scorer, "prepare_batch", lambda *a, **k: (None,["碳普惠"]))
    monkeypatch.setattr(sys, "argv", ["news_scorer.py", "碳普惠", "2099-01-01", "--rescore"])
    assert scorer.main() is True
    row = json.loads(saved.read_text())[0]
    assert row["score"] == score and row["score_status"] == "ok"
    assert row["publication_check"]["published_date"] == "2099-01-01"
    assert "scoring_policy_version" not in row
    assert calls == []
    assert main.execute_scoring("2099-01-01", "碳普惠") is True

@pytest.mark.parametrize("change", ["link", "title", "content", "date", "pending", "failed", "invalid"])
def test_rescore_does_not_certify_unverified_legacy_score(tmp_path, monkeypatch, change):
    monkeypatch.chdir(tmp_path)
    folder = Path("output/2099-01-01")
    folder.mkdir(parents=True)
    legacy = {"title":"政策", "content":"原有正文", "link":"https://a.test/1", "score":3}
    verified = dated({k:v for k,v in legacy.items() if k != "score"})
    if change in {"link", "title", "content"}:
        verified[change] += "changed"
    elif change == "date":
        dated(verified, "2099-01-02")
    elif change == "pending":
        verified["publication_check"]["status"] = "pending"
    elif change == "failed":
        legacy["score_status"] = "failed"
    else:
        legacy["score"] = 6
    (folder / "2099-01-01_碳普惠.json").write_text(json.dumps([verified]))
    saved = folder / "2099-01-01_碳普惠_scored.json"
    saved.write_text(json.dumps([legacy]))
    calls = fake_client(monkeypatch, [])
    monkeypatch.setattr(scorer, "prepare_batch", lambda *a, **k: (None,["碳普惠"]))
    monkeypatch.setattr(sys, "argv", ["news_scorer.py", "碳普惠", "2099-01-01", "--rescore"])
    assert scorer.main() is False
    assert json.loads(saved.read_text()) == [legacy]
    assert calls == []


def test_context_limit_is_explicit_terminal_without_fake_score(tmp_path, monkeypatch):
    calls = fake_client(monkeypatch, [RuntimeError("maximum context length exceeded")])
    result = scorer.score_news_result("新闻", "超长正文", "住房", "公积金")
    assert result.status == "skipped_context_limit"
    assert result.score is None and result.error_code == "context_limit"
    assert len(calls) == 1
    path = tmp_path / "scored.json"
    path.write_text(json.dumps([dated({"link":"a", **scorer.score_fields(result)})]))
    assert scorer.scored_file_complete(path, "公积金")
    path.write_text(json.dumps([dated({"link":"a", "score":None, "score_status":"failed", "score_error":"quota"})]))
    assert not scorer.scored_file_complete(path, "公积金")


def test_review_does_not_repeat_terminal_context_failure(tmp_path, monkeypatch):
    path = tmp_path / "raw.json"
    item = dated({"link":"a", "title":"标题", "content":"超长正文"})
    path.write_text(json.dumps([item]))
    saved = tmp_path / "raw_scored.json"
    saved.write_text(json.dumps([{**item, "score":None, "score_status":"skipped_context_limit", "score_error":"context_limit"}]))
    calls = fake_client(monkeypatch, [])
    result = scorer.refresh_review_scores(path, "公积金", {"a"})
    assert result[0]["score_status"] == "skipped_context_limit"
    assert calls == []
