"""內容框：一組校準換算到任何長寬比。

遊戲**不是**依視窗重排 UI，而是把一個固定 16:9 的內容框置中放進用戶端矩形，
UI 全部排在那個框裡；只有背景圖拉滿整個視窗（所以看起來沒有黑邊）。

下面所有數字都是 2026-08-21 從三種長寬比的實機截圖量出來的，不是算出來的
理想值 —— 這個檔案的作用就是「未來有人改了換算邏輯，這些實測值要還對得上」。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.geometry import (  # noqa: E402
    content_box,
    content_height,
    region_client_to_content,
    region_content_to_client,
    retarget,
    retarget_bottom_anchored,
)

# 實機尺寸
W169, H169 = 1474, 829
W43, H43 = 1269, 952
W219, H219 = 1367, 574
REF_W, REF_H = 1024, 438        # 內建樣板的來源截圖


class TestContentBox(unittest.TestCase):
    def test_16_9_window_has_no_letterbox(self):
        ox, oy, cw, ch = content_box(W169, H169)
        self.assertAlmostEqual(ox, 0, delta=0.5)
        self.assertAlmostEqual(oy, 0, delta=0.5)
        self.assertAlmostEqual(cw, W169, delta=1)

    def test_narrow_window_letterboxes_top_and_bottom(self):
        ox, oy, cw, ch = content_box(W43, H43)
        self.assertAlmostEqual(ox, 0, delta=0.5)
        self.assertAlmostEqual(ch, 713.8, delta=0.5)     # 1269 * 9/16
        self.assertAlmostEqual(oy, 119.1, delta=0.5)     # (952 - 713.8) / 2
        self.assertAlmostEqual(cw, W43, delta=0.5)

    def test_wide_window_letterboxes_left_and_right(self):
        ox, oy, cw, ch = content_box(W219, H219)
        self.assertAlmostEqual(ch, H219, delta=0.5)
        self.assertAlmostEqual(cw, 1020.4, delta=0.5)    # 574 * 16/9
        self.assertAlmostEqual(ox, 173.3, delta=0.5)
        self.assertAlmostEqual(oy, 0, delta=0.5)

    def test_invalid_sizes_do_not_crash(self):
        self.assertEqual(content_box(0, 0), (0.0, 0.0, 0, 0))
        self.assertEqual(content_height(0, 100), 0)


class TestMarkerScaleMatchesReality(unittest.TestCase):
    """實測的樣板縮放倍率（用 table_marker.png 多尺度比對量出來的）。

    舊公式用「視窗寬度比」，在 16:9 和 4:3 都差 24%，所以一換比例畫面標記的
    分數就整排掉下來。
    """

    OBSERVED = {
        (W169, H169): 1.894,
        (W43, H43): 1.632,
        (W219, H219): 1.308,
    }

    def test_content_height_ratio_matches_measurement(self):
        ref = content_height(REF_W, REF_H)
        self.assertAlmostEqual(ref, 438, delta=0.5)
        for (w, h), observed in self.OBSERVED.items():
            predicted = content_height(w, h) / ref
            self.assertAlmostEqual(predicted, observed, delta=observed * 0.005,
                                   msg=f"{w}x{h}")

    def test_the_old_width_ratio_was_badly_wrong(self):
        """留著這條當紀錄：說明為什麼一定要改。"""
        for (w, h), observed in self.OBSERVED.items():
            width_ratio = w / REF_W
            error = abs(width_ratio - observed) / observed
            if (w, h) == (W219, H219):
                self.assertLess(error, 0.03)      # 剛好接近，所以一直沒被發現
            else:
                self.assertGreater(error, 0.20)   # 16:9 與 4:3 都差超過 20%


class TestRetarget(unittest.TestCase):
    """五格手牌：16:9 與 4:3 各自從截圖量到的白色卡身。"""

    CARDS_169 = [(x, 258, 218, 307) for x in (107, 368, 629, 890, 1151)]
    CARDS_43 = [(x, 341, 188, 265) for x in (92, 317, 541, 766, 991)]

    def test_measured_cards_agree_in_content_coordinates(self):
        """換算到內容框座標後，兩種比例必須一致 —— 這是整個做法的地基。"""
        for a, b in zip(self.CARDS_169, self.CARDS_43):
            ca = region_client_to_content(
                {"x": a[0]/W169, "y": a[1]/H169, "w": a[2]/W169, "h": a[3]/H169},
                W169, H169)
            cb = region_client_to_content(
                {"x": b[0]/W43, "y": b[1]/H43, "w": b[2]/W43, "h": b[3]/H43},
                W43, H43)
            for key in ("x", "y", "w", "h"):
                self.assertAlmostEqual(ca[key], cb[key], delta=0.0015, msg=key)

    def test_retarget_reproduces_the_other_aspect_within_a_few_pixels(self):
        for a, b in zip(self.CARDS_169, self.CARDS_43):
            src = {"x": a[0]/W169, "y": a[1]/H169, "w": a[2]/W169, "h": a[3]/H169}
            got = retarget(src, W169, H169, W43, H43)
            self.assertAlmostEqual(got["x"] * W43, b[0], delta=3)
            self.assertAlmostEqual(got["y"] * H43, b[1], delta=3)
            self.assertAlmostEqual(got["w"] * W43, b[2], delta=3)
            self.assertAlmostEqual(got["h"] * H43, b[3], delta=3)

    def test_same_aspect_is_a_no_op(self):
        src = {"x": 0.1715, "y": 0.2956, "w": 0.1264, "h": 0.3985}
        got = retarget(src, 1843, 778, 1937, 817)   # 同樣是 21:9，只是視窗變大
        for key, value in src.items():
            self.assertAlmostEqual(got[key], value, delta=0.002, msg=key)

    def test_round_trip(self):
        src = {"x": 0.42, "y": 0.31, "w": 0.15, "h": 0.37}
        mid = region_client_to_content(src, W169, H169)
        back = region_content_to_client(mid, W169, H169)
        for key, value in src.items():
            self.assertAlmostEqual(back[key], value, places=6, msg=key)

    def test_points_without_size_survive(self):
        got = retarget({"x": 0.5, "y": 0.8128}, 1937, 817, W43, H43)
        self.assertNotIn("w", got)
        self.assertAlmostEqual(got["x"], 0.5, delta=0.002)


class TestBottomAnchored(unittest.TestCase):
    """對話框底部那排按鈕釘在**視窗底部**，不在內容框裡。

    實測（同一組 16:9→4:3 轉換）：
        地標          內容框預測誤差   貼視窗底預測誤差
        卡片上下緣        0.0 px          118.1 px
        大／小膠囊        0.3 px          118.4 px
        取消 上緣       118.4 px            0.3 px
    """

    def test_cancel_button_follows_the_window_bottom(self):
        # 16:9 量到的「取消」膠囊中心
        src = {"x": 0.6852, "y": 0.9150}
        got = retarget_bottom_anchored(src, W169, H169, W43, H43)
        # 4:3 量到的同一顆按鈕
        self.assertAlmostEqual(got["x"] * W43, 0.6856 * W43, delta=3)
        self.assertAlmostEqual(got["y"] * H43, 0.9359 * H43, delta=4)

    def test_content_box_math_would_be_off_by_a_hundred_pixels(self):
        src = {"x": 0.6852, "y": 0.9150}
        wrong = retarget(src, W169, H169, W43, H43)
        self.assertGreater(abs(wrong["y"] * H43 - 0.9359 * H43), 100)

    def test_same_aspect_is_a_no_op(self):
        src = {"x": 0.774, "y": 0.917}
        got = retarget_bottom_anchored(src, 1843, 778, 1937, 817)
        self.assertAlmostEqual(got["x"], src["x"], delta=0.002)
        self.assertAlmostEqual(got["y"], src["y"], delta=0.002)

    def test_degenerate_sizes_return_the_input(self):
        src = {"x": 0.5, "y": 0.9}
        self.assertEqual(retarget_bottom_anchored(src, 0, 0, W43, H43), src)


if __name__ == "__main__":
    unittest.main()
