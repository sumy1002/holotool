"""「認不出畫面」不等於「在等你下注」。

2026-08-21 的實機 log：比大小畫面上的牌認不出來，所有標記都沒過門檻，於是
每 2.5 秒點一次「投注並開始」，第 4、5、6 次…… 那顆按鈕在比大小畫面上什麼
都不會發生，所以永遠不會有進展，也永遠不會停。

修法是**正面確認**投注畫面：五個牌位都蓋著深色牌背。下面的數字全部是從
三種長寬比、二十幾張實機截圖量出來的真實值，不是猜的。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.state_machine import IDLE_SLOT_MAX_VALUE, FrameInfo  # noqa: E402


def frame(values):
    return FrameInfo(on_table=True, table_marker_score=0.9, slot_values=list(values))


# 實機量到的中位亮度（HSV 的 V）
BETTING_43 = [85, 112, 133, 125, 82]        # 4:3 投注畫面，五張深色牌背
DRAW_43 = [255, 255, 255, 255, 253]         # 4:3 選牌畫面，五張白牌面
HIGHLOW_219 = [255, 205, 247, 247, 251]     # 21:9 比大小（就是卡住那張）
HIGHLOW_169 = [248, 218, 248, 248, 251]
POKER_FAIL_169 = [248, 248, 248, 248, 248]
CHALLENGE_169 = [88, 247, 248, 248, 248]    # 第一格被立繪蓋住，所以很暗
CONGRATS_169 = [255, 255, 255, 236, 255]
# 16:9 的某一張比大小：第二格只有 173，離門檻 170 只差 3。
# 蓋著的那張牌剛好落在這一格，所以會比其他格暗很多。
HIGHLOW_169_TIGHT = [255, 173, 248, 248, 251]
BLURRY_TRANSITION_169 = [130, 247, 248, 248, 248]   # 動畫過場，整張糊掉


class TestBettingScreenDetection(unittest.TestCase):
    def test_the_betting_screen_is_recognized(self):
        self.assertTrue(frame(BETTING_43).looks_like_betting())

    def test_the_screen_that_caused_the_bug_is_rejected(self):
        """這一條就是那個 bug 本身。"""
        self.assertFalse(frame(HIGHLOW_219).looks_like_betting())

    def test_every_other_screen_is_rejected(self):
        for name, values in (
            ("選牌", DRAW_43),
            ("比大小 16:9", HIGHLOW_169),
            ("湊牌失敗", POKER_FAIL_169),
            ("過關", CONGRATS_169),
        ):
            self.assertFalse(frame(values).looks_like_betting(), name)

    def test_the_challenge_dialog_is_rejected_despite_one_dark_slot(self):
        """翻倍對話框的第一格被立繪蓋住（V=88），比投注畫面還暗。

        所以判斷條件必須是「五個都暗」；用「平均值」或「多數都暗」會把
        這個對話框當成投注畫面，然後在對話框上點投注 —— 比原本的 bug 更糟。
        """
        self.assertFalse(frame(CHALLENGE_169).looks_like_betting())
        self.assertLess(CHALLENGE_169[0], max(BETTING_43))

    def test_threshold_has_real_headroom_on_the_betting_side(self):
        """投注畫面最亮只有 133，門檻 170 留了 37 的餘裕。"""
        self.assertGreater(IDLE_SLOT_MAX_VALUE, max(BETTING_43) + 20)

    def test_a_single_slot_near_the_threshold_does_not_matter(self):
        """實機上真的有一格量到 173（離門檻只差 3）。

        安全性不能靠那 3 的差距 —— 它靠的是「五個都要暗」：同一張畫面
        另外四格是 248/251，不管第二格怎麼飄都不可能通過。
        """
        self.assertFalse(frame(HIGHLOW_169_TIGHT).looks_like_betting())
        self.assertGreaterEqual(
            sum(1 for v in HIGHLOW_169_TIGHT if v > IDLE_SLOT_MAX_VALUE), 4)

    def test_blurry_animation_frames_are_rejected(self):
        """動畫過場整張糊掉、第一格變暗（130，比投注畫面還暗）也不能誤判。"""
        self.assertFalse(frame(BLURRY_TRANSITION_169).looks_like_betting())


class TestDegradedInput(unittest.TestCase):
    def test_missing_measurements_never_count_as_the_betting_screen(self):
        """量不到就不要動作。寧可停著，也不要在不知道的畫面上亂點。"""
        for values in ([], [85], [85, 112, 133, 125]):
            self.assertFalse(frame(values).looks_like_betting(), values)

    def test_capture_failure_sentinel_is_not_dark(self):
        """median_value() 失敗會回 -1。-1 <= 門檻會讓它看起來「很暗」，

        但那一格其實根本沒量到 —— 所以 median_value 失敗時 detect_frame
        不會把它放進 slot_values，長度不足 5 就直接不成立（上面那條測試）。
        這裡確認 -1 真的比門檻小，說明為什麼不能只看門檻。
        """
        self.assertLess(-1, IDLE_SLOT_MAX_VALUE)

    def test_threshold_is_overridable(self):
        self.assertTrue(frame([200, 200, 200, 200, 200]).looks_like_betting(max_value=210))
        self.assertFalse(frame(BETTING_43).looks_like_betting(max_value=50))


if __name__ == "__main__":
    unittest.main()
