"""同時讀左上角與右下角。

實機症狀：2、5、8 一直跳問號，黑桃梅花也常常認錯。原因不是演算法錯，是
**只看一個角落，樣本數只有 1**。同一張牌的模糊、反光、被邊界切到，都會讓
正解剛好被別的字超車 —— 實測「2」被「7」超車、「6」被「A」超車、
黑桃被梅花超車，差距都只有 0.01~0.05。

撲克牌的點數與花色一定印兩次（右下角是左上角轉 180 度），所以多讀一次就等於
多一次獨立取樣，兩次平均之後雜訊互相抵銷。實測點數正確率 47/54 → 52/54，
而且原本認錯的那幾張全部翻正。

比大小畫面的牌會超出校準框，右下角切到的是牌桌背景，那種垃圾資料必須擋掉，
否則會把分數整體拉低到門檻以下。擋掉的方式是「那個角落自己的最高分要先過門檻」，
實測正常牌角 0.8 以上、背景垃圾只有 0.5~0.6，分得非常乾淨。
"""
from __future__ import annotations

import os
import sys
import unittest

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.cardparts import (  # noqa: E402
    DEFAULT_MIN_SCORE,
    RANK_SIZE,
    _best_match,
    centre_mask,
    extract_parts,
    load_part_templates,
    rightmost_card_rect,
)
from src.config import DEFAULT_CONFIG  # noqa: E402

WIDTH, HEIGHT = 1937, 817
PARTS_DIR = os.path.join(ROOT, "card_templates", "parts")


def _shot(name: str):
    img = cv2.imread(os.path.join(ROOT, "debug_captures", name + ".png"))
    return None if img is None else cv2.resize(img, (WIDTH, HEIGHT))


def _slot_parts(shot_name: str, index: int):
    img = _shot(shot_name)
    if img is None:
        return None
    slot = DEFAULT_CONFIG["regions"]["card_slots"][index]
    x, y = int(slot["x"] * WIDTH), int(slot["y"] * HEIGHT)
    w, h = int(slot["w"] * WIDTH), int(slot["h"] * HEIGHT)
    return extract_parts(img[y:y + h, x:x + w], w, h)


class TestBottomCornerExtraction(unittest.TestCase):
    def test_full_card_yields_a_second_corner(self):
        parts = _slot_parts("purple_draw", 0)
        if parts is None:
            self.skipTest("缺少測試截圖")
        self.assertIsNotNone(parts.get("rank2"), "整張牌看得到時應該要讀到右下角")
        self.assertIsNotNone(parts.get("suit2"))
        self.assertEqual((parts["rank2"].shape[1], parts["rank2"].shape[0]), RANK_SIZE)

    def test_partly_covered_card_has_no_second_corner(self):
        """比大小畫面的歷史牌被疊住，只露左邊一條，右下角不該硬切。"""
        img = _shot("thumb_0aa343d3")
        if img is None:
            self.skipTest("缺少測試截圖")
        region = DEFAULT_CONFIG["regions"]["highlow_card"]
        scan_right = DEFAULT_CONFIG.get("highlow_scan_right", 0.62)
        x, y = int(region["x"] * WIDTH), int(region["y"] * HEIGHT)
        w = int((scan_right - region["x"]) * WIDTH)
        h = int(region["h"] * HEIGHT)
        strip = img[y:y + h, x:x + w]
        card_w = int(round(region["w"] * WIDTH))
        rect = rightmost_card_rect(strip, card_w, h)
        self.assertIsNotNone(rect)
        parts = extract_parts(strip, card_w, h, rect=rect)
        self.assertIsNotNone(parts)
        # 就算真的切到東西，那也是牌桌背景，比對時必須被品質門檻擋掉
        if parts.get("rank2") is not None:
            templates = load_part_templates(PARTS_DIR)
            if templates.get("rank"):
                hit = _best_match(parts["rank"], templates["rank"], DEFAULT_MIN_SCORE, 0.0,
                                  query2=parts["rank2"])
                self.assertIsNotNone(hit, "垃圾角落把分數拖到門檻以下了，品質門檻沒有生效")


class TestTwoCornerScoring(unittest.TestCase):
    def _bank(self):
        good = np.zeros(RANK_SIZE[::-1], np.uint8)
        cv2.rectangle(good, (4, 4), (19, 27), 255, -1)
        other = np.zeros(RANK_SIZE[::-1], np.uint8)
        cv2.circle(other, (12, 16), 9, 255, -1)
        return {"good": [centre_mask(good)], "other": [centre_mask(other)]}, good, other

    def test_a_good_second_corner_breaks_a_tie(self):
        bank, good, other = self._bank()
        # 左上角糊掉，剛好比較像錯的那個；右下角很乾淨
        blurred = (cv2.GaussianBlur(other.astype(np.float32), (0, 0), 2.0) > 100)
        blurred = blurred.astype(np.uint8) * 255
        single = _best_match(blurred, bank, 0.0, 0.0)
        both = _best_match(blurred, bank, 0.0, 0.0, query2=good)
        self.assertEqual(single[0], "other")
        self.assertEqual(both[0], "good", "乾淨的第二個角落應該要把結果拉回正解")

    def test_a_junk_second_corner_is_ignored(self):
        bank, good, _ = self._bank()
        junk = np.zeros(RANK_SIZE[::-1], np.uint8)
        junk[0:3, 0:3] = 255
        with_junk = _best_match(good, bank, DEFAULT_MIN_SCORE, 0.0, query2=junk)
        self.assertIsNotNone(with_junk, "垃圾角落不可以把好角落的分數拖垮")
        self.assertEqual(with_junk[0], "good")


if __name__ == "__main__":
    unittest.main()
