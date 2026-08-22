"""效能改寫的等價性測試：算得快可以，算出不一樣的分數不行。

兩個改寫都是「把重複的準備工作抽出來」：

* `cardparts._score_bank`：舊寫法對每個（位移變體 × 樣板）配對重算投影輪廓，
  同一條輪廓被算上百次。新寫法先各算一次再交叉比對 —— 數學上必須完全等價。
* `recognize.marker_score`：樣板的灰階與各倍率縮放版本改成快取。樣板從載入到
  程式結束都不會變，但快取鍵用的是 id()，所以要驗「內容變了會發現」。
"""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import cardparts  # noqa: E402
from src import recognize  # noqa: E402


def _blob(shape, seed):
    rng = np.random.default_rng(seed)
    return (rng.random(shape) > 0.5).astype(np.uint8) * 255


def _gray_blob(shape, seed):
    """帶中間灰階值的查詢圖 —— 跟實際情況一樣。

    `_normalize` 用 INTER_AREA 縮放，產出的查詢小圖不是 0/255 的二值圖，
    邊緣都是中間值。重疊度是灰階的軟性 IoU，等價性一定要用這種圖來驗，
    只用二值圖驗會漏掉「偷偷改成二值交集」這種看起來很合理的錯誤改寫。
    """
    rng = np.random.default_rng(seed)
    big = (rng.random((shape[0] * 3, shape[1] * 3)) > 0.5).astype(np.uint8) * 255
    import cv2
    return cv2.resize(big, (shape[1], shape[0]), interpolation=cv2.INTER_AREA)


class TestScoreBankMatchesTheSlowFormula(unittest.TestCase):
    def _slow(self, query, bank, align=1):
        """舊版公式，逐配對呼叫 part_score()。當作真值。"""
        variants = cardparts._align_variants(query, align)
        return {label: max(cardparts.part_score(v, t) for t in imgs for v in variants)
                for label, imgs in bank.items()}

    def test_scores_are_identical(self):
        size = (cardparts.RANK_SIZE[1], cardparts.RANK_SIZE[0])   # (h, w)
        bank = {
            "A": [_blob(size, 1), _blob(size, 2)],
            "8": [_blob(size, 3)],
            "10": [_blob(size, 4), _blob(size, 5), _blob(size, 6)],
        }
        for seed in range(5):
            query = _blob(size, 100 + seed)
            fast = cardparts._score_bank(query, bank)
            slow = self._slow(query, bank)
            self.assertEqual(set(fast), set(slow))
            for label in bank:
                self.assertAlmostEqual(fast[label], slow[label], places=9,
                                       msg=f"標籤 {label} 分數不一致")

    def test_scores_are_identical_with_grayscale_queries(self):
        """真實查詢圖帶中間灰階值（軟性 IoU 的情況）也必須一個位元都不差。"""
        size = (cardparts.RANK_SIZE[1], cardparts.RANK_SIZE[0])
        bank = {
            "9": [_gray_blob(size, 11), _blob(size, 12)],
            "Q": [_gray_blob(size, 13)],
        }
        for seed in range(5):
            query = _gray_blob(size, 200 + seed)
            fast = cardparts._score_bank(query, bank)
            slow = self._slow(query, bank)
            for label in bank:
                self.assertEqual(fast[label], slow[label],
                                 f"標籤 {label}：灰階查詢的分數跟舊公式不一致")

    def test_mismatched_template_shape_scores_zero_like_before(self):
        size = (cardparts.RANK_SIZE[1], cardparts.RANK_SIZE[0])
        query = _blob(size, 7)
        bank = {"K": [_blob((8, 8), 8)]}          # 尺寸不對的樣板
        self.assertEqual(cardparts._score_bank(query, bank)["K"], 0.0)

    def test_align_zero_still_works(self):
        size = (cardparts.SUIT_SIZE[1], cardparts.SUIT_SIZE[0])
        query = _blob(size, 9)
        bank = {"S": [query.copy()]}
        fast = cardparts._score_bank(query, bank, align=0)
        slow = self._slow(query, bank, align=0)
        self.assertAlmostEqual(fast["S"], slow["S"], places=9)


class TestMarkerTemplateCache(unittest.TestCase):
    def _scene(self, seed=0):
        rng = np.random.default_rng(seed)
        tmpl = (rng.random((24, 60, 3)) * 255).astype(np.uint8)
        roi = (rng.random((120, 200, 3)) * 40).astype(np.uint8)
        roi[30:54, 50:110] = tmpl                 # 把樣板貼進場景
        return roi, tmpl

    def test_repeated_calls_return_the_same_score(self):
        roi, tmpl = self._scene()
        first = recognize.marker_score(roi, tmpl, expected_scale=1.0)
        for _ in range(3):
            self.assertEqual(recognize.marker_score(roi, tmpl, expected_scale=1.0), first)
        self.assertGreater(first, 0.9, "貼進去的樣板本人應該拿高分")

    def test_cache_matches_a_fresh_copy(self):
        """快取路徑跟「全新陣列（必然重算）」的結果一模一樣。"""
        roi, tmpl = self._scene(1)
        recognize.marker_score(roi, tmpl, expected_scale=1.0)      # 進快取
        cached = recognize.marker_score(roi, tmpl, expected_scale=1.0)
        fresh = recognize.marker_score(roi, tmpl.copy(), expected_scale=1.0)
        self.assertEqual(cached, fresh)

    def test_inplace_mutation_is_detected(self):
        """同一個陣列（同 id）內容被整個換掉 → 指紋不符 → 必須重算。"""
        roi, tmpl = self._scene(2)
        before = recognize.marker_score(roi, tmpl, expected_scale=1.0)
        other = self._scene(3)[1]
        tmpl[:] = other                            # id 不變、內容全換
        after = recognize.marker_score(roi, tmpl, expected_scale=1.0)
        fresh = recognize.marker_score(roi, other.copy(), expected_scale=1.0)
        self.assertEqual(after, fresh, "內容換了卻還在用舊快取")
        self.assertNotEqual(before, after)

    def test_different_scales_are_cached_separately(self):
        roi, tmpl = self._scene(4)
        a = recognize.marker_score(roi, tmpl, expected_scale=1.0)
        b = recognize.marker_score(roi, tmpl, expected_scale=0.5)
        # 再各叫一次，要跟第一次一致（各自命中自己的快取）
        self.assertEqual(recognize.marker_score(roi, tmpl, expected_scale=1.0), a)
        self.assertEqual(recognize.marker_score(roi, tmpl, expected_scale=0.5), b)


if __name__ == "__main__":
    unittest.main()
