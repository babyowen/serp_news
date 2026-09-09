"""Twenty system-level configuration cases; all external services are mocked."""
import base64
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import config_migration
from config_schema import ConfigConflict, ConfigError, digest
from config_store import ConfigStore, read_document, write_export
from runtime_config import bind_process, get_snapshot, value

ROOT = Path(__file__).resolve().parent


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    document = read_document(ROOT / "config_defaults.json")
    store = ConfigStore(tmp_path / "runtime.sqlite3")
    first, _ = store.initialize(document, {"kind": "system-test"})
    monkeypatch.setenv("SERP_CONFIG_STORE", str(store.path))
    monkeypatch.delenv("SERP_CONFIG_REVISION", raising=False)
    monkeypatch.setenv("RUN_LOG_PATH", str(tmp_path / "test.log"))
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "legacy"
    source.mkdir()
    for path in (ROOT / "tests/fixtures/legacy").iterdir():
        shutil.copyfile(path, source / path.name.removesuffix(".txt"))

    def cli(*args, selected_store=store):
        return subprocess.run([sys.executable, "-B", str(ROOT / "config_cli.py"), "--store", str(selected_store.path), *map(str, args)],
                              cwd=tmp_path, capture_output=True, text=True, timeout=20)

    return SimpleNamespace(root=tmp_path, document=document, store=store, first=first, source=source, cli=cli)


@pytest.fixture
def web(ctx):
    from app import app
    client = app.test_client()
    credentials = f"{os.environ['ADMIN_USERNAME']}:{os.environ['ADMIN_PASSWORD']}"
    headers = {"Authorization": "Basic " + base64.b64encode(credentials.encode()).decode()}
    assert client.get("/admin/keywords", headers=headers).status_code == 200
    with client.session_transaction() as session:
        csrf = session["config_csrf"]
    return SimpleNamespace(app=app, client=client, headers=headers, csrf=csrf)


def test_01_migrate_additional_prompt_whitespace_and_duplicate_terms(ctx):
    prompt = '  新增生产规则\r\n引号"、反斜杠\\、😀\n\t保留尾部空白  '
    path = ctx.source / "config.py"
    source = path.read_text(encoding="utf-8")
    entry = "\"养老\": ['养老']"
    assert entry in source
    source = source.replace(entry, "\"养老\": ['养老', '养老']")
    source += f"\nCUSTOM_SCORE_SYSTEM_PROMPT = {prompt!r}\n"
    path.write_bytes(source.replace("\n", "\r\n").encode("utf-8"))
    document, archive = config_migration.extract_legacy(ctx.source)
    assert len(document["prompts"]) == 23
    assert document["prompts"]["CUSTOM_SCORE_SYSTEM_PROMPT"]["text"] == prompt
    assert document["settings"]["SEARCH_KEYWORDS"]["养老"] == ["养老", "养老"]
    assert base64.b64decode(archive["files"]["config.py"]["base64"]) == path.read_bytes()
    assert document["settings"]["NEWS_RULE_BASED_SCORING"] == []


def test_02_source_change_or_missing_file_aborts_before_publish(ctx):
    original = config_migration._model
    def concurrent_change(*args, **kwargs):
        result = original(*args, **kwargs)
        with (ctx.source / "config.py").open("a") as handle:
            handle.write("\n# concurrent production edit\n")
        return result
    with patch.object(config_migration, "_model", side_effect=concurrent_change):
        with pytest.raises(ConfigError, match="发生变化"):
            config_migration.extract_legacy(ctx.source)
    (ctx.source / "news_region_utils.py").unlink()
    target = ConfigStore(ctx.root / "never-created" / "runtime.sqlite3")
    failed = ctx.cli("init", "--source", ctx.source, selected_store=target)
    assert failed.returncode == 1 and "Traceback" not in failed.stderr
    assert not target.path.parent.exists()
    assert ctx.store.read().token == ctx.first.token


def test_03_migration_rejects_decorators_and_imports_that_can_change_values(ctx):
    path = ctx.source / "config.py"
    original = path.read_text(encoding="utf-8")
    marker = ctx.root / "must-not-execute"
    changes = [
        f"\n@(lambda f: open({str(marker)!r}, 'w').write('executed'))\ndef custom_hook():\n    pass\n",
        "\nfrom custom_production_settings import SEARCH_KEYWORDS\n",
        "\ndef load_dotenv():\n    SEARCH_KEYWORDS.clear()\n",
    ]
    for extra in changes:
        path.write_text(original + extra, encoding="utf-8")
        with pytest.raises(ConfigError):
            config_migration.extract_legacy(ctx.source)
        assert not marker.exists()


def test_04_invalid_prompt_routes_templates_and_request_contracts_keep_old_version(ctx):
    mutations = [
        lambda d: d["keyword_prompt_ids"].update(养老="NEWS_SCORE_PROMPT"),
        lambda d: d["keyword_prompt_ids"].update(养老="NEWS_SUMMARY_SYSTEM_PROMPT"),
        lambda d: d["prompts"]["NEWS_SCORE_PROMPT"].update(text="{keyword} {title.__class__} {content}"),
        lambda d: d["models"]["scoring"]["parameters"].update(stream=True),
        lambda d: d["models"]["scoring"]["parameters"].update(timeout=0),
        lambda d: d["models"]["scoring"].update(base_url="https://user:secret@example.com/api"),
    ]
    for change in mutations:
        candidate = copy.deepcopy(ctx.document)
        change(candidate)
        with pytest.raises(ConfigError):
            ctx.store.save(candidate, ctx.first.token, "invalid contract")
        assert ctx.store.read().token == ctx.first.token
    assert len(ctx.store.history()) == 1


def test_05_commit_failure_rolls_back_active_pointer_revision_and_web_success(ctx, web):
    connect = sqlite3.connect
    class FailedCommit(sqlite3.Connection):
        def commit(self):
            raise sqlite3.OperationalError("simulated commit I/O failure")
    def fail_connection(*args, **kwargs):
        return connect(*args, **kwargs, factory=FailedCommit)
    data = {"action": "add", "main_keyword": "不得保存", "search_keywords": "新词",
            "config_version": ctx.first.token, "config_csrf": web.csrf}
    with patch("config_store.sqlite3.connect", side_effect=fail_connection):
        result = web.client.post("/admin/keywords", data=data, headers=web.headers)
    assert result.status_code == 400
    assert "配置已保存" not in result.get_data(as_text=True)
    assert ctx.store.read().token == ctx.first.token
    assert len(ctx.store.history()) == 1
    with sqlite3.connect(ctx.store.path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_06_online_backup_is_consistent_and_preserves_sources_and_batch_pins(ctx):
    document, source = config_migration.extract_legacy(ctx.source)
    store = ConfigStore(ctx.root / "migrated.sqlite3")
    first, _ = store.initialize(document, source)
    output = ctx.root / "output" / "2099-01-01"
    store.pin_batch(output, "养老")
    candidate = copy.deepcopy(document)
    candidate["prompts"]["NEWS_SCORE_SYSTEM_MSG"]["text"] += "\n已提交"
    second = store.save(candidate, first.token, "committed")
    with sqlite3.connect(store.path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO revisions(document,sha256,created_at,note,source) SELECT document,sha256,created_at,'uncommitted',source FROM revisions WHERE id=1")
        writer.execute("UPDATE metadata SET active=3")
        target = ctx.root / "backup.sqlite3"
        store.backup(target)
        writer.rollback()
    restored = ConfigStore(target)
    assert restored.read().token == second.token
    assert restored.history() == store.history()
    assert restored.original_source() == source
    assert restored.pin_batch(output, "养老")[0].token == first.token
    assert restored.pin_batch(output.parent / "2099-01-02", "养老")[0].token == second.token


def test_07_editable_export_diff_import_and_restore_round_trip_without_secrets(ctx, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "case-seven-private-key")
    candidate = ctx.root / "editable.json"
    exported = ctx.cli("export", "--editable", "--output", candidate)
    assert exported.returncode == 0, exported.stderr
    assert json.loads(exported.stdout)["source_version"] == ctx.first.token
    assert "case-seven-private-key" not in candidate.read_text()
    document = read_document(candidate)
    document["prompts"]["NEWS_SCORE_SYSTEM_MSG"]["text"] += "\n显式调整"
    candidate.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    diff = ctx.cli("diff", "--file", candidate)
    assert json.loads(diff.stdout)["changes"][0]["path"] == "prompts.NEWS_SCORE_SYSTEM_MSG.text"
    imported = ctx.cli("import", "--file", candidate, "--expected-version", ctx.first.token, "--note", "candidate")
    assert imported.returncode == 0, imported.stderr
    second = ctx.store.read()
    assert second.document == document
    restore = ctx.cli("restore", "--version", ctx.first.token, "--expected-version", second.token, "--note", "rollback")
    assert restore.returncode == 0, restore.stderr
    assert ctx.store.read().document == ctx.document
    assert len(ctx.store.history()) == 3


def test_08_dry_run_detects_stale_version_and_never_changes_current_data(ctx):
    candidate = ctx.root / "candidate.json"
    candidate.write_text(json.dumps(ctx.document))
    second = ctx.store.save(ctx.document, ctx.first.token, "another editor")
    for command in [
        ("import", "--file", candidate, "--expected-version", ctx.first.token, "--note", "stale", "--dry-run"),
        ("restore", "--version", ctx.first.token, "--expected-version", ctx.first.token, "--note", "stale", "--dry-run"),
    ]:
        result = ctx.cli(*command)
        assert result.returncode == 1, result.stdout
        assert "配置已被其他操作修改" in result.stderr
        assert ctx.store.read().token == second.token
    assert len(ctx.store.history()) == 2


def test_09_source_export_is_byte_exact_and_refuses_existing_and_symlink_destinations(ctx):
    document, source = config_migration.extract_legacy(ctx.source)
    store = ConfigStore(ctx.root / "migrated.sqlite3")
    store.initialize(document, source)
    target = ctx.root / "recovered-source"
    result = ctx.cli("export-sources", "--directory", target, selected_store=store)
    assert result.returncode == 0, result.stderr
    for name, info in source["files"].items():
        raw = (target / name).read_bytes()
        assert raw == (ctx.source / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == info["sha256"]
        assert (target / name).stat().st_mode & 0o777 == 0o600
    sentinel = target / "keep.txt"
    sentinel.write_text("must remain")
    assert ctx.cli("export-sources", "--directory", target, selected_store=store).returncode == 1
    assert sentinel.read_text() == "must remain"
    alias = ctx.root / "checkout-alias"
    alias.symlink_to(ROOT, target_is_directory=True)
    with pytest.raises(ConfigError):
        write_export(alias / "must-not-create.json", "{}")
    assert not (ROOT / "must-not-create.json").exists()


def test_10_admin_authentication_and_cross_session_or_unicode_csrf_block_writes(ctx, web):
    for route in ("keywords", "models", "config-history", "config-export"):
        response = web.client.get("/admin/" + route)
        assert response.status_code == 401
        assert "NEWS_SCORE_SYSTEM_MSG" not in response.get_data(as_text=True)
    data = {"action": "add", "main_keyword": "越权保存", "search_keywords": "词", "config_version": ctx.first.token}
    other_client = web.app.test_client()
    assert other_client.get("/admin/keywords", headers=web.headers).status_code == 200
    with other_client.session_transaction() as session:
        other_csrf = session["config_csrf"]
    assert other_csrf != web.csrf
    for csrf in ("", other_csrf, "非法中文令牌"):
        response = web.client.post("/admin/keywords", data={**data, "config_csrf": csrf}, headers=web.headers)
        assert response.status_code == 400
    response = web.client.post("/admin/keywords", data={**data, "config_csrf": web.csrf})
    assert response.status_code == 401
    assert ctx.store.read().token == ctx.first.token


def test_11_failed_request_releases_snapshot_and_missing_store_recovers_without_defaults(ctx, web, monkeypatch):
    import runtime_config
    absent = ctx.root / "absent.sqlite3"
    monkeypatch.setenv("SERP_CONFIG_STORE", str(absent))
    response = web.client.get("/admin/models", headers=web.headers)
    assert response.status_code == 503 and not absent.exists()
    assert runtime_config._request_snapshot.get() is None
    monkeypatch.setenv("SERP_CONFIG_STORE", str(ctx.store.path))
    with patch("routes.admin.read_model_config", side_effect=ConfigError("read failed")):
        assert web.client.get("/admin/models", headers=web.headers).status_code == 503
    assert runtime_config._request_snapshot.get() is None
    second = ctx.store.save(ctx.document, ctx.first.token, "after request failure")
    response = web.client.get("/admin/models", headers=web.headers)
    assert response.status_code == 200 and second.token in response.get_data(as_text=True)
    assert runtime_config._request_snapshot.get() is None


def test_12_admin_renders_stored_markup_as_text_and_exports_no_environment_secrets(ctx, web, monkeypatch):
    payload = '<img src=x onerror="alert(1)">'
    candidate = copy.deepcopy(ctx.document)
    candidate["settings"]["SEARCH_KEYWORDS"][payload] = [payload]
    candidate["prompts"]["NEWS_SCORE_SYSTEM_MSG"]["text"] += payload
    second = ctx.store.save(candidate, ctx.first.token, payload)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "private-export-sentinel")
    for route in ("/admin/keywords", "/admin/models", "/admin/config-history?version=" + ctx.first.token):
        response = web.client.get(route, headers=web.headers)
        assert response.status_code == 200
        assert payload not in response.get_data(as_text=True)
        assert "&lt;img" in response.get_data(as_text=True) or "\\u003cimg" in response.get_data(as_text=True)
    exported = web.client.get("/admin/config-export", headers=web.headers)
    assert exported.get_json()["document"] == second.document
    assert "private-export-sentinel" not in exported.get_data(as_text=True)


def test_13_delete_and_readd_keeps_tuned_mapping_and_last_keyword_cannot_be_deleted(ctx):
    from config_manager import edit_keywords
    candidate = copy.deepcopy(ctx.document)
    candidate["settings"]["NEWS_RULE_BASED_SCORING"] = [{"main_keyword": "养老", "title_contains": "推广", "score": 1}]
    second = ctx.store.save(candidate, ctx.first.token, "topic rule")
    removed = edit_keywords("delete", "养老", [], second.token)
    assert removed.document["keyword_prompt_ids"] == ctx.document["keyword_prompt_ids"]
    added = edit_keywords("add", "养老", ["养老"], removed.token)
    assert added.document["prompts"] == ctx.document["prompts"]
    assert added.document["keyword_prompt_ids"]["养老"] == ctx.document["keyword_prompt_ids"]["养老"]
    assert added.document["settings"]["NEWS_RULE_BASED_SCORING"] == candidate["settings"]["NEWS_RULE_BASED_SCORING"]
    only = added.document
    only["settings"]["SEARCH_KEYWORDS"] = {"养老": ["养老"]}
    last = ctx.store.save(only, added.token, "single topic")
    with pytest.raises(ConfigError, match="至少保留"):
        edit_keywords("delete", "养老", [], last.token)
    assert ctx.store.read().token == last.token


def test_14_keyword_rename_preserves_rule_scores_prompt_route_and_order(ctx):
    from config_manager import edit_keywords
    from news_scorer import rule_based_score, get_system_message
    document = copy.deepcopy(ctx.document)
    document["settings"]["NEWS_RULE_BASED_SCORING"] = [
        {"main_keyword": "养老", "title_contains": "民政部", "score": 5},
        {"main_keyword": "养老", "title_contains": "推广", "score": 1},
        {"main_keyword": "公积金", "title_contains": "缴存", "score": 3},
    ]
    second = ctx.store.save(document, ctx.first.token, "production rules")
    renamed = edit_keywords("edit", "养老专题", ["养老"], second.token, "养老")
    bind_process(renamed)
    assert list(value("SEARCH_KEYWORDS"))[0] == "养老专题"
    assert rule_based_score("民政部养老政策", "养老专题") == 5
    assert rule_based_score("推广活动", "养老专题") == 1
    assert rule_based_score("缴存通知", "公积金") == 3
    assert get_system_message("养老专题") == document["prompts"]["NEWS_SCORE_SYSTEM_MSG_ELDER_CARE"]["text"]
    assert ctx.store.read(second.token).document == document


def test_15_mixed_topic_revisions_require_separate_resume_even_after_restore(ctx):
    output = ctx.root / "output" / "2099-01-01"
    ctx.store.pin_batch(output, "养老")
    second = ctx.store.save(ctx.document, ctx.first.token, "next version")
    ctx.store.pin_batch(output, "公积金")
    restored = ctx.store.restore(ctx.first.token, second.token, "restore original contents")
    with pytest.raises(ConfigConflict, match="分别"):
        ctx.store.pin_batch(output)
    assert ctx.store.pin_batch(output, "养老")[0].token == ctx.first.token
    assert ctx.store.pin_batch(output, "公积金")[0].token == second.token
    assert ctx.store.pin_batch(output.parent / "2099-01-02")[0].token == restored.token


def test_16_background_launch_from_other_directory_uses_project_status_and_pinned_child(ctx):
    project = ctx.root / "isolated-project"
    project.mkdir()
    path = project / "run_manager.py"
    shutil.copyfile(ROOT / path.name, path)
    spec = importlib.util.spec_from_file_location("isolated_run_manager", path)
    manager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manager)
    captured = {}
    def start(*args, **kwargs):
        captured.update(kwargs)
        ctx.store.save(ctx.document, ctx.first.token, "changed just after dispatch")
        return SimpleNamespace(pid=987654321)
    with patch.object(manager.subprocess, "Popen", side_effect=start):
        status = manager.RunManager().start_run("2099-01-01")
    assert captured["cwd"] == str(project)
    assert captured["env"]["SERP_CONFIG_REVISION"] == ctx.first.token
    assert status["config_version"] == ctx.first.token
    saved_status = project / "output" / "run_status.json"
    assert saved_status.exists()
    assert json.loads(saved_status.read_text())["config_sha256"] == ctx.first.sha256
    assert not (ctx.root / "output" / "run_status.json").exists()
    assert ctx.store.pin_batch(project / "output" / "2099-01-01")[0].token == ctx.first.token


def test_17_pipeline_keeps_one_version_across_fetch_score_database_and_summary_children(ctx, monkeypatch):
    import main
    monkeypatch.setenv("ENABLE_ITEM_SUMMARIZER", "1")
    monkeypatch.setattr(main, "SEARCH_KEYWORDS", copy.deepcopy(ctx.document["settings"]["SEARCH_KEYWORDS"]))
    monkeypatch.setattr(main, "DEFAULT_KEYWORDS", list(ctx.document["settings"]["SEARCH_KEYWORDS"]))
    seen = []
    def stage(command, *args, **kwargs):
        parts = shlex.split(command)
        module = Path(parts[1]).stem
        if not seen:
            candidate = copy.deepcopy(ctx.document)
            candidate["prompts"]["NEWS_SCORE_SYSTEM_MSG"]["text"] += "\n批次开始后修改"
            candidate["settings"]["SEARCH_KEYWORDS"] = {"另一主题": ["另一检索词"]}
            ctx.store.save(candidate, ctx.first.token, "edit during pipeline")
        code = """
import importlib, json, socket, sys
from unittest.mock import Mock, patch
def blocked(*a, **k):
    raise AssertionError('Child attempted external access')
socket.socket.connect = blocked
socket.create_connection = blocked
with patch('db_utils.get_connection', return_value=Mock()):
    importlib.import_module(sys.argv[1])
from runtime_config import get_snapshot
print(json.dumps(get_snapshot().summary()))
"""
        child = subprocess.run([sys.executable, "-B", "-c", code, module], capture_output=True, text=True, timeout=20)
        assert child.returncode == 0, child.stderr
        summary = json.loads(child.stdout.splitlines()[-1])
        assert summary["version"] == ctx.first.token and summary["sha256"] == ctx.first.sha256
        seen.append(module)
        directory = ctx.root / "output" / "2099-01-01"
        if module == "fetch_and_filter":
            target = Path(parts[parts.index("--output") + 1])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps([{"title": "固定新闻", "link": "https://example.invalid/news"}]))
        elif module == "fetch_content":
            target = directory / "2099-01-01_养老.json"
            rows = json.loads(target.read_text())
            for row in rows:
                row["content"] = "固定正文"
            target.write_text(json.dumps(rows))
        elif module == "news_scorer":
            target = directory / "2099-01-01_养老_scored.json"
            target.write_text('[{"title":"固定新闻","score":5}]')
        return True
    with patch.object(main, "safe_subprocess_run", side_effect=stage), patch.object(main, "run_volume_alert") as alert:
        assert main.main("2099-01-01", keyword="养老") is True
    assert seen == ["fetch_and_filter", "fetch_content", "news_scorer", "write_to_mysql", "news_item_summarizer"]
    alert.assert_called_once_with(target_date="2099-01-01", keywords=["养老"])
    log = next((ctx.root / "output" / "2099-01-01").glob("run_*.log")).read_text()
    assert ctx.first.token in log and ctx.first.sha256 in log


def test_18_admin_rescore_finds_project_outputs_from_other_cwd_and_rejects_invalid_date(ctx, web, monkeypatch):
    import routes.admin as admin
    project = ctx.root / "rescore-project"
    directory = project / "output" / "2099-01-01"
    directory.mkdir(parents=True)
    monkeypatch.setattr(admin, "__file__", str(project / "routes" / "admin.py"))
    ctx.store.pin_batch(directory)
    (directory / "2099-01-01_养老_scored.json").write_text('[{"score":0}]')
    ctx.store.save(ctx.document, ctx.first.token, "after original scoring")
    # A failing mocked scorer avoids all business database work while proving dispatch.
    with patch("subprocess.run", return_value=SimpleNamespace(returncode=1)) as run, patch.object(admin, "get_connection") as database:
        response = web.client.post("/admin/runs/rescore", data={"date": "2099-01-01"}, headers=web.headers)
        assert response.status_code == 200
        run.assert_called_once()
        assert run.call_args.kwargs["cwd"] == str(project)
        assert run.call_args.kwargs["env"]["SERP_CONFIG_REVISION"] == ctx.first.token
        database.assert_not_called()
        run.reset_mock()
        invalid = web.client.post("/admin/runs/rescore", data={"date": "../bad-output"}, headers=web.headers)
        assert invalid.status_code == 400
        run.assert_not_called()
    with sqlite3.connect(ctx.store.path) as connection:
        assert {row[0] for row in connection.execute("SELECT output_directory FROM batch_pins")} == {str(directory)}


def test_19_unusable_configuration_stops_pipeline_before_any_service_or_output(ctx, monkeypatch):
    import main
    absent = ctx.root / "missing.sqlite3"
    monkeypatch.setenv("SERP_CONFIG_STORE", str(absent))
    with patch.object(main, "execute_news_fetching") as fetch, patch.object(main, "safe_subprocess_run") as child, patch.object(main, "run_volume_alert") as alert:
        assert main.main("2099-01-01", keyword="养老") is False
        fetch.assert_not_called()
        child.assert_not_called()
        alert.assert_not_called()
    assert not absent.exists()
    assert not (ctx.root / "output" / "2099-01-01").exists()


def test_20_all_summary_and_annotation_templates_use_pinned_model_and_messages(ctx):
    import news_region_utils as region
    import news_item_summarizer as summary
    document = copy.deepcopy(ctx.document)
    for stage in document["models"].values():
        stage["model"] = "pinned-test-model"
    pinned = ctx.store.save(document, ctx.first.token, "model candidate")
    bind_process(pinned)
    candidate = copy.deepcopy(document)
    candidate["models"]["region"]["model"] = "later-test-model"
    ctx.store.save(candidate, pinned.token, "next active version")
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content='{"summary":"固定摘要","region":"江苏省","business_types":[]}'))])
    cases = [
        (region.call_summary_and_region_llm, "NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500_GJJ_REGION", "NEWS_ITEM_SUMMARY_USER_PROMPT_500_GJJ_REGION"),
        (region.call_region_and_business_type_llm, "NEWS_ITEM_SHORT_CONTENT_SYSTEM_PROMPT_GJJ_REGION", "NEWS_ITEM_SHORT_CONTENT_USER_PROMPT_GJJ_REGION"),
        (region.call_business_type_llm, "NEWS_BUSINESS_TYPE_SYSTEM_PROMPT_GJJ", "NEWS_BUSINESS_TYPE_USER_PROMPT_GJJ"),
        (region.call_region_llm, "NEWS_REGION_SYSTEM_PROMPT_GJJ", "NEWS_REGION_USER_PROMPT_GJJ"),
        (summary.call_llm, "NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500", "NEWS_ITEM_SUMMARY_USER_PROMPT_500"),
    ]
    with patch.object(region._pool, "get_client", return_value=client), patch.object(summary._pool, "get_client", return_value=client):
        for call, system_id, user_id in cases:
            call("固定标题", "固定正文")
            arguments = client.chat.completions.create.call_args.kwargs
            expected_user = document["prompts"][user_id]["text"].format(title="固定标题", content="固定正文", business_type_catalog=region.format_business_type_catalog({}))
            assert arguments["messages"] == [{"role": "system", "content": document["prompts"][system_id]["text"]}, {"role": "user", "content": expected_user}]
            assert arguments["model"] == "pinned-test-model"
            assert arguments["timeout"] == 60 and arguments["stream"] is False
            assert ("temperature" not in arguments) if call is summary.call_llm else arguments["temperature"] == 0.2
    assert get_snapshot().token == pinned.token
