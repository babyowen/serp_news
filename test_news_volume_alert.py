"""news_volume_alert 的单元测试。

覆盖 issue #11 测试要求章节列出的 17 个用例：
- 基线计算（纯函数 + 注入 query_fn）
- 异常检测
- 卡片构造与 CLI
- 流水线集成（run 主入口）

DB 不依赖真实 MySQL；飞书 CLI 不依赖真实 lark-cli。
运行：pytest test_news_volume_alert.py -v
"""
import json
import subprocess

import pytest

import news_volume_alert as nva


# ============== 辅助：构造 query 桩件 ==============

def make_query_fn(day_map):
    """day_map: { date_str: (total, high) } -> callable(keyword, date) -> (total, high)"""
    def _q(keyword, date):
        return day_map.get(date, (0, 0))
    return _q


# ============== 1. 基线计算 ==============

def test_baseline_4_samples_averages_correctly():
    samples = [(10, 3), (8, 1), (12, 5), (6, 2)]
    b = nva.compute_baseline(samples)
    assert b["sample_size"] == 4
    assert b["avg_total"] == (10 + 8 + 12 + 6) / 4
    assert b["avg_high"] == (3 + 1 + 5 + 2) / 4


def test_baseline_partial_samples_average_by_actual_count():
    # 3 个样本
    b3 = nva.compute_baseline([(10, 1), (8, 2), (12, 3)])
    assert b3["sample_size"] == 3
    assert b3["avg_total"] == 30 / 3
    # 2 个样本
    b2 = nva.compute_baseline([(10, 1), (8, 2)])
    assert b2["sample_size"] == 2
    assert b2["avg_total"] == 9.0
    # 1 个样本
    b1 = nva.compute_baseline([(7, 0)])
    assert b1["sample_size"] == 1
    assert b1["avg_total"] == 7.0
    assert b1["avg_high"] == 0.0


def test_baseline_zero_samples_returns_none():
    assert nva.compute_baseline([]) is None


def test_baseline_includes_zero_high_as_valid_zero():
    # 高分=0 的样本必须作为有效 0 参与高分均值
    b = nva.compute_baseline([(10, 0), (8, 0), (12, 2)])
    assert b["avg_high"] == (0 + 0 + 2) / 3


def test_collect_history_skips_dates_with_zero_total():
    # -7/-14/-21/-28 中只有 -7 和 -21 有数据
    query = make_query_fn({
        "2026-06-15": (10, 2),  # -7
        "2026-06-01": (8, 1),   # -21
        # -14 / -28 在 day_map 中不存在 → 返回 (0,0) → 视为缺失样本
    })
    b = nva.collect_history("养老", "2026-06-22", query_fn=query)
    assert b["sample_size"] == 2
    assert b["avg_total"] == 9.0


def test_collect_history_all_dates_missing_returns_none():
    query = make_query_fn({})
    assert nva.collect_history("公积金", "2026-06-22", query_fn=query) is None


# ============== 2. 异常检测 ==============

def test_anomaly_total_increase_over_20_triggers_up():
    today = (15, 2)
    baseline = {"avg_total": 10.0, "avg_high": 2.0, "sample_size": 4}
    a = nva.detect_anomalies("养老", today, baseline)
    total_a = next(x for x in a if x["metric"] == "全量新闻数")
    assert total_a["direction"] == "上升"
    assert total_a["change_rate"] == 50.0


def test_anomaly_total_decrease_over_20_triggers_down():
    today = (5, 2)
    baseline = {"avg_total": 10.0, "avg_high": 2.0, "sample_size": 4}
    a = nva.detect_anomalies("养老", today, baseline)
    total_a = next(x for x in a if x["metric"] == "全量新闻数")
    assert total_a["direction"] == "下降"
    assert total_a["change_rate"] == -50.0


def test_anomaly_exactly_20_does_not_trigger():
    today = (12, 2)  # 10 → 12 = 20% 恰好
    baseline = {"avg_total": 10.0, "avg_high": 2.0, "sample_size": 4}
    a = nva.detect_anomalies("养老", today, baseline)
    metrics = {x["metric"] for x in a}
    assert "全量新闻数" not in metrics


def test_anomaly_below_threshold_does_not_trigger():
    today = (11, 2)  # 10 → 11 = 10%
    baseline = {"avg_total": 10.0, "avg_high": 2.0, "sample_size": 4}
    a = nva.detect_anomalies("养老", today, baseline)
    assert a == []


def test_anomaly_zero_baseline_zero_today_does_not_trigger():
    # 高分基线 0、今日 0：正常
    today = (10, 0)
    baseline = {"avg_total": 10.0, "avg_high": 0.0, "sample_size": 3}
    a = nva.detect_anomalies("养老", today, baseline)
    metrics = {x["metric"] for x in a}
    assert "高分新闻数" not in metrics


def test_anomaly_zero_baseline_positive_today_triggers_new():
    today = (10, 3)  # 高分基线 0、今日 3
    baseline = {"avg_total": 10.0, "avg_high": 0.0, "sample_size": 3}
    a = nva.detect_anomalies("养老", today, baseline)
    high_a = next(x for x in a if x["metric"] == "高分新闻数")
    assert high_a["direction"] == "新增"
    assert high_a["change_rate"] is None
    assert high_a["baseline_avg"] == 0.0


def test_anomaly_high_score_independent_from_total():
    # 全量正常、高分异常的情况独立触发
    today = (10, 8)  # 高分 2→8 = 300%
    baseline = {"avg_total": 10.0, "avg_high": 2.0, "sample_size": 4}
    a = nva.detect_anomalies("养老", today, baseline)
    metrics = {x["metric"] for x in a}
    assert "高分新闻数" in metrics
    assert "全量新闻数" not in metrics


def test_anomaly_no_baseline_returns_empty():
    today = (10, 3)
    assert nva.detect_anomalies("养老", today, None) == []


# ============== 3. 卡片构造与 CLI ==============

def test_build_card_contains_all_required_fields():
    anomalies = [
        {"keyword": "养老", "metric": "全量新闻数", "today": 15,
         "baseline_avg": 10.0, "sample_size": 4, "change_rate": 50.0,
         "direction": "上升"},
        {"keyword": "养老", "metric": "高分新闻数", "today": 3,
         "baseline_avg": 0.0, "sample_size": 4, "change_rate": None,
         "direction": "新增"},
        {"keyword": "公积金", "metric": "全量新闻数", "today": 4,
         "baseline_avg": 10.0, "sample_size": 3, "change_rate": -60.0,
         "direction": "下降"},
    ]
    card_json = nva.build_card("2026-06-22", anomalies)
    card = json.loads(card_json)
    assert card["header"]["template"] == "orange"
    assert "2026-06-22" in card["header"]["title"]["content"]
    # 卡片正文应是字符串，包含关键词和指标名
    blob = json.dumps(card, ensure_ascii=False)
    for kw in ("养老", "公积金"):
        assert kw in blob
    for metric in ("全量新闻数", "高分新闻数"):
        assert metric in blob
    assert "基线为 0 / 新增" in blob


def test_send_feishu_without_user_id_does_not_call_cli(monkeypatch, capsys):
    monkeypatch.delenv("FEISHU_USER_ID", raising=False)
    called = {"n": 0}

    def fake_run(*a, **kw):
        called["n"] += 1
        raise AssertionError("不应该调用 subprocess.run")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert nva.send_feishu("{}") is False
    assert called["n"] == 0
    assert "FEISHU_USER_ID" in capsys.readouterr().out


def test_send_feishu_cli_failure_returns_false_and_does_not_raise(monkeypatch):
    monkeypatch.setenv("FEISHU_USER_ID", "ou_test")

    def fake_run(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="lark-cli", timeout=30)

    monkeypatch.setattr(subprocess, "run", fake_run)
    # 不抛异常，返回 False
    assert nva.send_feishu("{}") is False


def test_send_feishu_success(monkeypatch):
    monkeypatch.setenv("FEISHU_USER_ID", "ou_test")

    class FakeResult:
        returncode = 0
        stderr = ""

    def fake_run(*a, **kw):
        return FakeResult()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert nva.send_feishu("{}") is True


# ============== 4. 流水线集成 ==============

def test_run_no_anomaly_does_not_send():
    # 今日与基线接近，无异常 → 不调 sender
    query = make_query_fn({
        "2026-06-22": (10, 2),   # today
        "2026-06-15": (10, 2),   # -7
        "2026-06-08": (9, 1),    # -14
        "2026-06-01": (11, 3),   # -21
        "2026-05-25": (10, 2),   # -28
    })
    sent = {"n": 0, "card": None}

    def fake_send(card_json):
        sent["n"] += 1
        sent["card"] = card_json
        return True

    anomalies = nva.run("2026-06-22", ["养老"],
                        query_fn=query, send_fn=fake_send)
    assert anomalies == []
    assert sent["n"] == 0


def test_run_aggregates_multiple_keywords_one_card():
    query_map = {
        "养老": {
            "2026-06-22": (20, 2),   # 全量翻倍触发上升
            "2026-06-15": (10, 2),
            "2026-06-08": (10, 2),
            "2026-06-01": (10, 2),
            "2026-05-25": (10, 2),
        },
        "公积金": {
            "2026-06-22": (2, 0),    # 全量从 10 砍到 2 触发下降
            "2026-06-15": (10, 1),
            "2026-06-08": (10, 1),
            "2026-06-01": (10, 1),
            "2026-05-25": (10, 1),
        },
    }

    def query(keyword, date):
        return query_map[keyword].get(date, (0, 0))

    sent = {"n": 0, "card": None}

    def fake_send(card_json):
        sent["n"] += 1
        sent["card"] = card_json
        return True

    anomalies = nva.run("2026-06-22", ["养老", "公积金"],
                        query_fn=query, send_fn=fake_send)
    # 多关键词、多指标异常 → 只发一张卡片
    assert sent["n"] == 1
    assert len(anomalies) >= 2
    blob = sent["card"]
    assert "养老" in blob and "公积金" in blob
    # 卡片是合法 JSON
    card = json.loads(blob)
    assert card["header"]["template"] == "orange"


def test_run_swallows_per_keyword_db_errors_and_continues():
    def query(keyword, date):
        if keyword == "养老":
            raise RuntimeError("DB down")
        return (10, 2) if date == "2026-06-22" else (10, 2)

    sent = {"n": 0}

    def fake_send(card_json):
        sent["n"] += 1
        return True

    # 不应抛异常；公积金按 4 样本基线 = 10 计算，今日 10 → 不触发
    anomalies = nva.run("2026-06-22", ["养老", "公积金"],
                        query_fn=query, send_fn=fake_send)
    assert anomalies == []
    assert sent["n"] == 0


def test_run_target_date_without_history_still_runs_today():
    # 今日有数据，但历史全缺 → 该关键词跳过告警（无基线），不抛
    query = make_query_fn({"2026-06-22": (10, 3)})
    sent = {"n": 0}
    anomalies = nva.run("2026-06-22", ["养老"],
                        query_fn=query, send_fn=lambda _: sent.__setitem__("n", 1))
    assert anomalies == []
    assert sent["n"] == 0


# ============== 5. 正常项汇总（新功能）==============

def test_detect_returns_both_anomalies_and_normals():
    today = (10, 2)  # 全量与基线持平；高分与基线持平
    baseline = {"avg_total": 10.0, "avg_high": 2.0, "sample_size": 4}
    anomalies, normals = nva.detect("养老", today, baseline)
    assert anomalies == []
    assert len(normals) == 2
    assert {n["metric"] for n in normals} == {"全量新闻数", "高分新闻数"}


def test_detect_mixed_anomaly_and_normal():
    today = (15, 2)  # 全量 +50% 异常；高分持平正常
    baseline = {"avg_total": 10.0, "avg_high": 2.0, "sample_size": 4}
    anomalies, normals = nva.detect("养老", today, baseline)
    assert len(anomalies) == 1
    assert anomalies[0]["metric"] == "全量新闻数"
    assert len(normals) == 1
    assert normals[0]["metric"] == "高分新闻数"


def test_build_card_with_normals_renders_summary_section():
    anomalies = [
        {"keyword": "养老", "metric": "全量新闻数", "today": 15,
         "baseline_avg": 10.0, "sample_size": 4, "change_rate": 50.0,
         "direction": "上升"},
    ]
    normals = [
        {"keyword": "养老", "metric": "高分新闻数", "today": 2,
         "baseline_avg": 2.0, "sample_size": 4, "change_rate": 0.0},
        {"keyword": "公积金", "metric": "全量新闻数", "today": 10,
         "baseline_avg": 9.5, "sample_size": 4, "change_rate": 5.3},
    ]
    card_json = nva.build_card("2026-06-22", anomalies, normals)
    blob = json.dumps(json.loads(card_json), ensure_ascii=False)
    assert "正常项（2 个）" in blob
    assert "养老" in blob
    assert "公积金" in blob


def test_build_card_without_normals_omits_section():
    anomalies = [
        {"keyword": "养老", "metric": "全量新闻数", "today": 15,
         "baseline_avg": 10.0, "sample_size": 4, "change_rate": 50.0,
         "direction": "上升"},
    ]
    blob = json.dumps(json.loads(nva.build_card("2026-06-22", anomalies)), ensure_ascii=False)
    assert "正常项" not in blob


def test_run_collects_normals_and_passes_to_card():
    # 全量翻倍（异常）、高分持平（正常）
    query = make_query_fn({
        "2026-06-22": (20, 2),
        "2026-06-15": (10, 2),
        "2026-06-08": (10, 2),
        "2026-06-01": (10, 2),
        "2026-05-25": (10, 2),
    })
    captured = {"card": None, "anomalies": None}
    def fake_send(card_json):
        captured["card"] = card_json
        return True
    anomalies = nva.run("2026-06-22", ["养老"], query_fn=query, send_fn=fake_send)
    captured["anomalies"] = anomalies
    blob = json.dumps(json.loads(captured["card"]), ensure_ascii=False).replace("**", "")
    assert "异常项 1 个" in blob
    assert "正常项（1 个）" in blob


# ============== 6. 综合边界与回归保护 ==============

def test_offset_date_handles_cross_month_and_year():
    # 跨月：6 月 1 日 - 7 天 = 5 月 25 日
    assert nva._offset_date("2026-06-01", 7) == "2026-05-25"
    # 跨年：1 月 5 日 - 7 天 = 去年 12 月 29 日
    assert nva._offset_date("2026-01-05", 7) == "2025-12-29"
    # 跨闰年 2 月：2024-03-01 - 7 天 = 2024-02-23（2024 是闰年）
    assert nva._offset_date("2024-03-01", 7) == "2024-02-23"
    # 28 天跨度：6 月 22 - 28 = 5 月 25
    assert nva._offset_date("2026-06-22", 28) == "2026-05-25"


def test_change_rate_function_directly():
    assert nva._change_rate(15, 10) == 50.0
    assert nva._change_rate(5, 10) == -50.0
    assert nva._change_rate(10, 10) == 0.0
    assert nva._change_rate(0, 10) == -100.0
    # 基线 0 返回 None（交由调用方区分）
    assert nva._change_rate(5, 0) is None
    assert nva._change_rate(0, 0) is None


def test_fetch_counts_sql_shape_with_mock(monkeypatch):
    """mock pymysql 连接，验证批量 SQL 的 placeholder 数量和参数顺序。"""
    captured = {}

    class FakeCursor:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = list(params)
        def fetchall(self):
            return []

    class FakeConn:
        def cursor(self):
            return FakeCursor()
        def close(self):
            pass

    monkeypatch.setattr(nva, "get_connection", lambda autocommit=True: FakeConn())
    nva.fetch_counts(["养老", "公积金"], "2026-06-22")

    sql = captured["sql"]
    # placeholders: 1 (HIGH_SCORE_MIN) + 2 keywords + 5 dates (today + 4 history)
    assert sql.count("%s") == 1 + 2 + 5
    assert "GROUP BY keyword, fetchdate" in sql
    assert "keyword IN" in sql and "fetchdate IN" in sql
    assert "score IS NOT NULL AND score >= %s" in sql

    params = captured["params"]
    assert params[0] == nva.HIGH_SCORE_MIN  # 第一个参数是高分阈值
    assert "养老" in params and "公积金" in params
    # 5 个日期：今天 + -7/-14/-21/-28
    assert "2026-06-22" in params
    assert "2026-06-15" in params  # -7
    assert "2026-06-01" in params  # -21


def test_make_query_fn_from_counts_defaults_missing_to_zero():
    counts = {("养老", "2026-06-22"): (10, 3)}
    q = nva._make_query_fn_from_counts(counts)
    assert q("养老", "2026-06-22") == (10, 3)
    # 不在 dict 中的组合 → 视为缺失样本 (0, 0)
    assert q("公积金", "2026-06-22") == (0, 0)
    assert q("养老", "2026-06-15") == (0, 0)


def test_detect_anomalies_back_compat_returns_just_anomaly_list():
    """detect_anomalies 必须仍只返回异常列表（向后兼容老调用方）。"""
    today = (15, 2)
    baseline = {"avg_total": 10.0, "avg_high": 2.0, "sample_size": 4}
    result = nva.detect_anomalies("养老", today, baseline)
    assert isinstance(result, list)
    # 全量上升 50% 异常；高分持平 → 仅 1 个异常项
    assert len(result) == 1
    assert result[0]["metric"] == "全量新闻数"


def test_run_with_empty_keywords_does_not_send():
    sent = {"n": 0}
    anomalies = nva.run("2026-06-22", [],
                        query_fn=lambda k, d: (0, 0),
                        send_fn=lambda c: sent.__setitem__("n", 1))
    assert anomalies == []
    assert sent["n"] == 0  # 无异常 → 不调 sender


def test_build_card_renders_baseline_zero_new_label():
    """change_rate=None 时卡片必须显示"基线为 0 / 新增"。"""
    anomalies = [
        {"keyword": "公积金", "metric": "高分新闻数", "today": 3,
         "baseline_avg": 0.0, "sample_size": 4, "change_rate": None,
         "direction": "新增"},
    ]
    blob = nva.build_card("2026-06-22", anomalies)
    assert "基线为 0 / 新增" in blob


def test_module_constants_locked_at_issue_values():
    """锁定 issue 规定的常量，防止未来误改阈值/高分线/历史窗口。"""
    assert nva.THRESHOLD_PCT == 20
    assert nva.HIGH_SCORE_MIN == 4
    assert nva.BASELINE_OFFSETS_DAYS == (7, 14, 21, 28)
    assert nva.METRIC_ICONS == {"全量新闻数": "📰", "高分新闻数": "⭐"}


def test_send_feishu_cli_nonzero_return_code_returns_false(monkeypatch):
    monkeypatch.setenv("FEISHU_USER_ID", "ou_test")

    class FakeResult:
        returncode = 1
        stderr = "validation failed"

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    assert nva.send_feishu("{}") is False


# ============== API 路径（Linux 服务器场景）==============

class _FakeResp:
    def __init__(self, payload, text=None):
        self._p = payload
        self.text = text or json.dumps(payload)
    def json(self):
        return self._p


def _api_endpoints(monkeypatch, token_payload, send_payload, counter):
    """把 requests.post 桩成两段：token URL 返回 token_payload，message URL 返回 send_payload。"""
    import requests as req_mod

    def fake_post(url, *args, **kwargs):
        counter["n"] += 1
        if "tenant_access_token" in url:
            counter["token_calls"] += 1
            return _FakeResp(token_payload)
        counter["send_calls"] += 1
        # 校验发送请求体格式（content 必须是 JSON 字符串）
        body = kwargs.get("json") or {}
        if body.get("msg_type") == "interactive":
            json.loads(body["content"])  # content 必须是合法 JSON 字符串
        return _FakeResp(send_payload)

    monkeypatch.setattr(req_mod, "post", fake_post)


def test_send_feishu_via_api_when_cli_missing(monkeypatch):
    """CLI 路径不存在 + 配了 APP_ID/SECRET → 走 API。"""
    monkeypatch.setenv("FEISHU_USER_ID", "ou_test")
    monkeypatch.setenv("LARK_CLI_PATH", "/nonexistent/lark-cli")
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret_test")

    counter = {"n": 0, "token_calls": 0, "send_calls": 0}
    _api_endpoints(monkeypatch,
                   token_payload={"tenant_access_token": "tok_abc"},
                   send_payload={"code": 0, "msg": "ok"},
                   counter=counter)

    assert nva.send_feishu('{"config":{}}') is True
    assert counter["token_calls"] == 1
    assert counter["send_calls"] == 1


def test_send_feishu_via_api_token_failure_returns_false(monkeypatch):
    """换 token 时 API 返回无 tenant_access_token → False。"""
    monkeypatch.setenv("FEISHU_USER_ID", "ou_test")
    monkeypatch.setenv("LARK_CLI_PATH", "/nonexistent/lark-cli")
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "wrong")

    counter = {"n": 0, "token_calls": 0, "send_calls": 0}
    _api_endpoints(monkeypatch,
                   token_payload={"code": 99991663, "msg": "invalid app_id"},
                   send_payload={"code": 0},
                   counter=counter)

    assert nva.send_feishu("{}") is False
    assert counter["send_calls"] == 0  # token 都没拿到，不发消息


def test_send_feishu_via_api_send_failure_returns_false(monkeypatch):
    """发送消息时 API 返回 code != 0 → False。"""
    monkeypatch.setenv("FEISHU_USER_ID", "ou_test")
    monkeypatch.setenv("LARK_CLI_PATH", "/nonexistent/lark-cli")
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret_test")

    counter = {"n": 0, "token_calls": 0, "send_calls": 0}
    _api_endpoints(monkeypatch,
                   token_payload={"tenant_access_token": "tok_abc"},
                   send_payload={"code": 230002, "msg": "user not found"},
                   counter=counter)

    assert nva.send_feishu("{}") is False


def test_send_feishu_falls_back_to_api_when_cli_fails(monkeypatch):
    """CLI 存在但发送失败 → 自动降级到 API。"""
    monkeypatch.setenv("FEISHU_USER_ID", "ou_test")
    monkeypatch.setenv("LARK_CLI_PATH", "/bin/echo")  # 真实存在但不是 lark-cli
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret_test")

    # 让 _send_feishu_via_cli 调用的 subprocess.run 返回非零
    class FakeResult:
        returncode = 1
        stderr = "auth fail"
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())

    counter = {"n": 0, "token_calls": 0, "send_calls": 0}
    _api_endpoints(monkeypatch,
                   token_payload={"tenant_access_token": "tok_abc"},
                   send_payload={"code": 0},
                   counter=counter)

    assert nva.send_feishu("{}") is True
    assert counter["token_calls"] == 1
    assert counter["send_calls"] == 1


def test_send_feishu_via_api_network_exception_returns_false(monkeypatch):
    """API 网络异常 → 不抛、返回 False。"""
    import requests as req_mod
    monkeypatch.setenv("FEISHU_USER_ID", "ou_test")
    monkeypatch.setenv("LARK_CLI_PATH", "/nonexistent/lark-cli")
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret_test")

    def boom(*a, **kw):
        raise req_mod.ConnectionError("network down")
    monkeypatch.setattr(req_mod, "post", boom)

    assert nva.send_feishu("{}") is False


def test_send_feishu_no_sender_configured_returns_false(monkeypatch):
    """既无 CLI 也无 APP_ID/SECRET → 返回 False、不抛。"""
    monkeypatch.setenv("FEISHU_USER_ID", "ou_test")
    monkeypatch.setenv("LARK_CLI_PATH", "/nonexistent/lark-cli")
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)

    assert nva.send_feishu("{}") is False


def test_build_card_preserves_keyword_input_order():
    """异常项在卡片中必须按传入顺序展示，不重排。"""
    anomalies = [
        {"keyword": "公积金", "metric": "全量新闻数", "today": 1,
         "baseline_avg": 10.0, "sample_size": 4, "change_rate": -90.0,
         "direction": "下降"},
        {"keyword": "养老", "metric": "全量新闻数", "today": 1,
         "baseline_avg": 10.0, "sample_size": 4, "change_rate": -90.0,
         "direction": "下降"},
        {"keyword": "数字政务", "metric": "全量新闻数", "today": 1,
         "baseline_avg": 10.0, "sample_size": 4, "change_rate": -90.0,
         "direction": "下降"},
    ]
    blob = nva.build_card("2026-06-22", anomalies)
    i_gjj = blob.find("公积金")
    i_yl = blob.find("养老")
    i_sz = blob.find("数字政务")
    assert i_gjj < i_yl < i_sz


# ============== 7. main.py 流水线集成（前置异常不阻断预警）==============

def test_main_runs_alert_when_prior_step_raises(monkeypatch, tmp_path):
    """issue #11 验收第 1 条：前置步骤抛异常时，预警必须仍被调用。

    场景：monkeypatch safe_subprocess_run 让步骤 4 的 run_step 抛异常，
    main() 内部主 try 应捕获该异常、标记失败、并在收尾路径调用预警。
    """
    import main as main_module

    alert = {"n": 0, "date": None, "keywords": None}

    def fake_alert(target_date, keywords, **kwargs):
        alert["n"] += 1
        alert["date"] = target_date
        alert["keywords"] = list(keywords)
        return []

    def fake_safe_subprocess_run(*a, **kw):
        raise RuntimeError("模拟前置步骤抛异常")

    monkeypatch.setattr(main_module, "run_volume_alert", fake_alert)
    monkeypatch.setattr(main_module, "safe_subprocess_run", fake_safe_subprocess_run)
    # 把批次日志重定向到 tmp，避免污染 output/
    log_path = tmp_path / "run.log"
    monkeypatch.setenv("RUN_LOG_PATH", str(log_path))

    result = main_module.main("2026-06-22")

    assert alert["n"] == 1, "前置异常后预警必须被调用一次"
    assert alert["date"] == "2026-06-22"
    assert alert["keywords"] == list(main_module.DEFAULT_KEYWORDS)
    # 主流程标记失败
    assert result is False


def test_main_runs_alert_on_full_success_path(monkeypatch, tmp_path):
    """对照测试：无异常的正常路径下，预警也被调用一次且返回 True。"""
    import main as main_module

    alert = {"n": 0}
    monkeypatch.setattr(main_module, "run_volume_alert",
                        lambda target_date, keywords, **kw: alert.__setitem__("n", alert["n"] + 1) or [])
    # safe_subprocess_run 返回 True 模拟所有子步骤成功
    monkeypatch.setattr(main_module, "safe_subprocess_run",
                        lambda *a, **k: True)
    monkeypatch.setenv("RUN_LOG_PATH", str(tmp_path / "run.log"))

    result = main_module.main("2026-06-22")

    assert alert["n"] == 1
    assert result is True


def test_main_alert_failure_does_not_flip_main_exit_code(monkeypatch, tmp_path):
    """预警自身抛异常时不应改变 main 的退出码（main_success 仍为 True）。"""
    import main as main_module

    def boom_alert(*a, **kw):
        raise RuntimeError("预警内部故障")
    monkeypatch.setattr(main_module, "run_volume_alert", boom_alert)
    monkeypatch.setattr(main_module, "safe_subprocess_run",
                        lambda *a, **k: True)
    monkeypatch.setenv("RUN_LOG_PATH", str(tmp_path / "run.log"))

    result = main_module.main("2026-06-22")
    # 主流程仍成功，预警故障被吞
    assert result is True
