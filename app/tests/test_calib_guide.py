"""校準的圖示提示：每一項都要有東西可以給使用者看。

背景：文字說明「框選左上角 High & Low 標題」有太多解讀空間 —— 含不含外框？
框大一點是不是比較保險？猜錯的下場是辨識分數莫名偏低，而且完全看不出原因。
所以每一個校準項目都要嘛有內建範例圖，要嘛至少有一個實機建議框。

這裡驗的是「對應關係不會悄悄壞掉」：
* CALIB_TARGETS 新增項目時，忘記加範例圖對應會被抓出來（max_win 兩項是已知例外）；
* 範例圖的檔案真的都在（打包時漏掉 defaults/ref 的話這裡就會紅）；
* 建議框的座標一定落在畫面內 —— 畫在畫面外等於沒有提示。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import calibguide as cg  # noqa: E402
from src.defaults_layout import SCREENSHOT_LAYOUT  # noqa: E402

# 這兩項沒有可用的截圖：「已達最高獲得金額」那個畫面從頭到尾只出現在對話紀錄裡，
# 拿不到像素。它們只會有實機建議框，沒有範例圖。
NO_EXAMPLE = {"regions.max_win_marker", "points.max_win_retry"}


def all_calib_paths() -> list[str]:
    """列出所有校準項目的 path，不 import gui.py（那會拉進 tkinter 與 cv2）。"""
    paths = []
    for name in SCREENSHOT_LAYOUT["regions"]:
        value = SCREENSHOT_LAYOUT["regions"][name]
        if isinstance(value, list):
            paths += [f"regions.{name}.{i}" for i in range(len(value))]
        else:
            paths.append(f"regions.{name}")
    for name in SCREENSHOT_LAYOUT["points"]:
        value = SCREENSHOT_LAYOUT["points"][name]
        if isinstance(value, list):
            paths += [f"points.{name}.{i}" for i in range(len(value))]
        else:
            paths.append(f"points.{name}")
    return paths


class TestGuideCoverage(unittest.TestCase):
    def test_every_calibration_item_has_a_reference_screen(self):
        missing = [p for p in all_calib_paths()
                   if p not in NO_EXAMPLE and cg.ref_key_for(p) is None]
        self.assertEqual(missing, [], f"這些校準項目忘了對應範例畫面：{missing}")

    def test_reference_map_has_no_stale_entries(self):
        known = set(all_calib_paths())
        stale = [p for p in cg.REF_FOR_PATH if p not in known]
        self.assertEqual(stale, [], f"REF_FOR_PATH 裡有已經不存在的項目：{stale}")

    def test_every_referenced_screen_has_a_title(self):
        for key in set(cg.REF_FOR_PATH.values()):
            self.assertIn(key, cg.REF_TITLES)

    def test_bundled_reference_images_exist(self):
        """defaults/ref/*.jpg 沒被打包進去的話，範例圖會整批消失。"""
        for key in sorted(set(cg.REF_FOR_PATH.values())):
            path = os.path.join(cg.ref_dir(), f"{key}.jpg")
            self.assertTrue(os.path.exists(path), f"缺少範例圖 {path}")


class TestDefaultValues(unittest.TestCase):
    def test_default_value_reads_nested_list_items(self):
        self.assertEqual(cg.default_value("regions.card_slots.2"),
                         SCREENSHOT_LAYOUT["regions"]["card_slots"][2])
        self.assertEqual(cg.default_value("points.hold_toggles.4"),
                         SCREENSHOT_LAYOUT["points"]["hold_toggles"][4])

    def test_unknown_path_returns_none_instead_of_raising(self):
        for path in ("regions.nope", "points.hold_toggles.9", "regions.card_slots.x", ""):
            self.assertIsNone(cg.default_value(path))

    def test_kind_is_derived_from_the_path(self):
        self.assertEqual(cg.kind_of("regions.table_marker"), "region")
        self.assertEqual(cg.kind_of("points.high_button"), "point")

    def test_defaults_all_land_inside_the_window(self):
        """建議框畫在畫面外等於沒有提示，而且使用者會以為程式壞了。"""
        for path in all_calib_paths():
            value = cg.default_value(path)
            self.assertIsNotNone(value, path)
            self.assertGreaterEqual(value["x"], 0.0, path)
            self.assertGreaterEqual(value["y"], 0.0, path)
            self.assertLessEqual(value["x"] + value.get("w", 0.0), 1.0, path)
            self.assertLessEqual(value["y"] + value.get("h", 0.0), 1.0, path)


class TestSuggestedValue(unittest.TestCase):
    def test_users_own_calibration_wins_over_the_default(self):
        mine = {"x": 0.11, "y": 0.22, "w": 0.33, "h": 0.44}
        cfg = {"regions": {"table_marker": dict(mine)}}
        self.assertEqual(cg.suggested_value(cfg, "regions.table_marker"), mine)

    def test_uncalibrated_item_falls_back_to_the_default(self):
        cfg = {"regions": {"table_marker": {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0}}}
        self.assertEqual(cg.suggested_value(cfg, "regions.table_marker"),
                         SCREENSHOT_LAYOUT["regions"]["table_marker"])

    def test_zero_point_falls_back_to_the_default(self):
        cfg = {"points": {"high_button": {"x": 0.0, "y": 0.0}}}
        self.assertEqual(cg.suggested_value(cfg, "points.high_button"),
                         SCREENSHOT_LAYOUT["points"]["high_button"])

    def test_broken_config_does_not_raise(self):
        self.assertIsNotNone(cg.suggested_value({}, "regions.table_marker"))
        self.assertIsNotNone(cg.suggested_value({"regions": None}, "regions.table_marker"))


class TestExampleImage(unittest.TestCase):
    def test_region_example_is_rendered_at_the_requested_width(self):
        image = cg.example_image("regions.table_marker", width=320)
        self.assertIsNotNone(image)
        self.assertEqual(image.width, 320)

    def test_point_example_is_rendered(self):
        self.assertIsNotNone(cg.example_image("points.high_button"))

    def test_items_without_a_reference_screen_return_none(self):
        for path in sorted(NO_EXAMPLE):
            self.assertIsNone(cg.example_image(path))

    def test_highlighted_area_is_brighter_than_the_dimmed_surroundings(self):
        """框內保留原亮度、框外壓暗 —— 反了的話提示會指向錯的地方。"""
        path = "regions.congrats_marker"
        image = cg.example_image(path)
        value = cg.default_value(path)
        w, h = image.size
        inside = image.crop((
            round(value["x"] * w) + 4, round(value["y"] * h) + 4,
            round((value["x"] + value["w"]) * w) - 4,
            round((value["y"] + value["h"]) * h) - 4,
        ))
        # 取同樣大小、但在框外的一塊來比
        outside = image.crop((4, h - inside.height - 4, 4 + inside.width, h - 4))

        def mean(img):
            data = list(img.convert("L").tobytes())
            return sum(data) / len(data)

        self.assertGreater(mean(inside), mean(outside))

    def test_caption_mentions_the_screen_and_what_to_do(self):
        caption = cg.caption_for("points.draw_confirm")
        self.assertIn("選擇要保留的牌", caption)
        self.assertIn("點擊", caption)


if __name__ == "__main__":
    unittest.main()
