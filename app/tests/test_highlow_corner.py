"""比大小畫面的角落定位與「兩角一致」規則。

實機症狀（2026-08-21）：比大小畫面停在那裡，log 一直是 `比大小=-`，
bot 完全不動。追下去是三件事疊在一起：

1. `rightmost_card_rect` 回傳的**高度**是呼叫端傳進來的長條高度，不是牌高。
2. 回傳的**寬度**是校準框換算的 `expected_w`，比實際牌寬大 11px
   （實機量測：160 vs 149）。角落是按「牌寬的固定比例」切的，差 11px 就
   足以讓右下角整個切歪 —— 實測右下角把 8 讀成「10」(0.593)。
3. 兩個角落都指向 8（左上 0.834、右下 0.808），但平均後的領先幅度
   0.04 < `part_min_margin` 0.05，於是整張牌判成「認不出來」。
   在比大小畫面「認不出來」的代價是**完全卡住**。

第 1、2 點修好之後右下角讀對了，第 3 點用「兩角各自的第一名一致就不再要求
領先幅度」解掉。三件都要有測試守著。
"""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import cardparts as cp  # noqa: E402


def glyph(size, kind: str) -> np.ndarray:
    """做一個可辨識的二值小圖。kind 不同 -> 形狀不同。"""
    w, h = size
    img = np.zeros((h, w), np.uint8)
    if kind == "bar":
        img[2:h - 2, w // 3:2 * w // 3] = 255
    elif kind == "ring":
        img[2:h - 2, 2:w - 2] = 255
        img[h // 4:3 * h // 4, w // 4:3 * w // 4] = 0
    elif kind == "blob":
        img[h // 4:3 * h // 4, w // 4:3 * w // 4] = 255
    return img


class TestExtractUsesTheSuppliedRect(unittest.TestCase):
    """rect 有帶尺寸時就要用它，不要用呼叫端傳的 expected_*。"""

    def _roi(self):
        """做一張假卡：白色卡身，左上角與右下角各畫一個深色的點數＋花色。

        角落的比例是 CORNER_X 0.020~0.215、RANK_Y 0.025~0.123、SUIT_Y 0.123~0.195，
        卡身 (60,40) 200x210 換算下來點數大約落在 x 64~103、y 45~66。
        """
        roi = np.zeros((300, 400, 3), np.uint8)
        roi[40:250, 60:260] = 255
        card = (60, 40, 200, 210)
        for x0, y0, x1, y1, flip in (
            (cp.CORNER_X0, cp.RANK_Y0, cp.CORNER_X1, cp.RANK_Y1, False),
            (cp.CORNER_X0, cp.SUIT_Y0, cp.CORNER_X1, cp.SUIT_Y1, False),
            (cp.CORNER_X0, cp.RANK_Y0, cp.CORNER_X1, cp.RANK_Y1, True),
            (cp.CORNER_X0, cp.SUIT_Y0, cp.CORNER_X1, cp.SUIT_Y1, True),
        ):
            bx, by, bw, bh = card
            ax = bx + int(bw * (1 - x1)) if flip else bx + int(bw * x0)
            ay = by + int(bh * (1 - y1)) if flip else by + int(bh * y0)
            w = int(bw * (x1 - x0))
            h = int(bh * (y1 - y0))
            # 畫一個有筆畫、不是滿版方塊的圖形（滿版會被當成背景濾掉）
            roi[ay + 2:ay + h - 2, ax + w // 3:ax + 2 * w // 3] = 20
            roi[ay + h // 2:ay + h // 2 + 3, ax + 2:ax + w - 2] = 20
        return roi

    def test_rect_dimensions_win_when_rect_is_given(self):
        roi = self._roi()
        # expected_* 故意給得離譜（長條高度那種），rect 帶的才是對的
        a = cp.extract_parts(roi, 200, 210, rect=(60, 40, 200, 210))
        b = cp.extract_parts(roi, 999, 999, rect=(60, 40, 200, 210))
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        # 兩次都該切在同一個位置 —— 因為 rect 一樣，expected_* 不該有影響
        np.testing.assert_array_equal(a["rank"], b["rank"])

    def test_behaviour_without_rect_is_unchanged(self):
        """選牌畫面那五格是不傳 rect 的，必須維持用 expected_*。"""
        roi = self._roi()
        a = cp.extract_parts(roi, 200, 210)
        b = cp.extract_parts(roi, 200, 260)
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        # expected_h 不同 -> 切出來的位置就該不同（證明它還在用 expected_*）
        self.assertFalse(np.array_equal(a["rank"], b["rank"]))


class TestTwoCornerAgreement(unittest.TestCase):
    """兩個角落各自的第一名一致時，不再要求領先幅度。"""

    def setUp(self):
        size = cp.PART_SIZES["rank"]
        self.bank = {
            "8": [cp.centre_mask(glyph(size, "ring"))],
            "5": [cp.centre_mask(glyph(size, "blob"))],
        }
        self.query = cp.centre_mask(glyph(size, "ring"))

    def test_agreement_accepts_a_narrow_lead(self):
        hit = cp._best_match(self.query, self.bank, min_score=0.5, min_margin=0.99,
                             query2=self.query)
        self.assertIsNotNone(hit, "兩角都說同一個答案卻還是判認不出來")
        self.assertEqual(hit[0], "8")

    def test_a_narrow_lead_is_still_refused_with_only_one_corner(self):
        """只有一個角落時，領先幅度的門檻照舊 —— 一致規則不能變成全面放寬。"""
        self.assertIsNone(
            cp._best_match(self.query, self.bank, min_score=0.5, min_margin=0.99))

    def test_second_corner_below_min_score_is_ignored(self):
        """右下角根本沒切到牌（分數很低）時不可以拿來當「一致」的證據。"""
        junk = np.zeros(cp.PART_SIZES["rank"][::-1], np.uint8)
        junk[0:3, 0:3] = 255
        self.assertIsNone(
            cp._best_match(self.query, self.bank, min_score=0.5, min_margin=0.99,
                           query2=junk))

    def test_disagreement_keeps_the_strict_margin(self):
        other = cp.centre_mask(glyph(cp.PART_SIZES["rank"], "blob"))
        hit = cp._best_match(self.query, self.bank, min_score=0.5, min_margin=0.99,
                             query2=other)
        self.assertIsNone(hit, "兩角答案不一致就不該放寬")


class TestRightmostRectMeasuresTheCard(unittest.TestCase):
    def _strip(self):
        """一條長條：左邊一張只露出邊緣的歷史牌，右邊一張完整的牌。"""
        strip = np.zeros((240, 500, 3), np.uint8)
        strip[20:230, 40:80] = 255        # 歷史牌露出的一小條
        strip[20:230, 80:229] = 255       # 最右邊那張（寬 149）
        # 在右邊那張的左上角放一個點數字，讓定位找得到
        strip[26:50, 88:104] = 0
        return strip

    def test_height_comes_from_the_detected_card_not_the_strip(self):
        strip = self._strip()
        rect = cp.rightmost_card_rect(strip, expected_w=160, expected_h=240)
        self.assertIsNotNone(rect)
        # 牌高 210，長條高 240 —— 不可以回傳 240
        self.assertLess(rect[3], 240)
        self.assertAlmostEqual(rect[3], 210, delta=6)

    def test_width_comes_from_the_detected_card_not_the_calibration_box(self):
        strip = self._strip()
        rect = cp.rightmost_card_rect(strip, expected_w=160, expected_h=240)
        self.assertIsNotNone(rect)
        # 校準框換算是 160，實際牌寬 149
        self.assertLess(rect[2], 160)
        self.assertAlmostEqual(rect[2], 149, delta=10)

    def test_an_absurd_measurement_falls_back_to_the_calibrated_width(self):
        """量出來的寬度離譜（黏到別的東西）時退回校準值，不要拿去用。"""
        strip = np.zeros((240, 500, 3), np.uint8)
        strip[20:230, 40:480] = 255        # 一整片白，量出來會遠大於一張牌
        strip[26:50, 48:64] = 0
        rect = cp.rightmost_card_rect(strip, expected_w=160, expected_h=240)
        if rect is not None:
            self.assertLessEqual(rect[2], 160 * 1.4)


if __name__ == "__main__":
    unittest.main()
