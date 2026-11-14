"""Run-scoped evidence exchanged with the existing pipeline child processes."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re

from government_affairs_pipeline import atomic_json


@contextmanager
def pipeline_lock(day):
    directory = Path("output") / day / "diagnostics"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".wechat_pipeline.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Close this process's copy on exit; LOCK_UN would also unlock copies
        # retained by surviving business children after parent interruption.
        yield lock.fileno()


@contextmanager
def stage_environment(run):
    settings = {"SERP_WECHAT_STAGE_RUN_ID": run["run_id"], "SERP_WECHAT_STAGE_DATE": run["date"]}
    old = {key: os.environ.get(key) for key in settings}
    os.environ.update(settings)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def stage_run(day):
    run_id = os.environ.get("SERP_WECHAT_STAGE_RUN_ID", "")
    if (os.environ.get("ENABLE_GOVERNMENT_WECHAT") == "1"
            and os.environ.get("SERP_WECHAT_STAGE_DATE") == day
            and re.fullmatch(r"[0-9a-f]{32}", run_id)):
        return {"run_id": run_id, "date": day}
    return None


def _stage_path(run, stage):
    return Path("output/wechat_runs") / run["date"] / "stages" / run["run_id"] / (stage + ".json")


def write_stage(run, stage, status, counts, articles):
    atomic_json(_stage_path(run, stage), {
        "version": 1, "date": run["date"], "run_id": run["run_id"], "stage": stage,
        "status": status, "counts": counts, "articles": articles,
    })


def read_stage(run, stage):
    try:
        report = json.loads(_stage_path(run, stage).read_text(encoding="utf-8"))
        if (report["version"] == 1 and report["date"] == run["date"]
                and report["run_id"] == run["run_id"] and report["stage"] == stage
                and isinstance(report["counts"], dict) and isinstance(report["articles"], list)):
            return report
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def build_outcome(run, collection, error):
    outcome = {"collection": collection, "status": "success", "error": error}
    for stage in ("scoring", "database", "summary"):
        report = read_stage(run, stage)
        if report:
            outcome[stage] = {**report["counts"], "status": report["status"]}
        if (not report or report["status"] in {"failed", "partial", "pending"}) and not outcome["error"]:
            outcome["error"] = stage + "_failed"
    if error == "interrupted":
        outcome["status"] = "interrupted"
    elif outcome["error"] or not collection or not collection["complete"]:
        outcome["error"] = outcome["error"] or "collection_incomplete"
        worked = any(outcome.get(stage, {}).get(field, 0)
                     for stage, field in (("collection", "reused_count"), ("collection", "collected_count"),
                                          ("scoring", "succeeded"), ("database", "inserted"),
                                          ("summary", "succeeded")) if outcome.get(stage))
        outcome["status"] = "partial" if worked and error in {None, "collection_incomplete"} else "failed"
    return outcome
