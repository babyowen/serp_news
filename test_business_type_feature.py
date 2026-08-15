"""Regression coverage for Issue #10 housing-fund business-type labeling."""

import unittest
from unittest import mock

from news_business_type_utils import (
    BUSINESS_TYPE_LEVEL1,
    build_business_type_catalog,
    format_business_type_catalog,
    get_business_type_dashboard,
    merge_secondary_labels,
    normalize_business_types,
    resolve_canonical_level2,
    validate_llm_business_types,
    validate_business_type_table_name,
)
from news_business_type_analyzer import build_query
from news_region_utils import (
    call_business_type_llm,
    call_region_and_business_type_llm,
    call_summary_and_region_llm,
)


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self.rows


class MergeCursor:
    def __init__(self):
        self.aliases = {("贷款", "首付比例"): "贷款首付"}
        self.news = [(1, '[{"level1":"贷款","level2":"贷款首付"},{"level1":"提取","level2":"物业费"}]')]
        self.rows = []

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT level1, retired_level2"):
            self.rows = [(level1, retired, canonical) for (level1, retired), canonical in self.aliases.items()]
        elif normalized.startswith("SELECT id, business_types"):
            self.rows = self.news
        elif normalized.startswith("UPDATE scored_news_test SET business_types"):
            self.news = [(params[1], params[0])]
        elif normalized.startswith("INSERT INTO news_business_type_aliases"):
            self.aliases[(params[1], params[2])] = params[3]
        elif normalized.startswith("UPDATE news_business_type_aliases"):
            old = params[3]
            for key, value in list(self.aliases.items()):
                if value == old:
                    self.aliases[key] = params[0]

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class MergeConnection:
    def __init__(self):
        self.cursor_instance = MergeCursor()
        self.committed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        raise AssertionError("merge should not roll back")


class DashboardCursor:
    def __init__(self):
        self.responses = [
            [(10, 7, 3, 1)],
            [
                ('[{"level1":"提取","level2":"新增提取情形"}]',),
                ('[{"level1":"贷款","level2":"贷款额度调整"}]',),
            ],
            [
                ("2026-08-14", "示例新闻", 4, "南京", '[{"level1":"提取","level2":"新增提取情形"}]'),
            ],
        ]
        self.executed = []
        self.rows = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        self.rows = self.responses.pop(0)

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


class BusinessTypeUtilsTests(unittest.TestCase):
    def test_normalize_accepts_at_most_two_valid_unique_tags(self):
        canonical = {("贷款", "首付比例"): "贷款首付"}
        result = normalize_business_types([
            {"level1": "贷款", "level2": "首付比例"},
            {"level1": "提取", "level2": "支付物业费"},
            {"level1": "缴存", "level2": "缴存基数"},
            {"level1": "贷款", "level2": "首付比例"},
        ], canonical)

        self.assertEqual(result, [
            {"level1": "贷款", "level2": "贷款首付"},
            {"level1": "提取", "level2": "支付物业费"},
        ])

    def test_normalize_keeps_other_but_discards_invalid_primary_type(self):
        result = normalize_business_types([
            {"level1": "其它", "level2": "账户查询"},
            {"level1": "投资", "level2": "基金"},
        ])
        self.assertEqual(result, [{"level1": "其它", "level2": "账户查询"}])
        self.assertEqual(BUSINESS_TYPE_LEVEL1, ("缴存", "提取", "贷款", "其它"))

    def test_llm_validation_rejects_more_than_two_or_invalid_tags(self):
        self.assertIsNone(validate_llm_business_types([
            {"level1": "贷款", "level2": "首付"},
            {"level1": "提取", "level2": "物业费"},
            {"level1": "缴存", "level2": "基数"},
        ]))
        self.assertIsNone(validate_llm_business_types([{"level1": "投资", "level2": "基金"}]))

    def test_llm_validation_allows_alias_and_duplicate_to_deduplicate(self):
        result = validate_llm_business_types([
            {"level1": "贷款", "level2": "首付比例"},
            {"level1": "贷款", "level2": "贷款首付"},
        ], {("贷款", "首付比例"): "贷款首付"})
        self.assertEqual(result, [{"level1": "贷款", "level2": "贷款首付"}])

    def test_alias_chain_resolves_to_final_canonical_label(self):
        aliases = {
            ("贷款", "首付比例"): "贷款首付",
            ("贷款", "贷款首付"): "购房贷款条件",
        }
        self.assertEqual(
            resolve_canonical_level2("贷款", "首付比例", aliases),
            "购房贷款条件",
        )

    def test_catalog_excludes_retired_labels_and_groups_by_primary_type(self):
        cursor = FakeCursor(rows=[
            ('[{"level1":"贷款","level2":"贷款首付"}]',),
            ('[{"level1":"提取","level2":"支付物业费"}]',),
        ])
        catalog = build_business_type_catalog(
            cursor,
            "scored_news_test",
            {("贷款", "首付比例"): "贷款首付"},
        )

        self.assertEqual(catalog["贷款"], ["贷款首付"])
        self.assertEqual(catalog["提取"], ["支付物业费"])
        self.assertNotIn("首付比例", catalog["贷款"])

    def test_backfill_query_only_selects_missing_labels_without_force(self):
        sql, params = build_query("scored_news_test", date_from="2026-01-01", date_to="2026-01-31", limit=10)
        self.assertIn("business_types IS NULL", sql)
        self.assertEqual(params, ["公积金", "2026-01-01", "2026-01-31", 10])

        forced_sql, forced_params = build_query("scored_news_test", force=True)
        self.assertNotIn("business_types IS NULL", forced_sql)
        self.assertEqual(forced_params, ["公积金"])

    def test_llm_helpers_include_catalog_and_normalize_returned_tags(self):
        catalog = {"贷款": ["贷款首付"], "提取": [], "缴存": [], "其它": []}
        with mock.patch("news_region_utils._call_llm", return_value='''
            {"short_summary":"摘要", "region":"南京市", "business_types":[
              {"level1":"贷款","level2":"首付比例"}
            ]}
        ''') as call:
            result = call_summary_and_region_llm(
                "标题", "正文", catalog, {("贷款", "首付比例"): "贷款首付"}
            )
        self.assertEqual(result["business_types"], [{"level1": "贷款", "level2": "贷款首付"}])
        self.assertIn("贷款首付", call.call_args.args[1])

        with mock.patch("news_region_utils._call_llm", return_value='''
            {"business_types": []}
        '''):
            self.assertEqual(call_business_type_llm("标题", "正文", catalog), [])

    def test_short_content_helper_does_not_require_summary(self):
        with mock.patch("news_region_utils._call_llm", return_value='''
            {"region":"南京市", "business_types":[{"level1":"提取","level2":"物业费"}]}
        ''') as call:
            result = call_region_and_business_type_llm("短标题", "短正文", {"提取": ["物业费"]})
        self.assertEqual(result, {
            "region": "南京",
            "business_types": [{"level1": "提取", "level2": "物业费"}],
        })
        self.assertIn("不需要生成摘要", call.call_args.args[0])

    def test_merge_updates_news_and_rewrites_existing_alias_chain(self):
        conn = MergeConnection()
        updated = merge_secondary_labels(
            conn, "scored_news_test", "贷款", ["贷款首付"], "购房贷款条件"
        )
        self.assertEqual(updated, 1)
        self.assertTrue(conn.committed)
        self.assertEqual(
            conn.cursor_instance.news[0][1],
            '[{"level1": "贷款", "level2": "购房贷款条件"}, {"level1": "提取", "level2": "物业费"}]',
        )
        self.assertEqual(conn.cursor_instance.aliases[("贷款", "首付比例")], "购房贷款条件")

    def test_catalog_format_has_safe_empty_state_and_grouped_labels(self):
        self.assertIn("暂无既有二级标签", format_business_type_catalog({}))
        rendered = format_business_type_catalog({"贷款": ["贷款申请", "贷款首付"]})
        self.assertIn("- 贷款：贷款申请、贷款首付", rendered)

    def test_table_name_validation_rejects_sql_injection(self):
        with self.assertRaises(ValueError):
            validate_business_type_table_name("scored_news_test; DROP TABLE scored_news")

    def test_dashboard_aggregates_coverage_types_and_recent_news(self):
        cursor = DashboardCursor()
        dashboard = get_business_type_dashboard(cursor, "scored_news_test", "2026-08-13", "2026-08-14")
        self.assertEqual(dashboard["coverage"], 70.0)
        self.assertEqual(dashboard["pending"], 3)
        self.assertEqual(dashboard["empty"], 1)
        self.assertEqual(dashboard["level1_counts"][1], {"level1": "提取", "count": 1})
        self.assertEqual(dashboard["recent_items"][0]["business_types"][0]["level2"], "新增提取情形")
        self.assertIn("fetchdate >= %s", cursor.executed[0][0])


if __name__ == "__main__":
    unittest.main()
