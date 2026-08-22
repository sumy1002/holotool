"""為什麼「多抓幾次」以前完全沒有用（2026-08-21）。

使用者回報：「我現在自己手動讀取目前畫面、輸入或修改很多次，但很多時候出現之前
修改過的卡還是會判定錯誤。我以為我多輸入多抓取程式會更容易判斷，但看起來好像
都沒變化。」「有時候方塊會被認為是愛心。」

拿他實機的 67 個樣板量出來，一共四個獨立的 bug 疊在一起：

1. **「哪些是內建的」只比檔名。** 內建檔叫 `suit_D_1.png`~`suit_D_8.png`，而
   `next_part_path()` 挑「目前空著的最小編號」—— 內建檔被刪掉之後就是 `_1`。
   於是他自己抓的樣板被寫進一個「看起來像內建」的檔名，然後
   **下一次儲存會把上一次的成果當成內建檔刪掉**。永遠累積不起來。
   實測：67 個檔案裡有 19 個是他自己的、卻坐在內建檔名上。

2. **同一批檔案在比對時也被當成內建糊圖丟掉**（`load_part_templates` 的
   fallback 桶）。存了等於沒存。

3. **`part_is_usable` 在 `centre_mask` 之後才判斷。** centre_mask 是平移，
   貼邊的圖案平移後會被切掉一角、佔滿度掉下來，於是好樣板被判成「裁壞的小點」
   而安靜丟掉。實測誤殺 6 張。

4. **「自己的有 1 張就丟掉內建那 8 張」。** 這條規則本來是要避免糊掉的內建「7」
   搶走清楚的「2」，但在樣板還很少的時候是致命的：他的 suit_D 只有 1 張可用，
   一丟掉內建的 8 張，方塊就只能拿 1 張去跟 3 張紅心比 —— **這就是方塊被認成
   愛心的直接原因**。

留一法實測（查詢與樣板都用他實機的圖）：**88.7% → 96.8%，花色錯誤全部消失。**
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import cv2  # noqa: E402

from src import cardparts as cp  # noqa: E402


def blob(size, fill=0.6, offset=(0, 0)):
    """畫一個佔滿度夠高的方塊，當成「正常的樣板」。"""
    w, h = size
    img = np.zeros((h, w), np.uint8)
    bw, bh = int(w * fill), int(h * fill)
    x = max(0, min(w - bw, (w - bw) // 2 + offset[0]))
    y = max(0, min(h - bh, (h - bh) // 2 + offset[1]))
    img[y:y + bh, x:x + bw] = 255
    return img


def speck(size):
    """只切到一角的小點 —— 佔滿度遠低於門檻。"""
    w, h = size
    img = np.zeros((h, w), np.uint8)
    img[h // 2: h // 2 + 3, w // 2: w // 2 + 3] = 255
    return img


class PartsDirTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.parts = os.path.join(self._tmp.name, "parts")
        self.bundled = os.path.join(self._tmp.name, "defaults")
        os.makedirs(self.parts)
        os.makedirs(self.bundled)

    def write(self, folder, name, img):
        cv2.imwrite(os.path.join(folder, name), img)

    def make_bundled(self, kind, key, count):
        """做出 count 張內建樣板，並照 install_default_parts 的方式複製進 parts/。"""
        import shutil
        size = cp.PART_SIZES[kind]
        for i in range(1, count + 1):
            name = f"{kind}_{key}_{i}.png"
            self.write(self.bundled, name, blob(size, 0.5, (i % 2, 0)))
            shutil.copy2(os.path.join(self.bundled, name),
                         os.path.join(self.parts, name))

    def save_own(self, kind, key, img=None):
        """走正式的儲存路徑（next_part_path + 刪 stale + 寫檔）。"""
        path, stale = cp.next_part_path(self.parts, self.bundled, kind, key)
        if path is None:
            return None
        for old in stale:
            os.remove(old)
        self.write(self.parts, os.path.basename(path),
                   img if img is not None else blob(cp.PART_SIZES[kind], 0.6))
        return os.path.basename(path)

    def own_files(self, kind, key):
        fps = cp.bundled_fingerprints(self.bundled)
        prefix = f"{kind}_{key}_"
        return sorted(f for f in os.listdir(self.parts)
                      if f.startswith(prefix)
                      and not cp.is_bundled_copy(self.parts, f, fps))


class TestSavesAccumulate(PartsDirTestCase):
    """核心回歸測試：連續儲存必須越存越多。"""

    def test_repeated_saves_keep_piling_up(self):
        self.make_bundled("suit", "D", 8)
        saved = [self.save_own("suit", "D") for _ in range(5)]
        self.assertEqual(len(self.own_files("suit", "D")), 5,
                         f"存了 5 次卻只剩 {self.own_files('suit', 'D')}")
        self.assertEqual(len(set(saved)), 5, f"檔名撞在一起了：{saved}")

    def test_new_files_never_reuse_a_bundled_filename(self):
        """新檔的編號從「內建最大編號 + 1」起跳，撞不到內建的名字。"""
        self.make_bundled("suit", "D", 8)
        bundled_names = {f"suit_D_{i}.png" for i in range(1, 9)}
        for _ in range(4):
            name = self.save_own("suit", "D")
            self.assertNotIn(name, bundled_names, f"{name} 撞到內建檔名了")

    def test_saving_never_deletes_anything(self):
        """存檔這條路上不能有任何刪檔動作。

        「存自己的就順手刪內建的」原本是為了繞開舊的上限 bug，但那讓
        `load_part_templates` 在自己的樣板還不夠時失去墊背 ——
        方塊只剩 1 張樣板去跟紅心比。內建的要清，得使用者自己按那顆按鈕。
        """
        self.make_bundled("suit", "D", 8)
        before = sorted(os.listdir(self.parts))
        _path, stale = cp.next_part_path(self.parts, self.bundled, "suit", "D")
        self.assertEqual(stale, [], "next_part_path 不該再回報要刪的檔案")
        self.save_own("suit", "D")
        after = sorted(os.listdir(self.parts))
        self.assertTrue(set(before).issubset(set(after)), "有檔案被儲存流程刪掉了")

    def test_own_files_are_never_deleted_by_a_later_save(self):
        self.make_bundled("rank", "7", 4)
        first = self.save_own("rank", "7")
        for _ in range(3):
            self.save_own("rank", "7")
        self.assertTrue(os.path.exists(os.path.join(self.parts, first)),
                        f"{first} 被後來的儲存刪掉了")

    def test_cap_counts_only_your_own_templates(self):
        self.make_bundled("suit", "S", 8)
        for _ in range(cp.MAX_OWN_PER_LABEL):
            self.assertIsNotNone(self.save_own("suit", "S"))
        self.assertIsNone(self.save_own("suit", "S"), "超過上限應該回 None")
        self.assertEqual(len(self.own_files("suit", "S")), cp.MAX_OWN_PER_LABEL)

    def test_gui_does_not_override_the_cap(self):
        """上限只能有一份，在 cardparts.MAX_OWN_PER_LABEL。

        2026-08-22 檢視時發現：cardparts 這邊早就因為「rank_5 存滿 8 張、
        再抓也存不進去」把上限從 8 放寬到 16，但 gui.py 的 `_write_part`
        還自己傳了一個寫死的 8 進來 —— 使用者實際走的那條路上限根本沒變，
        那次修正等於從來沒有生效。這條測試直接看 gui.py 的原始碼，
        擋住「同一個數字存在兩個地方」這種靜默還原。
        """
        gui_path = os.path.join(os.path.dirname(ROOT), "gui.py")
        if not os.path.exists(gui_path):
            self.skipTest("找不到 gui.py")
        with open(gui_path, encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn("_MAX_PER_LABEL", source,
                         "gui.py 又出現自己的樣板上限了 —— 上限只能住在 "
                         "cardparts.MAX_OWN_PER_LABEL")
        self.assertIn("next_part_path(PARTS_DIR, default_parts_dir(), kind, key)",
                      source, "gui 應該用 next_part_path 的預設上限")


class TestBundledIdentityIsContent(PartsDirTestCase):
    def test_own_file_sitting_on_a_bundled_filename_is_not_bundled(self):
        """這是整組 bug 的根源。"""
        size = cp.PART_SIZES["suit"]
        self.write(self.bundled, "suit_D_1.png", blob(size, 0.5))
        self.write(self.parts, "suit_D_1.png", blob(size, 0.7))   # 內容不同 = 他自己的
        fps = cp.bundled_fingerprints(self.bundled)
        self.assertFalse(cp.is_bundled_copy(self.parts, "suit_D_1.png", fps))

    def test_an_untouched_copy_is_bundled(self):
        import shutil
        size = cp.PART_SIZES["suit"]
        self.write(self.bundled, "suit_D_1.png", blob(size, 0.5))
        shutil.copy2(os.path.join(self.bundled, "suit_D_1.png"),
                     os.path.join(self.parts, "suit_D_1.png"))
        fps = cp.bundled_fingerprints(self.bundled)
        self.assertTrue(cp.is_bundled_copy(self.parts, "suit_D_1.png", fps))

    def test_own_template_on_a_bundled_name_is_actually_used(self):
        """比對時也不能把它當成內建糊圖丟掉 —— 否則存了等於沒存。"""
        self.make_bundled("suit", "D", 8)
        self.make_bundled("suit", "H", 8)
        # 手動把他自己的樣板寫進一個內建檔名（重現舊版留下的狀態）
        self.write(self.parts, "suit_D_1.png", blob(cp.PART_SIZES["suit"], 0.75))
        loaded = cp.load_part_templates(self.parts, self.bundled)
        self.assertIn("D", loaded["suit"])
        # _1 被他自己的蓋過去了，所以是 7 張內建 + 1 張自己的。
        # 重點是那 1 張**有被算進去**（舊版會整個丟掉，只剩 7 張）。
        self.assertEqual(len(loaded["suit"]["D"]), 8)

    def test_clearing_bundled_never_touches_your_own(self):
        self.make_bundled("suit", "C", 8)
        mine = self.save_own("suit", "C")
        self.write(self.parts, "suit_C_1.png", blob(cp.PART_SIZES["suit"], 0.75))
        present = cp.bundled_copies_present(self.parts, self.bundled)
        self.assertNotIn(mine, present)
        self.assertNotIn("suit_C_1.png", present)


class TestKeepBundledUntilEnoughOwn(PartsDirTestCase):
    """「有 1 張自己的就丟掉內建 8 張」是方塊被認成愛心的直接原因。"""

    def test_one_own_template_still_uses_the_bundled_ones(self):
        self.make_bundled("suit", "D", 8)
        self.make_bundled("suit", "H", 8)
        self.save_own("suit", "D")
        loaded = cp.load_part_templates(self.parts, self.bundled)
        self.assertGreater(len(loaded["suit"]["D"]), 1,
                           "只有 1 張自己的就丟掉內建 = 方塊只能拿 1 張去比")

    def test_enough_own_templates_drop_the_bundled_ones(self):
        for suit in ("H", "D"):
            self.make_bundled("suit", suit, 8)
            for _ in range(cp.MIN_OWN_TO_DROP_BUNDLED):
                self.save_own("suit", suit)
        loaded = cp.load_part_templates(self.parts, self.bundled)
        for suit in ("H", "D"):
            self.assertEqual(len(loaded["suit"][suit]), cp.MIN_OWN_TO_DROP_BUNDLED)

    def test_the_whole_colour_group_decides_together(self):
        """H 有 3 張、D 只有 1 張時，不可以變成「3 張清楚的 H vs 1 張清楚的 D」。

        那是一場不公平的比賽，而且偏向錯的那一邊。
        """
        self.make_bundled("suit", "H", 8)
        self.make_bundled("suit", "D", 8)
        for _ in range(cp.MIN_OWN_TO_DROP_BUNDLED):
            self.save_own("suit", "H")
        self.save_own("suit", "D")
        loaded = cp.load_part_templates(self.parts, self.bundled)
        self.assertGreater(len(loaded["suit"]["H"]), cp.MIN_OWN_TO_DROP_BUNDLED,
                           "D 還不夠的時候，H 也要繼續用內建的，兩邊才公平")
        self.assertGreater(len(loaded["suit"]["D"]), 1)

    def test_spades_and_clubs_are_a_separate_group_from_the_reds(self):
        for suit in ("S", "C"):
            self.make_bundled("suit", suit, 8)
            for _ in range(cp.MIN_OWN_TO_DROP_BUNDLED):
                self.save_own("suit", suit)
        self.make_bundled("suit", "H", 8)
        self.make_bundled("suit", "D", 8)
        self.save_own("suit", "H")
        loaded = cp.load_part_templates(self.parts, self.bundled)
        # 黑色那組滿了就自己丟內建，不受紅色那組還沒滿的影響
        self.assertEqual(len(loaded["suit"]["S"]), cp.MIN_OWN_TO_DROP_BUNDLED)
        self.assertGreater(len(loaded["suit"]["H"]), 1)


class TestUsabilityCheckedBeforeCentring(PartsDirTestCase):
    def test_a_glyph_touching_the_edge_is_not_thrown_away(self):
        """centre_mask 會把貼邊的圖案切掉一角，所以要先判斷再置中。"""
        size = cp.PART_SIZES["rank"]
        w, h = size
        img = np.zeros((h, w), np.uint8)
        img[0:int(h * 0.8), 0:int(w * 0.8)] = 255      # 貼在左上角
        self.write(self.parts, "rank_7_9.png", img)
        loaded = cp.load_part_templates(self.parts, self.bundled)
        self.assertIn("7", loaded["rank"], "貼邊的好樣板被誤殺了")

    def test_inventory_junk_count_matches_unusable_parts(self):
        """畫面說「有 3 個壞的」、按鈕說「沒有壞的」是最糟的組合。"""
        size = cp.PART_SIZES["suit"]
        self.write(self.parts, "suit_S_9.png", speck(size))
        self.write(self.parts, "suit_S_10.png", blob(size, 0.6))
        inventory = cp.part_inventory(self.parts, self.bundled)
        listed = [n for n, _cov in cp.unusable_parts(self.parts)]
        self.assertEqual(inventory["suit"]["S"]["junk"], len(listed))
        self.assertEqual(listed, ["suit_S_9.png"])

    def test_specks_are_still_rejected(self):
        self.write(self.parts, "suit_D_9.png", speck(cp.PART_SIZES["suit"]))
        loaded = cp.load_part_templates(self.parts, self.bundled)
        self.assertNotIn("D", loaded["suit"])


class TestRankSpeckRemoval(PartsDirTestCase):
    def test_a_stray_suit_fragment_is_dropped_from_a_rank_crop(self):
        w, h = cp.PART_SIZES["rank"]
        img = np.zeros((h, w), np.uint8)
        img[2:h - 8, 4:w - 4] = 255            # 主體
        img[h - 3:h, w // 2:w // 2 + 2] = 255  # 底下那顆花色殘渣
        cleaned = cp.clean_part_mask(img, "rank")
        self.assertEqual(int(cleaned[h - 2, w // 2]), 0, "雜點沒有被清掉")
        self.assertGreater(int(cleaned[h // 2, w // 2]), 0, "主體被清掉了")

    def test_ten_keeps_both_digits(self):
        """「10」是兩塊，所以不能只留最大的那一塊。"""
        w, h = cp.PART_SIZES["rank"]
        img = np.zeros((h, w), np.uint8)
        img[4:h - 4, 3:8] = 255                # 1
        img[4:h - 4, 11:w - 3] = 255           # 0
        cleaned = cp.clean_part_mask(img, "rank")
        self.assertGreater(int(cleaned[h // 2, 5]), 0, "「1」被當成雜點刪掉了")
        self.assertGreater(int(cleaned[h // 2, w - 6]), 0, "「0」不見了")

    def test_suits_are_left_alone(self):
        """梅花本身就是好幾瓣，去雜點會把真正的花色瓣切掉（實測 suit_H 被誤判）。"""
        w, h = cp.PART_SIZES["suit"]
        img = np.zeros((h, w), np.uint8)
        img[2:8, 2:8] = 255
        img[h - 6:h - 2, w - 6:w - 2] = 255
        self.assertTrue(np.array_equal(cp.clean_part_mask(img, "suit"), img))


if __name__ == "__main__":
    unittest.main()
