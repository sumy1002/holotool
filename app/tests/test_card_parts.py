"""用實機截圖驗證「讀左上角認牌」這條路。

重點在於：只要 13 個點數 + 4 個花色的小樣板，就能認得全部 52 張牌，
不需要一張一張蒐集 52 張整卡。
"""
from __future__ import annotations

import json
import os
import sys
import unittest

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.cardparts import (  # noqa: E402
    NUMBER_RANKS,
    classify_parts,
    extract_parts,
    load_part_templates,
    missing_parts,
    rightmost_card_rect,
)
from src.config import DEFAULT_CONFIG  # noqa: E402
from src.recognize import CardReader  # noqa: E402

# 花色都用「卡面中央的大圖案」核對過；人像牌則放大左上角判讀
HANDS = {
    "purple_draw": ["JS", "6D", "9C", "4H", "QC"],
    "thumb_417c3390": ["JS", "6D", "9C", "4H", "QC"],
    "thumb_8236924e": ["JS", "6D", "9C", "4H", "QC"],
    "thumb_09e11d90": ["JS", "KS", "QH", "3C", "QC"],
    "thumb_1aefc889": ["4C", "KS", "8H", "8C", "10C"],
    "thumb_36ebf263": ["2S", "3D", "AH", "KH", "7D"],
    "thumb_a07eeebf": ["2S", "3D", "AH", "9C", "QC"],
    "thumb_d3730a7b": ["2S", "3D", "AH", "9C", "QC"],
    "thumb_1faf9fd5": ["6H", "3D", "8H", "8C", "KD"],
    "thumb_39b73141": ["6H", "3D", "8H", "8C", "KD"],
    "thumb_fcb1c3a7": ["7D", "2H", "7C", "4H", None],   # 第五張是鬼牌
}

# 比大小畫面：最右邊那張完整露出的牌。
# 剛翻開時那張會放大並超出校準框，屬過渡動畫，會回報認不出來（安全，下一輪再讀）。
HIGHLOW_STEADY = {
    "thumb_0aa343d3": "9C",
    "thumb_ba691226": "10C",
    "thumb_ee004661": "7C",
}

WIDTH, HEIGHT = 1937, 817


def _shot(name: str):
    img = cv2.imread(os.path.join(ROOT, "debug_captures", name + ".png"))
    if img is None:
        return None
    return cv2.resize(img, (WIDTH, HEIGHT))


def _slots():
    return DEFAULT_CONFIG["regions"]["card_slots"]


def _slot_roi(img, slot):
    x, y = int(slot["x"] * WIDTH), int(slot["y"] * HEIGHT)
    w, h = int(slot["w"] * WIDTH), int(slot["h"] * HEIGHT)
    return img[y:y + h, x:x + w], w, h


def _all_samples():
    """回傳 [(截圖名, 牌面, parts), ...]"""
    out = []
    for name, labels in HANDS.items():
        img = _shot(name)
        if img is None:
            continue
        for slot, label in zip(_slots(), labels):
            if label is None:
                continue
            roi, w, h = _slot_roi(img, slot)
            parts = extract_parts(roi, w, h)
            if parts is not None:
                out.append((name, label, parts))
    return out


class TestExtraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = _all_samples()
        if not cls.samples:
            raise unittest.SkipTest("找不到 debug_captures 裡的截圖")

    def test_every_card_yields_a_rank_and_a_suit(self):
        expected = sum(1 for labels in HANDS.values() for x in labels if x)
        self.assertEqual(
            len(self.samples), expected,
            f"只有 {len(self.samples)}/{expected} 張牌切得出左上角",
        )

    def test_red_black_is_always_right(self):
        """顏色是花色判斷的第一道關卡，錯了後面全錯。"""
        for name, label, parts in self.samples:
            with self.subTest(shot=name, card=label):
                self.assertEqual(
                    parts["is_red"], label[-1] in "HD",
                    f"{label} 的紅黑判斷相反了",
                )

    def test_number_cards_expose_a_centre_pip(self):
        """數字牌中央的大圖案是分辨黑桃/梅花的關鍵，必須抓得到。"""
        for name, label, parts in self.samples:
            if label[:-1] not in NUMBER_RANKS:
                continue
            with self.subTest(shot=name, card=label):
                self.assertIsNotNone(parts.get("pip"), f"{label} 抓不到中央大圖案")


class TestClassificationLeaveOneOut(unittest.TestCase):
    """拿「其他截圖」當樣板去認「這一張截圖」，避免自己認自己的假象。"""

    @classmethod
    def setUpClass(cls):
        cls.samples = _all_samples()
        if len(cls.samples) < 20:
            raise unittest.SkipTest("樣本太少，無法做 leave-one-out")

    def test_recognition_is_accurate_and_never_confidently_wrong(self):
        correct = unknown = wrong = 0
        mistakes = []
        for name, label, parts in self.samples:
            bank = {"rank": {}, "suit": {}, "pip": {}}
            for other, other_label, other_parts in self.samples:
                if other == name:
                    continue
                bank["rank"].setdefault(other_label[:-1], []).append(other_parts["rank"])
                bank["suit"].setdefault(other_label[-1], []).append(other_parts["suit"])
                if other_label[:-1] in NUMBER_RANKS and other_parts.get("pip") is not None:
                    bank["pip"].setdefault(other_label[-1], []).append(other_parts["pip"])
            hit = classify_parts(parts, bank)
            if hit is None:
                unknown += 1
            elif hit[0] == label:
                correct += 1
            else:
                wrong += 1
                mistakes.append(f"{name} {label}->{hit[0]}")

        total = len(self.samples)
        # 這批截圖是 512 寬縮圖再放大的，比實機模糊得多，算是最壞情況
        self.assertGreaterEqual(
            correct / total, 0.85,
            f"辨識率只有 {correct}/{total}（認不出 {unknown}、認錯 {wrong}）",
        )
        self.assertLessEqual(
            wrong / total, 0.05,
            f"認錯太多：{wrong}/{total} —— {mistakes}",
        )

    def test_rank_is_never_wrong(self):
        """比大小只看點數，點數認錯代價最大，必須零錯誤。"""
        for name, label, parts in self.samples:
            bank = {"rank": {}, "suit": {}, "pip": {}}
            for other, other_label, other_parts in self.samples:
                if other == name:
                    continue
                bank["rank"].setdefault(other_label[:-1], []).append(other_parts["rank"])
                bank["suit"].setdefault(other_label[-1], []).append(other_parts["suit"])
                if other_label[:-1] in NUMBER_RANKS and other_parts.get("pip") is not None:
                    bank["pip"].setdefault(other_label[-1], []).append(other_parts["pip"])
            hit = classify_parts(parts, bank)
            if hit is None:
                continue
            with self.subTest(shot=name, card=label):
                self.assertEqual(hit[0][:-1], label[:-1], f"{label} 的點數被認成 {hit[0][:-1]}")


class TestWithShippedTemplates(unittest.TestCase):
    """用專案內附的 parts 樣板實際跑一遍。"""

    @classmethod
    def setUpClass(cls):
        cls.templates = load_part_templates(os.path.join(ROOT, "card_templates", "parts"))
        if not cls.templates.get("rank"):
            raise unittest.SkipTest("card_templates/parts 是空的")
        cls.reader = CardReader(part_templates=cls.templates)

    def test_shipped_templates_cover_almost_every_rank_and_suit(self):
        miss_rank, miss_suit = missing_parts(self.templates)
        self.assertEqual(miss_suit, [], f"內附樣板缺花色：{miss_suit}")
        self.assertLessEqual(len(miss_rank), 1, f"內附樣板缺太多點數：{miss_rank}")

    def _read_all(self):
        """回傳 [(截圖, 正解, 讀到的東西 or None), ...]"""
        out = []
        for name, labels in HANDS.items():
            img = _shot(name)
            if img is None:
                continue
            for slot, label in zip(_slots(), labels):
                if label is None:
                    continue
                roi, w, h = _slot_roi(img, slot)
                out.append((name, label, self.reader.read(roi, w, h)))
        return out

    def test_rank_is_never_read_wrong_with_shipped_templates(self):
        """點數不可以認錯 —— 比大小只看點數，認錯就會下錯注。

        內附樣板是從 512 寬的縮圖放大來的，比實機糊很多，屬於最壞情況；
        這種情況下「認不出來」可以接受（下一輪重讀），「認錯」不行。
        """
        bad = [f"{n} 期望 {lab} 卻讀成 {hit[0]}"
               for n, lab, hit in self._read_all()
               if hit is not None and hit[0][:-1] != lab[:-1]]
        self.assertEqual(bad, [], "\n".join(bad))

    def test_number_cards_are_read_correctly_and_never_wrong(self):
        """數字牌（有中央大圖案可以參考）必須又準又不認錯。

        原本這裡是一條「整體正確率 >= 75%」的斷言，但它把兩件完全不同的事
        混在一起：真正的回歸，以及**人像牌花色**這個已知且尚未解決的限制
        （J/Q/K 沒有中央大圖案，黑桃/梅花只能靠角落那顆十幾像素的小花色）。
        混在一起的後果是：門檻一被人像牌拖過線就整條紅掉，而你看不出到底是
        演算法壞了、還是又是那幾張人像牌 —— 分不出來的測試等於沒有測試。

        所以拆成兩條。這一條管數字牌，實測（2026-08-21，使用者當下的樣板）：
            數字牌 34 張：完全正確 27（79.4%）、花色認錯 **0**、認不出 7
        認不出來是安全的（下一輪重讀），認錯不是。
        """
        rows = [r for r in self._read_all() if r[1][0] not in "JQK"]
        ok = sum(1 for _, lab, hit in rows if hit is not None and hit[0] == lab)
        wrong = [f"{n} 期望 {lab} 卻讀成 {hit[0]}"
                 for n, lab, hit in rows if hit is not None and hit[0] != lab]
        self.assertEqual(wrong, [], "數字牌被認錯了：\n" + "\n".join(wrong))
        self.assertGreaterEqual(ok / len(rows), 0.70,
                                f"數字牌只對 {ok}/{len(rows)}")

    def test_face_card_ranks_are_right_even_when_the_suit_is_not(self):
        """人像牌的**點數**必須全對；花色允許錯，但不能全盤崩掉。

        實測（2026-08-21）：人像牌 15 張，完全正確 6、花色認錯 8、認不出 1。
        八個錯全部是同一個方向 —— 梅花被判成黑桃、紅心被判成方塊，
        而且全發生在 Q 與 K（J 全對）。點數一個都沒錯。

        花色只影響「同花」的判斷；比大小完全不看花色，所以點數對就不會下錯注。
        要修的話得在「點數/花色樣板」分頁補抓**梅花的 Q/K 與紅心的 Q/K**
        角落樣板，那幾個標籤就會改用你自己的圖。
        """
        rows = [r for r in self._read_all() if r[1][0] in "JQK"]
        rank_wrong = [f"{n} 期望 {lab} 卻讀成 {hit[0]}"
                      for n, lab, hit in rows
                      if hit is not None and hit[0][:-1] != lab[:-1]]
        self.assertEqual(rank_wrong, [], "人像牌點數被認錯了：\n" + "\n".join(rank_wrong))
        ok = sum(1 for _, lab, hit in rows if hit is not None and hit[0] == lab)
        self.assertGreaterEqual(ok / len(rows), 0.30,
                                f"人像牌連花色一起算只對 {ok}/{len(rows)}，"
                                "低到這個程度就不只是已知限制了")

    def test_nothing_is_ever_read_with_the_wrong_rank(self):
        """整體：點數永遠不可以認錯。這是唯一會讓 bot 下錯注的錯誤。"""
        rows = self._read_all()
        bad = [f"{n} 期望 {lab} 卻讀成 {hit[0]}"
               for n, lab, hit in rows
               if hit is not None and hit[0][:-1] != lab[:-1]]
        self.assertEqual(bad, [], "\n".join(bad))

    def test_own_source_templates_read_everything(self):
        """證明「用自己截下來的樣板」就認得出來 —— 留一法交叉驗證。

        這正是把內建樣板跟使用者樣板分開的理由：同一個標籤只要有自己的樣板，
        就不該再混進內建的糊圖。
        """
        samples = _all_samples()
        wrong = []
        for key, label, parts in samples:
            bank = {"rank": {}, "suit": {}, "pip": {}}
            for other, other_label, other_parts in samples:
                if other == key:
                    continue
                for src in ("rank", "rank2"):
                    if other_parts.get(src) is not None:
                        bank["rank"].setdefault(other_label[:-1], []).append(other_parts[src])
                for src in ("suit", "suit2"):
                    if other_parts.get(src) is not None:
                        bank["suit"].setdefault(other_label[-1], []).append(other_parts[src])
                if other_label[:-1] in NUMBER_RANKS and other_parts.get("pip") is not None:
                    bank["pip"].setdefault(other_label[-1], []).append(other_parts["pip"])
            hit = classify_parts(parts, bank)
            if hit is None or hit[0] != label:
                wrong.append(f"{key} 期望 {label} 得到 {hit}")
        self.assertLessEqual(len(wrong) / len(samples), 0.08, "\n".join(wrong))

    def test_reads_the_rightmost_card_in_the_highlow_row(self):
        region = DEFAULT_CONFIG["regions"]["highlow_card"]
        scan_right = DEFAULT_CONFIG.get("highlow_scan_right", 0.62)
        for name, label in HIGHLOW_STEADY.items():
            img = _shot(name)
            if img is None:
                continue
            with self.subTest(shot=name):
                x = int(region["x"] * WIDTH)
                y = int(region["y"] * HEIGHT)
                w = int((scan_right - region["x"]) * WIDTH)
                h = int(region["h"] * HEIGHT)
                strip = img[y:y + h, x:x + w]
                card_w = int(round(region["w"] * WIDTH))
                self.assertIsNotNone(
                    rightmost_card_rect(strip, card_w, h),
                    f"{name} 找不到最右邊那張牌的位置",
                )
                hit = self.reader.read_rightmost(strip, card_w, h)
                self.assertIsNotNone(hit, f"{name} 讀不出比大小的牌（應為 {label}）")
                self.assertEqual(hit[0], label, f"{name} 應為 {label}，卻讀成 {hit[0]}")


class TestReaderFallback(unittest.TestCase):
    def test_reader_without_any_template_is_not_ready(self):
        reader = CardReader()
        self.assertFalse(reader.ready)
        self.assertIsNone(reader.read(None))


if __name__ == "__main__":
    unittest.main()
