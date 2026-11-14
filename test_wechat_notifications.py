"""Drive the real ledger and scheduler through an isolated output root."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
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
    assert TARGET in text and run["run_id"] in text and "09:00" in text
    assert "成功 1" in text and "零文章 1" in text
    assert "采集 2" in text and "正文不足 1" in text
    assert "本次新增 1" in text and "缓存复用 1" in text
    assert "新增入库 0" in text and "重复 1" in text
    assert "已完成 1" in text and "0.14" in text and "0.31" in text
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
    assert "采集 0" in text and "有效正文 0" in text and "零文章 1" in text
    assert "新增入库 0" in text and "重复 未知" in text
    assert "评分（未知）" in text and "摘要（未知）" in text
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
    assert "国管局（gh_7af7ed4664f7） · 供应商余额不足" in text
    assert "勤惟宁（gh_b658f8c01c4a） · 发布时间冲突" in text
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
        assert old["run_id"] in content(messages[0])
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
        finally:
            process.terminate()
            process.wait(timeout=5)
    for _ in range(2):
        assert ledger.check_missing(now=SHANGHAI_DAY, output_root=tmp_path,
                                    send_fn=capture(messages))["status"] == "interrupted"
    assert len(messages) == 2
    assert "中断" in content(messages[-1]) and "新增入库 未知" in content(messages[-1])
    assert run["run_id"] in content(messages[-1])


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


@pytest.mark.parametrize("flag", ["--check-missing", "--retry-pending"])
def test_wechat_cli_explicitly_skips_when_disabled_without_creating_ledger(ledger, tmp_path, monkeypatch, capsys, flag):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENABLE_GOVERNMENT_WECHAT", "0")
    assert ledger.main([flag]) == 0
    assert "skipped" in capsys.readouterr().out
    assert not (tmp_path / "output").exists()
