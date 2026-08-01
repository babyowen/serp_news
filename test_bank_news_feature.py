"""Ten regression tests for issue #14 bank-news monitoring."""

import json
import os
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import (
    DEFAULT_KEYWORDS,
    KEYWORD_SPECIFIC_SYSTEM_PROMPTS,
    NEWS_SCORE_SYSTEM_MSG,
    NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK,
    SEARCH_KEYWORDS,
)
from news_dedup_schema import ensure_keyword_scoped_dedup_index
from news_scorer import get_system_message


BANKS = [
    "工商银行", "农业银行", "中国银行", "建设银行",
    "交通银行", "中信银行", "浦发银行", "南京银行",
]


class FakeCursor:
    def __init__(self, index_rows):
        self.index_rows = index_rows
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self.index_rows

    def close(self):
        pass


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor([])

    def cursor(self):
        return self.cursor_instance

    def close(self):
        pass


class BankNewsFeatureTests(unittest.TestCase):
    def test_bank_keyword_configuration_is_exact(self):
        self.assertEqual(SEARCH_KEYWORDS["烟草服务银行"], BANKS)


    def test_bank_keyword_is_automatically_in_the_batch(self):
        self.assertIn("烟草服务银行", DEFAULT_KEYWORDS)


    def test_bank_keyword_uses_its_dedicated_scoring_prompt(self):
        self.assertEqual(
            KEYWORD_SPECIFIC_SYSTEM_PROMPTS["烟草服务银行"],
            NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK,
        )
        self.assertEqual(
            get_system_message("烟草服务银行", "工商银行"),
            NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK,
        )
        self.assertIn("江苏省内", NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK)
        self.assertIn("银行与烟草", NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK)


    def test_other_keywords_keep_the_generic_scoring_prompt(self):
        self.assertEqual(get_system_message("数字政务", "数字政务"), NEWS_SCORE_SYSTEM_MSG)


    def test_bank_prompt_contains_the_confirmed_score_boundaries(self):
        self.assertIn("5分：银行总行", NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK)
        self.assertIn("4分：江苏省内银行", NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK)
        self.assertIn("银行与烟草", NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK)
        self.assertIn("不得给4分或5分", NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK)


    def test_bank_prompt_requires_one_of_the_eight_banks(self):
        self.assertIn("八家银行", NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK)
        self.assertIn("直接评1分", NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK)
        self.assertIn("工商银行、农业银行、中国银行、建设银行、交通银行、中信银行、浦发银行、南京银行", NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK)


    def test_bank_prompt_caps_brand_promotion_at_three(self):
        self.assertIn("直接评3分", NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK)
        self.assertIn("服务纪实", NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK)
        self.assertIn("企业形象稿", NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK)


    def test_single_keyword_run_selects_only_bank_monitoring(self):
        import main

        self.assertEqual(main.get_active_keywords("烟草服务银行"), ["烟草服务银行"])


    def test_default_run_selects_all_main_keywords(self):
        import main

        self.assertEqual(main.get_active_keywords(), DEFAULT_KEYWORDS)


    def test_unknown_single_keyword_is_rejected(self):
        import main

        with self.assertRaises(ValueError):
            main.get_active_keywords("不存在的关键词")


    def test_single_keyword_mode_scopes_every_pipeline_stage(self):
        import main

        with tempfile.TemporaryDirectory() as tmp_dir:
            previous_cwd = os.getcwd()
            previous_run_log_path = os.environ.get("RUN_LOG_PATH")
            os.chdir(tmp_dir)
            try:
                with patch.object(main, "log_script_start"), \
                     patch.object(main, "log_script_complete"), \
                     patch.object(main, "execute_news_fetching", return_value=True) as fetch, \
                     patch.object(main, "execute_content_fetching", return_value=True) as content, \
                     patch.object(main, "execute_scoring_concurrent", return_value=(1, 0)) as scoring, \
                     patch.object(main, "run_step", return_value=True) as database, \
                     patch.object(main, "safe_subprocess_run", return_value=True) as summary, \
                     patch.object(main, "run_volume_alert") as volume_alert:
                    self.assertTrue(main.main("2099-01-01", keyword="烟草服务银行"))
            finally:
                os.chdir(previous_cwd)
                if previous_run_log_path is None:
                    os.environ.pop("RUN_LOG_PATH", None)
                else:
                    os.environ["RUN_LOG_PATH"] = previous_run_log_path

        self.assertEqual(fetch.call_args_list[0].args, ("2099-01-01", "烟草服务银行"))
        self.assertEqual(content.call_args_list[0].args, ("2099-01-01", "烟草服务银行"))
        self.assertEqual(scoring.call_args.args[:2], ("2099-01-01", ["烟草服务银行"]))
        self.assertIn("烟草服务银行", shlex.split(database.call_args.args[0]))
        self.assertIn("烟草服务银行", shlex.split(summary.call_args.args[0]))
        self.assertEqual(volume_alert.call_args.kwargs["keywords"], ["烟草服务银行"])


    def test_same_news_under_two_bank_terms_keeps_first_search_keyword(self):
        import main

        def fake_subprocess(cmd, *_args, **_kwargs):
            parts = shlex.split(cmd)
            search_keyword = parts[2]
            output_path = Path(parts[parts.index("--output") + 1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps([{
                "title": "银行与烟草合作新闻",
                "link": "https://example.test/news/1",
                "content": "正文",
            }], ensure_ascii=False), encoding="utf-8")
            return True

        with tempfile.TemporaryDirectory() as tmp_dir:
            previous_cwd = os.getcwd()
            os.chdir(tmp_dir)
            try:
                with patch.dict(
                    main.SEARCH_KEYWORDS,
                    {"烟草服务银行": ["工商银行", "农业银行"]},
                ), patch.object(main, "safe_subprocess_run", side_effect=fake_subprocess):
                    self.assertTrue(main.execute_news_fetching("2099-01-01", "烟草服务银行"))

                merged_path = Path("output/2099-01-01/2099-01-01_烟草服务银行.json")
                merged_items = json.loads(merged_path.read_text(encoding="utf-8"))
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(len(merged_items), 1)
        self.assertEqual(merged_items[0]["keyword"], "烟草服务银行")
        self.assertEqual(merged_items[0]["search_keyword"], "工商银行")


    def test_dashboard_shows_bank_keyword_before_its_first_news_arrives(self):
        import routes.views as views
        from app import app

        with patch.object(views, "get_connection", return_value=FakeConnection()), patch.object(
            views, "get_table_name", return_value="scored_news_test"
        ):
            response = app.test_client().get("/?date_from=2099-01-01&date_to=2099-01-01")

        self.assertEqual(response.status_code, 200)
        self.assertIn("烟草服务银行".encode(), response.data)
        self.assertIn("当前筛选条件下暂无烟草服务银行新闻".encode(), response.data)


    def test_legacy_global_index_is_migrated_to_keyword_scoped_index(self):
        cursor = FakeCursor([
            ("title_link", "title", 1),
            ("title_link", "link", 2),
        ])

        self.assertTrue(ensure_keyword_scoped_dedup_index(cursor, "scored_news_test"))
        self.assertEqual(len(cursor.executed), 2)
        alter_sql, params = cursor.executed[-1]
        self.assertIsNone(params)
        self.assertIn("DROP INDEX `title_link`", alter_sql)
        self.assertIn(
            "ADD UNIQUE INDEX `keyword_title_link` (`keyword`(100), `title`, `link`(255))",
            alter_sql,
        )


    def test_existing_keyword_scoped_index_is_not_changed_again(self):
        cursor = FakeCursor([
            ("keyword_title_link", "keyword", 1),
            ("keyword_title_link", "title", 2),
            ("keyword_title_link", "link", 3),
        ])

        self.assertFalse(ensure_keyword_scoped_dedup_index(cursor, "scored_news_test"))
        self.assertEqual(len(cursor.executed), 1)


    def test_legacy_index_is_removed_even_when_new_index_already_exists(self):
        cursor = FakeCursor([
            ("keyword_title_link", "keyword", 1),
            ("keyword_title_link", "title", 2),
            ("keyword_title_link", "link", 3),
            ("title_link", "title", 1),
            ("title_link", "link", 2),
        ])

        self.assertTrue(ensure_keyword_scoped_dedup_index(cursor, "scored_news_test"))
        alter_sql, _ = cursor.executed[-1]
        self.assertIn("DROP INDEX `title_link`", alter_sql)
        self.assertNotIn("ADD UNIQUE INDEX", alter_sql)


    def test_dedup_migration_rejects_unsafe_table_name(self):
        with self.assertRaises(ValueError):
            ensure_keyword_scoped_dedup_index(FakeCursor([]), "scored_news; DROP TABLE scored_news")
