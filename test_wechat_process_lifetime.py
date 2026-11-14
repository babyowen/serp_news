"""Real shell-launched writers retain exclusion after their pipeline parent dies."""
import fcntl
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

DAY = "2026-11-13"

STAGE = '''
import os, sys, time
from pathlib import Path
stage = Path(sys.argv[0]).stem
if stage == "news_scorer":
    stage = "score-" + sys.argv[1]
if stage == "fetch_content":
    stage = "content"
if stage == "write_to_mysql":
    stage = "database"
if stage == "news_item_summarizer":
    stage = "summary"
# Even an unrelated, explicitly inheritable parent descriptor must stay private.
try:
    stat = os.fstat(int(os.environ["PRIVATE_FD"]))
except OSError:
    pass
else:
    private = os.stat("private-parent-file")
    assert (stat.st_dev, stat.st_ino) != (private.st_dev, private.st_ino)
Path(stage + ".started").touch()
if stage in os.environ["WAIT_STAGES"].split(","):
    while not Path(stage + ".release").exists():
        time.sleep(.02)
Path(stage + ".written").touch()
if os.environ.get("FAIL_STAGE") == stage:
    attempt = Path(stage + ".attempts")
    attempt.write_text(attempt.read_text() + "x" if attempt.exists() else "x")
    sys.exit(7)
'''

PARENT = '''
import json, os, shlex, sys
from pathlib import Path
from types import SimpleNamespace
import main
import news_volume_alert
import wechat_notifications
from datetime import datetime
from topic_config import TOPIC

os.environ["ENABLE_ITEM_SUMMARIZER"] = "1"
os.environ["ENABLE_TOBACCO_CRAWLER"] = "0"
private = open("private-parent-file", "w")
os.set_inheritable(private.fileno(), True)
os.environ["PRIVATE_FD"] = str(private.fileno())
keywords = [TOPIC, "other1", "other2"]
snapshot = SimpleNamespace(document={"settings": {"SEARCH_KEYWORDS": {kw: [kw] for kw in keywords}}},
                           token="isolated", sha256="isolated")
main.prepare_batch = lambda *a: (snapshot, keywords)
main.execute_news_fetching = lambda *a, **kw: True
main.execute_content_fetching = lambda day, kw: main.safe_subprocess_run(
    shlex.join([sys.executable, "-B", "fetch_content.py"]), "content", retries=1)
main.run_volume_alert = lambda **kw: None
news_volume_alert.send_feishu = lambda *a, **kw: True
wechat_notifications._now = lambda: datetime.fromisoformat("2026-11-14T08:00:00+08:00")
for kw in keywords:
    path = Path("output/2026-11-13") / ("2026-11-13_" + kw + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]")
Path("result").write_text(str(main.main("2026-11-13")))
'''

POSTLUDE = '''
import fcntl
from pathlib import Path
from wechat_notifications import _is_running
lock_path = Path("output/2026-11-13/diagnostics/.wechat_pipeline.lock")
if lock_path.exists():
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not any(_is_running(p) for p in Path("output/wechat_runs/2026-11-13").glob("*.json"))
Path("postlude.written").touch()
'''


def wait_until(predicate, description):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    pytest.fail("timed out waiting for " + description)


def day_locked(tmp_path):
    with (tmp_path / f"output/{DAY}/diagnostics/.wechat_pipeline.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False


@pytest.fixture
def pipeline_process(tmp_path):
    for script in ("fetch_content.py", "news_scorer.py", "write_to_mysql.py", "news_item_summarizer.py"):
        (tmp_path / script).write_text(STAGE)
    (tmp_path / "date_review.py").write_text(POSTLUDE)
    (tmp_path / "parent.py").write_text(PARENT)
    processes = []

    def launch(wait_stages=(), fail_stage="", enabled=True):
        log = (tmp_path / "process.log").open("w")
        p = subprocess.Popen([sys.executable, "-B", "parent.py"], cwd=tmp_path,
                             env={**os.environ, "WAIT_STAGES": ",".join(wait_stages), "FAIL_STAGE": fail_stage,
                                  "ENABLE_GOVERNMENT_WECHAT": "1" if enabled else "0"},
                             stdout=log, stderr=log, start_new_session=True)
        log.close()
        processes.append(p)
        return p

    yield launch
    for p in processes:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait(timeout=5)


@pytest.mark.parametrize("stages", [
    ("content",), ("score-江苏机关事务",), ("score-other1", "score-other2"),
    ("database",), ("summary",),
])
def test_wechat_parent_sigkill_keeps_writers_exclusive_and_live(tmp_path, pipeline_process, stages):
    from wechat_notifications import _is_running, check_missing

    p = pipeline_process(stages)
    wait_until(lambda: all((tmp_path / (stage + ".started")).exists() for stage in stages), "stage startup")
    record = next((tmp_path / f"output/wechat_runs/{DAY}").glob("*.json"))
    p.kill()
    p.wait(timeout=5)
    assert day_locked(tmp_path), "a surviving writer must exclude a replacement run"
    assert _is_running(record), "a surviving writer must retain the run's liveness lease"
    check = lambda: check_missing(now=datetime.fromisoformat("2026-11-14T09:00:00+08:00"),
                                 output_root=tmp_path / "output", send_fn=lambda _: True)
    assert check()["status"] == "running"
    for stage in stages:
        (tmp_path / (stage + ".release")).touch()
        wait_until(lambda: (tmp_path / (stage + ".written")).exists(), stage + " final write")
        if stage != stages[-1]:
            assert day_locked(tmp_path), "each parallel writer must retain the day lock"
            assert _is_running(record)
    wait_until(lambda: not day_locked(tmp_path) and not _is_running(record), "last writer exit")
    assert json.loads(record.read_text())["status"] == "running"  # checker may now recover this interruption
    assert check()["status"] == "interrupted"
    assert json.loads(record.read_text())["status"] == "interrupted"
    assert not (tmp_path / "postlude.written").exists()


@pytest.mark.parametrize("fail_stage", ["", "score-other1"])
def test_wechat_child_exit_releases_locks_before_postlude(tmp_path, pipeline_process, fail_stage):
    from wechat_notifications import _is_running

    p = pipeline_process(fail_stage=fail_stage)
    p.wait(timeout=15)
    assert p.returncode == 0, (tmp_path / "process.log").read_text()
    assert (tmp_path / "result").read_text() == ("False" if fail_stage else "True")
    assert (tmp_path / "postlude.written").exists(), (tmp_path / "process.log").read_text()
    assert not day_locked(tmp_path)
    record = next((tmp_path / f"output/wechat_runs/{DAY}").glob("*.json"))
    assert not _is_running(record)
    if fail_stage:
        assert (tmp_path / (fail_stage + ".attempts")).read_text() == "xx"


def test_wechat_disabled_pipeline_needs_no_lock_descriptors(tmp_path, pipeline_process):
    p = pipeline_process(enabled=False)
    p.wait(timeout=15)
    assert p.returncode == 0, (tmp_path / "process.log").read_text()
    assert (tmp_path / "result").read_text() == "True"
    assert (tmp_path / "postlude.written").exists()
    assert not (tmp_path / "output/wechat_runs").exists()
    assert not (tmp_path / f"output/{DAY}/diagnostics/.wechat_pipeline.lock").exists()
