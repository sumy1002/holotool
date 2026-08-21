"""鬼牌（Joker）要認得出來，而且不能拖累其他 52 張牌。

## 這個測試在守什麼

使用者實機的死結（2026-08-21 21:31 的 log）：

    已看到選牌畫面，但手牌只認出 4/5，以下是認不出來的那幾格：
       第 2 張：點數 8=0.64  2=0.62 ←分數未達 0.72 | 中央大圖案(黑) S=0.65 C=0.64
    === 已停止 (F9) ===

那一格是鬼牌。它左上角印的不是點數字，而是一個「$」，對 13 個點數的分數
全部落在 0.6 上下 —— 沒有任何門檻可以救，因為正解根本不在候選名單裡。
`_tick` 又是 `len(recognized) == 5` 才動作，所以 bot 就停在那裡不動了。

GUI 當時還會擋下來：代號填 JK 會得到一句「鬼牌沒有點數/花色，不用蒐集」。
那句話是錯的 —— 鬼牌的版面跟普通牌完全一樣（左上角一次、右下角轉 180 度
再一次），只要把 JK 當成第 14 個點數標籤存起來就認得出來。

## 三件必須成立的事

1. 有鬼牌樣板時，鬼牌認得出來，而且回傳的是乾淨的 "JK"（不是 "JKS" 這種
   硬配一個花色上去的標籤）。
2. 加入鬼牌樣板**不會**改變其他牌的判讀（鬼牌樣板對真牌的分數遠低於正解）。
3. 沒有鬼牌樣板時，行為與加這個功能之前完全一樣，而且診斷訊息會直接教人
   去抓 JK，不要再叫人調門檻（把 part_min_score 調到 0.6 才是災難）。
"""
from __future__ import annotations

import json
import os
import sys
import unittest

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import cardparts  # noqa: E402
from src.cardparts import (  # noqa: E402
    JOKER_RANK,
    RANKS,
    RANK_LABELS,
    COMPARISON_GROUPS,
    _group_for,
    classify_parts,
    explain_parts,
    extract_parts,
    joker_template_count,
    load_part_templates,
    missing_parts,
    parse_part_name,
)
from src.handeval import Card, classify_hand  # noqa: E402

CAPTURES = os.path.join(ROOT, "debug_captures")
PARTS = os.path.join(ROOT, "card_templates", "parts")
BUNDLED = os.path.join(ROOT, "defaults", "parts")

# 這張截圖的第 2 格是鬼牌（畫面中央印著 JOKER），其餘四格是普通牌。
JOKER_SHOT = "shot_1365x576_20260821-204456.png"
JOKER_SLOT = 1


def _slot_rois(shot_name: str):
    """依設定檔的手牌校準框，把一張截圖切成五格。找不到檔案回傳 None。"""
    path = os.path.join(CAPTURES, shot_name)
    config_path = os.path.join(ROOT, "config", "config.json")
    if not os.path.exists(path) or not os.path.exists(config_path):
        return None
    image = cv2.imread(path)
    if image is None:
        return None
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    slots = (cfg.get("regions") or {}).get("card_slots") or []
    if len(slots) != 5:
        return None
    height, width = image.shape[:2]
    out = []
    for r in slots:
        x, y = int(r["x"] * width), int(r["y"] * height)
        w, h = int(r["w"] * width), int(r["h"] * height)
        out.append(image[y: y + h, x: x + w])
    return out


def _without_joker(templates: dict) -> dict:
    return {
        "rank": {k: v for k, v in (templates.get("rank") or {}).items() if k != JOKER_RANK},
        "suit": dict(templates.get("suit") or {}),
        "pip": dict(templates.get("pip") or {}),
    }


class TestJokerLabelPlumbing(unittest.TestCase):
    """不需要任何圖片就能守住的規則。"""

    def test_ranks_does_not_contain_joker(self):
        # RANKS 是「真正的 13 個點數」。很多地方靠它算「還缺哪幾個」，
        # 一旦鬼牌混進去，蒐集進度就永遠不會顯示齊全。
        self.assertNotIn(JOKER_RANK, RANKS)
        self.assertEqual(len(RANKS), 13)
        self.assertIn(JOKER_RANK, RANK_LABELS)

    def test_joker_is_not_in_the_rank_comparison_group(self):
        """鬼牌自成一組，不能拉著 13 個點數一起「因為樣板不夠而退回內建糊圖」。

        `load_part_templates` 的 `enough` 是整組一起判斷的：只要組裡有任何一個
        標籤自己抓的樣板不到 MIN_OWN_TO_DROP_BUNDLED 張，整組就繼續混用內建圖。
        內建樣板裡沒有鬼牌，所以把 JK 放進 rank 組 = 永遠 enough=False =
        已經抓滿樣板的人升級後辨識率無聲倒退。
        """
        self.assertNotIn(JOKER_RANK, COMPARISON_GROUPS["rank"][0])
        self.assertEqual(_group_for("rank", JOKER_RANK), (JOKER_RANK,))
        self.assertEqual(_group_for("rank", "8"), tuple(RANKS))

    def test_joker_template_filename_round_trips(self):
        self.assertEqual(parse_part_name("rank_JK_1.png"), ("rank", "JK"))
        self.assertEqual(parse_part_name("rank_JK_12.png"), ("rank", "JK"))

    def test_missing_parts_ignores_joker(self):
        """「還缺哪幾個點數」不算鬼牌，否則永遠不會顯示蒐集齊全。"""
        full = {"rank": {r: [None] for r in RANKS}, "suit": {s: [None] for s in "SHDC"}}
        self.assertEqual(missing_parts(full), ([], []))
        self.assertEqual(joker_template_count(full), 0)
        full["rank"][JOKER_RANK] = [None, None]
        self.assertEqual(missing_parts(full), ([], []))
        self.assertEqual(joker_template_count(full), 2)

    def test_classify_returns_plain_jk_without_a_suit(self):
        """鬼牌不能被硬配一個花色 —— "JKS" 會被 Card.from_label 吃掉，然後
        整條牌型判定都以為手上有一張黑桃。"""
        rank_img = np.zeros((32, 24), np.uint8)
        rank_img[8:24, 6:18] = 255
        suit_img = np.zeros((24, 24), np.uint8)
        suit_img[6:18, 6:18] = 255
        parts = {"rank": rank_img, "suit": suit_img, "rank2": None, "suit2": None,
                 "is_red": False, "corner": None, "pip": None}
        templates = {
            "rank": {JOKER_RANK: [rank_img], "8": [np.zeros((32, 24), np.uint8)]},
            "suit": {"S": [suit_img], "C": [np.zeros((24, 24), np.uint8)]},
            "pip": {},
        }
        hit = classify_parts(parts, templates)
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0], JOKER_RANK)
        # 下游真的吃得下
        self.assertEqual(Card.from_label(hit[0]), Card("JK", "X"))

    def test_joker_is_wild_in_hand_evaluation(self):
        """鬼牌在牌型判定裡是萬能牌 —— 這一段本來就寫好了，這裡只是守住
        「辨識端回傳 JK」與「評估端吃 JK」真的接得起來。"""
        cards = [Card.from_label(l) for l in ("JK", "8H", "8C", "3D", "QS")]
        category, _ = classify_hand(cards)
        self.assertGreaterEqual(category, 3)  # 鬼牌補成三條


@unittest.skipUnless(os.path.isdir(PARTS), "沒有樣板資料夾")
class TestJokerOnRealCapture(unittest.TestCase):
    """用實機原生截圖（1365x576）驗證。缺圖時整組跳過，不讓 CI 紅掉。"""

    @classmethod
    def setUpClass(cls):
        cls.templates = load_part_templates(PARTS, BUNDLED)
        cls.rois = _slot_rois(JOKER_SHOT)

    def setUp(self):
        if self.rois is None:
            self.skipTest(f"找不到 {JOKER_SHOT} 或設定檔")
        if joker_template_count(self.templates) == 0:
            self.skipTest("還沒有鬼牌樣板")

    def _parts(self, index):
        roi = self.rois[index]
        return extract_parts(roi, roi.shape[1], roi.shape[0])

    def test_joker_slot_is_recognised(self):
        parts = self._parts(JOKER_SLOT)
        self.assertIsNotNone(parts, "鬼牌那一格切不出左上角")
        hit = classify_parts(parts, self.templates)
        self.assertIsNotNone(hit, "鬼牌認不出來 —— bot 會卡在選牌畫面 4/5")
        self.assertEqual(hit[0], JOKER_RANK)

    def test_joker_is_unrecognisable_without_its_template(self):
        """守住「這個修法真的有在做事」：拿掉鬼牌樣板就會回到卡死的狀態。"""
        parts = self._parts(JOKER_SLOT)
        self.assertIsNone(classify_parts(parts, _without_joker(self.templates)))

    def test_adding_the_joker_template_changes_nothing_else(self):
        """鬼牌樣板不能把其他牌吃掉。

        實測（10 張實機截圖、50 格）：加進去之後只有鬼牌那一格改變，
        其餘 49 格判讀結果完全一致；鬼牌樣板對真牌的最高分只有 0.66，
        而每一格正解都在 0.72~0.95。
        """
        without = _without_joker(self.templates)
        checked = 0
        for shot in sorted(os.listdir(CAPTURES)):
            if not shot.startswith("shot_") or not shot.endswith(".png"):
                continue
            rois = _slot_rois(shot)
            if rois is None:
                continue
            for index, roi in enumerate(rois):
                parts = extract_parts(roi, roi.shape[1], roi.shape[0])
                if parts is None:
                    continue
                checked += 1
                before = classify_parts(parts, without)
                after = classify_parts(parts, self.templates)
                if shot == JOKER_SHOT and index == JOKER_SLOT:
                    self.assertIsNone(before)
                    self.assertEqual(after[0], JOKER_RANK)
                    continue
                self.assertEqual(
                    before[0] if before else None,
                    after[0] if after else None,
                    f"{shot} 第 {index + 1} 格的判讀被鬼牌樣板改掉了",
                )
        self.assertGreater(checked, 20, "檢查到的格數太少，這個測試等於沒跑")

    def test_explain_tells_the_user_to_collect_jk(self):
        """沒有鬼牌樣板時，診斷訊息要直接說「去抓 JK」。

        以前這條路只會印「←分數未達 0.72」，把人往「調低門檻」帶 ——
        而鬼牌對每個點數都只有 0.6 上下，真的調到那麼低反而開始認錯真牌。
        """
        parts = self._parts(JOKER_SLOT)
        text = explain_parts(parts, _without_joker(self.templates))
        self.assertIn("JK", text)
        self.assertIn("鬼牌", text)

    def test_explain_does_not_talk_about_suits_for_a_joker(self):
        parts = self._parts(JOKER_SLOT)
        text = explain_parts(parts, self.templates)
        self.assertIn("鬼牌", text)
        self.assertNotIn("中央大圖案", text)
        self.assertNotIn("角落花色", text)


if __name__ == "__main__":
    unittest.main()
