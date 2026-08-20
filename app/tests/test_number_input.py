"""設定欄位的數字正規化。

使用者回報：「輸入格無法輸入數字鍵的 `.`，一定要打 `>` 按鍵的 `.` 才能輸入進去」。
中文輸入法會把數字鍵盤那顆小數點換成「。」或整個吃掉，所以存檔時把常見的
全形替代字元一律換成半角。

刻意只測純函式（`HoloToolApp._normalize_number` 的邏輯複製在下面），
gui.py 需要 tkinter，沒有 GUI 的機器 import 就會失敗。
"""
from __future__ import annotations

import unittest

# 與 gui.py 的 HoloToolApp._DECIMAL_ALIASES 保持一致
DECIMAL_ALIASES = str.maketrans({
    "。": ".", "．": ".", "、": ".", "，": ".", ",": ".", "·": ".", "‧": ".",
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
    "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
})


def normalize(raw: str) -> str:
    return raw.strip().translate(DECIMAL_ALIASES)


class TestNormalize(unittest.TestCase):
    def test_full_width_period_becomes_a_decimal_point(self):
        self.assertEqual(normalize("0。5"), "0.5")
        self.assertEqual(float(normalize("0。5")), 0.5)

    def test_other_chinese_separators(self):
        for raw in ("0、5", "0，5", "0·5", "0．5", "0,5"):
            self.assertEqual(float(normalize(raw)), 0.5, raw)

    def test_full_width_digits(self):
        self.assertEqual(float(normalize("０．７２")), 0.72)
        self.assertEqual(int(normalize("２５")), 25)

    def test_normal_input_is_untouched(self):
        for raw in ("0.5", "25", "1.2", "0.05", "15.0"):
            self.assertEqual(normalize(raw), raw)

    def test_whitespace_is_trimmed(self):
        self.assertEqual(normalize("  0.83  "), "0.83")
        self.assertEqual(normalize(" 0。83 "), "0.83")

    def test_garbage_still_fails_loudly(self):
        """正規化不該把亂打的東西變成合法數字 —— 該報錯就要報錯。"""
        for raw in ("abc", "", "0.5.5", "--3"):
            with self.assertRaises(ValueError, msg=raw):
                float(normalize(raw))

    def test_negative_and_exponent_survive(self):
        self.assertEqual(float(normalize("-1.5")), -1.5)
        self.assertEqual(float(normalize("1e-3")), 0.001)


if __name__ == "__main__":
    unittest.main()
