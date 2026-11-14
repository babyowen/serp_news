"""Drive the real ledger and scheduler through an isolated output root."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
from uuid import uuid4

import pytest


@pytest.fixture
def ledger(monkeypatch):
    import wechat_notifications

    monkeypatch.setattr(wechat_notifications, "_now", lambda: SHANGHAI_DAY)
    monkeypatch.setattr(wechat_notifications.time, "sleep", lambda delay: None)
    return wechat_notifications


SHANGHAI_DAY = datetime(2026, 11, 14, 1, tzinfo=timezone.utc)
TARGET = "2026-11-13"


def capture(messages, *, success=True):
    def send(card_json):
        messages.append(json.loads(card_json))
        return success

    return send


def content(card):
    return "\n".join([card["header"]["title"]["content"]] + [
        element["text"]["content"] for element in card["elements"] if "text" in element
    ])


def test_wechat_result_distinguishes_new_rows_and_duplicates(ledger, tmp_path):
    run = ledger.start_run(TARGET, output_root=tmp_path)
    messages = []
    result = ledger.finish_run(run, {
        "status": "success",
        "collection": {
            "date": TARGET,
            "complete": True,
            "accounts": [{"status": "ok"}, {"status": "empty"}],
            "articles": [{"wechat_content_status": "ok"}, {"wechat_content_status": "insufficient"}],
            "collected_count": 1, "reused_count": 1, "api_cost": 0.14, "total_api_cost": 0.31,
        },
        "scoring": {"candidates": 1, "succeeded": 1, "failed": 0, "skipped": 1},
        "database": {"inserted": 0, "duplicate": 1, "failed": 0, "skipped": 1},
        "summary": {"succeeded": 0, "failed": 0, "already_done": 1},
    }, output_root=tmp_path, send_fn=capture(messages))
    text = content(messages[0])
    assert TARGET in text and run["run_id"] not in text and "09:00" in text
    assert "无当日文章 1" in text and "正文不足 1" in text
    assert "采集到 2 篇" in text and "新增入库 0 篇" in text and "重复 1 篇" in text
    assert "本次读取 1" in text and "缓存复用 1" in text
    assert "0.14" in text and "0.31" in text
    assert result["notification"]["state"] == "sent"


def test_wechat_result_explicit_zero_and_missing_counters_are_distinct(ledger, tmp_path):
    run = ledger.start_run(TARGET, output_root=tmp_path)
    messages = []
    ledger.finish_run(run, {
        "status": "success",
        "collection": {
            "date": TARGET, "accounts": [{"status": "empty"}], "articles": [],
            "complete": True, "api_cost": 0,
        },
        "scoring": {"succeeded": True}, "database": {"inserted": 0}, "summary": {},
    }, output_root=tmp_path, send_fn=capture(messages))
    text = content(messages[0])
    assert "采集到 0 篇" in text and "无当日文章 1" in text
    assert "新增入库 0" in text and "重复 未知" in text
    assert "评分结果未确认" in text
    assert "摘要结果未确认" in text
    assert "本次 ¥0.00" in text and "当日累计 未知" in text


def test_wechat_result_partial_failure_has_reason_without_raw_error_or_content(ledger, tmp_path):
    run = ledger.start_run(TARGET, output_root=tmp_path)
    messages = []
    result = ledger.finish_run(run, {
        "status": "partial", "error": "raw-key-in-provider-response",
        "collection": {"date": TARGET, "complete": False,
                       "accounts": [{"status": "failed", "error": "raw-key-in-provider-response"}],
                       "articles": [{"wechat_content_status": "failed", "content": "private-article-body"}]},
        "database": {"status": "partial", "inserted": 2, "duplicate": 1, "failed": 1},
    }, output_root=tmp_path, send_fn=capture(messages))
    text = content(messages[0])
    assert "部分失败" in text and "新增入库 2" in text and "失败 1" in text
    assert "官微采集未完整结束" in text
    assert "其他运行错误" in text
    assert "raw-key-in-provider-response" not in text + json.dumps(result)
    assert "private-article-body" not in text + json.dumps(result)
    path = tmp_path / "wechat_runs" / TARGET / f'{run["run_id"]}.json'
    assert "raw-key-in-provider-response" not in path.read_text()


def test_wechat_result_names_known_failed_accounts_with_sanitized_causes(ledger, tmp_path):
    run = ledger.start_run(TARGET, output_root=tmp_path)
    messages = []
    report = {"date": TARGET, "complete": False, "error": "缺少 WECHAT_API_KEY", "accounts": [
        {"ghid": "gh_7af7ed4664f7", "name": "FAKE-NAME-SECRET", "status": "failed",
         "error": "极致了余额不足（20001） https://example.org/?key=SECRET"},
        {"ghid": "gh_b658f8c01c4a", "status": "failed",
         "error": "1 篇正文未通过：正文发布时间与列表不符"},
        {"ghid": "gh_UNKNOWN_SECRET", "name": "FAKE-NAME-SECRET", "status": "failed", "error": "SECRET"},
    ], "articles": []}
    result = ledger.finish_run(run, {"status": "partial", "collection": report},
                               output_root=tmp_path, send_fn=capture(messages))
    text = content(messages[0])
    assert "国管局 · 供应商余额不足" in text
    assert "勤惟宁 · 发布时间冲突" in text
    assert 'gh_7af7ed4664f7' not in text and 'gh_b658f8c01c4a' not in text
    assert "接口鉴权或配置" in text
    assert "SECRET" not in text + json.dumps(result)


def test_wechat_ack_preserves_first_outcome_and_new_run_sends_own_card(ledger, tmp_path):
    first = ledger.start_run(TARGET, output_root=tmp_path)
    second = ledger.start_run(TARGET, output_root=tmp_path)
    messages = []
    send = capture(messages)
    original = ledger.finish_run(first, {"status": "failed", "error": "database_failed"},
                                 output_root=tmp_path, send_fn=send)
    repeated = ledger.finish_run(first, {"status": "success"}, output_root=tmp_path, send_fn=send)
    ledger.finish_run(second, {"status": "partial"}, output_root=tmp_path, send_fn=send)
    assert repeated == original and repeated["status"] == "failed"
    assert first["run_id"] != second["run_id"] and len(messages) == 2
    assert "入库阶段失败" in content(messages[0])


def test_wechat_failed_send_retries_original_card_approximately_once_per_check_day(ledger, tmp_path):
    run = ledger.start_run(TARGET, output_root=tmp_path)
    messages = []
    unsuccessful = capture(messages, success=False)
    result = ledger.finish_run(run, {"status": "partial"}, output_root=tmp_path, send_fn=unsuccessful)
    assert result["notification"]["state"] == "pending" and len(messages) == 3
    assert messages[0] == messages[1] == messages[2]
    ledger.check_missing(now=SHANGHAI_DAY, output_root=tmp_path, send_fn=unsuccessful)
    ledger.check_missing(now=SHANGHAI_DAY, output_root=tmp_path, send_fn=unsuccessful)
    assert len(messages) == 4
    result = ledger.retry_pending(output_root=tmp_path, send_fn=capture(messages))
    assert result["sent"] == 1 and result["pending"] == 0
    assert len(messages) == 5 and messages[0] == messages[-1]
    assert ledger.check_missing(now=SHANGHAI_DAY, output_root=tmp_path,
                                send_fn=capture(messages))["status"] == "partial"
    assert len(messages) == 5


def test_wechat_pending_older_result_retried_without_fulfilling_todays_duty(ledger, tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "_now", lambda: SHANGHAI_DAY - timedelta(days=1))
    run = ledger.start_run("2026-11-12", output_root=tmp_path)
    def failing_send(card):
        raise RuntimeError("private-send-error")
    ledger.finish_run(run, {"status": "failed"}, output_root=tmp_path, send_fn=failing_send)
    monkeypatch.setattr(ledger, "_now", lambda: SHANGHAI_DAY)
    messages = []
    check = ledger.check_missing(now=SHANGHAI_DAY, output_root=tmp_path, send_fn=capture(messages))
    assert check["status"] == "missing" and check["date"] == TARGET
    assert len(messages) == 2
    assert "2026-11-12" in content(messages[0]) and "失败" in content(messages[0])
    assert "2026-11-13" in content(messages[1]) and "未启动" in content(messages[1])
    assert all("private-send-error" not in content(card) for card in messages)


def test_wechat_checker_recovers_dead_backfill_and_keeps_today_status_separate(ledger, tmp_path):
    date = "2026-11-09"
    old = ledger.start_run(date, output_root=tmp_path)
    ledger.release_run(old, output_root=tmp_path)
    live = ledger.start_run(date, output_root=tmp_path)
    messages = []
    try:
        check = ledger.check_missing(now=SHANGHAI_DAY, output_root=tmp_path, send_fn=capture(messages))
        assert check["status"] == "missing" and check["date"] == TARGET
        assert len(messages) == 2
        assert date in content(messages[0]) and "中断" in content(messages[0])
        assert old["run_id"] not in content(messages[0])
        saved = json.loads((tmp_path / 'wechat_runs' / date / f"{old['run_id']}.json").read_text())
        assert saved['run_id'] == old['run_id'] and saved['status'] == 'interrupted'
        assert all(live["run_id"] not in content(card) for card in messages)
    finally:
        ledger.release_run(live, output_root=tmp_path)


def test_wechat_checker_uses_beijing_cutoff_dedupes_missing_alerts(ledger, tmp_path):
    messages = []
    assert ledger.check_missing(now=SHANGHAI_DAY - timedelta(seconds=1), output_root=tmp_path,
                                send_fn=capture(messages))["status"] == "too_early"
    assert not messages and not (tmp_path / "wechat_runs").exists()
    for _ in range(2):
        report = ledger.check_missing(now=SHANGHAI_DAY, output_root=tmp_path, send_fn=capture(messages))
        assert report["status"] == "missing" and report["date"] == TARGET
    assert len(messages) == 1 and "未启动" in content(messages[0])


def test_wechat_checker_shares_overdue_ack_between_missing_and_running(ledger, tmp_path):
    messages = []
    send = capture(messages)
    assert ledger.check_missing(now=SHANGHAI_DAY, output_root=tmp_path, send_fn=send)["status"] == "missing"
    run = ledger.start_run(TARGET, output_root=tmp_path)
    assert ledger.check_missing(now=SHANGHAI_DAY, output_root=tmp_path, send_fn=send)["status"] == "running"
    assert len(messages) == 1
    ledger.finish_run(run, {"status": "success"}, output_root=tmp_path, send_fn=send)
    assert len(messages) == 2


def test_wechat_checker_ignores_old_or_corrupt_completion_records(ledger, tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "_now", lambda: SHANGHAI_DAY - timedelta(days=1))
    old_run = ledger.start_run(TARGET, output_root=tmp_path)
    ledger.finish_run(old_run, {"status": "success"}, output_root=tmp_path, send_fn=lambda _: True)
    monkeypatch.setattr(ledger, "_now", lambda: SHANGHAI_DAY)
    fake = uuid4().hex
    directory = tmp_path / "wechat_runs" / TARGET
    (directory / f"{fake}.json").write_text(json.dumps({
        "version": 1, "date": TARGET, "run_id": fake, "status": "success",
        "started_at": SHANGHAI_DAY.isoformat(), "finished_at": SHANGHAI_DAY.isoformat(),
        "notification": {"state": "sent"},
    }))
    (directory / f"{uuid4().hex}.json").write_bytes(b'\xff')
    (tmp_path / "run_status.json").write_text('{"status":"finished"}')
    messages = []
    check = ledger.check_missing(now=SHANGHAI_DAY, output_root=tmp_path, send_fn=capture(messages))
    assert check["status"] == "missing"
    assert len(messages) == 1 and "没有有效结果记录" in content(messages[0])


def test_wechat_checker_ignores_run_scoped_stage_files(ledger, tmp_path):
    run = ledger.start_run(TARGET, output_root=tmp_path)
    completed = ledger.finish_run(run, {"status": "success"}, output_root=tmp_path, send_fn=lambda _: True)
    directory = tmp_path / "wechat_runs" / TARGET
    path = directory / f'{run["run_id"]}.json'
    stage = directory / "stages" / run["run_id"] / path.name
    stage.parent.mkdir(parents=True)
    path.rename(stage)
    messages = []
    check = ledger.check_missing(now=SHANGHAI_DAY, output_root=tmp_path, send_fn=capture(messages))
    assert completed["status"] == "success"
    assert check["status"] == "missing" and len(messages) == 1
    assert "没有有效结果记录" in content(messages[0]) and "未启动" not in content(messages[0])


@pytest.mark.parametrize("unusable", ["directory", "permission"])
def test_wechat_historical_read_error_does_not_prevent_replay_or_current_check(
        ledger, tmp_path, monkeypatch, unusable):
    bad_day = tmp_path / "wechat_runs" / "2026-11-09"
    bad_day.mkdir(parents=True)
    bad_path = bad_day / f"{uuid4().hex}.json"
    if unusable == "directory":
        bad_path.mkdir()
    else:
        bad_path.write_text("{}")
        original_read = Path.read_text

        def guarded_read(path, *args, **kwargs):
            if path == bad_path:
                raise PermissionError("injected unreadable record")
            return original_read(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", guarded_read)
    run = ledger.start_run("2026-11-10", output_root=tmp_path)
    ledger.finish_run(run, {"status": "failed"}, output_root=tmp_path, send_fn=lambda _: False)
    messages = []
    check = ledger.check_missing(now=SHANGHAI_DAY, output_root=tmp_path, send_fn=capture(messages))
    assert check["status"] == "missing" and check["sent"] == 1
    assert len(messages) == 2
    assert "2026-11-10" in content(messages[0]) and TARGET in content(messages[1])


def test_wechat_live_process_is_detected_and_process_exit_records_interrupted(ledger, tmp_path):
    script = """
import json, sys
from datetime import datetime
import wechat_notifications as ledger
ledger._now = lambda: datetime.fromisoformat(sys.argv[3])
print(json.dumps(ledger.start_run(sys.argv[2], output_root=sys.argv[1])), flush=True)
sys.stdin.readline()
"""
    with subprocess.Popen([sys.executable, "-B", "-c", script, str(tmp_path), TARGET, SHANGHAI_DAY.isoformat()],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as process:
        run = json.loads(process.stdout.readline())
        messages = []
        try:
            for _ in range(2):
                assert ledger.check_missing(now=SHANGHAI_DAY, output_root=tmp_path,
                                            send_fn=capture(messages))["status"] == "running"
            assert len(messages) == 1 and "仍运行" in content(messages[0])
            assert run["run_id"] not in content(messages[0])
        finally:
            process.terminate()
            process.wait(timeout=5)
    for _ in range(2):
        assert ledger.check_missing(now=SHANGHAI_DAY, output_root=tmp_path,
                                    send_fn=capture(messages))["status"] == "interrupted"
    assert len(messages) == 2
    assert "中断" in content(messages[-1]) and "新增入库 未知" in content(messages[-1])
    assert run["run_id"] not in content(messages[-1])
    saved = json.loads((tmp_path / 'wechat_runs' / TARGET / f"{run['run_id']}.json").read_text())
    assert saved['run_id'] == run['run_id'] and saved['status'] == 'interrupted'


def test_wechat_releasing_run_without_finishing_leaves_interrupted_evidence(ledger, tmp_path):
    run = ledger.start_run(TARGET, output_root=tmp_path)
    ledger.release_run(run, output_root=tmp_path)
    messages = []
    assert ledger.check_missing(now=SHANGHAI_DAY, output_root=tmp_path,
                                send_fn=capture(messages))["status"] == "interrupted"
    assert "中断" in content(messages[0])


def test_wechat_parallel_finish_and_checker_send_one_result(ledger, tmp_path):
    run = ledger.start_run(TARGET, output_root=tmp_path)
    messages = []
    sending = Event()
    release = Event()

    def send(card):
        sending.set()
        assert release.wait(timeout=10)
        return capture(messages)(card)

    with ThreadPoolExecutor(max_workers=6) as pool:
        finish = pool.submit(ledger.finish_run, run, {"status": "failed"}, output_root=tmp_path, send_fn=send)
        try:
            assert sending.wait(timeout=5)
            check = pool.submit(ledger.check_missing, now=SHANGHAI_DAY, output_root=tmp_path, send_fn=send)
            repeated = pool.submit(ledger.finish_run, run, {"status": "success"}, output_root=tmp_path, send_fn=send)
        finally:
            release.set()
        assert finish.result(timeout=5)["notification"]["state"] == "sent"
        assert repeated.result(timeout=5)["status"] == "failed"
        assert check.result(timeout=5)["status"] == "failed"
    assert len(messages) == 1


def test_wechat_checker_observes_acknowledged_finish_that_wins_date_lock(ledger, tmp_path, monkeypatch):
    run = ledger.start_run(TARGET, output_root=tmp_path)
    messages = []
    lock = ledger._day_lock

    @contextmanager
    def finish_before_checker_acquires_lock(directory):
        monkeypatch.setattr(ledger, "_day_lock", lock)
        monkeypatch.setattr(ledger, "_now", lambda: SHANGHAI_DAY + timedelta(seconds=10))
        result = ledger.finish_run(run, {"status": "failed"}, output_root=tmp_path, send_fn=capture(messages))
        assert result["notification"]["state"] == "sent"
        with lock(directory):
            yield

    monkeypatch.setattr(ledger, "_day_lock", finish_before_checker_acquires_lock)
    report = ledger.check_missing(output_root=tmp_path, send_fn=capture(messages))
    assert report["status"] == "failed" and report["run_id"] == run["run_id"]
    assert len(messages) == 1


def test_wechat_finishes_result_after_local_registry_is_replaced(ledger, tmp_path, monkeypatch):
    import wechat_collector

    registry = tmp_path / "invalid-registry.json"
    registry.write_text("{")
    monkeypatch.setattr(wechat_collector, "REGISTRY", registry)
    run = ledger.start_run(TARGET, output_root=tmp_path)
    messages = []
    report = {"date": TARGET, "complete": False,
              "accounts": [{"ghid": "gh_7af7ed4664f7", "status": "failed", "error": "极致了余额不足"}]}
    result = ledger.finish_run(run, {"status": "partial", "collection": report},
                               output_root=tmp_path, send_fn=capture(messages))
    assert result["notification"]["state"] == "sent"
    assert "账号未识别 · 供应商余额不足" in content(messages[0])


def test_wechat_api_retries_use_same_uuid_and_independent_runs_use_distinct_uuids(ledger, tmp_path, monkeypatch):
    import news_volume_alert
    import requests

    monkeypatch.setenv("FEISHU_USER_ID", "ou_test")
    monkeypatch.setenv("FEISHU_APP_ID", "test_app")
    monkeypatch.setenv("FEISHU_APP_SECRET", "test_secret")
    monkeypatch.setenv("LARK_CLI_PATH", "")
    sent = []

    class Response:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def post(url, **kwargs):
        if url == news_volume_alert.FEISHU_TOKEN_URL:
            return Response({"tenant_access_token": "test-token"})
        assert url == news_volume_alert.FEISHU_MESSAGE_URL
        sent.append(kwargs["json"])
        return Response({"code": 0 if len(sent) > 1 else 23})

    monkeypatch.setattr(requests, "post", post)
    for _ in range(2):
        run = ledger.start_run(TARGET, output_root=tmp_path)
        ledger.finish_run(run, {"status": "success"}, output_root=tmp_path)
    assert len(sent) == 3
    assert sent[0]["uuid"] == sent[1]["uuid"] != sent[2]["uuid"]
    assert len(sent[0]["uuid"]) <= 50
    assert sent[0]["content"] == sent[1]["content"]


def test_wechat_standalone_check_sends_with_malformed_mysql_port(tmp_path):
    script = """
import sys
from datetime import datetime
import requests
import wechat_notifications as ledger

class Response:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data

def post(url, **kwargs):
    if url.endswith("/tenant_access_token/internal"):
        return Response({"tenant_access_token": "stub-token"})
    assert url.endswith("/im/v1/messages") and kwargs["json"]["uuid"]
    print("feishu-message-attempt")
    return Response({"code": 0})

requests.post = post
ledger._now = lambda: datetime.fromisoformat(sys.argv[1])
result = ledger.main(["--check-missing"])
assert "db_utils" not in sys.modules
raise SystemExit(result)
"""
    env = {**os.environ, "MYSQL_PORT": "invalid-port", "ENABLE_GOVERNMENT_WECHAT": "1",
           "FEISHU_USER_ID": "ou_test", "FEISHU_APP_ID": "test_app",
           "FEISHU_APP_SECRET": "test_secret", "LARK_CLI_PATH": ""}
    proc = subprocess.run([sys.executable, "-B", "-c", script, SHANGHAI_DAY.isoformat()],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("feishu-message-attempt") == 1
    report = json.loads(proc.stdout.splitlines()[-1])
    assert report["alert"] == "sent"


@pytest.mark.parametrize("flag", ["--check-missing", "--retry-pending"])
def test_wechat_cli_explicitly_skips_when_disabled_without_creating_ledger(ledger, tmp_path, monkeypatch, capsys, flag):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENABLE_GOVERNMENT_WECHAT", "0")
    assert ledger.main([flag]) == 0
    assert "skipped" in capsys.readouterr().out
    assert not (tmp_path / "output").exists()


def reader_fixture(monkeypatch, tmp_path, kinds, *, duplicate=False):
    """A committed day with one summary row per retained article."""
    from wechat_collector import load_registry
    from wechat_pipeline_run import write_stage
    from topic_config import TOPIC

    accounts = {a['region']: a for a in load_registry()['accounts'] if a['enabled']}
    run = {'date': TARGET, 'run_id': 'a' * 32}
    collection = {'date': TARGET, 'complete': True, 'accounts': [], 'articles': []}
    db, summary = [], []
    for i, (region, status, method, reason, score) in enumerate(kinds, 1):
        a = accounts[region]
        link = f"https://mp.weixin.qq.com/s?__biz={a['biz']}&mid={100+i}&idx=1"
        candidate = {'title': f'新闻{i}', 'link': link, 'article_id': f"{a['biz']}/{100+i}/1"}
        collection['articles'].append(dict(candidate, keyword=TOPIC, sourceapi='极致了api',
            wechat_article_id=candidate['article_id'], wechat_ghid=a['ghid'], wechat_content_status='ok',
            region='SECRET-FAKE', source='SECRET-FAKE'))
        db.append(dict(candidate, stored_link=link, status='inserted'))
        summary.append(dict(row_id=i, candidates=[candidate], status=status, stored_link=link,
            stored_date=TARGET, score=score, summary_method=method, reason=reason))
    if duplicate:
        a = accounts['国管局']
        candidate = dict(title='历史重复', link=f"https://mp.weixin.qq.com/s?__biz={a['biz']}&mid=1000&idx=1",
                         article_id=f"{a['biz']}/1000/1")
        collection['articles'].append(dict(candidate, keyword=TOPIC, sourceapi='极致了api', wechat_content_status='ok'))
        db.append(dict(candidate, stored_link=candidate['link'], status='duplicate'))
        summary.append(dict(row_id=1000, candidates=[candidate], status='already_done', stored_date='2026-11-01'))
    monkeypatch.chdir(tmp_path)
    write_stage(run, 'database', 'success', {'inserted': len(kinds), 'duplicate': int(duplicate), 'failed': 0, 'skipped': 0}, db)
    write_stage(run, 'summary', 'success', {key: sum(r['status'] == key for r in summary)
        for key in ('succeeded', 'already_done', 'not_needed', 'failed', 'pending')}, summary)
    write_stage(run, 'scoring', 'success', {'succeeded': len(db), 'failed': 0}, [])
    return run, collection, db, summary


def test_reader_counts_new_rows_and_verified_publisher_regions_only(monkeypatch, tmp_path, ledger):
    from wechat_pipeline_run import build_outcome

    kinds = [('浙江', 'not_needed', None, 'low_score', 2),
             ('厦门', 'succeeded', 'generated', None, 4),
             ('厦门', 'succeeded', 'direct', None, 4)]
    run, collection, _, _ = reader_fixture(monkeypatch, tmp_path, kinds, duplicate=True)
    outcome = build_outcome(run, collection, None)
    overview = outcome.get('reader_overview')
    assert overview is not None, 'committed new rows need a separate reader denominator'
    assert overview['inserted'] == 3
    assert overview['summary']['ready_generated'] == 1
    assert overview['summary']['ready_direct'] == 1
    assert overview['summary']['low_score'] == 1
    assert sum(overview['summary'].values()) == 3
    assert overview['regions'] == [{'label': '浙江', 'count': 1}, {'label': '福建·厦门', 'count': 2}]
    assert overview['region_unknown'] == 0
    actual_run = ledger.start_run(TARGET, output_root=tmp_path)
    cards = []
    result = ledger.finish_run(actual_run, outcome, output_root=tmp_path, send_fn=capture(cards))
    text = content(cards[0])
    assert '新增 3 篇中' in text and '摘要就绪 2 篇' in text and '低于 3 分 1 篇' in text
    assert '模型生成 1' in text and '短正文直接采用 1' in text
    assert '浙江 1' in text and '福建·厦门 2' in text and '中央机关' not in text
    assert actual_run['run_id'] not in text and 'SECRET' not in text + json.dumps(result)


@pytest.mark.parametrize('details,want', [
    ({'status': 'not_needed'}, 'unknown'),
    ({'status': 'not_needed', 'reason': 'body_unusable', 'score': 4}, 'body_unusable'),
    ({'status': 'pending', 'reason': 'score_missing', 'score': None}, 'score_unconfirmed'),
    ({'status': 'failed', 'score': 4}, 'failed'),
    ({'status': 'pending', 'score': 4}, 'pending'),
    ({'status': 'succeeded'}, 'ready_unknown'),
])
def test_reader_does_not_infer_low_score_from_legacy_not_needed(monkeypatch, tmp_path, details, want):
    from wechat_pipeline_run import build_outcome, write_stage

    run, collection, _, rows = reader_fixture(monkeypatch, tmp_path, [('江西', 'pending', None, None, None)])
    for key in ('score', 'reason', 'summary_method'):
        rows[0].pop(key)
    rows[0].update(details)
    write_stage(run, 'summary', 'success', {}, rows)
    summary = build_outcome(run, collection, None).get('reader_overview', {}).get('summary', {})
    assert summary.get(want) == 1
    assert sum(summary.values()) == 1


def test_reader_same_row_duplicates_count_once_and_exclude_duplicate_publisher(monkeypatch, tmp_path):
    from wechat_pipeline_run import build_outcome, write_stage

    run, collection, db, rows = reader_fixture(monkeypatch, tmp_path, [('厦门', 'succeeded', 'direct', None, 4)], duplicate=True)
    rows[0]['candidates'].append(db[-1])
    write_stage(run, 'summary', 'success', {}, rows[:1])
    overview = build_outcome(run, collection, None).get('reader_overview', {})
    assert overview.get('summary', {}).get('ready_direct') == 1
    assert overview.get('regions') == [{'label': '福建·厦门', 'count': 1}]


def test_reader_unconfirmed_commit_cannot_become_confirmed_insert(monkeypatch, tmp_path):
    from wechat_pipeline_run import build_outcome, write_stage

    run, collection, db, _ = reader_fixture(monkeypatch, tmp_path, [('浙江', 'succeeded', 'direct', None, 4)])
    db[0]['status'] = 'unknown'
    write_stage(run, 'database', 'failed', {}, db)
    outcome = build_outcome(run, collection, 'database_failed')
    assert outcome.get('reader_overview') is None
    assert outcome['database'].get('inserted') is None


def test_reader_rejects_conflicting_summary_row_or_unverified_region(monkeypatch, tmp_path):
    from wechat_pipeline_run import build_outcome, write_stage

    run, collection, db, rows = reader_fixture(monkeypatch, tmp_path, [('浙江', 'succeeded', 'direct', None, 4)])
    collection['articles'][0]['wechat_ghid'] = 'gh_forged'
    rows[0]['stored_link'] = 'https://example.test/unrelated'
    write_stage(run, 'summary', 'success', {}, rows)
    overview = build_outcome(run, collection, None).get('reader_overview', {})
    assert overview.get('summary', {}).get('unknown') == 1
    assert overview.get('regions') == [] and overview.get('region_unknown') == 1


def test_reader_optional_frozen_overview_survives_retry_and_invalid_input_is_not_leaked(ledger, monkeypatch, tmp_path):
    from wechat_pipeline_run import build_outcome

    _, collection, _, _ = reader_fixture(monkeypatch, tmp_path, [('浙江', 'not_needed', None, 'low_score', 1)])
    evidence_run = {'date': TARGET, 'run_id': 'a' * 32}
    outcome = build_outcome(evidence_run, collection, None)
    run = ledger.start_run(TARGET, output_root=tmp_path)
    cards = []
    result = ledger.finish_run(run, outcome, output_root=tmp_path, send_fn=capture(cards, success=False))
    assert result['outcome'].get('reader_overview') is not None
    outcome['reader_overview']['summary']['low_score'] = 999
    retried = ledger.retry_pending(output_root=tmp_path, send_fn=capture(cards))
    assert retried['sent'] == 1 and cards[-1] == cards[0]
    run2 = ledger.start_run(TARGET, output_root=tmp_path)
    outcome['reader_overview']['regions'][0]['label'] = 'SECRET-CREDENTIAL'
    result2 = ledger.finish_run(run2, outcome, output_root=tmp_path, send_fn=capture(cards))
    assert 'SECRET' not in json.dumps(result2) + content(cards[-1])
    assert 'reader_overview' not in result2['outcome']


@pytest.mark.parametrize('broken', ['collection_articles', 'summary_status'])
def test_reader_malformed_optional_evidence_keeps_committed_totals(monkeypatch, tmp_path, broken):
    from wechat_pipeline_run import build_outcome, write_stage

    run, collection, _, rows = reader_fixture(monkeypatch, tmp_path, [('浙江', 'succeeded', 'direct', None, 4)])
    if broken == 'collection_articles':
        collection['articles'] = None
    else:
        rows[0]['status'] = {'unexpected': 'SECRET'}
        write_stage(run, 'summary', 'success', {}, rows)
    overview = build_outcome(run, collection, None).get('reader_overview', {})
    assert overview.get('inserted') == 1
    if broken == 'collection_articles':
        assert overview.get('region_unknown') == 1
    else:
        assert overview.get('summary', {}).get('unknown') == 1
    assert 'SECRET' not in json.dumps(overview)


def test_reader_missing_candidate_link_does_not_interrupt_finalization(monkeypatch, tmp_path):
    from wechat_pipeline_run import build_outcome, write_stage

    run, collection, db, _ = reader_fixture(monkeypatch, tmp_path, [('浙江', 'succeeded', 'direct', None, 4)])
    db[0].pop('link')
    write_stage(run, 'database', 'success', {'inserted': 1, 'duplicate': 0}, db)
    outcome = build_outcome(run, collection, 'scoring_failed')
    assert outcome['error'] == 'scoring_failed'
    assert outcome['database']['inserted'] == 1
    overview = outcome.get('reader_overview', {})
    assert overview.get('summary', {}).get('unknown') == 1
    assert overview.get('region_unknown') == 1


@pytest.mark.parametrize('reports', [None, [], {'database': []}, {'database': {'counts': None}},
    {'database': {'counts': {'inserted': 1}, 'articles': None}},
    {'database': {'counts': {'inserted': 1}, 'articles': {}}}])
def test_reader_malformed_report_cannot_invent_new_row_evidence(reports):
    from wechat_reader_outcome import build_reader_overview

    assert build_reader_overview(None, reports, TARGET) is None


@pytest.mark.parametrize('summary', [None, [], {'articles': None}, {'articles': {}}])
def test_reader_malformed_summary_keeps_new_rows_unknown(monkeypatch, tmp_path, summary):
    from wechat_reader_outcome import build_reader_overview

    _, collection, db, _ = reader_fixture(monkeypatch, tmp_path, [('浙江', 'succeeded', 'direct', None, 4)])
    overview = build_reader_overview(collection, {'database': {'counts': {'inserted': 1}, 'articles': db},
                                                'summary': summary}, TARGET)
    assert overview['summary']['unknown'] == 1
    assert overview['regions'] == [{'label': '浙江', 'count': 1}]


def test_reader_optional_statistics_failure_preserves_business_result(monkeypatch, tmp_path):
    import wechat_reader_outcome
    from wechat_pipeline_run import build_outcome

    run, collection, _, _ = reader_fixture(monkeypatch, tmp_path, [('浙江', 'succeeded', 'direct', None, 4)])
    def broken(*args):
        raise RuntimeError('SECRET statistics error')
    monkeypatch.setattr(wechat_reader_outcome, 'build_reader_overview', broken)
    outcome = build_outcome(run, collection, 'scoring_failed')
    assert outcome['error'] == 'scoring_failed' and outcome['status'] == 'failed'
    assert outcome['database']['inserted'] == 1 and 'reader_overview' not in outcome
    assert 'SECRET statistics error' not in json.dumps(outcome)
