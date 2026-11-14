"""Offline fixtures matching the provider's history and article detail responses."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from news_freshness import eligible


DAY = "2026-11-13"
GOV = "gh_0123456789ab"
CITY = "gh_0123456789cd"
BIZ = "MzA3MDc2NzA4Nw=="
OTHER_BIZ = "Mzg2MjU5ODk0NA=="
BODY = "会议通报了全市公共机构能耗情况，并部署了下一阶段节能改造工作。"
LIST_EDGE = {
    "before": (1794499199, "2026-11-12 23:59:59"),
    "start": (1794499200, "2026-11-13 00:00:00"),
    "end": (1794585599, "2026-11-13 23:59:59"),
    "after": (1794585600, "2026-11-14 00:00:00"),
}


class Provider:
    def __init__(self, pages=None, bodies=None, details=None):
        self.pages = pages or {}
        self.bodies = bodies or {}
        self.details = details or {}
        self.calls = []

    def post_history(self, ghid, offset):
        self.calls.append(("history", ghid, offset))
        return self.pages[ghid, offset].pop(0)

    def fetch_body_html(self, url):
        self.calls.append(("http", url))
        return self.bodies.get(url, f'<div id="js_content"><p>{BODY}</p></div>')

    def article_detail(self, url):
        self.calls.append(("detail", url))
        return self.details[url].pop(0)


def account(ghid=GOV, biz=BIZ, name="某机关事务管理局"):
    return {"ghid": ghid, "biz": biz, "name": name, "enabled": True}


def registry(tmp_path, *accounts):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"version": 1, "keyword": "江苏机关事务",
                                "accounts": list(accounts)}, ensure_ascii=False))
    return path


def article(mid, idx=1, when="start", biz=BIZ, *, title=None):
    epoch, formatted = LIST_EDGE[when]
    return {"position": idx, "url": f"https://mp.weixin.qq.com/s?__biz={biz}&mid={mid}&idx={idx}&sn=abcd#rd",
            "post_time": epoch, "post_time_str": formatted, "title": title or f"文章{mid}-{idx}",
            "digest": "列表中的摘要不能当作正文", "appmsgid": mid, "item_show_type": 0, "types": 9}


def page(*entries, ghid=GOV, offset="", end=1, cost=0.14):
    return {"code": 0, "msg": "", "cost_money": cost, "remain_money": 2.47,
            "data": list(entries), "offset": offset, "is_end": end, "error_msg": "",
            "nickname": "实测示例账号", "ghid": ghid}


def detail(item, *, content=BODY, ghid=GOV, when="start", **fields):
    return {"code": 0, "cost_money": 0.03, "remain_money": 2.33,
            "title": item["title"], "url": item["url"], "user_name": ghid,
            "idx": str(item["position"]), "mid": str(item["appmsgid"]), "biz": BIZ,
            "pubtime": LIST_EDGE[when][0], "content": content, "content_multi_text": "",
            **fields}


def collect(tmp_path, client, manifest=None):
    from wechat_collector import collect_wechat

    return collect_wechat(DAY, registry_path=manifest or registry(tmp_path, account()),
                          client=client, output_root=tmp_path / "output")


def test_wechat_registry_contains_verified_sources_and_excludes_ningbo():
    from wechat_collector import load_registry

    official = load_registry()
    enabled = [a for a in official["accounts"] if a["enabled"]]
    assert len(enabled) == 38
    assert {a["level"]: sum(b["level"] == a["level"] for b in enabled) for a in enabled} == {
        "national": 1, "province": 25, "city": 12,
    }
    assert [a["region"] for a in official["accounts"] if not a["enabled"]] == ["宁波"]
    assert any(a["name"] == "勤惟宁" for a in enabled)


def test_wechat_paginates_multi_article_posts_across_beijing_day_boundaries(tmp_path):
    midnight, second_midnight = article(100, 1), article(100, 2)
    last_second = article(101, when="end")
    client = Provider({(GOV, ""): [page(article(102, when="after"), last_second, offset="next", end=0)],
                       (GOV, "next"): [page(midnight, second_midnight, article(99, when="before"),
                                            offset="old", end=0)]})
    report = collect(tmp_path, client)
    assert report["date"] == DAY
    assert report["complete"]
    assert report["accounts"][0]["status"] == "ok"
    assert report["accounts"][0]["count"] == 3
    assert report["api_cost"] == report["total_api_cost"] == 0.28
    assert {row["wechat_article_id"] for row in report["articles"]} == {
        f"{BIZ}/100/1", f"{BIZ}/100/2", f"{BIZ}/101/1",
    }
    assert [call for call in client.calls if call[0] == "history"] == [
        ("history", GOV, ""), ("history", GOV, "next"),
    ]
    for row in report["articles"]:
        assert (row["keyword"], row["main_keyword"], row["source"], row["search_keyword"]) == (
            "江苏机关事务", "江苏机关事务", "某机关事务管理局", "某机关事务管理局",
        )
        assert row["wechat_ghid"] == GOV
        assert row["sourceapi"] == "极致了api"
        assert row["date"] == row["fetchdate"] == DAY and eligible(row)
        assert row["wordcount"] == len(BODY) and row["content"] == BODY
        assert row["link"].endswith("&sn=abcd#rd")
    diagnostic = tmp_path / "output" / DAY / "diagnostics" / "wechat_collection.json"
    assert json.loads(diagnostic.read_text()) == report


@pytest.mark.parametrize("bad_page", [
    page(article(100, biz=OTHER_BIZ)),
    page(article(100), ghid=CITY),
    page(article(100), article(101, when="end"), article(102, when="after")),
    {**page(article(100)), "error_msg": "possibly redirected"},
    {**page(article(100)), "data": None},
    page({**article(100), "post_time_str": "2026-11-14 00:00:01"}),
    page({**article(100), "url": article(100)["url"].replace("mp.weixin.qq.com", "mp.weixin.qq.com.example.net")}),
])
def test_wechat_abnormal_page_does_not_fetch_unverified_articles(tmp_path, bad_page):
    client = Provider({(GOV, ""): [bad_page]})
    report = collect(tmp_path, client)
    assert not report["complete"]
    assert report["accounts"][0]["status"] == "failed"
    assert report["articles"] == []
    assert report["api_cost"] == 0.14
    assert client.calls == [("history", GOV, "")]


@pytest.mark.parametrize("continuation", [
    page(article(100), offset="cursor", end=0),
    page(article(101), offset="cursor", end=0),
    page(article(100), offset="different", end=0),
    page(article(101, when="after"), offset="different", end=0),
])
def test_wechat_stale_pagination_retains_known_target_articles(tmp_path, continuation):
    client = Provider({(GOV, ""): [page(article(100), offset="cursor", end=0)],
                       (GOV, "cursor"): [continuation]})
    report = collect(tmp_path, client)
    assert not report["complete"]
    assert report["accounts"][0]["status"] == "failed"
    assert [row["title"] for row in report["articles"]] == ["文章100-1"]
    assert report["api_cost"] == 0.28


@pytest.mark.parametrize(("ending", "status"), [
    (page(), "empty"),
    (page(article(99, when="before"), offset="more", end=0), "empty"),
    ({"code": 110, "msg": "no articles", "cost_money": 0.14}, "failed"),
])
def test_wechat_distinguishes_verified_empty_from_initial_provider_error(tmp_path, ending, status):
    report = collect(tmp_path, Provider({(GOV, ""): [ending]}))
    assert report["complete"] == (status == "empty")
    assert report["accounts"][0]["status"] == status
    assert report["accounts"][0]["count"] == 0


def test_wechat_retries_only_failed_account_and_body_and_separates_costs(tmp_path):
    usable, blocked = article(100), article(101)
    manifest = registry(tmp_path, account(), account(CITY, OTHER_BIZ, "某市机关事务"))
    client = Provider(
        {(GOV, ""): [page(usable, blocked)],
         (CITY, ""): [{"code": 104, "msg": "not found", "cost_money": 0.14}, page(ghid=CITY)]},
        bodies={blocked["url"]: '<div id="js_content">环境异常，请完成验证</div>'},
        details={blocked["url"]: [{"code": 101, "cost_money": 0.03}, detail(blocked)]},
    )
    failed = collect(tmp_path, client, manifest)
    assert not failed["complete"]
    assert failed["api_cost"] == 0.31
    assert [row["wechat_content_status"] for row in failed["articles"]] == ["ok", "failed"]
    client.calls.clear()
    recovered = collect(tmp_path, client, manifest)
    assert recovered["complete"]
    assert recovered["api_cost"] == 0.17 and recovered["total_api_cost"] == 0.48
    assert recovered["accounts"][0]["api_cost"] == 0.03
    assert recovered["accounts"][0]["total_api_cost"] == 0.20
    assert recovered["collected_count"] == 0 and recovered["reused_count"] == 2
    assert all(row["wechat_content_status"] == "ok" and eligible(row) for row in recovered["articles"])
    assert client.calls == [("http", blocked["url"]), ("detail", blocked["url"]), ("history", CITY, "")]
    client.calls.clear()
    cached = collect(tmp_path, client, manifest)
    assert cached["complete"] and cached["api_cost"] == 0 and cached["total_api_cost"] == 0.48
    assert not client.calls


def test_wechat_completed_pages_resume_from_saved_cursor(tmp_path, no_retry_wait):
    client = Provider({(GOV, ""): [page(article(100), offset="cursor", end=0)],
                       (GOV, "cursor"): [{"code": 2003}] * 3 + [page(article(101))]})
    report = collect(tmp_path, client)
    assert not report["complete"]
    assert report["articles"][0]["content"] == BODY
    client.calls.clear()
    report = collect(tmp_path, client)
    assert report["complete"] and report["api_cost"] == 0.14
    assert client.calls == [("history", GOV, "cursor"), ("http", article(101)["url"])]


@pytest.mark.parametrize(("update", "status"), [
    ({"user_name": CITY}, "failed"),
    ({"biz": OTHER_BIZ}, "failed"),
    ({"idx": "2"}, "failed"),
    ({"url": article(101)["url"]}, "failed"),
    ({"pubtime": LIST_EDGE["before"][0]}, "failed"),
    ({"pubtime": ""}, "failed"),
    ({"content": ""}, "insufficient"),
    ({"content": "您访问的页面不存在"}, "insufficient"),
    ({"content": "", "content_multi_text": f"<p>{BODY}</p>"}, "ok"),
])
def test_wechat_detail_requires_matching_identity_and_publication_evidence(tmp_path, update, status):
    entry = article(100)
    fallback = detail(entry)
    fallback.update(update)
    client = Provider({(GOV, ""): [page(entry)]},
                      bodies={entry["url"]: '<div class="weui-msg">环境异常</div>'},
                      details={entry["url"]: [fallback]})
    report = collect(tmp_path, client)
    assert report["complete"] == (status != "failed")
    row = report["articles"][0]
    assert row["wechat_content_status"] == status
    assert row["content"] == (BODY if status == "ok" else "")
    assert report["api_cost"] == 0.17
    if status != "failed":
        client.calls.clear()
        assert collect(tmp_path, client)["complete"]
        assert not client.calls
    if update.get("pubtime") == LIST_EDGE["before"][0]:
        assert not eligible(row) and row["publication_check"]["reason"] == "conflicting_publication_dates"


def test_wechat_verified_nontext_http_article_retains_link_without_paid_fallback(tmp_path):
    entry = article(100)
    client = Provider({(GOV, ""): [page(entry)]},
                      bodies={entry["url"]: '<div id="js_content"><img src="picture"></div>'})
    report = collect(tmp_path, client)
    row = report["articles"][0]
    assert report["complete"]
    assert row["link"] == entry["url"] and row["wechat_content_status"] == "insufficient"
    assert report["api_cost"] == 0.14


def test_wechat_missing_credentials_fail_without_network(tmp_path, monkeypatch):
    monkeypatch.delenv("WECHAT_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_API_VERIFYCODE", raising=False)
    report = collect(tmp_path, None)
    assert not report["complete"] and report["api_cost"] == 0
    assert report["accounts"][0]["status"] == "failed"
    assert "WECHAT_API_KEY" in report["accounts"][0]["error"]


def test_wechat_provider_error_and_transport_url_never_expose_credentials(tmp_path, monkeypatch, no_retry_wait):
    import requests

    monkeypatch.setenv("WECHAT_API_KEY", "secret-test-api-key")
    monkeypatch.setenv("WECHAT_API_VERIFYCODE", "secret-test-verify-code")
    requests_made = []

    class Response:
        status_code = 200

        def json(self):
            return {"code": 10002, "cost_money": 0.14,
                    "msg": "secret-test-api-key secret-test-verify-code"}

    def wire(self, method, url, **kwargs):
        requests_made.append((method, url, kwargs.get("json")))
        if len(requests_made) == 1:
            return Response()
        raise requests.ConnectionError("https://www.dajiala.com/?key=secret-test-api-key"
                                       "&verifycode=secret-test-verify-code")

    monkeypatch.setattr("requests.Session.request", wire)
    for _ in range(2):
        report = collect(tmp_path, None)
        assert not report["complete"]
        assert "secret-test" not in json.dumps(report)
        assert all("secret-test" not in p.read_text() for p in tmp_path.rglob("wechat*.json"))
    assert requests_made[0] == (
        "POST", "https://www.dajiala.com/fbmain/monitor/v3/post_history",
        {"ghid": GOV, "url": "", "nickname": "", "offset": "", "key": "secret-test-api-key",
         "verifycode": "secret-test-verify-code"},
    )
    assert report["api_cost"] == 0 and report["total_api_cost"] == 0.14


def test_wechat_registry_change_invalidates_only_replaced_identity(tmp_path):
    manifest = registry(tmp_path, account(), account(CITY, OTHER_BIZ, "旧名字"))
    client = Provider({(GOV, ""): [page()], (CITY, ""): [page(ghid=CITY), page(ghid=CITY)]})
    first = collect(tmp_path, client, manifest)
    manifest = registry(tmp_path, account(name="新名字"), account(CITY, "MzkxNjY0NTcwNA==", "新号"))
    client.calls.clear()
    second = collect(tmp_path, client, manifest)
    assert first["complete"] and second["complete"]
    assert first["registry_sha256"] != second["registry_sha256"]
    assert second["total_api_cost"] == 0.42
    assert client.calls == [("history", CITY, "")]


def test_wechat_same_date_lock_reuses_work_of_concurrent_invocation(tmp_path):
    manifest = registry(tmp_path, account())
    entered, release = Event(), Event()

    class HeldProvider(Provider):
        def post_history(self, ghid, offset):
            entered.set()
            if not release.wait(timeout=5):
                raise AssertionError("release was never signaled")
            return super().post_history(ghid, offset)

    first = HeldProvider({(GOV, ""): [page()]})
    second = Provider()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_call = pool.submit(collect, tmp_path, first, manifest)
        try:
            assert entered.wait(timeout=5)
            second_call = pool.submit(collect, tmp_path, second, manifest)
        finally:
            release.set()
        assert first_call.result(timeout=5)["api_cost"] == 0.14
        assert second_call.result(timeout=5)["complete"]
        assert not second.calls


def test_wechat_journals_successful_body_before_next_paid_call(tmp_path):
    first, second = article(100), article(101)
    blocker = '<div class="weui-msg">验证环境</div>'

    class InterruptedProvider(Provider):
        def article_detail(self, url):
            if url == second["url"]:
                raise KeyboardInterrupt
            return super().article_detail(url)

    interrupted = InterruptedProvider({(GOV, ""): [page(first, second)]},
                                      bodies={first["url"]: blocker, second["url"]: blocker},
                                      details={first["url"]: [detail(first)]})
    with pytest.raises(KeyboardInterrupt):
        collect(tmp_path, interrupted)
    resumed = Provider()
    report = collect(tmp_path, resumed)
    assert report["complete"] and report["api_cost"] == 0 and report["total_api_cost"] == 0.17
    assert resumed.calls == [("http", second["url"])]


def test_wechat_direct_date_conflict_blocks_paid_fallback_on_first_run_and_resume(tmp_path):
    entry = article(100)
    client = Provider({(GOV, ""): [page(entry)]},
                      bodies={entry["url"]: '<meta property="article:published_time" content="2026-11-12">'
                                            '<div id="js_content">不能入库的错误正文</div>'},
                      details={entry["url"]: [detail(entry)]})
    report = collect(tmp_path, client)
    assert not report["complete"] and report["api_cost"] == 0.14
    row = report["articles"][0]
    assert row["wechat_content_status"] == "failed" and row["content"] == "" and not eligible(row)
    check = row["publication_check"]
    assert check["reason"] == "conflicting_publication_dates"
    assert {(item["source"], item["date"]) for item in check["evidence"]} == {
        ("wechat_history:post_time", DAY), ("wechat_history:post_time_str", DAY),
        ("meta:article:published_time", "2026-11-12"),
    }
    assert client.calls == [("history", GOV, ""), ("http", entry["url"])]
    client.calls.clear()
    client.bodies[entry["url"]] = "<title>环境异常</title>"
    resumed = collect(tmp_path, client)
    assert not resumed["complete"] and resumed["articles"][0]["publication_check"] == check
    assert resumed["api_cost"] == 0 and resumed["total_api_cost"] == 0.14
    assert not any(kind == "detail" for kind, *_ in client.calls)


def test_wechat_fallback_date_conflict_is_not_charged_again_on_resume(tmp_path):
    entry = article(100)
    client = Provider({(GOV, ""): [page(entry)]},
                      bodies={entry["url"]: "<title>环境异常</title>"},
                      details={entry["url"]: [detail(entry, when="before"), detail(entry)]})
    first = collect(tmp_path, client)
    assert first["api_cost"] == 0.17 and not first["complete"]
    client.calls.clear()
    resumed = collect(tmp_path, client)
    assert not resumed["complete"] and resumed["api_cost"] == 0
    assert resumed["articles"][0]["publication_check"] == first["articles"][0]["publication_check"]
    assert not client.calls


@pytest.mark.parametrize(("published", "beijing_day"), [
    ("2026-11-12 16:00:00+00:00", DAY),
    ("2026-11-13 00:10:00+10:00", "2026-11-12"),
])
def test_wechat_normalizes_space_separated_html_timestamps_with_offsets(tmp_path, published, beijing_day):
    entry = article(100)
    client = Provider({(GOV, ""): [page(entry)]},
                      bodies={entry["url"]: f'<meta property="article:published_time" content="{published}">'
                                            f'<div id="js_content">{BODY}</div>'},
                      details={entry["url"]: [detail(entry)]})
    report = collect(tmp_path, client)
    row = report["articles"][0]
    assert report["complete"] == eligible(row) == (beijing_day == DAY)
    assert report["api_cost"] == 0.14
    assert {"source": "meta:article:published_time", "raw": published, "date": beijing_day} in (
        row["publication_check"]["evidence"]
    )


def test_wechat_optional_verifycode_is_sent_blank_and_detail_uses_get(tmp_path, monkeypatch):
    entry = article(100)
    monkeypatch.setenv("WECHAT_API_KEY", "test-key")
    monkeypatch.delenv("WECHAT_API_VERIFYCODE", raising=False)

    class Response:
        status_code = 200
        url = entry["url"]
        text = '<title>环境异常</title>'

        def __init__(self, payload=None):
            self.payload = payload

        def json(self):
            return self.payload

    def wire(self, method, url, **kwargs):
        if method == "GET" and url == entry["url"].split("#")[0]:
            return Response()
        if method == "POST" and url.endswith("/post_history"):
            assert kwargs["json"]["verifycode"] == ""
            return Response(page(entry))
        if method == "GET" and url.endswith("/article_detail"):
            assert kwargs["params"] == {"url": entry["url"], "mode": 2, "key": "test-key", "verifycode": ""}
            return Response(detail(entry))
        raise AssertionError(f"Unexpected request {method} {url}")

    monkeypatch.setattr("requests.Session.request", wire)
    report = collect(tmp_path, None)
    assert report["complete"] and report["api_cost"] == 0.17
    assert report["articles"][0]["content"] == BODY


@pytest.fixture
def no_retry_wait(monkeypatch):
    from wechat_collector import time

    monkeypatch.setattr(time, "sleep", lambda _: None)


def test_wechat_transient_provider_errors_retry_with_cost_but_auth_does_not(tmp_path, no_retry_wait):
    manifest = registry(tmp_path, account(), account(CITY, OTHER_BIZ, "某市机关事务"))
    client = Provider({(GOV, ""): [{"code": 10002, "cost_money": 0.14}],
                       (CITY, ""): [{"code": -1, "cost_money": 0.02},
                                    {"code": 2005, "cost_money": 0.05}, page(ghid=CITY)]})
    report = collect(tmp_path, client, manifest)
    assert not report["complete"]
    assert report["accounts"][0]["status"] == "failed" and report["accounts"][0]["api_cost"] == 0.14
    assert report["accounts"][1]["status"] == "empty" and report["accounts"][1]["api_cost"] == 0.21
    assert report["api_cost"] == report["total_api_cost"] == 0.35
    assert client.calls.count(("history", GOV, "")) == 1
    assert client.calls.count(("history", CITY, "")) == 3


def test_wechat_fallback_retries_transient_detail_and_journals_its_charges(tmp_path, no_retry_wait):
    entry = article(100)
    client = Provider({(GOV, ""): [page(entry)]},
                      bodies={entry["url"]: "<title>环境异常</title>"},
                      details={entry["url"]: [{"code": 107, "cost_money": 0.03}, detail(entry)]})
    report = collect(tmp_path, client)
    assert report["complete"]
    assert report["articles"][0]["content"] == BODY
    assert report["api_cost"] == 0.20


def test_wechat_cli_loads_environment_and_propagates_incomplete_status(monkeypatch, capsys):
    import sys

    import runtime_config
    import wechat_collector

    def load_env():
        monkeypatch.setenv("WECHAT_API_KEY", "not-live")

    def offline_collect(day):
        assert day == DAY
        assert wechat_collector.os.environ["WECHAT_API_KEY"] == "not-live"
        return {"date": DAY, "complete": False, "collected_count": 0, "reused_count": 0,
                "api_cost": 0, "total_api_cost": 0}

    monkeypatch.setattr(sys, "argv", ["wechat_collector.py", DAY])
    monkeypatch.delenv("WECHAT_API_KEY", raising=False)
    monkeypatch.setattr(runtime_config, "load_environment", load_env)
    monkeypatch.setattr(wechat_collector, "collect_wechat", offline_collect)
    assert wechat_collector.main() == 1
    assert json.loads(capsys.readouterr().out)["complete"] is False


def test_wechat_missing_key_does_not_hide_behind_successful_cache(tmp_path, monkeypatch):
    entry = article(100)
    previous = collect(tmp_path, Provider({(GOV, ""): [page(entry)]}))
    monkeypatch.delenv("WECHAT_API_KEY", raising=False)
    report = collect(tmp_path, None)
    assert previous["complete"] and not report["complete"]
    assert report["accounts"][0]["status"] == "ok"
    assert "WECHAT_API_KEY" in report["error"]
    assert report["articles"] == previous["articles"]
    assert report["api_cost"] == 0 and report["total_api_cost"] == 0.14


def test_wechat_migrated_disabled_account_is_never_called(tmp_path):
    active, inactive = account(), {**account(CITY, OTHER_BIZ), "enabled": False}
    manifest = registry(tmp_path, active, inactive)
    client = Provider({(GOV, ""): [page()]})
    report = collect(tmp_path, client, manifest)
    assert report["complete"]
    assert [summary["ghid"] for summary in report["accounts"]] == [GOV]
    assert client.calls == [("history", GOV, "")]


@pytest.mark.parametrize(("responses", "status", "cost", "attempts", "error"), [
    ([(429, {"code": 20001, "cost_money": 0.02})], "failed", 0.02, 1, "20001"),
    ([(503, {"code": 104, "cost_money": 0.02})], "failed", 0.02, 1, "104"),
    ([(401, {"code": 2005, "cost_money": 0.02})], "failed", 0.02, 1, "HTTP 401"),
    ([(403, {"code": 2005, "cost_money": 0.02})], "failed", 0.02, 1, "HTTP 403"),
    ([(404, {"code": 2005, "cost_money": 0.02})], "failed", 0.02, 1, "HTTP 404"),
    ([(503, {"cost_money": 0.02}), (502, {"code": 0, "cost_money": 0.03}),
      (200, page())], "empty", 0.19, 3, None),
])
def test_wechat_accounts_for_billable_http_errors_before_retry_decision(
        tmp_path, monkeypatch, no_retry_wait, responses, status, cost, attempts, error):
    monkeypatch.setenv("WECHAT_API_KEY", "test-key")
    queued = iter(responses)
    calls = []

    class Response:
        def __init__(self, status_code, body):
            self.status_code = status_code
            self.body = body

        def json(self):
            return self.body

    def wire(self, method, url, **kwargs):
        calls.append((method, url))
        return Response(*next(queued, responses[-1]))

    monkeypatch.setattr("requests.Session.request", wire)
    report = collect(tmp_path, None)
    assert len(calls) == attempts
    assert report["accounts"][0]["status"] == status
    assert report["api_cost"] == report["total_api_cost"] == cost
    if error:
        assert error in report["accounts"][0]["error"]
