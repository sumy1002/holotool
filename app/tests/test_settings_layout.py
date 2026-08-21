"""設定分頁的欄位分組不可以有洞。

分組之後多了一種很難發現的壞法：欄位排在畫面上，但沒有進到「完整清單」裡 ——
使用者調了值、按了儲存、程式回報「設定已儲存」，那一格卻被忽略。
所以這裡驗四件事：

* 每個欄位都在 SETTING_FIELDS 裡（存檔與還原預設值都以那份為準）；
* 沒有欄位被歸到兩組（畫面上會出現兩格，後存的贏，看起來像存檔沒生效）；
* 每個路徑在 DEFAULT_CONFIG 裡真的存在（打錯字的話那格會是空白）；
* 「比對門檻」預設展開、「動作時間」預設收合 —— 這是使用者明確要求的預設狀態。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import DEFAULT_CONFIG, get_by_path  # noqa: E402
from src.settings_layout import (  # noqa: E402
    INT_SETTINGS,
    OTHER_FIELDS,
    SETTING_FIELDS,
    SETTING_SECTIONS,
    THRESHOLD_FIELDS,
    TIMING_FIELDS,
)


class TestSettingsLayout(unittest.TestCase):
    def test_every_field_is_in_the_master_list(self):
        grouped = [key for group in (THRESHOLD_FIELDS, TIMING_FIELDS, OTHER_FIELDS)
                   for key, _n, _h in group]
        self.assertEqual(sorted(grouped), sorted(key for key, _n, _h in SETTING_FIELDS))

    def test_no_field_appears_in_two_groups(self):
        keys = [key for key, _n, _h in SETTING_FIELDS]
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        self.assertEqual(duplicates, [], f"這些欄位被歸到不只一組：{duplicates}")

    def test_every_path_exists_in_the_default_config(self):
        for key, name, _hint in SETTING_FIELDS:
            try:
                value = get_by_path(DEFAULT_CONFIG, key)
            except (KeyError, IndexError, TypeError):
                self.fail(f"設定路徑不存在：{key}（{name}）")
            self.assertIsInstance(value, (int, float), f"{key} 不是數值欄位")

    def test_int_settings_really_are_integers_by_default(self):
        for key in INT_SETTINGS:
            self.assertIn(key, [k for k, _n, _h in SETTING_FIELDS])
            self.assertIsInstance(get_by_path(DEFAULT_CONFIG, key), int, key)

    def test_named_sections_and_default_expansion(self):
        titles = [title for title, _sub, _fields, _expanded in SETTING_SECTIONS]
        self.assertEqual(titles, ["比對門檻設定", "動作時間設定"])
        expanded = {title: exp for title, _sub, _fields, exp in SETTING_SECTIONS}
        self.assertTrue(expanded["比對門檻設定"], "比對門檻設定要預設展開")
        self.assertFalse(expanded["動作時間設定"], "動作時間設定要預設收合")

    def test_sections_do_not_cover_the_loose_fields(self):
        """SETTING_SECTIONS 只放需要收合的兩組；OTHER_FIELDS 是直接攤在外面的。"""
        in_sections = {key for _t, _s, fields, _e in SETTING_SECTIONS
                       for key, _n, _h in fields}
        for key, _name, _hint in OTHER_FIELDS:
            self.assertNotIn(key, in_sections)

    def test_every_field_has_a_name_and_a_hint(self):
        for key, name, hint in SETTING_FIELDS:
            self.assertTrue(name.strip(), key)
            self.assertTrue(hint.strip(), key)


if __name__ == "__main__":
    unittest.main()
