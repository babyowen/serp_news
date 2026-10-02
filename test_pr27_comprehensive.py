"""Twenty offline scenarios spanning PR 27's real module and storage boundaries."""
import copy
import importlib.util
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

import config_cli
import evaluate_government_affairs as evaluator
import fetch_and_filter as filtering
import government_affairs_pipeline as pipeline
import news_fetcher as fetcher
import news_item_summarizer as summarizer
import news_scorer as scorer
import runtime_config
import topic_evaluation as evaluation
from config_schema import ConfigConflict, ConfigError
from config_store import ConfigStore, read_document
from routes import views
from topic_config import TOPIC, PROMPT_ID, build_topic_candidate

ROOT = Path(__file__).resolve().parent
DATE = "2026-07-12"
ENGINES = ("google", "baidu", "bing", "duckduckgo")


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    monkeypatch.setenv("RUN_LOG_PATH", str(tmp_path / "run.log"))
    document = build_topic_candidate(read_document(ROOT / "config_defaults.json"))["document"]
    snapshot = SimpleNamespace(document=document, token="comprehensive:1")
    monkeypatch.setattr(runtime_config, "get_snapshot", lambda: snapshot)
    monkeypatch.setattr(pipeline, "get_snapshot", lambda: snapshot)
    monkeypatch.setattr(fetcher.time, "sleep", lambda _: None)
    return snapshot


def empty_response(name):
    kind = "organic" if name in ("baidu", "bing") else "news"
    return {"search_metadata": {"status": "Success"},
            "search_information": {f"{kind}_results_state": "Fully empty"},
            "error": f"{name} hasn't returned any results for this query."}


def news(title="资产调剂", link="https://example.test/a", date=DATE, **extra):
    return {"title": title, "link": link, "date": date, "source": "测试来源", **extra}


def results_response(name, rows):
    key = "organic_results" if name in ("baidu", "bing") else "news_results"
    return {"search_metadata": {"status": "Success"}, key: rows}


def install_http(monkeypatch, handler):
    calls, attempts = [], Counter()

    def get(url, *, params, timeout):
        assert url == "https://serpapi.com/search.json" and timeout == 15
        name, term, start = params["engine"].removesuffix("_news"), params["q"], params.get("start", 0)
        key = (name, term, start)
        attempts[key] += 1
        calls.append(key)
        payload = handler(name, term, start, attempts[key])
        if isinstance(payload, Exception):
            raise payload
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: copy.deepcopy(payload))

    monkeypatch.setattr(fetcher.requests, "get", get)
    return calls


def collect(monkeypatch, terms):
    def search(term, output):
        monkeypatch.setattr(sys, "argv", ["fetch_and_filter.py", term, DATE,
                                        "--main_keyword", TOPIC, "--output", output])
        return filtering.main()
    return pipeline.collect(DATE, terms, search)


def collection():
    folder = Path("output") / DATE
    path = folder / f"{DATE}_{TOPIC}.json"
    return path, json.loads(path.read_text()), json.loads(
        (folder / "diagnostics/government_affairs_fetch.json").read_text())


def install_model(monkeypatch, answers, pool=None):
    queue, calls, options = iter(answers), [], []

    def create(**kwargs):
        calls.append(kwargs)
        answer = next(queue)
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=answer))],
                               usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2, total_tokens=12))

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    def with_options(**kwargs):
        options.append(kwargs)
        return client

    client.with_options = with_options
    monkeypatch.setattr(pool or scorer._scoring_client_pool, "get_client", lambda *args, **kwargs: client)
    return calls, options


@pytest.fixture
def candidate(tmp_path):
    original = read_document(ROOT / "config_defaults.json")
    original["settings"]["SEARCH_KEYWORDS"]["客户自定义"] = ["客户保留词"]
    original["prompts"]["NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500"]["text"] += "\n客户摘要规则"
    store = ConfigStore(tmp_path / "runtime.sqlite3")
    snapshot, _ = store.initialize(original, {"kind": "comprehensive-test"})
    return store, snapshot, build_topic_candidate(original)["document"]


def reviewed_report(candidate, tmp_path):
    store, snapshot, document = candidate
    samples = evaluation.load_samples()

    def score(sample):
        if not sample["content"].strip():
            return SimpleNamespace(score=0, status="empty_content", error_code=None,
                                   attempts=0, raw_response=None)
        value = sample["expected_min"]
        return SimpleNamespace(score=value, status="ok", error_code=None, attempts=1, raw_response=str(value))

    report = evaluation.evaluate_samples(samples, evaluation.evaluation_manifest(snapshot, document), score)
    report["human_review"] = {"approved": True, "reviewer": "offline-test-reviewer",
                              "reviewed_at": "2026-10-02T18:00:00+08:00",
                              "reviewed_ids": [row["id"] for row in samples]}
    path = tmp_path / "synthetic-evaluation.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path, report


def enable_args(candidate, report_path, report, backup):
    store, snapshot, _ = candidate
    return config_cli.parser().parse_args([
        "--store", str(store.path), "enable-topic", "--topic", "government-affairs", "--apply",
        "--expected-version", snapshot.token, "--expected-candidate-sha256", report["candidate_sha256"],
        "--evaluation-report", str(report_path), "--backup", str(backup), "--note", "离线综合测试"])


class SQLiteConnection:
    """Translate placeholders only; the application SQL executes against real rows."""
    def __init__(self, db, statements, result_rows, dict_rows=False):
        self.db, self.statements, self.dict_rows = db, statements, dict_rows
        self.result_rows = result_rows

    def cursor(self):
        parent = self

        class Cursor:
            def __init__(self):
                self.raw = parent.db.cursor()

            def execute(self, sql, params=()):
                parent.statements.append((sql, params))
                return self.raw.execute(sql.replace("%s", "?").replace(
                    "LEFT(short_summary, 101)", "substr(short_summary,1,101)"), params)

            @property
            def rowcount(self):
                return self.raw.rowcount

            def fetchall(self):
                rows = self.raw.fetchall()
                parent.result_rows.extend(dict(row) for row in rows)
                return [dict(row) if parent.dict_rows else tuple(row) for row in rows]

            def fetchone(self):
                row = self.raw.fetchone()
                return (dict(row) if parent.dict_rows else tuple(row)) if row is not None else None

            def close(self):
                self.raw.close()

        return Cursor()

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()

    def ping(self, **kwargs):
        pass

    def close(self):
        pass


@pytest.fixture
def database(monkeypatch):
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE scored_news_test (
        id INTEGER PRIMARY KEY, date TEXT, title TEXT, link TEXT, source TEXT, fetchdate TEXT,
        sourceapi TEXT, thumbnail TEXT, keyword TEXT, content TEXT, wordcount INTEGER,
        custom_grab INTEGER, score INTEGER, search_keyword TEXT, short_summary TEXT)""")
    statements, result_rows = [], []

    def connect(**kwargs):
        return SQLiteConnection(db, statements, result_rows, kwargs.get("dict_cursor", False))

    monkeypatch.setattr("db_utils.get_connection", connect)
    monkeypatch.setattr("db_utils.get_table_name", lambda: "scored_news_test")
    monkeypatch.setattr(views, "get_connection", connect)
    monkeypatch.setattr(views, "get_table_name", lambda: "scored_news_test")
    monkeypatch.setattr(summarizer, "get_conn", connect)
    monkeypatch.setattr(summarizer, "get_table_name", lambda: "scored_news_test")
    monkeypatch.setattr(summarizer, "prepare_batch", lambda *args, **kwargs: (None, [TOPIC]))
    monkeypatch.setattr(summarizer, "table_has_region_column", lambda *args: False)
    monkeypatch.setattr(summarizer, "table_has_business_types_column", lambda *args: False)
    app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
    app.register_blueprint(views.views_bp)
    app.config["TESTING"] = True
    spec = importlib.util.spec_from_file_location("pr27_comprehensive_writer", ROOT / "write_to_mysql.py")
    writer = importlib.util.module_from_spec(spec)
    # Import installs a process-wide exception hook; restore it after each case.
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    spec.loader.exec_module(writer)
    yield SimpleNamespace(db=db, statements=statements, result_rows=result_rows,
                          writer=writer, client=app.test_client())
    writer.cursor.close()
    db.close()


def add_db_row(database, title, *, keyword=TOPIC, score=3, content="正文", link=None,
               summary=None, fetchdate=DATE, search_keyword="公物仓"):
    return database.db.execute("""INSERT INTO scored_news_test
        (title,link,keyword,score,content,short_summary,fetchdate,source,sourceapi,search_keyword)
        VALUES (?,?,?,?,?,?,?,?,?,?)""", (title, link or f"https://example.test/{title}", keyword,
        score, content, summary, fetchdate, "测试来源", "api", search_keyword)).lastrowid


def summarize(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["news_item_summarizer.py", DATE, "--keyword", TOPIC])
    return summarizer.main()


def test_01_all_eleven_empty_searches_complete_and_resume(monkeypatch, isolated_runtime):
    terms = isolated_runtime.document["settings"]["SEARCH_KEYWORDS"][TOPIC]
    assert len(terms) == 11
    calls = install_http(monkeypatch, lambda name, *args: empty_response(name))
    assert collect(monkeypatch, terms)
    _, rows, diagnostic = collection()
    assert rows == [] and [row["status"] for row in diagnostic["searches"]] == ["empty"] * 11
    assert len(calls) == 44
    assert collect(monkeypatch, terms) and len(calls) == 44


def test_02_cross_engine_and_term_duplicates_keep_first_provenance(monkeypatch):
    def response(name, term, start, attempt):
        if start or name == "duckduckgo" or term == "空词":
            return empty_response(name)
        return results_response(name, [news()])
    install_http(monkeypatch, response)
    assert collect(monkeypatch, ["公物仓", "空词", "资产管理"])
    _, rows, diagnostic = collection()
    assert len(rows) == 1 and rows[0]["search_keyword"] == "公物仓"
    assert rows[0]["keyword"] == TOPIC and rows[0]["main_keyword"] == TOPIC
    assert [row["status"] for row in diagnostic["searches"]] == ["ok", "empty", "ok"]
    assert [row["count"] for row in diagnostic["searches"]] == [1, 0, 1]


def test_03_transient_provider_errors_recover_to_successful_empty(monkeypatch):
    calls = install_http(monkeypatch, lambda name, term, start, attempt:
                         {"error": "temporarily unavailable"} if attempt == 1 else empty_response(name))
    assert collect(monkeypatch, ["机关运行成本"])
    assert collection()[2]["searches"][0]["status"] == "empty"
    assert Counter(name for name, _, _ in calls) == {name: 2 for name in ENGINES}


def test_04_later_page_failure_keeps_rows_and_blocks_automatic_resume(monkeypatch):
    def response(name, term, start, attempt):
        if name != "google":
            return empty_response(name)
        return ({"search_metadata": {"status": "Error"}, "error": "provider unavailable"}
                if start else results_response(name, [news()]))
    calls = install_http(monkeypatch, response)
    assert not collect(monkeypatch, ["公物仓"])
    path, rows, diagnostic = collection()
    assert len(rows) == 1 and diagnostic["searches"][0]["status"] == "failed"
    before, request_count = path.read_bytes(), len(calls)
    assert not collect(monkeypatch, ["公物仓"])
    assert path.read_bytes() == before and len(calls) == request_count == 7


def test_05_unclassified_error_and_timeout_cannot_become_valid_empty(monkeypatch):
    def response(name, term, start, attempt):
        if name == "baidu":
            return {"error": "unknown failure without metadata"}
        if name == "bing":
            return TimeoutError("timeout")
        return empty_response(name)
    calls = install_http(monkeypatch, response)
    assert not collect(monkeypatch, ["碳普惠"])
    _, rows, diagnostic = collection()
    assert rows == [] and diagnostic["searches"][0]["error"] == "search_process_failed"
    assert Counter(name for name, _, _ in calls) == {"google": 1, "baidu": 3, "bing": 3, "duckduckgo": 1}


def test_06_historical_dates_blacklist_and_empty_sources_combine_correctly(monkeypatch):
    monkeypatch.setattr(filtering, "blacklist_keywords", ["广告"])
    items = [news(), news("广告", "https://example.test/ad"),
             news("未来", "https://example.test/future", "2099-01-01"),
             news("过去", "https://example.test/past", "2000-01-01"),
             news("相对时间", "https://example.test/relative", "1 day ago")]
    install_http(monkeypatch, lambda name, term, start, attempt:
                 results_response(name, items) if name == "google" and not start else empty_response(name))
    assert collect(monkeypatch, ["公物仓"])
    _, rows, diagnostic = collection()
    assert [row["title"] for row in rows] == ["资产调剂"]
    assert rows[0]["date"] == rows[0]["fetchdate"] == DATE
    assert diagnostic["searches"][0]["count"] == 1


def test_07_config_revision_drift_rejects_reuse_without_new_requests(monkeypatch, isolated_runtime):
    calls = install_http(monkeypatch, lambda name, *args: empty_response(name))
    assert collect(monkeypatch, ["公物仓"])
    path, _, _ = collection()
    before = path.read_bytes()
    isolated_runtime.token = "comprehensive:2"
    assert not collect(monkeypatch, ["公物仓"])
    assert path.read_bytes() == before and len(calls) == 4


def test_08_content_enrichment_allows_reuse_but_provenance_tampering_does_not(monkeypatch):
    install_http(monkeypatch, lambda name, term, start, attempt:
                 results_response(name, [news(link="https://finance.people.com.cn/a"),
                                         news("视频", "https://tv.cctv.com/a")])
                 if name == "google" and not start else empty_response(name))
    assert collect(monkeypatch, ["公物仓"])
    path, rows, _ = collection()
    rows = [row for row in rows if "tv.cctv.com" not in row["link"]]
    rows[0].update(link="http://finance.people.com.cn/a", content="补充正文", wordcount=4)
    path.write_text(json.dumps(rows))
    assert collect(monkeypatch, ["公物仓"])
    rows[0]["search_keyword"] = "伪造来源"
    path.write_text(json.dumps(rows))
    assert not collect(monkeypatch, ["公物仓"])


def test_09_preview_requires_profile_but_never_credentials_or_worker(candidate, tmp_path, monkeypatch, capsys):
    store, snapshot, _ = candidate
    for key in ("LLM_API_KEY", "LLM_SCORING_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    worker_calls = []
    monkeypatch.setattr(evaluator, "run_evaluation", lambda *args: worker_calls.append(args))
    output = tmp_path / "must-not-exist.json"
    assert evaluator.main(["--store", str(store.path), "--output", str(output)]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["mode"] == "preview" and preview["sample_count"] == 55
    assert worker_calls == [] and not output.exists() and store.read().token == snapshot.token
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_SCORING_BASE_URL", raising=False)
    assert evaluator.main(["--store", str(store.path)]) == 1
    assert worker_calls == [] and len(store.history()) == 1


def test_10_reviewed_enable_backup_noop_and_customization_are_preserved(candidate, tmp_path):
    store, snapshot, document = candidate
    report_path, report = reviewed_report(candidate, tmp_path)
    backup = tmp_path / "backup.sqlite3"
    assert config_cli.run(enable_args(candidate, report_path, report, backup))["status"] == "enabled"
    assert ConfigStore(backup).read().document == snapshot.document
    installed = store.read()
    assert installed.document == document and len(store.history()) == 2
    args = enable_args((store, installed, document), report_path, report, backup)
    assert config_cli.run(args)["status"] == "noop" and len(store.history()) == 2
    changed = copy.deepcopy(document)
    changed["settings"]["SEARCH_KEYWORDS"][TOPIC] = ["用户新词"]
    changed["prompts"][PROMPT_ID]["text"] = "用户新规则"
    custom = store.save(changed, installed.token, "用户修改")
    with pytest.raises(ConfigConflict):
        config_cli.run(enable_args((store, custom, changed), report_path, report, backup))
    assert store.read().document == changed
    assert ConfigStore(backup).read().document == snapshot.document


def test_11_stale_enable_cannot_erase_concurrent_configuration(candidate, tmp_path):
    store, snapshot, _ = candidate
    path, report = reviewed_report(candidate, tmp_path)
    changed = copy.deepcopy(snapshot.document)
    changed["settings"]["SEARCH_KEYWORDS"]["客户自定义"].append("并发新词")
    current = store.save(changed, snapshot.token, "并发更新")
    backup = tmp_path / "backup.sqlite3"
    with pytest.raises(ConfigConflict):
        config_cli.run(enable_args(candidate, path, report, backup))
    assert store.read().token == current.token and store.read().document == changed
    assert not backup.exists() and len(store.history()) == 2


def test_12_model_drift_rejects_previously_approved_report(candidate, tmp_path, monkeypatch):
    store, snapshot, _ = candidate
    path, report = reviewed_report(candidate, tmp_path)
    monkeypatch.setenv("LLM_SCORING_MODEL", "changed-after-review")
    backup = tmp_path / "backup.sqlite3"
    with pytest.raises(ConfigError):
        config_cli.run(enable_args(candidate, path, report, backup))
    assert store.read().token == snapshot.token and not backup.exists()


def test_13_existing_backup_is_not_overwritten_during_valid_enable(candidate, tmp_path):
    store, snapshot, _ = candidate
    path, report = reviewed_report(candidate, tmp_path)
    backup = tmp_path / "backup.sqlite3"
    store.backup(backup)
    original = backup.read_bytes()
    with pytest.raises((ConfigError, FileExistsError)):
        config_cli.run(enable_args(candidate, path, report, backup))
    assert backup.read_bytes() == original and store.read().token == snapshot.token


def test_14_batch_score_distinguishes_zero_empty_failure_and_rescores_only_failure(tmp_path, monkeypatch, isolated_runtime):
    isolated_runtime.document["settings"]["NEWS_RULE_BASED_SCORING"].append(
        {"main_keyword": TOPIC, "title_contains": "通知", "score": 5})
    folder = Path("output") / DATE
    folder.mkdir()
    path = folder / f"{DATE}_{TOPIC}.json"
    path.write_text(json.dumps([
        news("通知-无关", "https://example.test/zero", content="居民积分", wordcount=0),
        news("空正文", "https://example.test/empty", content=" ", wordcount=999),
        news("待恢复", "https://example.test/failed", content="资产管理"),
        news("有效高分", "https://example.test/high", content="公物仓调剂")]))
    calls, _ = install_model(monkeypatch, ["0", "5分", "10", "bad", "4"])
    rows, counts, total, rules = scorer.batch_score_news(path, TOPIC)
    assert [(row["score"], row["score_status"]) for row in rows] == [
        (0, "ok"), (0, "empty_content"), (None, "failed"), (4, "ok")]
    assert len(calls) == 5 and total == 4 and counts[0] == 2 and rules == []
    scored_path = scorer.write_scored_json(rows, str(path))
    assert not scorer.scored_file_complete(scored_path, TOPIC)
    recovery_calls, _ = install_model(monkeypatch, ["0"])
    monkeypatch.setattr(scorer, "prepare_batch", lambda *args: (None, [TOPIC]))
    monkeypatch.setattr(sys, "argv", ["news_scorer.py", TOPIC, DATE, "--rescore"])
    assert scorer.main() and scorer.scored_file_complete(scored_path, TOPIC)
    recovered = {row["title"]: row for row in json.loads(Path(scored_path).read_text())}
    assert len(recovery_calls) == 1 and recovered["待恢复"]["score"] == 0
    assert recovered["通知-无关"]["score"] == 0 and recovered["空正文"]["score_status"] == "empty_content"
    assert recovered["有效高分"]["score"] == 4


def test_15_transport_then_invalid_output_recovers_within_three_attempts(monkeypatch):
    calls, options = install_model(monkeypatch, [TimeoutError("secret transport text"), "5分", " 3\n"])
    result = scorer.score_news_result("资产", "正文", "公物仓", TOPIC)
    assert (result.score, result.status, result.attempts) == (3, "ok", 3)
    assert result.token_usage == {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    assert len(calls) == 3 and options == [{"max_retries": 0}] * 3
    assert "secret transport text" not in Path("run.log").read_text()


def test_16_permanent_model_failures_return_none_without_retries_or_secret_logs(monkeypatch):
    for status, message in [(401, "private-auth"), (402, "private-quota"), (400, "context length private-context")]:
        error = RuntimeError(message)
        error.status_code = status
        calls, _ = install_model(monkeypatch, [error])
        assert scorer.score_news("资产", "正文", "公物仓", TOPIC) is None
        assert len(calls) == 1 and message not in Path("run.log").read_text()


def test_17_summary_partial_failure_and_resume_keep_existing_rows(database, monkeypatch):
    short = add_db_row(database, "短文", content="短" * 500)
    long = add_db_row(database, "长文", content="长" * 501)
    low = add_db_row(database, "低分", score=2)
    other = add_db_row(database, "其他专题", keyword="未启用专题")
    old = add_db_row(database, "其他日期", fetchdate="2000-01-01")
    calls, _ = install_model(monkeypatch, [TimeoutError("temporary")] * 9, summarizer._pool)
    assert not summarize(monkeypatch)
    rows = dict(database.db.execute("SELECT id,short_summary FROM scored_news_test"))
    assert rows[short] == "短" * 500 and rows[long] is None and len(calls) == 9
    calls, _ = install_model(monkeypatch, ["恢复后的摘要"], summarizer._pool)
    assert summarize(monkeypatch) and len(calls) == 1
    rows = dict(database.db.execute("SELECT id,short_summary FROM scored_news_test"))
    assert rows[short] == "短" * 500 and rows[long] == "恢复后的摘要"
    assert all(rows[rid] is None for rid in (low, other, old))


def test_18_homepage_budget_lazy_summary_and_removed_topic_access(database, monkeypatch):
    text = "<script>alert(1)</script>\n" + "摘要" * 10000
    ids = [add_db_row(database, f"综合新闻-{number:03}", summary=text,
                      score=None if number == 0 else 3) for number in range(51)]
    prefix = f"/?date_from={DATE}&date_to={DATE}&keyword={TOPIC}"
    first = database.client.get(prefix).get_data(as_text=True)
    second = database.client.get(prefix + "&page=2").get_data(as_text=True)
    assert first.count('class="news-summary"') == 50 and second.count('class="news-summary"') == 1
    assert len(first.encode()) < 120000 and text not in first and text not in second
    assert "未完成评分" in second and "<script>alert(1)</script>" not in first
    loaded = [row for row in database.result_rows if "short_summary" in row]
    assert len(loaded) == 51
    assert all(len(row["short_summary"]) <= 101 and "content" not in row for row in loaded)
    response = database.client.get(f"/api/news/{ids[0]}/summary")
    assert response.json["short_summary"] == text and response.json["search_keyword"] == "公物仓"
    assert response.headers["Cache-Control"] == "no-store" and "content" not in response.json
    monkeypatch.setattr(views, "value", lambda key: ["其他专题"])
    assert database.client.get(f"/api/news/{ids[0]}/summary").status_code == 404


def test_19_score_backfill_matches_topic_title_link_and_preserves_valid_zero(database, tmp_path):
    target = add_db_row(database, "同名", link="https://example.test/match", score=None)
    other_link = add_db_row(database, "同名", link="https://example.test/other", score=None)
    other_topic = add_db_row(database, "同名", link="https://example.test/match", keyword="其他专题", score=None)
    zero = add_db_row(database, "已有零分", score=0)
    high = add_db_row(database, "已有高分", score=4)
    invalid = add_db_row(database, "评分失败", score=None)
    payload = [news("同名", "https://example.test/match", keyword=TOPIC, score=0, score_status="ok"),
               news("已有零分", "https://example.test/已有零分", keyword=TOPIC, score=5, score_status="ok"),
               news("已有高分", "https://example.test/已有高分", keyword=TOPIC, score=1, score_status="ok"),
               news("评分失败", "https://example.test/评分失败", keyword=TOPIC, score=None, score_status="failed")]
    path = tmp_path / "rescore.json"
    path.write_text(json.dumps(payload))
    assert database.writer.update_scores_from_json(path, TOPIC) == 1
    scores = dict(database.db.execute("SELECT id,score FROM scored_news_test"))
    assert scores == {target: 0, other_link: None, other_topic: None, zero: 0, high: 4, invalid: None}
    assert database.writer.update_scores_from_json(path, TOPIC) == 0


def test_20_collection_scoring_import_summary_and_page_form_one_workflow(database, monkeypatch):
    install_http(monkeypatch, lambda name, term, start, attempt:
                 results_response(name, [news()]) if name == "google" and not start else empty_response(name))
    assert collect(monkeypatch, ["公物仓", "资产管理"])
    path, rows, _ = collection()
    assert len(rows) == 1
    rows[0].update(content="资产管理正文" * 100, wordcount=600)
    path.write_text(json.dumps(rows))
    score_calls, _ = install_model(monkeypatch, ["4"])
    scored, _, _, _ = scorer.batch_score_news(path, TOPIC)
    scored_path = scorer.write_scored_json(scored, str(path))
    assert scorer.scored_file_complete(scored_path, TOPIC) and len(score_calls) == 1
    database.writer.import_scored_news_with_retry(scored_path, TOPIC)
    database.writer.import_scored_news_with_retry(scored_path, TOPIC)
    assert database.db.execute("SELECT count(*) FROM scored_news_test").fetchone()[0] == 1
    summary_calls, _ = install_model(monkeypatch, ["机关资产跨部门调剂实践"], summarizer._pool)
    assert summarize(monkeypatch) and len(summary_calls) == 1
    row = database.db.execute("SELECT id,score,search_keyword FROM scored_news_test").fetchone()
    assert (row["score"], row["search_keyword"]) == (4, "公物仓")
    page = database.client.get(f"/?date_from={DATE}&date_to={DATE}&keyword={TOPIC}")
    assert page.status_code == 200 and "资产调剂" in page.get_data(as_text=True)
    detail = database.client.get(f"/api/news/{row['id']}/summary")
    assert detail.json["short_summary"] == "机关资产跨部门调剂实践"
    assert detail.json["search_keyword"] == "公物仓"
    assert collect(monkeypatch, ["公物仓", "资产管理"])
