"""Run ledger and Feishu delivery for the government WeChat pipeline.

finish_run outcome contract (all counters describe WeChat articles only):
* status: success / partial / failed / interrupted.
* collection: collect_wechat report for this date, or None. Account status,
  article content status, collected_count, reused_count and costs feed the card.
* scoring: candidates, succeeded, failed, skipped, already_done.
* database: inserted (committed new rows), duplicate, failed, skipped.
* summary: succeeded, failed, already_done, not_needed, pending.
* error: optional allowlisted reason code (ERROR_REASONS). Any other error
  is represented by a generic reason. Never pass an exception into the card.

Each stage may also report status: success / partial / failed / interrupted /
skipped / not_needed / pending / already_done. Counters are nonnegative ints.
Missing/invalid counters display as unknown. Use explicit zero when measured.
An injected send_fn(card_json) must return True to acknowledge delivery.
"""
import argparse
from contextlib import contextmanager
from datetime import date, datetime, time as clock_time, timedelta
import fcntl
import json
import math
import os
from pathlib import Path
import re
import time
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

from government_affairs_pipeline import atomic_json


BEIJING = ZoneInfo("Asia/Shanghai")
RUN_ID = re.compile(r"[0-9a-f]{32}")
TERMINAL = {"success": "成功", "partial": "部分失败", "failed": "失败", "interrupted": "中断"}
STAGE_STATUS = {**TERMINAL, "skipped": "已跳过", "not_needed": "无需处理",
                "pending": "待处理", "already_done": "已完成"}
ERROR_REASONS = {
    "collection_failed": "官微采集阶段失败",
    "collection_incomplete": "官微采集未完整结束",
    "scoring_failed": "评分阶段失败",
    "database_failed": "入库阶段失败",
    "summary_failed": "摘要阶段失败",
    "interrupted": "运行中断，未能记录完整结果",
    "other": "其他运行错误",
}
STAGE_COUNTERS = {
    "scoring": ("评分", {"candidates": "候选", "succeeded": "成功", "failed": "失败",
                       "skipped": "跳过", "already_done": "已完成"}),
    "database": ("入库", {"inserted": "新增入库", "duplicate": "重复", "failed": "失败", "skipped": "跳过"}),
    "summary": ("摘要", {"succeeded": "成功", "failed": "失败", "already_done": "已完成",
                        "not_needed": "无需处理", "pending": "待处理"}),
}
COLLECTION_COUNTERS = {
    "accounts_ok": "成功", "accounts_empty": "零文章", "accounts_failed": "失败",
    "articles": "采集", "collected_count": "本次新增", "reused_count": "缓存复用",
    "body_ok": "有效正文", "body_insufficient": "正文不足", "body_failed": "正文失败",
}
COLLECTION_CAUSES = {
    "auth": ("接口鉴权或配置", ("鉴权失败", "缺少 WECHAT_API_KEY", "WECHAT_API_BASE_URL 必须")),
    "quota": ("供应商余额不足", ("极致了余额不足",)),
    "http": ("网络或 HTTP 请求失败", (" HTTP ", "网络请求失败", "请求频繁")),
    "date": ("发布时间冲突", ("发布时间与列表不符", "发布时间存在冲突")),
    "identity": ("账号或文章身份不符", ("身份与", "原始 ID", "已删除、账号被封或已迁移")),
    "body": ("正文处理失败", ("篇正文未通过", "正文字段", "文章解析失败")),
    "list": ("历史列表或接口响应异常", ("历史列表", "历史页", "响应格式", "系统错误", "内部服务器错误")),
    "unknown": ("原因待核查", ()),
}
PUBLIC_GHID = re.compile(r"gh_[0-9a-zA-Z]{1,32}")
PUBLIC_NAME = re.compile(r"[\u3400-\u9fffA-Za-z0-9 ·（）()\-]{1,64}")
_leases = {}


def _now():
    return datetime.now(BEIJING)


def _day(value):
    if isinstance(value, str) and date.fromisoformat(value).isoformat() == value:
        return value
    raise ValueError("业务日须为 YYYY-MM-DD")


def _timestamp(value):
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError("运行记录时间缺少时区")
    return stamp.astimezone(BEIJING)


def _ledger_root(output_root):
    return (Path(output_root) / "wechat_runs").resolve()


@contextmanager
def _day_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".ledger.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _record_path(run, output_root):
    run_id = run["run_id"]
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ValueError("无效的官微运行 ID")
    return _ledger_root(output_root) / _day(run["date"]) / f"{run_id}.json"


def _lease_path(path):
    return path.with_name(f".{path.stem}.live.lock")


def start_run(target_date, *, output_root="output"):
    """Record a run and hold its exclusive liveness lease until finish or exit."""
    run = {"version": 1, "run_id": uuid4().hex, "date": _day(target_date),
           "started_at": _now().isoformat(), "status": "running"}
    path = _record_path(run, output_root)
    with _day_lock(path.parent):
        lease = _lease_path(path).open("a")
        try:
            fcntl.flock(lease, fcntl.LOCK_EX)
            atomic_json(path, run)
        except BaseException:
            lease.close()
            raise
        _leases[path] = lease
    return run


def release_run(run, *, output_root="output"):
    """Release a local run's liveness lease, leaving its recorded state intact."""
    lease = _leases.pop(_record_path(run, output_root), None)
    if lease is not None:
        lease.close()


def _is_running(path):
    with _lease_path(path).open("a") as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _cost(value):
    if type(value) is int or type(value) is float:
        try:
            if value >= 0 and math.isfinite(value):
                return value
        except OverflowError:
            pass
    return None


def _status_counts(rows, field, statuses):
    if not isinstance(rows, list):
        return {}
    counts = dict.fromkeys(statuses, 0)
    for row in rows:
        status = row.get(field) if isinstance(row, dict) else None
        if not isinstance(status, str) or status not in counts:
            return {}
        counts[status] += 1
    return counts


def _collection_cause(error):
    if not isinstance(error, str) or not error:
        return None
    for code, (_, indicators) in COLLECTION_CAUSES.items():
        if any(indicator in error for indicator in indicators):
            return code
    return "unknown"


def _failed_accounts(accounts):
    failed = [account for account in accounts if account["status"] == "failed"]
    if not failed:
        return []
    # Names and IDs come from the local, reviewed registry. Report-supplied
    # values and unrecognized identities never become free-form card text.
    from wechat_collector import load_registry

    try:
        names = {account["ghid"]: account["name"] for account in load_registry()["accounts"] if account["enabled"]}
    except (OSError, ValueError):
        # A failed or replaced registry is itself a reason to report the run.
        names = {}
    failures = []
    for account in failed:
        ghid = account.get("ghid")
        name = names.get(ghid) if isinstance(ghid, str) else None
        if name is None or not PUBLIC_GHID.fullmatch(ghid) or not PUBLIC_NAME.fullmatch(name):
            ghid = name = None
        failures.append({"ghid": ghid, "name": name, "cause": _collection_cause(account.get("error")) or "unknown"})
    return failures


def _collection(report, target_date):
    report = report if isinstance(report, dict) and report.get("date") == target_date else {}
    accounts, articles = report.get("accounts"), report.get("articles")
    account_counts = _status_counts(accounts, "status", ("ok", "empty", "failed"))
    body_counts = _status_counts(articles, "wechat_content_status", ("ok", "insufficient", "failed"))
    complete = report.get("complete")
    return {
        "complete": complete if type(complete) is bool else None,
        "cause": _collection_cause(report.get("error")),
        "failed_accounts": _failed_accounts(accounts) if account_counts else [],
        **{f"accounts_{key}": account_counts.get(key) for key in ("ok", "empty", "failed")},
        "articles": len(articles) if isinstance(articles, list) and all(isinstance(row, dict) for row in articles)
        else None,
        **{f"body_{key}": body_counts.get(key) for key in ("ok", "insufficient", "failed")},
        **{key: _count(report.get(key)) for key in ("collected_count", "reused_count")},
        **{key: _cost(report.get(key)) for key in ("api_cost", "total_api_cost")},
    }


def _stage(stage, counters):
    stage = stage if isinstance(stage, dict) else {}
    status = stage.get("status")
    return {"status": status if isinstance(status, str) and status in STAGE_STATUS else None,
            **{key: _count(stage.get(key)) for key in counters}}


def _outcome_summary(outcome, target_date):
    error = outcome.get("error")
    if error and (not isinstance(error, str) or error not in ERROR_REASONS):
        error = "other"
    return {
        "collection": _collection(outcome.get("collection"), target_date),
        **{key: _stage(outcome.get(key), fields) for key, (_, fields) in STAGE_COUNTERS.items()},
        "error": error or None,
    }


def _valid_outcome(result):
    if not isinstance(result, dict):
        return False
    collection = result.get("collection")
    if not isinstance(collection, dict) or not (
        set(collection) == {"complete", "api_cost", "total_api_cost", "cause", "failed_accounts", *COLLECTION_COUNTERS}
        and (collection["complete"] is None or type(collection["complete"]) is bool)
        and all(collection[key] is None or _count(collection[key]) is not None for key in COLLECTION_COUNTERS)
        and all(collection[key] is None or _cost(collection[key]) is not None for key in ("api_cost", "total_api_cost"))
    ):
        return False
    cause = collection["cause"]
    if cause is not None and (not isinstance(cause, str) or cause not in COLLECTION_CAUSES):
        return False
    failures = collection["failed_accounts"]
    if not isinstance(failures, list) or len(failures) != (collection["accounts_failed"] or 0):
        return False
    for failure in failures:
        if not isinstance(failure, dict) or set(failure) != {"ghid", "name", "cause"}:
            return False
        ghid, name, cause = failure["ghid"], failure["name"], failure["cause"]
        if (not isinstance(cause, str) or cause not in COLLECTION_CAUSES
                or (ghid is not None or name is not None) and not (
                    isinstance(ghid, str) and PUBLIC_GHID.fullmatch(ghid)
                    and isinstance(name, str) and PUBLIC_NAME.fullmatch(name))):
            return False
    for key, (_, counters) in STAGE_COUNTERS.items():
        stage = result.get(key)
        if not isinstance(stage, dict) or set(stage) != {"status", *counters}:
            return False
        status = stage["status"]
        if status is not None and (not isinstance(status, str) or status not in STAGE_STATUS):
            return False
        if any(stage[key] is not None and _count(stage[key]) is None for key in counters):
            return False
    error = result.get("error")
    return error is None or isinstance(error, str) and error in ERROR_REASONS


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def _valid_notification(notification):
    if not isinstance(notification, dict):
        return False
    attempts, state = notification.get("attempts"), notification.get("state")
    if _count(attempts) is None or state not in ("pending", "sent"):
        return False
    try:
        if attempts:
            _timestamp(notification["last_attempt_at"])
        if notification.get("last_check_day") is not None:
            _day(notification["last_check_day"])
        if state == "sent":
            return bool(attempts and _timestamp(notification["sent_at"]) >= _timestamp(notification["last_attempt_at"]))
    except (TypeError, ValueError, KeyError, OverflowError):
        return False
    return True


def _load_run(path):
    run = _read_json(path)
    if not (isinstance(run, dict) and type(run.get("version")) is int and run["version"] == 1
            and run.get("run_id") == path.stem and run.get("date") == path.parent.name):
        return None
    try:
        started = _timestamp(run["started_at"])
        if run["status"] == "running" and not any(key in run for key in ("finished_at", "outcome", "notification")):
            return run
        if (run["status"] in TERMINAL and _timestamp(run["finished_at"]) >= started
                and _valid_outcome(run.get("outcome")) and _valid_notification(run.get("notification"))):
            return run
    except (TypeError, ValueError, KeyError, OverflowError):
        pass
    return None


def _card(title, lines, template):
    return json.dumps({
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": template},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}],
    }, ensure_ascii=False)


def _shown(value):
    return "未知" if value is None else str(value)


def _money(value):
    return "未知" if value is None else f"¥{value:.2f}"


def _result_card(run):
    status = run["status"]
    outcome = run["outcome"]
    collection = outcome["collection"]
    start = _timestamp(run["started_at"]).strftime("%Y-%m-%d %H:%M:%S")
    end = _timestamp(run["finished_at"]).strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"**业务日** {run['date']}  **运行 ID** {run['run_id']}",
             f"**北京时间** {start} 至 {end}"]
    for title, keys in (("账号", ("accounts_ok", "accounts_empty", "accounts_failed")),
                        ("采集与正文", ("articles", "collected_count", "reused_count",
                                     "body_ok", "body_insufficient", "body_failed"))):
        counts = " / ".join(f"{COLLECTION_COUNTERS[key]} {_shown(collection[key])}" for key in keys)
        lines.append(f"**{title}** {counts}")
    if collection["failed_accounts"]:
        lines.append("**失败账号**")
        for failed in collection["failed_accounts"]:
            account = f"{failed['name']}（{failed['ghid']}）" if failed["ghid"] else "账号未识别"
            lines.append(f"- {account} · {COLLECTION_CAUSES[failed['cause']][0]}")
    for key, (title, counters) in STAGE_COUNTERS.items():
        stage = outcome[key]
        counts = " / ".join(f"{label} {_shown(stage[field])}" for field, label in counters.items())
        stage_status = STAGE_STATUS.get(stage["status"])
        if stage_status is None and all(stage[field] is None for field in counters):
            stage_status = "未知"
        label = f"{title}（{stage_status}）" if stage_status else title
        lines.append(f"**{label}** {counts}")
    lines.append(f"**接口费用** 本次 {_money(collection['api_cost'])} / 当日累计 {_money(collection['total_api_cost'])}")
    reasons = []
    if collection["complete"] is False:
        reasons.append(ERROR_REASONS["collection_incomplete"])
    if collection["cause"]:
        reasons.append(COLLECTION_CAUSES[collection["cause"]][0])
    error = outcome["error"]
    if error and ERROR_REASONS[error] not in reasons:
        reasons.append(ERROR_REASONS[error])
    if status != "success" or reasons:
        lines.append("**原因** " + ("；".join(reasons) if reasons else "未知，详见该业务日运行日志"))
    return _card(f"机关事务官微结果 · {TERMINAL[status]}", lines,
                 {"success": "green", "partial": "orange"}.get(status, "red"))


def _end_run(path, run, outcome, finished):
    run.update(status=outcome["status"], finished_at=finished.isoformat(),
               outcome=_outcome_summary(outcome, run["date"]),
               notification={"state": "pending", "attempts": 0})
    atomic_json(path, run)


def _deliver(path, record, card, message_uuid, send_fn, *, delays=(0,), check_day=None):
    """Persist each attempt before transport, then persist a confirmed acknowledgement."""
    notification = record["notification"]
    if notification["state"] == "sent" or (check_day and notification.get("last_check_day") == check_day):
        return False
    for delay in delays:
        if delay:
            time.sleep(delay)
        notification.update(attempts=notification["attempts"] + 1, last_attempt_at=_now().isoformat())
        if check_day:
            notification["last_check_day"] = check_day
        atomic_json(path, record)
        try:
            if send_fn is None:
                from news_volume_alert import send_feishu
                acknowledged = send_feishu(card, message_uuid=message_uuid)
            else:
                acknowledged = send_fn(card)
        except Exception:
            acknowledged = False
        if acknowledged is True:
            notification.update(state="sent", sent_at=_now().isoformat())
            atomic_json(path, record)
            return True
    return False


def _deliver_result(path, run, send_fn, **options):
    return _deliver(path, run, _result_card(run), run["run_id"], send_fn, **options)


def finish_run(run, outcome, *, output_root="output", send_fn=None):
    """Freeze the first terminal result, with up to three attempts to deliver it."""
    path = _record_path(run, output_root)
    try:
        with _day_lock(path.parent):
            record = _load_run(path)
            if record is None:
                raise ValueError("官微运行记录缺失或损坏")
            if record["status"] == "running":
                if outcome.get("status") not in TERMINAL:
                    raise ValueError("无效的官微运行结果")
                _end_run(path, record, outcome, _now())
            _deliver_result(path, record, send_fn, delays=(0, 1, 2))
            return record
    finally:
        release_run(run, output_root=output_root)


def _days(root, latest):
    days = []
    if root.exists():
        for directory in root.iterdir():
            if not directory.is_dir():
                continue
            try:
                business_date = _day(directory.name)
            except ValueError:
                continue
            if business_date <= latest:
                days.append(directory)
    return sorted(days)


def _records(directory):
    for path in sorted(directory.glob("*.json")):
        if RUN_ID.fullmatch(path.stem):
            try:
                run = _load_run(path)
            except (OSError, RecursionError):
                # Keep evidence of an unusable record without abandoning the date scan.
                run = None
            yield path, run


def _has_lease_marker(directory):
    return any(RUN_ID.fullmatch(marker.name[1:-len(".live.lock")])
               for marker in directory.glob(".*.live.lock"))


def _retry_results(records, now, send_fn, *, check_day=None):
    result = {"sent": 0, "pending": 0}
    for path, run in records:
        if (run is None or run["status"] not in TERMINAL or _timestamp(run["finished_at"]) > now
                or run["notification"]["state"] == "sent"):
            continue
        acknowledged = _deliver_result(path, run, send_fn, check_day=check_day)
        result["sent" if acknowledged else "pending"] += 1
    return result


def retry_pending(*, output_root="output", send_fn=None):
    """Retry each unsent terminal result once, including results from past business days."""
    now = _now().astimezone(BEIJING)
    result = {"sent": 0, "pending": 0}
    for directory in _days(_ledger_root(output_root), now.date().isoformat()):
        with _day_lock(directory):
            retried = _retry_results(_records(directory), _now(), send_fn)
            for key, count in retried.items():
                result[key] += count
    return result


def _overdue(directory, check_day, status, send_fn, run=None, *, ambiguous=False):
    path = directory / "overdue.json"
    record = _read_json(path)
    if (not isinstance(record, dict) or record.get("check_day") != check_day
            or record.get("date") != directory.name or not _valid_notification(record.get("notification"))):
        record = {"date": directory.name, "check_day": check_day,
                  "notification": {"state": "pending", "attempts": 0}}
    message_uuid = uuid5(NAMESPACE_URL, f"serp-news/wechat-overdue/{check_day}").hex
    if status == "running":
        reason = f"截至北京时间 09:00，昨日业务批次仍运行（ID {run['run_id']}）。"
    elif ambiguous:
        reason = "没有有效结果记录，请核对运行日志。"
    else:
        reason = "截至北京时间 09:00，昨日业务批次未启动。"
    card = _card("机关事务官微 · 09:00 检查", [f"**业务日** {directory.name}", reason], "orange")
    _deliver(path, record, card, message_uuid, send_fn, delays=(0, 1, 2), check_day=check_day)
    return record["notification"]["state"]


def check_missing(*, now=None, output_root="output", send_fn=None):
    """After 09:00 Beijing, retry pending results and inspect yesterday's current batch."""
    current = _now() if now is None else now
    current = current.replace(tzinfo=BEIJING) if current.utcoffset() is None else current.astimezone(BEIJING)
    business_date = (current.date() - timedelta(days=1)).isoformat()
    check_day = current.date().isoformat()
    result = {"date": business_date, "status": "too_early", "sent": 0, "pending": 0}
    if current.time() < clock_time(9):
        return result
    root = _ledger_root(output_root)
    target = root / business_date
    for directory in sorted(set(_days(root, check_day)) | {target}):
        with _day_lock(directory):
            observed_at = _now() if now is None else current
            records = list(_records(directory))
            eligible = []
            for path, run in records:
                if run is None:
                    continue
                started = _timestamp(run["started_at"])
                if started > observed_at:
                    continue
                if run["status"] == "running":
                    if not _is_running(path):
                        _end_run(path, run, {"status": "interrupted", "error": "interrupted"}, observed_at)
                elif _timestamp(run["finished_at"]) > observed_at:
                    continue
                if directory == target and started.date() == current.date():
                    eligible.append(run)
            retried = _retry_results(records, observed_at, send_fn, check_day=check_day)
            for key, count in retried.items():
                result[key] += count
            if directory == target:
                latest = max(eligible, key=lambda run: (_timestamp(run["started_at"]), run["run_id"]), default=None)
                result["status"] = latest["status"] if latest else "missing"
                if latest:
                    result["run_id"] = latest["run_id"]
                if latest is None or latest["status"] == "running":
                    result["alert"] = _overdue(directory, check_day, result["status"], send_fn, latest,
                                               ambiguous=bool(records) or _has_lease_marker(directory))
    return result


def main(argv=None):
    from runtime_config import load_environment

    parser = argparse.ArgumentParser(description="独立检查机关事务官微运行结果与飞书通知")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--check-missing", action="store_true", help="执行北京时间 09:00 检查")
    actions.add_argument("--retry-pending", action="store_true", help="补发未确认送达的结果")
    args = parser.parse_args(argv)
    load_environment()
    if os.getenv("ENABLE_GOVERNMENT_WECHAT", "").strip() != "1":
        print(json.dumps({"status": "skipped", "reason": "ENABLE_GOVERNMENT_WECHAT disabled"}))
        return 0
    report = check_missing() if args.check_missing else retry_pending()
    print(json.dumps(report, ensure_ascii=False))
    return int(report["pending"] > 0 or report.get("alert") == "pending")


if __name__ == "__main__":
    raise SystemExit(main())
