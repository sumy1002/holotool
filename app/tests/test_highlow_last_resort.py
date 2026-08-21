"""比大小的點數不能因為「領先幅度不足」而讓 bot 卡死。

使用者實機 log：

    比大小認不到牌：點數 8=0.84  5=0.79 ←領先不足 0.05 | 中央大圖案(紅) D=0.97  H=0.78
    === 已停止 (F9) ===

那張牌是 8♦。花色判斷完美（D=0.97 對 H=0.78），點數的**第一名也是對的**，
卻因為只領先 0.0498（門檻 0.05）被 `_best_match` 擋掉，回報「認不出來」。
比大小畫面沒有任何畫面標記，所以讀不到牌不會退回別的判斷，而是一路掉到待機，
bot 就永遠停在那裡等 —— 使用者只能按 F9。

兩邊代價不對等：
* 猜錯點數 → 這一次大小猜壞（而且 bot 本來就要求連續兩次讀到同一張才動作）。
* 認不出來 → 整個任務停擺。

所以比大小這條路（`read_rightmost`）放寬領先幅度，選牌那條路（`read`）維持嚴格 ——
選牌讀錯會影響湊牌決策，而且那個畫面認不出來時使用者可以自己補樣板。

`min_score` **不放寬**：分數本身沒過代表根本不像任何一個點數（框沒對準、
切壞了），那種情況硬猜沒有意義，只會讓 bot 拿垃圾去下決策。
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
from src.recognize import CardReader  # noqa: E402


def _mask(size, fill_rows):
    """做一張假的二值小圖：指定哪幾列填滿。"""
    w, h = size
    img = np.zeros((h, w), np.uint8)
    for r in fill_rows:
        img[r, :] = 255
    return img


def _parts(rank_img, pip_img=None, is_red=True):
    return {
        "rank": rank_img,
        "rank2": None,
        "suit": _mask(cardparts.SUIT_SIZE, (4, 5, 6)),
        "suit2": None,
        "pip": pip_img,
        "is_red": is_red,
    }


def _templates(rank_bank, suit_bank=None, pip_bank=None):
    return {
        "rank": rank_bank,
        "suit": suit_bank if suit_bank is not None
                else {s: [_mask(cardparts.SUIT_SIZE, (4, 5, 6))] for s in cardparts.SUITS},
        "pip": pip_bank or {},
    }


class TestThinMargin(unittest.TestCase):
    """人工造出「兩個點數分數很接近、第一名是對的」的情況。"""

    def setUp(self):
        h = cardparts.RANK_SIZE[1]
        # 查詢圖跟 "8" 的樣板完全一樣，跟 "5" 只差一列 —— 分數必然很接近。
        self.query = _mask(cardparts.RANK_SIZE, tuple(range(2, h - 2)))
        self.bank = {
            "8": [_mask(cardparts.RANK_SIZE, tuple(range(2, h - 2)))],
            "5": [_mask(cardparts.RANK_SIZE, tuple(range(2, h - 3)))],
        }
        # 確認這組資料真的落在「第一名正確但領先不足」的區間，否則測試沒意義
        scores = cardparts._score_bank(self.query, self.bank, 1)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        self.assertEqual(ranked[0][0], "8", scores)
        self.lead = ranked[0][1] - ranked[1][1]
        self.assertGreater(ranked[1][1], 0.5, scores)
        self.assertLess(self.lead, 0.5, scores)
        self.margin = self.lead + 0.01   # 剛好擋得住

    def test_strict_path_still_rejects(self):
        """選牌畫面不放寬 —— 讀錯牌會影響湊牌決策。"""
        got = cardparts.classify_parts(
            _parts(self.query), _templates(self.bank),
            min_score=0.2, min_margin=self.margin,
        )
        self.assertIsNone(got)

    def test_last_resort_takes_the_leader(self):
        got = cardparts.classify_parts(
            _parts(self.query), _templates(self.bank),
            min_score=0.2, min_margin=self.margin, rank_last_resort=True,
        )
        self.assertIsNotNone(got)
        self.assertTrue(got[0].startswith("8"), got)

    def test_last_resort_explains_itself(self):
        """用了猜的，log 一定要講出來，不能默默改行為。"""
        notes: list = []
        cardparts.classify_parts(
            _parts(self.query), _templates(self.bank),
            min_score=0.2, min_margin=self.margin,
            rank_last_resort=True, notes=notes,
        )
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("8", notes[0])
        self.assertIn("5", notes[0])      # 第二名也要講，才知道要補哪兩張樣板

    def test_no_note_when_the_margin_was_fine(self):
        notes: list = []
        got = cardparts.classify_parts(
            _parts(self.query), _templates(self.bank),
            min_score=0.2, min_margin=0.0,
            rank_last_resort=True, notes=notes,
        )
        self.assertIsNotNone(got)
        self.assertEqual(notes, [])


class TestMinScoreIsNotRelaxed(unittest.TestCase):
    """只放寬「領先幅度」，分數門檻照舊。"""

    def test_garbage_is_still_rejected(self):
        h = cardparts.RANK_SIZE[1]
        query = _mask(cardparts.RANK_SIZE, (0,))   # 幾乎全黑，跟誰都不像
        bank = {
            "8": [_mask(cardparts.RANK_SIZE, tuple(range(2, h - 2)))],
            "5": [_mask(cardparts.RANK_SIZE, tuple(range(2, h - 3)))],
        }
        notes: list = []
        got = cardparts.classify_parts(
            _parts(query), _templates(bank),
            min_score=0.9, min_margin=0.05,
            rank_last_resort=True, notes=notes,
        )
        self.assertIsNone(got)
        self.assertEqual(notes, [])


class TestExplainWording(unittest.TestCase):
    """診斷訊息不能再叫使用者去調一個已經不會擋人的門檻。"""

    def setUp(self):
        h = cardparts.RANK_SIZE[1]
        self.query = _mask(cardparts.RANK_SIZE, tuple(range(2, h - 2)))
        self.bank = {
            "8": [_mask(cardparts.RANK_SIZE, tuple(range(2, h - 2)))],
            "5": [_mask(cardparts.RANK_SIZE, tuple(range(2, h - 3)))],
        }

    def test_draw_screen_still_names_the_margin(self):
        why = cardparts.explain_parts(
            _parts(self.query), _templates(self.bank),
            min_score=0.2, min_margin=0.99,
        )
        self.assertIn("領先不足", why)

    def test_highlow_does_not_name_the_margin(self):
        why = cardparts.explain_parts(
            _parts(self.query), _templates(self.bank),
            min_score=0.2, min_margin=0.99, rank_last_resort=True,
        )
        self.assertNotIn("領先不足", why)

    def test_score_failure_is_still_reported(self):
        why = cardparts.explain_parts(
            _parts(_mask(cardparts.RANK_SIZE, (0,))), _templates(self.bank),
            min_score=0.9, min_margin=0.05, rank_last_resort=True,
        )
        self.assertIn("分數未達", why)


class TestReaderPlumbing(unittest.TestCase):
    """`CardReader.last_note` 是 bot 唯一拿得到這件事的管道。"""

    def test_note_starts_empty_and_is_cleared_each_read(self):
        reader = CardReader(part_templates={"rank": {}, "suit": {}})
        self.assertEqual(reader.last_note, "")
        reader.last_note = "舊的"
        reader.read_rightmost(None, 10, 10)
        self.assertEqual(reader.last_note, "")

    def test_read_rightmost_passes_the_flag(self):
        """不要只測「有這個參數」，要測真的傳了 True 下去。"""
        seen = {}
        orig = cardparts.recognize_by_corner

        def spy(*a, **kw):
            seen.update(kw)
            return ("8D", 0.84)

        cardparts.recognize_by_corner = spy
        orig_rect = cardparts.rightmost_card_rect
        cardparts.rightmost_card_rect = lambda *a, **kw: (0, 0, 8, 12)
        try:
            reader = CardReader(part_templates={
                "rank": {"8": [_mask(cardparts.RANK_SIZE, (1,))]},
                "suit": {"D": [_mask(cardparts.SUIT_SIZE, (1,))]},
            })
            got = reader.read_rightmost(np.zeros((12, 40, 3), np.uint8), 8, 12)
        finally:
            cardparts.recognize_by_corner = orig
            cardparts.rightmost_card_rect = orig_rect
        self.assertEqual(got, ("8D", 0.84))
        self.assertTrue(seen.get("rank_last_resort"))

    def test_the_strict_read_path_does_not_relax(self):
        seen = {}
        orig = cardparts.recognize_by_corner

        def spy(*a, **kw):
            seen.update(kw)
            return ("8D", 0.84)

        cardparts.recognize_by_corner = spy
        try:
            reader = CardReader(part_templates={
                "rank": {"8": [_mask(cardparts.RANK_SIZE, (1,))]},
                "suit": {"D": [_mask(cardparts.SUIT_SIZE, (1,))]},
            })
            reader.read(np.zeros((12, 8, 3), np.uint8), 8, 12)
        finally:
            cardparts.recognize_by_corner = orig
        self.assertFalse(seen.get("rank_last_resort", False))


if __name__ == "__main__":
    unittest.main()
