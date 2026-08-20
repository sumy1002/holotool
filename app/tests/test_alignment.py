"""對位：同一個字在不同影格切出來會差一兩像素，不對位就會整片認不出來。

實機症狀（2026-08-20 19:22）：「點數 13/13、花色 4/4」都蒐集齊了，
執行時卻只認出 2/5。原因是正規化只能把字置中到整數像素，同一個點數在不同
影格的位置常差一兩格，IoU 就掉 0.15~0.20 —— 正解分數中位數只有 0.78，
剛好卡在門檻 0.80 底下，於是全部被判定「認不出來」。
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
    DEFAULT_MIN_MARGIN,
    DEFAULT_MIN_SCORE,
    RANK_SIZE,
    _align_variants,
    _shift,
    centre_mask,
    load_part_templates,
    part_score,
)

PARTS_DIR = os.path.join(ROOT, "card_templates", "parts")


def _jitter(img, dx, dy, sigma):
    """模擬下一個影格看到同一張牌：位移一兩像素 + 抗鋸齒略有不同。"""
    moved = _shift(img, dx, dy).astype(np.float32)
    return (cv2.GaussianBlur(moved, (0, 0), sigma) > 127).astype(np.uint8) * 255


JITTERS = [(1, 0, 0.6), (0, 1, 0.6), (-1, 1, 0.9), (1, -1, 0.9), (2, 0, 1.1), (0, -2, 1.1)]


class TestCentreMask(unittest.TestCase):
    def test_centres_the_foreground_centroid(self):
        canvas = np.zeros((32, 24), np.uint8)
        canvas[2:10, 2:8] = 255           # 一塊偏左上的前景
        out = centre_mask(canvas)
        ys, xs = np.nonzero(out > 127)
        self.assertAlmostEqual(xs.mean(), (24 - 1) / 2, delta=1.0)
        self.assertAlmostEqual(ys.mean(), (32 - 1) / 2, delta=1.0)

    def test_a_shifted_copy_lands_in_the_same_place(self):
        canvas = np.zeros((32, 24), np.uint8)
        canvas[8:20, 6:16] = 255
        a = centre_mask(canvas)
        b = centre_mask(_shift(canvas, 2, -2))
        self.assertGreaterEqual(part_score(a, b), 0.97,
                                "位移後置中，兩張圖應該幾乎一模一樣")

    def test_survives_an_empty_mask(self):
        empty = np.zeros((32, 24), np.uint8)
        self.assertIsNotNone(centre_mask(empty))


class TestAlignmentRecoversScores(unittest.TestCase):
    """一兩像素的位移不應該讓分數掉到門檻以下。"""

    @classmethod
    def setUpClass(cls):
        cls.templates = load_part_templates(PARTS_DIR)
        if not cls.templates.get("rank"):
            raise unittest.SkipTest("card_templates/parts 是空的")

    def _score_with_align(self, query, template):
        return max(part_score(v, template) for v in _align_variants(query, 1))

    def test_shifted_glyph_still_scores_high(self):
        worst = 1.0
        worst_at = ""
        for label, imgs in self.templates["rank"].items():
            for img in imgs[:2]:
                for dx, dy, sigma in JITTERS:
                    score = self._score_with_align(_jitter(img, dx, dy, sigma), img)
                    if score < worst:
                        worst, worst_at = score, f"{label} (dx={dx},dy={dy})"
        self.assertGreaterEqual(
            worst, DEFAULT_MIN_SCORE,
            f"位移後最低分只有 {worst:.2f}（{worst_at}），低於門檻 {DEFAULT_MIN_SCORE}",
        )

    def test_alignment_actually_helps(self):
        """沒對位時分數會明顯較低——這是這個修正存在的理由。"""
        gains = []
        for label, imgs in self.templates["rank"].items():
            for img in imgs[:2]:
                for dx, dy, sigma in JITTERS:
                    q = _jitter(img, dx, dy, sigma)
                    gains.append(self._score_with_align(q, img) - part_score(q, img))
        self.assertGreater(
            float(np.median(gains)), 0.0,
            "對位之後分數沒有變好，這個修正就沒意義了",
        )

    def test_right_answer_still_leads_by_a_clear_margin(self):
        """對位會讓所有分數變高，必須確認正解仍然明顯領先。"""
        bad = []
        for label, imgs in self.templates["rank"].items():
            for dx, dy, sigma in JITTERS[:3]:
                query = _jitter(imgs[0], dx, dy, sigma)
                scores = {
                    other: max(self._score_with_align(query, t) for t in pool)
                    for other, pool in self.templates["rank"].items()
                }
                ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
                if ranked[0][0] != label:
                    bad.append(f"{label} 被認成 {ranked[0][0]}")
                elif ranked[0][1] - ranked[1][1] < DEFAULT_MIN_MARGIN:
                    bad.append(f"{label} 領先只有 {ranked[0][1] - ranked[1][1]:.3f}")
        self.assertEqual(bad, [], "；".join(bad))


class TestExplainFailures(unittest.TestCase):
    """辨識失敗時要說得出原因，不能只說『請補樣板』。"""

    def test_explain_reports_the_top_candidates(self):
        from src.cardparts import explain_parts

        templates = load_part_templates(PARTS_DIR)
        if not templates.get("rank"):
            self.skipTest("card_templates/parts 是空的")
        label, imgs = next(iter(templates["rank"].items()))
        suit_label, suit_imgs = next(iter(templates["suit"].items()))
        parts = {"rank": imgs[0], "suit": suit_imgs[0], "is_red": suit_label in "HD"}
        text = explain_parts(parts, templates)
        self.assertIn("點數", text)
        self.assertIn(label, text)

    def test_explain_handles_missing_templates(self):
        from src.cardparts import explain_parts

        self.assertIn("沒有", explain_parts({"rank": None, "suit": None, "is_red": False}, {}))


if __name__ == "__main__":
    unittest.main()
