"""Candidate evaluation is explicit, reproducible and checked before installation."""
import copy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from config_schema import ConfigError
from config_store import ConfigStore, read_document
from topic_config import build_topic_candidate
ROOT=Path(__file__).resolve().parent

def module():
    assert importlib.util.find_spec("topic_evaluation"), "missing candidate evaluation contract"
    return importlib.import_module("topic_evaluation")

@pytest.fixture
def candidate(tmp_path):
    store=ConfigStore(tmp_path/"prod.sqlite3")
    snap,_=store.initialize(read_document(ROOT/"config_defaults.json"),{"kind":"test"})
    return store,snap,build_topic_candidate(snap.document)["document"]

def passing_report(candidate):
    ev=module()
    store,snap,doc=candidate
    samples=ev.load_samples()
    def score(sample):
        if not sample["content"].strip():
            return SimpleNamespace(score=0,status="empty_content",error_code=None,attempts=0,raw_response=None)
        value=sample["expected_min"]
        return SimpleNamespace(score=value,status="ok",error_code=None,attempts=1,raw_response=str(value))
    report=ev.evaluate_samples(samples,ev.evaluation_manifest(snap,doc),score)
    report["human_review"]={"approved":True,"reviewer":"业务审核测试替身","reviewed_at":"2026-10-02T18:00:00+08:00",
                            "reviewed_ids":[s["id"] for s in samples]}
    return report

def test_dataset_quotas_and_deterministic_sources():
    samples=module().load_samples()
    base=[s for s in samples if s["base"]]
    assert len(base)==40
    assert {k:sum(s["category"]==k for s in base) for k in ["direct","unrelated","ambiguous"]} == {"direct":20,"unrelated":10,"ambiguous":10}
    assert all(s["source"]["kind"]=="synthetic" and s["reason"] for s in samples)
    assert len({s["id"] for s in samples})==len(samples)
    assert any(s.get("variant_of") for s in samples)

def test_valid_report_and_model_drift(candidate,tmp_path,monkeypatch):
    ev=module(); store,snap,doc=candidate
    report=passing_report(candidate); path=tmp_path/"report.json"
    path.write_text(json.dumps(report))
    ev.validate_report(path,snap,doc)
    monkeypatch.setenv("LLM_SCORING_MODEL","different-model")
    with pytest.raises(ConfigError): ev.validate_report(path,snap,doc)
    assert store.read().token==snap.token

@pytest.mark.parametrize("mutation",["source_version","candidate_sha256","dataset_sha256","implementation_sha256",
    "raw","null","missing","duplicate","fake_pass","human","reason","attempts"])
def test_changed_or_false_report_cannot_enable(candidate,tmp_path,mutation):
    ev=module(); _,snap,doc=candidate
    report=passing_report(candidate)
    if mutation.endswith("sha256") or mutation=="source_version": report[mutation]="wrong"
    elif mutation=="raw": report["results"][0]["raw_response"]="5分"
    elif mutation=="null": report["results"][0].update(score=None,status="failed")
    elif mutation=="missing": report["results"].pop()
    elif mutation=="duplicate": report["results"][-1]=report["results"][0]
    elif mutation=="fake_pass": report["results"][0].update(score=0,raw_response="0"); report["passed"]=True
    elif mutation=="human": report["human_review"]["approved"]=False
    elif mutation=="reason": report["results"][0]["error_code"]="timeout"
    elif mutation=="attempts": report["results"][0]["attempts"]=0
    path=tmp_path/"invalid.json";path.write_text(json.dumps(report))
    with pytest.raises(ConfigError): ev.validate_report(path,snap,doc)

def test_failures_not_counted_as_valid_zero(candidate):
    ev=module(); _,snap,doc=candidate
    samples=ev.load_samples()
    failed=lambda sample:SimpleNamespace(score=None,status="failed",error_code="timeout",attempts=3,raw_response=None)
    report=ev.evaluate_samples(samples,ev.evaluation_manifest(snap,doc),failed)
    assert report["passed"] is False
    assert report["summary"]["failed"]==len(samples)
    assert report["summary"]["valid_zero"]==0

def test_preview_does_not_launch_worker_or_create_output(candidate,tmp_path,monkeypatch,capsys):
    ev=module()
    tool=importlib.import_module("evaluate_government_affairs")
    store,snap,_=candidate
    def forbidden(*a,**k): raise AssertionError("preview must not call evaluation worker")
    monkeypatch.setattr(tool,"run_evaluation",forbidden)
    report=tmp_path/"preview.json"
    assert tool.main(["--store",str(store.path),"--output",str(report)])==0
    assert not report.exists()
    assert store.read().token==snap.token
    assert "candidate_sha256" in capsys.readouterr().out

def test_profile_never_records_key_or_url_credentials(monkeypatch):
    ev=module()
    monkeypatch.setenv("LLM_API_KEY","VERY_PRIVATE_KEY")
    monkeypatch.setenv("LLM_SCORING_BASE_URL","https://user:secret@example.test/v1?token=private")
    with pytest.raises(ConfigError):
        ev.scoring_profile()  # Existing model validation rejects embedded credentials.
    monkeypatch.setenv("LLM_SCORING_BASE_URL","https://example.test/v1")
    profile=ev.scoring_profile()
    text=json.dumps(profile)
    assert all(s not in text for s in ["VERY_PRIVATE_KEY","secret","private","user:"])
    assert profile["base_url"]=="https://example.test/v1"

def test_actual_report_allows_atomic_enable(candidate,tmp_path):
    ev=module()
    import config_cli
    store,snap,doc=candidate
    report=passing_report(candidate)
    path=tmp_path/"report.json"; path.write_text(json.dumps(report))
    args=config_cli.parser().parse_args(["--store",str(store.path),"enable-topic","--topic","government-affairs",
        "--apply","--expected-version",snap.token,"--expected-candidate-sha256",report["candidate_sha256"],
        "--backup",str(tmp_path/"backup.sqlite3"),"--note","测试启用","--evaluation-report",str(path)])
    assert config_cli.run(args)["status"]=="enabled"
    assert store.read().document==doc
