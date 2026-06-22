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
