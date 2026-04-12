import unittest

from news_region_utils import normalize_region


class NormalizeRegionTests(unittest.TestCase):
    def test_rolls_district_up_to_city(self):
        self.assertEqual(normalize_region("南京市建邺区"), "南京")
        self.assertEqual(normalize_region("苏州市昆山市"), "苏州")

    def test_extracts_city_from_province_city_value(self):
        self.assertEqual(normalize_region("江苏省南京市"), "南京")
        self.assertEqual(normalize_region("新疆维吾尔自治区乌鲁木齐市"), "乌鲁木齐")

    def test_preserves_prefecture_level_names(self):
        self.assertEqual(normalize_region("喀什地区疏附县"), "喀什地区")
        self.assertEqual(normalize_region("阿坝藏族羌族自治州汶川县"), "阿坝州")
        self.assertEqual(normalize_region("锡林郭勒盟正蓝旗"), "锡林郭勒盟")

    def test_normalizes_multi_value_output(self):
        self.assertEqual(normalize_region("南京、苏州,南京"), "南京|苏州")
        self.assertEqual(normalize_region("江苏省|浙江省"), "江苏|浙江")

    def test_national_overrides_local_values(self):
        self.assertEqual(normalize_region("全国|南京|苏州"), "全国")

    def test_returns_none_for_empty_values(self):
        self.assertIsNone(normalize_region("未知"))
        self.assertIsNone(normalize_region(""))


if __name__ == "__main__":
    unittest.main()
