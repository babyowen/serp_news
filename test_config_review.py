"""Regression cases for the four issues found during the Issue #18 review."""
import ast
import copy
import sys
from unittest.mock import Mock, patch

import pytest

from config_migration import extract_legacy
from config_schema import ConfigError
from runtime_config import get_snapshot
from test_config_system import ctx  # Reuse the isolated store/source fixture.


@pytest.mark.parametrize("filename,override", [
    ("news_scorer.py", "NEWS_SCORE_SYSTEM_MSG = 'production prompt'"),
    ("news_scorer.py", "DEEPSEEK_BASE_URL = 'https://production.invalid'"),
    ("news_scorer.py", "KEYWORD_SPECIFIC_SYSTEM_PROMPTS['养老'] = 'production rule'"),
    ("news_item_summarizer.py", "NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500 = 'production prompt'"),
    ("news_item_summarizer.py", "DEEPSEEK_BASE_URL = 'https://production.invalid'"),
    ("news_region_utils.py", "NEWS_REGION_SYSTEM_PROMPT_GJJ = 'production prompt'"),
    ("news_region_utils.py", "DEEPSEEK_BASE_URL = 'https://production.invalid'"),
])
def test_migration_rejects_stage_overrides_before_initializing(ctx, filename, override):
    path = ctx.source / filename
    source = path.read_text(encoding="utf-8")
    line = next(node.lineno for node in ast.parse(source).body if isinstance(node, ast.FunctionDef))
    lines = source.splitlines(keepends=True)
    lines.insert(line - 1, override + "\n\n")
    path.write_text("".join(lines), encoding="utf-8")
    result = ctx.cli("init", "--source", ctx.source, "--dry-run")
    assert result.returncode == 1
    assert filename in result.stderr
    assert "人工核对" in result.stderr
    assert ctx.store.read().token == ctx.first.token


def test_migration_rejects_dynamic_model_selection_and_inline_message_changes(ctx):
    scorer = ctx.source / "news_scorer.py"
    original = scorer.read_text(encoding="utf-8")
    assignment = "model_to_use = 'deepseek-v4-flash'"
    assert assignment in original
    scorer.write_text(original.replace(assignment, assignment + "\n    model_to_use += '-production-tuned'"), encoding="utf-8")
    with pytest.raises(ConfigError, match="人工核对"):
        extract_legacy(ctx.source)
    scorer.write_text(original, encoding="utf-8")
    summary = ctx.source / "news_item_summarizer.py"
    original = summary.read_text(encoding="utf-8")
    before = '"content": NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500'
    assert before in original
    summary.write_text(original.replace(before, '"content": "inline production prompt"'), encoding="utf-8")
    with pytest.raises(ConfigError, match="人工核对"):
        extract_legacy(ctx.source)


def test_migration_still_preserves_explicit_model_and_parameter_tuning(ctx):
    scorer = ctx.source / "news_scorer.py"
    source = scorer.read_text(encoding="utf-8")
    assert "model_to_use = 'deepseek-v4-flash'" in source and "temperature=1," in source
    source = source.replace("model_to_use = 'deepseek-v4-flash'", "model_to_use = 'production-tuned-model'")
    source = source.replace("temperature=1,", "temperature=0.7,\n                max_tokens=512,")
    scorer.write_text("# Comment-only changes are harmless\n" + source, encoding="utf-8")
    document, _ = extract_legacy(ctx.source)
    assert document["models"]["scoring"]["model"] == "production-tuned-model"
    assert document["models"]["scoring"]["parameters"]["temperature"] == 0.7
    assert document["models"]["scoring"]["parameters"]["max_tokens"] == 512
    assert "temperature" not in document["models"]["item_summarizer"]["parameters"]
    assert document["prompts"] == ctx.document["prompts"]


def test_rename_does_not_merge_retained_rules_but_allows_existing_topic_edits(ctx):
    from config_manager import edit_keywords
    document = copy.deepcopy(ctx.document)
    assert "公积金" not in document["keyword_prompt_ids"]  # Exercise a rules-only conflict.
    document["settings"]["NEWS_RULE_BASED_SCORING"] = [
        {"main_keyword": "公积金", "title_contains": "通知", "score": 0},
        {"main_keyword": "养老", "title_contains": "通知", "score": 5},
    ]
    initial = ctx.store.save(document, ctx.first.token, "topic rules")
    removed = edit_keywords("delete", "公积金", [], initial.token)
    with pytest.raises(ConfigError, match="评分规则"):
        edit_keywords("edit", "公积金", ["养老"], removed.token, "养老")
    assert ctx.store.read().token == removed.token
    assert ctx.store.read().document == removed.document
    edited = edit_keywords("edit", "养老", ["养老政策"], removed.token, "养老")
    assert edited.document["settings"]["NEWS_RULE_BASED_SCORING"] == document["settings"]["NEWS_RULE_BASED_SCORING"]
    renamed = edit_keywords("edit", "养老专题", ["养老"], edited.token, "养老")
    assert renamed.document["settings"]["NEWS_RULE_BASED_SCORING"] == [
        {"main_keyword": "公积金", "title_contains": "通知", "score": 0},
        {"main_keyword": "养老专题", "title_contains": "通知", "score": 5},
    ]


@pytest.mark.parametrize("mode", ["all", "single", "resume"])
def test_summary_only_processes_topics_bound_to_its_selected_version(ctx, monkeypatch, mode):
    import news_item_summarizer as summary
    document = copy.deepcopy(ctx.document)
    document["settings"]["SEARCH_KEYWORDS"] = {"当前主题": ["当前词"], "另一个主题": ["另一个词"]}
    selected = ctx.store.save(document, ctx.first.token, "summary topics")
    args = ["news_item_summarizer.py", "2099-01-01"]
    expected_topics = ["当前主题", "另一个主题"]
    if mode == "single":
        args += ["--keyword", "当前主题"]
        expected_topics = ["当前主题"]
    elif mode == "resume":
        ctx.store.pin_batch(ctx.root / "output" / "2099-01-01")
        later = copy.deepcopy(document)
        later["settings"]["SEARCH_KEYWORDS"] = {"下一批主题": ["下一批词"]}
        ctx.store.save(later, selected.token, "new active version")
    records = [
        (1, "当前标题", "正文" * 300, "当前主题"),
        (2, "另一标题", "正文" * 300, "另一个主题"),
        (3, "历史标题", "正文" * 300, "已经删除的主题"),
    ]
    class Cursor:
        def __init__(self):
            self.updated = set()
            self.rows = []
            self.queries = []

        def execute(self, sql, params):
            if sql.startswith("SELECT id,"):
                self.queries.append((sql, params))
                topics = params[1:] if "AND keyword" in sql else None
                self.rows = [row for row in records if row[0] not in self.updated and (topics is None or row[3] in topics)]
            elif sql.startswith("UPDATE "):
                self.updated.add(params[-1])
            else:
                raise AssertionError("Unexpected database operation: " + sql)

        def fetchall(self):
            return self.rows

    cursor = Cursor()
    connection = Mock()
    connection.cursor.return_value = cursor
    monkeypatch.setattr(sys, "argv", args)
    with patch.object(summary, "get_conn", return_value=connection), patch.object(summary, "table_has_region_column", return_value=False), patch.object(summary, "table_has_business_types_column", return_value=False), patch.object(summary, "call_llm", return_value="固定摘要") as llm, patch.object(summary.time, "sleep"):
        assert summary.main() is True
    assert cursor.updated == {row[0] for row in records if row[3] in expected_topics}
    assert llm.call_count == len(expected_topics)
    assert all(tuple(query[1]) == ("2099-01-01", *expected_topics) for query in cursor.queries)
    assert get_snapshot().token == selected.token


def test_summary_rejects_unbound_explicit_topic_before_database_or_llm(ctx, monkeypatch):
    import news_item_summarizer as summary
    monkeypatch.setattr(sys, "argv", ["news_item_summarizer.py", "2099-01-01", "--keyword", "已经删除的主题"])
    with patch.object(summary, "get_conn") as database, patch.object(summary, "call_llm") as llm:
        assert summary.main() is False
    database.assert_not_called()
    llm.assert_not_called()


def test_prompt_ids_cannot_shadow_settings_derived_values_or_credentials(ctx):
    for name in ("DEFAULT_KEYWORDS", "DEFAULT_KEYWORD", "SEARCH_KEYWORDS", "KEYWORD_SPECIFIC_SYSTEM_PROMPTS", "NEWS_SUMMARY_MODELS", "NEWS_SUMMARY_PLATFORM", "NEWS_SUMMARY_MODEL", "USE_EMOJI_OUTPUT", "API_KEY", "DEEPSEEK_API_KEY", "GNEWS_API_KEY", "SERPAPI_KEY"):
        for status in ("active", "archived"):
            document = copy.deepcopy(ctx.document)
            document["prompts"][name] = {"text": "must not shadow runtime settings", "status": status}
            with pytest.raises(ConfigError, match="保留名称"):
                ctx.store.save(document, ctx.first.token, "invalid namespace")
    assert ctx.store.read().token == ctx.first.token
    document = copy.deepcopy(ctx.document)
    document["prompts"]["CUSTOM_SCORE_SYSTEM_PROMPT"] = {"text": "valid additional prompt", "status": "active"}
    saved = ctx.store.save(document, ctx.first.token, "valid additional prompt")
    assert saved.document["prompts"]["CUSTOM_SCORE_SYSTEM_PROMPT"]["text"] == "valid additional prompt"
