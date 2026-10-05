"""Issue 20: production-owned configuration survives installation and code updates."""
import copy
import importlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import config_cli
from config_schema import ConfigConflict, ConfigError
from config_store import ConfigStore, read_document

ROOT = Path(__file__).resolve().parent
TOPIC = "江苏机关事务"
PROMPT = "NEWS_SCORE_SYSTEM_MSG_GOVERNMENT_AFFAIRS"
TERMS = ["机关事务管理", "国有资产管理", "公物仓", "办公用房管理", "公务用车管理",
         "公共机构节能管理", "碳普惠", "机关住房管理", "公务接待", "后勤服务", "机关运行成本"]


def module():
    assert importlib.util.find_spec("topic_config"), "missing explicit topic installer"
    return importlib.import_module("topic_config")


@pytest.fixture
def configured(tmp_path):
    document = read_document(ROOT / "config_defaults.json")
    document["settings"]["SEARCH_KEYWORDS"]["客户自定义"] = ["定制检索"]
    document["prompts"]["CUSTOM_SYSTEM"] = {"text": "客户生产规则", "status": "active"}
    document["keyword_prompt_ids"]["客户自定义"] = "CUSTOM_SYSTEM"
    document["prompts"]["NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500"]["text"] += "\n客户摘要规则"
    store = ConfigStore(tmp_path / "runtime.sqlite3")
    first, _ = store.initialize(document, {"kind": "test"})
    return store, first


def args(store, *extra):
    return config_cli.parser().parse_args(["--store", str(store.path), "enable-topic",
                                          "--topic", "government-affairs", *extra])


def test_candidate_adds_only_topic_without_mutating_production(configured):
    store, first = configured
    before = copy.deepcopy(first.document)
    proposal = module().build_topic_candidate(before)
    assert before == first.document
    candidate = proposal["document"]
    assert candidate["settings"]["SEARCH_KEYWORDS"][TOPIC] == TERMS
    assert list(candidate["settings"]["SEARCH_KEYWORDS"])[-1] == TOPIC
    assert candidate["keyword_prompt_ids"][TOPIC] == PROMPT
    assert candidate["prompts"][PROMPT]["status"] == "active"
    del candidate["settings"]["SEARCH_KEYWORDS"][TOPIC]
    del candidate["keyword_prompt_ids"][TOPIC]
    del candidate["prompts"][PROMPT]
    assert candidate == first.document
    assert store.read().token == first.token


@pytest.mark.parametrize("kind", ["partial", "prompt", "old_topic", "old_route", "rule", "old_rule"])
def test_partial_or_legacy_install_never_overwrites(configured, kind):
    store, first = configured
    doc = copy.deepcopy(first.document)
    if kind == "partial":
        doc["settings"]["SEARCH_KEYWORDS"][TOPIC] = TERMS
    elif kind == "prompt":
        doc["prompts"][PROMPT] = {"text": "生产版本", "status": "active"}
    elif kind == "old_topic":
        doc["settings"]["SEARCH_KEYWORDS"]["机关事务管理局"] = TERMS
    elif kind == "old_route":
        doc["keyword_prompt_ids"]["机关事务管理局"] = "CUSTOM_SYSTEM"
    else:
        doc["settings"]["NEWS_RULE_BASED_SCORING"].append({
            "main_keyword": TOPIC if kind == "rule" else "机关事务管理局",
            "title_contains": "通知", "score": 5})
    before = copy.deepcopy(doc)
    with pytest.raises(ConfigConflict):
        module().build_topic_candidate(doc)
    assert doc == before and store.read().token == first.token


def test_preview_is_readonly_and_checks_version(configured):
    store, first = configured
    module()
    result = config_cli.run(args(store))
    assert result["status"] == "add"
    assert result["current_version"] == first.token
    assert result["added_search_keywords"] == 11
    assert len(store.history()) == 1
    with pytest.raises(ConfigConflict):
        config_cli.run(args(store, "--expected-version", "stale"))


def test_missing_store_is_not_initialized(tmp_path):
    module()
    store = ConfigStore(tmp_path / "missing" / "runtime.sqlite3")
    with pytest.raises(ConfigError):
        config_cli.run(args(store))
    assert not store.path.exists()


def test_apply_backup_noop_and_production_customization(configured, tmp_path, monkeypatch):
    topic = module()
    store, first = configured
    preview = config_cli.run(args(store))
    report = tmp_path / "evaluation.json"
    report.write_text("{}")
    monkeypatch.setattr(topic, "validate_evaluation_report", lambda *a: None)
    backup = tmp_path / "before.sqlite3"
    flags = ["--apply", "--expected-version", first.token,
             "--expected-candidate-sha256", preview["candidate_sha256"],
             "--backup", str(backup), "--note", "启用", "--evaluation-report", str(report)]
    applied = config_cli.run(args(store, *flags))
    assert applied["status"] == "enabled"
    assert ConfigStore(backup).read().document == first.document
    installed = store.read()
    assert installed.document["settings"]["SEARCH_KEYWORDS"][TOPIC] == TERMS
    # Exact rerun does not back up or create another revision.
    repeated = config_cli.run(args(store, "--apply", "--expected-version", installed.token,
        "--expected-candidate-sha256", installed.sha256, "--backup", str(backup), "--note", "重复"))
    assert repeated["status"] == "noop" and store.read().token == installed.token
    modified = copy.deepcopy(installed.document)
    modified["settings"]["SEARCH_KEYWORDS"][TOPIC] = ["客户新增检索"]
    modified["prompts"][PROMPT]["text"] = "生产修改后的评分"
    modified["prompts"]["NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500"]["text"] = "生产修改后的摘要"
    custom = store.save(modified, installed.token, "生产编辑")
    with pytest.raises(ConfigConflict):
        topic.build_topic_candidate(custom.document)
    # Reading runtime config or a changed repository package must not re-install anything.
    with patch("runtime_config.get_snapshot", return_value=custom):
        from runtime_config import value
        assert value("SEARCH_KEYWORDS")[TOPIC] == ["客户新增检索"]
        assert value(PROMPT) == "生产修改后的评分"
        assert value("NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500") == "生产修改后的摘要"
    assert store.read().token == custom.token
    dev = ConfigStore(tmp_path / "dev.sqlite3")
    dev.initialize(first.document, {"kind": "dev"})
    assert store.read().document == modified


@pytest.mark.parametrize("failure", ["hash", "report", "backup", "save"])
def test_failed_enable_leaves_active_unchanged(configured, tmp_path, monkeypatch, failure):
    topic = module()
    store, first = configured
    preview = config_cli.run(args(store))
    report = tmp_path / "report.json"
    report.write_text("{}")
    def reject(*a, **kw):
        raise ConfigError("controlled failure")
    monkeypatch.setattr(topic, "validate_evaluation_report", reject if failure == "report" else lambda *a: None)
    if failure in ("backup", "save"):
        monkeypatch.setattr(ConfigStore, failure, reject)
    with pytest.raises(ConfigError):
        config_cli.run(args(store, "--apply", "--expected-version", first.token,
            "--expected-candidate-sha256", "wrong" if failure == "hash" else preview["candidate_sha256"],
            "--backup", str(tmp_path / "backup.sqlite3"), "--note", "test",
            "--evaluation-report", str(report)))
    assert store.read().token == first.token
