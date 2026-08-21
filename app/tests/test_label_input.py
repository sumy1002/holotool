"""代號輸入框的正規化：輸入法切到中文也不該讓人打不出 "10H"。

使用者回報：「每次我要抓這一輪的牌的時候，他一定會切換我的輸入法變成中文，
但我剛剛是切 SHIFT 打英文的。」

原因：焦點一移到代號欄，Windows 就把輸入法狀態換成那個視窗的預設值 ——
剛剛用 Shift 切成英文的狀態**不會跟著焦點走**，所以每抓一輪牌都要重切一次。
實際打進去的是中文，或是全形的 "１０Ｈ"，而存檔時只會看到一句「格式不對」，
完全看不出是輸入法造成的。

兩層處理：
1. `gui.py` 對那六個輸入框呼叫 `ImmAssociateContext(hwnd, 0)` 直接關掉輸入法；
2. 這裡的 `normalize_label_input` 當保險 —— 不管進來什麼，只留英數字、
   一律大寫、全形折成半角。

這個測試只碰純函式（`src/handeval.py` 沒有 tkinter 依賴），
所以在沒有 GUI 的環境也跑得起來。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.handeval import (  # noqa: E402
    LABEL_INPUT_MAX_LEN,
    Card,
    normalize_label_input,
)


class TestCaseInsensitive(unittest.TestCase):
    def test_lowercase_becomes_uppercase(self):
        self.assertEqual(normalize_label_input("10h"), "10H")
        self.assertEqual(normalize_label_input("as"), "AS")
        self.assertEqual(normalize_label_input("qd"), "QD")

    def test_uppercase_is_left_alone(self):
        for label in ("10H", "AS", "QD", "JK"):
            self.assertEqual(normalize_label_input(label), label)

    def test_mixed_case_works_too(self):
        self.assertEqual(normalize_label_input("10Hh"[:3]), "10H")
        self.assertEqual(normalize_label_input("jK"), "JK")


class TestFullWidth(unittest.TestCase):
    """中文輸入法打出來的常常是全形 —— 看起來對，但 from_label 認不得。"""

    def test_full_width_letters_and_digits_are_folded(self):
        self.assertEqual(normalize_label_input("１０Ｈ"), "10H")
        self.assertEqual(normalize_label_input("ＡＳ"), "AS")
        self.assertEqual(normalize_label_input("ｑｄ"), "QD")

    def test_mixture_of_widths(self):
        self.assertEqual(normalize_label_input("10Ｈ"), "10H")
        self.assertEqual(normalize_label_input("Ａs"), "AS")


class TestDroppedCharacters(unittest.TestCase):
    def test_chinese_is_dropped_entirely(self):
        self.assertEqual(normalize_label_input("你好"), "")
        self.assertEqual(normalize_label_input("紅心10"), "10")

    def test_spaces_and_punctuation_are_dropped(self):
        self.assertEqual(normalize_label_input("10 h"), "10H")
        self.assertEqual(normalize_label_input("10。h"), "10H")
        self.assertEqual(normalize_label_input(" as "), "AS")
        self.assertEqual(normalize_label_input("-10-H-"), "10H")

    def test_empty_stays_empty(self):
        """清空那一格是有意義的操作（代表「這格不要存」），不能被吃掉。"""
        self.assertEqual(normalize_label_input(""), "")
        self.assertEqual(normalize_label_input("   "), "")


class TestLength(unittest.TestCase):
    def test_truncated_to_the_longest_valid_label(self):
        self.assertEqual(len(normalize_label_input("QDQDQDQD")), LABEL_INPUT_MAX_LEN)

    def test_joker_still_fits(self):
        """`Card.from_label` 認 JOKER，所以上限不能訂成 3。"""
        self.assertEqual(normalize_label_input("joker"), "JOKER")
        self.assertEqual(Card.from_label(normalize_label_input("joker")).rank, "JK")


class TestIdempotent(unittest.TestCase):
    """gui.py 是用 StringVar 的 trace 做的：改完會再觸發一次自己。

    正規化必須是 idempotent 的，否則那個 trace 會無限遞迴。
    """

    def test_normalizing_twice_changes_nothing(self):
        for raw in ("10h", "ＡＳ", "你好", "", "10 h", "joker", "QDQDQDQD", "１０Ｈ"):
            once = normalize_label_input(raw)
            self.assertEqual(normalize_label_input(once), once, raw)


class TestRoundTripsIntoACard(unittest.TestCase):
    def test_every_real_label_survives_a_lowercase_round_trip(self):
        from src.handeval import full_deck
        for card in full_deck():
            typed = card.label.lower()
            self.assertEqual(Card.from_label(normalize_label_input(typed)), card)


if __name__ == "__main__":
    unittest.main()
