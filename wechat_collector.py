"""Collect verified government WeChat articles for one Beijing business day."""
import argparse
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import requests

from content_quality import body_rejection_reason
from government_affairs_pipeline import atomic_json
from news_freshness import assess_html, assess_publication_evidence
from topic_config import TOPIC

REGISTRY = Path(__file__).resolve().parent / "topic_packages" / "government_affairs_wechat.json"
BEIJING = ZoneInfo("Asia/Shanghai")
API_PREFIX = "/fbmain/monitor/v3"
DEFAULT_API_BASE = "https://www.dajiala.com"
MAX_PAGES_PER_ACCOUNT = 100
MAX_PROVIDER_ATTEMPTS = 3
TIMEOUT = (5, 25)
WECHAT_HOST = "mp.weixin.qq.com"
BODY_DONE = {"ok", "insufficient"}
PROVIDER_ERRORS = {
    -1: "极致了请求频繁",
    101: "文章已删除、账号被封或已迁移",
    104: "公众号原始 ID 不存在",
    105: "文章解析失败",
    106: "文章解析失败",
    107: "文章解析失败",
    110: "历史页无文章，未确认列表完整",
    2003: "极致了系统资源请求出错",
    2005: "极致了系统错误",
    10002: "极致了鉴权失败",
    20001: "极致了余额不足",
    50000: "极致了内部服务器错误",
}
RETRY_DELAY_BY_CODE = {-1: 5, 103: 2, 105: 2, 106: 2, 107: 2, 2003: 2, 2005: 2, 50000: 2}


class CollectionError(Exception):
    """A fixed diagnostic safe to persist in the collection report."""

    def __init__(self, message, *, retry_after=None, cost_money=None):
        super().__init__(message)
        self.retry_after = retry_after
        self.cost_money = cost_money


def _registry_bytes(path):
    return Path(path or REGISTRY).read_bytes()


def _parse_registry(raw):
    registry = json.loads(raw)
    if (not isinstance(registry, dict) or registry.get("version") != 1
            or registry.get("keyword") != TOPIC or not isinstance(registry.get("accounts"), list)):
        raise ValueError("无效的官微来源注册表")
    ghids, businesses = set(), set()
    for account in registry["accounts"]:
        if not isinstance(account, dict) or type(account.get("enabled")) is not bool:
            raise ValueError("无效的官微启用状态")
        if not account["enabled"]:
            continue
        ghid, biz, name = (account.get(field) for field in ("ghid", "biz", "name"))
        if (not isinstance(ghid, str) or not re.fullmatch(r"gh_[a-zA-Z0-9]+", ghid)
                or not isinstance(biz, str) or not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", biz)
                or not isinstance(name, str) or not name.strip()):
            raise ValueError("启用的官微缺少可用的 ghid、biz 或名称")
        if ghid in ghids or biz in businesses:
            raise ValueError("启用的官微存在重复 ghid 或 biz")
        ghids.add(ghid)
        businesses.add(biz)
    return registry


def load_registry(path=None):
    return _parse_registry(_registry_bytes(path))


class _WechatClient:
    def __init__(self, session):
        self.session = session
        self.key = os.environ.get("WECHAT_API_KEY", "").strip()
        self.verifycode = os.environ.get("WECHAT_API_VERIFYCODE", "").strip()
        self.base_url = os.environ.get("WECHAT_API_BASE_URL", "").strip().rstrip("/") or DEFAULT_API_BASE
        self.last_provider_request = None

    def configuration_error(self):
        if not self.key:
            return "缺少 WECHAT_API_KEY"
        try:
            base = urlsplit(self.base_url)
            valid = base.scheme == "https" and base.hostname and not (
                base.username or base.password or base.query or base.fragment
            )
        except ValueError:
            valid = False
        return None if valid else "WECHAT_API_BASE_URL 必须为有效的 HTTPS 地址"

    def _provider(self, method, endpoint, *, fields):
        if self.last_provider_request is not None:
            time.sleep(max(0, 0.35 - (time.monotonic() - self.last_provider_request)))
        self.last_provider_request = time.monotonic()
        payload = {**fields, "key": self.key, "verifycode": self.verifycode}
        try:
            response = self.session.request(
                method, self.base_url + API_PREFIX + endpoint, timeout=TIMEOUT, allow_redirects=False,
                **({"json": payload} if method == "POST" else {"params": payload}),
            )
        except requests.RequestException:
            # Exception text can contain the entire request URL, including credentials.
            raise CollectionError("极致了网络请求失败", retry_after=2) from None
        try:
            data = response.json()
        except ValueError:
            data = None
        if response.status_code == 200:
            return data
        data = data if isinstance(data, dict) else {}
        cost, code = data.get("cost_money"), data.get("code")
        if type(code) is int and code != 0:
            raise _provider_code_error(code, cost_money=cost)
        retry_after = 2 if response.status_code in {408, 425, 429, 500, 502, 503, 504} else None
        raise CollectionError(f"极致了 HTTP {response.status_code}", retry_after=retry_after, cost_money=cost)

    def post_history(self, ghid, offset):
        return self._provider("POST", "/post_history", fields={
            "ghid": ghid, "url": "", "nickname": "", "offset": offset,
        })

    def article_detail(self, url):
        return self._provider("GET", "/article_detail", fields={"url": url, "mode": 2})

    def fetch_body_html(self, url):
        parts = urlsplit(url)
        secure_url = urlunsplit(("https", WECHAT_HOST, parts.path, parts.query, ""))
        try:
            response = self.session.request("GET", secure_url, timeout=TIMEOUT, allow_redirects=False)
            if response.status_code != 200:
                raise CollectionError(f"微信正文 HTTP {response.status_code}")
            return response.text
        except requests.RequestException:
            raise CollectionError("微信正文网络请求失败") from None


def _cost(value):
    try:
        amount = Decimal(str(value))
        if amount.is_finite() and amount >= 0:
            return amount
    except InvalidOperation:
        pass
    return Decimal(0)


def _check_provider_response(response):
    if not isinstance(response, dict):
        raise CollectionError("极致了响应格式异常", retry_after=2)
    code = response.get("code")
    if type(code) is not int:
        raise CollectionError("极致了响应缺少有效状态码", retry_after=2)
    if code != 0:
        raise _provider_code_error(code)


def _provider_code_error(code, *, cost_money=None):
    message = PROVIDER_ERRORS.get(code, "极致了请求失败")
    return CollectionError(f"{message}（{code}）", retry_after=RETRY_DELAY_BY_CODE.get(code), cost_money=cost_money)


def _article_identity(url):
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != WECHAT_HOST
                or parsed.path != "/s"):
            return None
        # WeChat's biz parameter is base64; a literal "+" must stay part of it.
        params = parse_qs(parsed.query.replace("+", "%2B"))
        biz, mid, idx = (params.get(field, []) for field in ("__biz", "mid", "idx"))
        if (len(biz) == len(mid) == len(idx) == 1 and re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", biz[0])
                and re.fullmatch(r"[1-9][0-9]*", mid[0]) and re.fullmatch(r"[1-9][0-9]*", idx[0])):
            return biz[0], mid[0], idx[0]
    except ValueError:
        pass
    return None


def _published_at(value):
    if isinstance(value, bool):
        return None
    raw = str(value or "").strip()
    try:
        if re.fullmatch(r"[0-9]{10,13}", raw) and len(raw) in (10, 13):
            epoch = int(raw) / (1000 if len(raw) == 13 else 1)
            return datetime.fromtimestamp(epoch, BEIJING)
        parsed = datetime.fromisoformat(raw)
        return parsed.astimezone(BEIJING) if parsed.tzinfo else parsed.replace(tzinfo=BEIJING)
    except (ValueError, OverflowError, OSError):
        return None


def _publication(fields, keys, label):
    evidence, instants = [], []
    for key in keys:
        value = fields.get(key)
        if value is None or value == "":
            continue
        instant = _published_at(value)
        if instant is None:
            raise CollectionError(f"{label}发布时间无法解析")
        evidence.append({"source": f"{label}:{key}", "raw": str(value), "date": instant.date().isoformat()})
        instants.append(instant.timestamp())
    if not evidence:
        raise CollectionError(f"{label}缺少发布时间")
    return evidence, instants[0]


def _check(evidence, target_date, link):
    return {**assess_publication_evidence(evidence, target_date), "link": link}


def _history_article(item, account, target_date):
    if not isinstance(item, dict):
        raise CollectionError("历史列表条目格式异常")
    link, title = item.get("url"), item.get("title")
    identity = _article_identity(link)
    if (not identity or identity[0] != account["biz"]
            or any(str(item[key]) != identifier for key, identifier in
                   zip(("biz", "appmsgid", "position"), identity) if key in item)):
        raise CollectionError("历史列表文章身份与注册账号不符")
    if not isinstance(title, str) or not title.strip():
        raise CollectionError("历史列表文章缺少标题")
    evidence, instant = _publication(item, ("post_time", "post_time_str"), "wechat_history")
    check = _check(evidence, target_date, link)
    if check["published_date"] is None:
        raise CollectionError("历史列表发布时间存在冲突")
    row = {
        "keyword": TOPIC, "main_keyword": TOPIC, "search_keyword": account["name"], "source": account["name"],
        "sourceapi": "极致了api", "date": check["published_date"], "fetchdate": target_date,
        "title": title.strip(), "link": link, "content": "", "wordcount": 0,
        "wechat_ghid": account["ghid"], "wechat_article_id": "/".join(identity),
        "wechat_content_status": "failed", "wechat_content_error": "正文待采集",
        "publication_check": check,
    }
    return row, instant


def _read_page(response, account, state, target_date):
    if response.get("ghid") != account["ghid"]:
        raise CollectionError("历史列表原始 ID 与注册账号不符")
    entries, end = response.get("data"), response.get("is_end")
    if (response.get("error_msg") or not isinstance(entries, list)
            or type(end) not in (int, bool) or end not in (0, 1) or (not entries and not end)):
        raise CollectionError("历史列表页格式异常")
    offset = response.get("offset")
    if not end and (not isinstance(offset, str) or not offset.strip()
                    or offset in state["visited_offsets"] or offset == state["offset"]):
        raise CollectionError("历史列表翻页游标重复或缺失")
    rows = [_history_article(item, account, target_date) for item in entries]
    instants = [instant for _, instant in rows]
    if any(a < b for a, b in zip(instants, instants[1:])):
        raise CollectionError("历史列表发布时间顺序异常")
    ids = [row["wechat_article_id"] for row, _ in rows]
    if len(set(ids)) != len(ids):
        raise CollectionError("历史列表含重复文章")
    if rows and state["oldest_time"] is not None:
        if instants[0] > state["oldest_time"]:
            raise CollectionError("历史列表翻页时间倒退")
        if set(ids) <= set(state["boundary_ids"]):
            raise CollectionError("历史列表翻页内容重复")
    done = bool(end or (rows and rows[-1][0]["date"] < target_date))
    if rows:
        oldest = instants[-1]
        boundary_ids = (state["boundary_ids"].copy() if oldest == state["oldest_time"] else [])
        boundary_ids.extend(row["wechat_article_id"] for row, instant in rows
                            if instant == oldest and row["wechat_article_id"] not in boundary_ids)
        state.update(oldest_time=oldest, boundary_ids=boundary_ids)
    for row, _ in rows:
        if row["date"] == target_date:
            state["articles"].setdefault(row["wechat_article_id"], {
                "history_evidence": row["publication_check"]["evidence"], "row": row,
            })
    state["visited_offsets"].append(state["offset"])
    state.update(offset=offset if not done else "", list_done=done, list_error=None)


def _text(element):
    for hidden in element.select("script, style, template, noscript"):
        hidden.decompose()
    return element.get_text("\n", strip=True)


def _blocked_body(text):
    return bool(re.match(
        r"^(?:环境异常|访问环境异常|访问频繁|请完成验证|请在微信客户端打开|"
        r"此内容因违规无法查看|该内容已被发布者删除|该公众号已迁移)",
        (text or "").strip(),
    ))


def _direct_body(html, row):
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one("#js_content")
    if (body is None or soup.select_one("#js_verify, #js_verify_page")
            or _blocked_body(soup.title.get_text(strip=True) if soup.title else "")):
        raise CollectionError("微信正文不可直接读取")
    text = _text(body)
    if _blocked_body(text) or body_rejection_reason(text, row["title"]) == "error_page":
        raise CollectionError("微信正文遇到验证或错误页面")
    check = assess_html(html, row["fetchdate"])
    direct, unparsed = [], False
    for observation in check["evidence"] + check.get("unparsed_evidence", []):
        instant = _published_at(observation["raw"])
        if instant is not None:
            direct.append({**observation, "date": instant.date().isoformat()})
        elif observation.get("date"):
            direct.append(observation)
        else:
            unparsed = True
    # WeChat article pages also expose their publication epoch in an inline variable.
    epochs = re.findall(r'\b(?:var |window\.)ct\s*=\s*["\']?(\d{10,13})\b', html)
    for value in dict.fromkeys(epochs):
        parsed, _ = _publication({"ct": value}, ("ct",), "wechat_http")
        direct.extend(parsed)
    return (None if unparsed else text), direct


def _detail_body(response, row, evidence):
    identity = _article_identity(row["link"])
    returned_identity = _article_identity(response.get("url"))
    if (returned_identity != identity
            or any(str(response[key]) != identifier for key, identifier in
                   zip(("biz", "mid", "idx"), identity) if key in response)
            or any(response[key] != row["wechat_ghid"] for key in
                   ("user_name", "ghid", "gh_id") if key in response)):
        raise CollectionError("极致了正文文章身份与列表不符")
    detail_evidence, _ = _publication(response, ("pubtime", "post_time", "post_time_str"), "wechat_detail")
    check = _check(evidence + detail_evidence, row["fetchdate"], row["link"])
    if check["status"] != "accepted":
        return "", check
    text = response.get("content")
    markup = response.get("content_multi_text")
    if not isinstance(text, str) and not isinstance(markup, str):
        raise CollectionError("极致了正文响应缺少正文字段")
    text = text.strip() if isinstance(text, str) else ""
    if not text and isinstance(markup, str):
        text = _text(BeautifulSoup(markup, "html.parser"))
    return text, check


def _store_body(row, text, check, method):
    row["publication_check"] = check
    row["wechat_content_method"] = method
    if check["status"] != "accepted":
        raise CollectionError("极致了正文发布时间与列表不符")
    reason = body_rejection_reason(text, row["title"])
    if not text or reason or _blocked_body(text) or text.strip() == row["title"]:
        row.update(wechat_content_status="insufficient", wechat_content_error=reason or "正文无有效文字")
        return
    row.update(content=text, wordcount=len(text), wechat_content_status="ok")
    row.pop("wechat_content_error", None)


class _CollectionState:
    """Checkpoint the next cursor, article bodies and provider charges under the daily lock."""

    def __init__(self, path, target_date):
        self.path = path
        self.accounts = {}
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("version") != 1 or saved.get("date") != target_date:
                raise ValueError("官微采集检查点与业务日不符")
            self.accounts = saved["accounts"]
        self.target_date = target_date
        self.initial_cost = self.total_cost()
        self.initial = {key: (_cost(record["total_api_cost"]), set(record["articles"]))
                        for key, record in self.accounts.items()}

    def account(self, account):
        key = f"{account['ghid']}/{account['biz']}"
        return key, self.accounts.setdefault(key, {
            "offset": "", "visited_offsets": [], "oldest_time": None, "boundary_ids": [],
            "list_done": False, "list_error": "历史列表待采集", "articles": {}, "total_api_cost": 0,
        })

    def total_cost(self):
        return sum((_cost(record["total_api_cost"]) for record in self.accounts.values()), Decimal(0))

    def save(self):
        atomic_json(self.path, {"version": 1, "date": self.target_date, "accounts": self.accounts})

    def request(self, account_state, fetch, *args):
        for attempt in range(MAX_PROVIDER_ATTEMPTS):
            try:
                response = fetch(*args)
                if isinstance(response, dict):
                    self._record_cost(account_state, response.get("cost_money"))
                _check_provider_response(response)
                return response
            except CollectionError as exc:
                self._record_cost(account_state, exc.cost_money)
                if exc.retry_after is None or attempt == MAX_PROVIDER_ATTEMPTS - 1:
                    raise
                self.save()
                time.sleep(exc.retry_after)

    @staticmethod
    def _record_cost(account_state, amount):
        total = _cost(account_state["total_api_cost"]) + _cost(amount)
        account_state["total_api_cost"] = str(total)


def _collect_account(client, journal, account):
    key, state = journal.account(account)
    for _ in range(MAX_PAGES_PER_ACCOUNT):
        if state["list_done"]:
            break
        try:
            page = journal.request(state, client.post_history, account["ghid"], state["offset"])
            _read_page(page, account, state, journal.target_date)
        except CollectionError as exc:
            state["list_error"] = str(exc)
            journal.save()
            break
        journal.save()
    else:
        if not state["list_done"]:
            state["list_error"] = "历史列表达到本次分页上限"
            journal.save()

    for article in state["articles"].values():
        row = article["row"]
        if row["wechat_content_status"] in BODY_DONE:
            continue
        direct = article.get("direct_evidence", []).copy()
        link = row["link"]
        text = None
        try:
            html = client.fetch_body_html(link)
            text, observed = _direct_body(html, row)
        except CollectionError:
            observed = []
        new_evidence = [item for item in observed if item not in direct]
        direct.extend(new_evidence)
        evidence = article["history_evidence"] + direct
        check = _check(evidence, journal.target_date, link)
        row["publication_check"] = check
        if new_evidence:
            article["direct_evidence"] = direct
            # Persist new direct evidence before spending on fallback.
            journal.save()
        try:
            method = "http"
            if text is None or check["status"] != "accepted":
                detail = journal.request(state, client.article_detail, link)
                text, check = _detail_body(detail, row, evidence)
                method = "article_detail"
            _store_body(row, text, check, method)
        except CollectionError as exc:
            row["wechat_content_error"] = str(exc)
        journal.save()
    return _account_report(account, journal, key, state)


def _account_report(account, journal, key, state):
    previous_cost, previous_ids = journal.initial.get(key, (Decimal(0), set()))
    articles = [{**record["row"], "source": account["name"], "search_keyword": account["name"]}
                for record in state["articles"].values()]
    failures = sum(row["wechat_content_status"] == "failed" for row in articles)
    error = state["list_error"]
    if failures:
        first_error = next(row["wechat_content_error"] for row in articles if row["wechat_content_status"] == "failed")
        article_error = f"{failures} 篇正文待重试：{first_error}"
        error = "；".join(filter(None, (error, article_error)))
    costs = _cost(state["total_api_cost"])
    reused = len(previous_ids & state["articles"].keys())
    status = "failed" if error else ("ok" if articles else "empty")
    report = {
        "ghid": account["ghid"], "name": account["name"], "status": status, "error": error,
        "count": len(articles), "collected_count": len(articles) - reused, "reused_count": reused,
        "api_cost": float(costs - previous_cost), "total_api_cost": float(costs),
    }
    return report, articles


def _collect_locked(target_date, registry, registry_sha256, client, directory, configuration_error=None):
    journal = _CollectionState(directory / "wechat_collection_state.json", target_date)
    accounts, articles = [], []
    for account in registry["accounts"]:
        if not account["enabled"]:
            continue
        if configuration_error is None:
            summary, rows = _collect_account(client, journal, account)
        else:
            key, state = journal.account(account)
            summary, rows = _account_report(account, journal, key, state)
            if summary["status"] == "failed":
                summary["error"] = configuration_error
        accounts.append(summary)
        articles.extend(rows)
    total_cost = journal.total_cost()
    report = {
        "date": target_date, "registry_sha256": registry_sha256, "accounts": accounts, "articles": articles,
        "complete": configuration_error is None and all(account["status"] in {"ok", "empty"} for account in accounts),
        "error": configuration_error,
        "collected_count": sum(account["collected_count"] for account in accounts),
        "reused_count": sum(account["reused_count"] for account in accounts),
        "api_cost": float(total_cost - journal.initial_cost), "total_api_cost": float(total_cost),
    }
    atomic_json(directory / "wechat_collection.json", report)
    return report


def collect_wechat(target_date, *, registry_path=None, client=None, output_root="output"):
    """Return the daily report. An injected client implements post_history, fetch_body_html and article_detail."""
    if date.fromisoformat(target_date).isoformat() != target_date:
        raise ValueError("业务日须为 YYYY-MM-DD")
    raw_registry = _registry_bytes(registry_path)
    registry = _parse_registry(raw_registry)
    registry_sha256 = hashlib.sha256(raw_registry).hexdigest()
    directory = Path(output_root) / target_date / "diagnostics"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".wechat_collection.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if client is not None:
            return _collect_locked(target_date, registry, registry_sha256, client, directory)
        with requests.Session() as session:
            client = _WechatClient(session)
            return _collect_locked(target_date, registry, registry_sha256, client, directory,
                                   configuration_error=client.configuration_error())


def main():
    from runtime_config import load_environment

    parser = argparse.ArgumentParser(description="采集指定北京时间业务日的机关事务官微文章")
    parser.add_argument("date", metavar="YYYY-MM-DD")
    args = parser.parse_args()
    try:
        load_environment()
        report = collect_wechat(args.date)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"官微采集失败：{exc}\n")
    summary = {key: report[key] for key in ("date", "complete", "collected_count", "reused_count",
                                           "api_cost", "total_api_cost")}
    if report.get("error"):
        summary["error"] = report["error"]
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
