"""Bounded automatic date review for every platform keyword."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import hashlib
import json
from pathlib import Path
from government_affairs_pipeline import atomic_json

MAX_ATTEMPTS = 2

def today_beijing():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()

def track_review(row, today):
    check = row.get("publication_check", {})
    state = row.get("date_review")
    if state is None:
        if check.get("status") != "pending":
            return
        state = row["date_review"] = {"version":1, "first_seen":today, "attempts":0,
            "status":"pending", "next_date":(date.fromisoformat(today)+timedelta(days=1)).isoformat(),
            "expires":(date.fromisoformat(today)+timedelta(days=3)).isoformat()}
    if check.get("status") == "accepted":
        if state["status"] not in {"ready", "delivered", "delivery_failed"}:
            state.update(status="ready", next_date=today)
    elif check.get("status") == "old":
        state.update(status="excluded", next_date=None)
    elif state["attempts"] >= MAX_ATTEMPTS:
        state.update(status="archived", next_date=None)
    expire_review(row, today)

def expire_review(row, today):
    state = row.get("date_review", {})
    if state.get("status") == "pending" and today > state["expires"]:
        state.update(status="archived", next_date=None)

def review_due(row, today):
    state = row.get("date_review", {})
    return (state.get("status") == "pending" and state.get("attempts",0) < MAX_ATTEMPTS
            and state.get("next_date", "9999-12-31") <= today <= state["expires"])

def start_review(row, today):
    if not review_due(row, today):
        raise ValueError("date review is not due")
    state = row["date_review"]
    import copy
    state.setdefault("history", []).append({"review_date":today,
        "publication_check":copy.deepcopy(row.get("publication_check"))})
    state.update(attempts=state["attempts"]+1, last_attempt=today,
                 next_date=(date.fromisoformat(today)+timedelta(days=1)).isoformat())

def job_path(day, keyword):
    key = hashlib.sha256(json.dumps([day,keyword], ensure_ascii=False).encode()).hexdigest()
    return Path("output/date_reviews") / (key + ".json")

def sync_job(rows, day, keyword):
    path = job_path(day, keyword)
    active = [r["date_review"] for r in rows if r.get("date_review",{}).get("status") in {"pending","ready"}]
    if active:
        atomic_json(path, {"date":day,"keyword":keyword,
                          "next_date":min(s.get("next_date") or today_beijing() for s in active)})
    else:
        path.unlink(missing_ok=True)


def deliver_rows(raw_path, keyword, links):
    """Resume bodies, scores and DB import without rerunning search or valid scores."""
    from fetch_content import process_json, content_fetch_complete
    from news_freshness import eligible
    from news_scorer import refresh_review_scores
    day = raw_path.parent.name
    if not process_json(keyword, day):
        return False
    current = json.loads(raw_path.read_text(encoding="utf-8"))
    selected = [r for r in current if r.get("link") in links]
    if len({r.get("link") for r in selected}) != len(links):
        return False
    body_links = {r["link"] for r in selected if eligible(r) and content_fetch_complete(r, keyword)}
    results = [r for r in refresh_review_scores(raw_path, keyword, body_links) if r.get("score_status") == "ok"]
    if not results:
        return set()
    from write_to_mysql import import_scored_news_with_retry
    selection = raw_path.parent / "diagnostics" / ("review_delivery_" + job_path(day,keyword).name)
    atomic_json(selection, results)
    report = import_scored_news_with_retry(str(selection), keyword, report=True)
    return set(report["completed_links"])


def review_batch(day, keyword, today=None, deliver=None):
    """Run in an isolated child process so the original batch's config stays pinned."""
    from batch_config import prepare_batch
    from fetch_content import check_topic_dates, date_diagnostic_path
    from news_freshness import write_diagnostic
    today = today or today_beijing()
    date.fromisoformat(day)
    date.fromisoformat(today)
    # Validate the keyword against its pinned configuration before resolving paths.
    prepare_batch(day, keyword)
    path = Path("output") / day / f"{day}_{keyword}.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    all_ok = True
    for row in rows:
        expire_review(row, today)
        state = row.get("date_review", {})
        if state.get("status") == "ready" and state.get("delivery_attempts",0) >= 3:
            state.update(status="delivery_failed", next_date=None)
        if not review_due(row, today):
            continue
        start_review(row, today)
        # Claim the attempt before I/O: crashes cannot cause unlimited retries.
        atomic_json(path, rows)
        try:
            check_topic_dates([row], day, keyword, force=True, today=today)
        except Exception as exc:
            row["date_review"]["last_error"] = type(exc).__name__
            all_ok = False
        track_review(row, today)
        atomic_json(path, rows)
    ready = [r for r in rows if r.get("date_review",{}).get("status") == "ready"
             and r["date_review"].get("next_date",today) <= today]
    for row in ready:
        state = row["date_review"]
        state["delivery_attempts"] = state.get("delivery_attempts",0) + 1
        state["next_date"] = (date.fromisoformat(today)+timedelta(days=1)).isoformat()
        state["last_delivery"] = today
    atomic_json(path, rows)
    if ready:
        try:
            outcome = (deliver or deliver_rows)(path, keyword, {r["link"] for r in ready})
            delivered = {r["link"] for r in ready} if outcome is True else (outcome if isinstance(outcome, set) else set())
        except Exception as exc:
            delivered = set()
            for row in ready:
                row["date_review"]["last_error"] = type(exc).__name__
        # Body/scoring helpers may have enriched the raw file; don't overwrite them.
        enriched = {r.get("link"):r for r in json.loads(path.read_text(encoding="utf-8"))}
        for row in rows:
            if row in ready:
                state = row["date_review"]
                row.update({k:v for k,v in enriched.get(row.get("link"),{}).items() if k != "date_review"})
                if row.get("publication_check",{}).get("status") == "old":
                    state["status"] = "excluded"
                else:
                    state["status"] = "delivered" if row["link"] in delivered else ("delivery_failed" if state["delivery_attempts"] >= 3 else "ready")
                if state["status"] in {"excluded", "delivered", "delivery_failed"}:
                    state["next_date"] = None
        all_ok = all_ok and all(r["date_review"]["status"] in {"delivered", "excluded"} for r in ready)
    atomic_json(path, rows)
    sync_job(rows, day, keyword)
    write_diagnostic(rows, day, date_diagnostic_path(day, keyword))
    counts = {}
    for row in rows:
        status = row.get("date_review",{}).get("status")
        if status:
            counts[status] = counts.get(status,0)+1
    print(f"[DATE_REVIEW] date={day} keyword={keyword} counts={json.dumps(counts,ensure_ascii=False)}")
    return all_ok


def run_due(today=None, keywords=None):
    """Scan only compact active job files; expire stale jobs without paid work."""
    import os
    import subprocess
    import sys
    import fcntl
    today = today or today_beijing()
    folder = Path("output/date_reviews")
    folder.mkdir(parents=True, exist_ok=True)
    outcomes = []
    with (folder / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("[DATE_REVIEW] another review worker is running")
            return outcomes
        for path in sorted(folder.glob("*.json")):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if (keywords is not None and job["keyword"] not in keywords) or job["next_date"] > today:
                    continue
                date.fromisoformat(job["date"])
                env = dict(os.environ)
                # Never pass the current day's pinned revision into a historical job.
                env.pop("SERP_CONFIG_REVISION", None)
                result = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                    "--batch-date", job["date"], "--keyword", job["keyword"], "--today", today],
                    env=env, timeout=600, check=False)
                outcomes.append({"date":job["date"],"keyword":job["keyword"],"ok":result.returncode==0})
            except Exception as exc:
                outcomes.append({"job":path.name,"ok":False,"error":type(exc).__name__})
    atomic_json(folder / "last_run.report", {"date":today,"jobs":outcomes})
    print(f"[DATE_REVIEW] jobs={len(outcomes)} failed={sum(not r['ok'] for r in outcomes)}")
    return outcomes


if __name__ == "__main__":
    import argparse
    import os
    parser = argparse.ArgumentParser(description="平台新闻日期自动复核")
    parser.add_argument("--batch-date")
    parser.add_argument("--keyword")
    parser.add_argument("--today")
    parser.add_argument("--keywords", nargs="*")
    args = parser.parse_args()
    if args.batch_date:
        os.environ.pop("SERP_CONFIG_REVISION", None)
        raise SystemExit(0 if review_batch(args.batch_date,args.keyword,args.today) else 1)
    run_due(args.today,args.keywords)
