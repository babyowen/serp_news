"""Offline coverage of live configuration reads, admin edits and model messages."""
import base64
import copy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_config_store as fixtures
from runtime_config import begin_request, end_request, get_snapshot, child_environment


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        fixtures.StoreTests.setUp(self)
        from app import app
        self.app = app
        self.client = app.test_client()
        credentials = f"{os.environ.get('ADMIN_USERNAME', 'admin')}:{os.environ.get('ADMIN_PASSWORD', 'changeme')}"
        self.headers = {"Authorization": "Basic " + base64.b64encode(credentials.encode()).decode()}

    def tearDown(self):
        fixtures.StoreTests.tearDown(self)

    def csrf(self):
        response = self.client.get("/admin/keywords", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            return session["config_csrf"]

    def test_admin_save_refreshes_live_version_and_stale_form_conflicts(self):
        csrf = self.csrf()
        source_before = (fixtures.ROOT / "config.py").read_bytes()
        data = {"action": "add", "main_keyword": '引号"主题\\', "search_keywords": '检索"词',
                "config_version": self.first.token, "config_csrf": csrf}
        saved = self.client.post("/admin/keywords", data=data, headers=self.headers)
        self.assertEqual(saved.status_code, 302)
        second = self.store.read()
        self.assertIn('引号"主题\\', second.document["settings"]["SEARCH_KEYWORDS"])
        self.assertNotEqual(second.token, self.first.token)
        current_page = self.client.get("/admin/keywords", headers=self.headers)
        self.assertIn(second.token.encode(), current_page.data)
        self.assertIn("配置已保存为新版本".encode(), current_page.data)
        data["main_keyword"] = "过期修改"
        conflict = self.client.post("/admin/keywords", data=data, headers=self.headers)
        self.assertEqual(conflict.status_code, 409)
        self.assertIn("配置已被其他操作修改".encode(), conflict.data)
        self.assertEqual(self.store.read().token, second.token)
        self.assertEqual((fixtures.ROOT / "config.py").read_bytes(), source_before)

    def test_keyword_rename_preserves_position_and_special_prompt(self):
        data = {"action": "edit", "old_keyword": "养老", "main_keyword": "养老专题", "search_keywords": "养老",
                "config_version": self.first.token, "config_csrf": self.csrf()}
        result = self.client.post("/admin/keywords", data=data, headers=self.headers)
        self.assertEqual(result.status_code, 302)
        document = self.store.read().document
        self.assertEqual(next(iter(document["settings"]["SEARCH_KEYWORDS"])), "养老专题")
        self.assertEqual(document["keyword_prompt_ids"]["养老专题"], self.document["keyword_prompt_ids"]["养老"])

    def test_models_and_export_include_archived_prompts_without_credentials(self):
        response = self.client.get("/admin/models", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NEWS_SUMMARY_JUDGE_SYSTEM_PROMPT", response.data)
        exported = self.client.get("/admin/config-export", headers=self.headers)
        self.assertEqual(exported.status_code, 200)
        bundle = exported.get_json()
        self.assertEqual(bundle["document"], self.document)
        self.assertNotIn('"api_key":', exported.get_data(as_text=True))
        self.assertEqual(exported.headers["Cache-Control"], "no-store")

    def test_history_preview_restore_and_csrf(self):
        changed = copy.deepcopy(self.document)
        changed["prompts"]["NEWS_SCORE_SYSTEM_MSG"]["text"] += "\n测试修改"
        second = self.store.save(changed, self.first.token, "temporary")
        preview = self.client.get("/admin/config-history", query_string={"version": self.first.token}, headers=self.headers)
        self.assertEqual(preview.status_code, 200)
        self.assertIn(b"prompts.NEWS_SCORE_SYSTEM_MSG.text", preview.data)
        data = {"version": self.first.token, "config_version": second.token, "note": "restore test"}
        denied = self.client.post("/admin/config-restore", data=data, headers=self.headers)
        self.assertEqual(denied.status_code, 400)
        data["config_csrf"] = self.csrf()
        restored = self.client.post("/admin/config-restore", data=data, headers=self.headers)
        self.assertEqual(restored.status_code, 302)
        self.assertEqual(self.store.read().document, self.document)
        self.assertEqual(len(self.store.history()), 3)
        history_page = self.client.get("/admin/config-history", headers=self.headers)
        self.assertIn("已恢复为一个新版本".encode(), history_page.data)

    def test_live_request_is_consistent_while_active_changes(self):
        token = begin_request()
        try:
            before = get_snapshot()
            changed = copy.deepcopy(self.document)
            changed["settings"]["SEARCH_KEYWORDS"]["新主题"] = ["新词"]
            after = self.store.save(changed, before.token, "concurrent request")
            self.assertEqual(get_snapshot().token, before.token)
            history = self.store.history(at_version=before.token)
            self.assertEqual(history[0]["version"], before.token)
            self.assertTrue(history[0]["active"])
        finally:
            end_request(token)
        token = begin_request()
        try:
            self.assertEqual(get_snapshot().token, after.token)
        finally:
            end_request(token)

    def test_child_uses_pinned_version_from_different_working_directory(self):
        env = child_environment(self.first)
        changed = copy.deepcopy(self.document)
        changed["settings"]["SEARCH_KEYWORDS"]["新主题"] = ["新词"]
        self.store.save(changed, self.first.token, "after dispatch")
        env["PYTHONPATH"] = str(fixtures.ROOT)
        result = subprocess.run([sys.executable, "-B", "-c", "from runtime_config import get_snapshot; print(get_snapshot().token)"],
                                cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.first.token)

    def test_model_calls_keep_messages_and_actual_parameters(self):
        import news_scorer
        import news_item_summarizer
        import news_region_utils
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="4"))])
        tokenizer = SimpleNamespace(get_encoding=lambda _: SimpleNamespace(encode=lambda text: []))
        with patch.dict(sys.modules, {"tiktoken": tokenizer}), patch.object(news_scorer._scoring_client_pool, "get_client", return_value=client):
            self.assertEqual(news_scorer.score_news("标题", "正文", "工商银行", "烟草服务银行"), 4)
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["messages"][0]["content"], self.document["prompts"]["NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK"]["text"])
        self.assertEqual(kwargs["messages"][1]["content"], self.document["prompts"]["NEWS_SCORE_PROMPT"]["text"].format(title="标题", content="正文", keyword="工商银行"))
        self.assertEqual(kwargs["model"], "deepseek-v4-flash")
        self.assertEqual(kwargs["temperature"], 1)
        with patch.object(news_item_summarizer._pool, "get_client", return_value=client):
            news_item_summarizer.call_llm("标题", "正文")
        self.assertNotIn("temperature", client.chat.completions.create.call_args.kwargs)
        with patch.object(news_region_utils._pool, "get_client", return_value=client):
            news_region_utils._call_llm("系统提示词", "用户提示词")
        self.assertEqual(client.chat.completions.create.call_args.kwargs["temperature"], 0.2)

    def test_main_quotes_keyword_arguments_before_shell_execution(self):
        import main
        malicious_looking_keyword = '主题"; echo forbidden #'
        with patch.object(main, "safe_subprocess_run", return_value=True) as run, patch.object(main.os.path, "exists", side_effect=[True, False]):
            main.execute_scoring("2099-01-01", malicious_looking_keyword)
        self.assertEqual(shlex.split(run.call_args.args[0]), [sys.executable, "news_scorer.py", malicious_looking_keyword, "2099-01-01"])


if __name__ == "__main__":
    unittest.main()
