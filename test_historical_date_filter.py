"""Regression coverage for issue #15 historical backfill date filtering."""

from datetime import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from fetch_and_filter import (
    is_baidu_news_on_date,
    is_bing_news_on_date,
    is_duckduckgo_news_on_date,
    is_google_news_on_date,
)
from news_fetcher import fetch_serpapi_duckduckgo_news, fetch_serpapi_google_news


NOW = datetime(2026, 7, 16, 10, 0, 0)
HISTORICAL_DATE = "2026-07-12"


class FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"news_results": []}


class HistoricalDateFilterTests(unittest.TestCase):
    def test_baidu_accepts_matching_absolute_date(self):
        self.assertTrue(is_baidu_news_on_date("2026-07-12 09:30", HISTORICAL_DATE, NOW))

    def test_google_accepts_matching_absolute_date(self):
        self.assertTrue(is_google_news_on_date("07/12/2026, 09:30 AM", HISTORICAL_DATE, NOW))

    def test_bing_accepts_matching_absolute_date(self):
        self.assertTrue(is_bing_news_on_date("2026-07-12", HISTORICAL_DATE, NOW))

    def test_duckduckgo_accepts_matching_absolute_date(self):
        self.assertTrue(is_duckduckgo_news_on_date("2026-07-12", HISTORICAL_DATE, NOW))

    def test_historical_backfill_rejects_relative_dates_for_every_source(self):
        checks = (
            (is_baidu_news_on_date, "昨天 09:30"),
            (is_google_news_on_date, "12小时前"),
            (is_bing_news_on_date, "20h"),
            (is_duckduckgo_news_on_date, "1 day ago"),
        )
        for check, value in checks:
            with self.subTest(source=check.__name__):
                self.assertFalse(check(value, HISTORICAL_DATE, NOW))

    def test_historical_backfill_rejects_future_absolute_dates(self):
        checks = (
            (is_baidu_news_on_date, "2026-07-16 09:30"),
            (is_google_news_on_date, "07/16/2026, 09:30 AM"),
            (is_bing_news_on_date, "2026-07-16"),
            (is_duckduckgo_news_on_date, "2026-07-16"),
        )
        for check, value in checks:
            with self.subTest(source=check.__name__):
                self.assertFalse(check(value, HISTORICAL_DATE, NOW))

    def test_daily_run_still_accepts_relative_dates_for_yesterday(self):
        target_date = "2026-07-15"
        checks = (
            (is_baidu_news_on_date, "昨天 09:30"),
            (is_google_news_on_date, "12小时前"),
            (is_bing_news_on_date, "20h"),
            (is_duckduckgo_news_on_date, "1 day ago"),
        )
        for check, value in checks:
            with self.subTest(source=check.__name__):
                self.assertTrue(check(value, target_date, NOW))

    @patch("news_fetcher.requests.get", return_value=FakeResponse())
    def test_google_query_uses_exact_custom_date_range(self, mock_get):
        fetch_serpapi_google_news("工商银行", fetch_date=HISTORICAL_DATE, max_pages=1)
        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["tbs"], "cdr:1,cd_min:7/12/2026,cd_max:7/12/2026")

    @patch("news_fetcher.requests.get", return_value=FakeResponse())
    def test_duckduckgo_query_uses_exact_custom_date_range(self, mock_get):
        fetch_serpapi_duckduckgo_news("工商银行", fetch_date=HISTORICAL_DATE, max_pages=1)
        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["df"], "2026-07-12..2026-07-12")

    def test_fetch_entrypoint_keeps_only_the_requested_historical_date(self):
        import fetch_and_filter

        with tempfile.TemporaryDirectory() as tmp_dir:
            previous_cwd, previous_argv = os.getcwd(), sys.argv
            os.chdir(tmp_dir)
            output_path = os.path.join(tmp_dir, "filtered.json")
            try:
                with patch.object(
                    fetch_and_filter,
                    "fetch_serpapi_google_news",
                    return_value={"news_results": [
                        {"title": "目标日", "link": "https://example.test/target", "date": "07/12/2026, 09:30 AM"},
                        {"title": "未来日期", "link": "https://example.test/future", "date": "07/16/2026, 09:30 AM"},
                    ]},
                ) as google, patch.object(
                    fetch_and_filter, "fetch_serpapi_baidu_news", return_value={"organic_results": []}
                ), patch.object(
                    fetch_and_filter, "fetch_serpapi_bing_news", return_value={"organic_results": []}
                ), patch.object(
                    fetch_and_filter, "fetch_serpapi_duckduckgo_news", return_value={"news_results": []}
                ), patch.object(fetch_and_filter, "log_script_start"), patch.object(fetch_and_filter, "log_script_complete"):
                    sys.argv = ["fetch_and_filter.py", "工商银行", HISTORICAL_DATE, "--output", output_path]
                    self.assertTrue(fetch_and_filter.main())
            finally:
                os.chdir(previous_cwd)
                sys.argv = previous_argv

            with open(output_path, encoding="utf-8") as output_file:
                items = json.load(output_file)

        self.assertEqual([item["title"] for item in items], ["目标日"])
        self.assertEqual(items[0]["date"], HISTORICAL_DATE)
        self.assertEqual(items[0]["fetchdate"], HISTORICAL_DATE)
        self.assertEqual(google.call_args.kwargs["fetch_date"], HISTORICAL_DATE)


if __name__ == "__main__":
    unittest.main()
