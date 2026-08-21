"""存樣板時的名額計算。

實機症狀：使用者反覆按「全部儲存」，點數存得下去、**花色一張都沒存進去**，
畫面完全不報錯。結果花色永遠只能靠內建那組糊圖，怎麼調門檻都認不準。

原因：內建樣板每個花色剛好 8 張（suit_S_1~8、suit_C_1~8…），而「同一個標籤
最多留 8 張」的上限把內建的也算進去 —— 一開始就是滿的。點數只有 3~4 張內建，
所以點數存得進去，花色存不進去，症狀才會那麼奇怪。

## 2026-08-21 更新：不再刪內建檔

當初的修法是「存自己的就順手刪掉同標籤的內建檔」。那個動作後來自己變成 bug 的
一部分：內建檔被刪掉後編號 1~8 就空出來，下一次儲存又寫進 `suit_D_1.png`，
一個「檔名像內建、內容是使用者自己的」檔案 —— 然後**再下一次儲存又把它當成內建
檔刪掉**。使用者按了幾十次「全部儲存」，花色永遠只有一兩張。

而且 `load_part_templates` 現在要等自己的樣板累積到 `MIN_OWN_TO_DROP_BUNDLED`
張才不用內建的，在那之前**需要內建那批當墊背**。第一次儲存就刪掉等於抽掉墊背。

所以現在：**儲存這條路上完全不刪檔**（`stale` 永遠是空的），
新檔編號從「內建最大編號 + 1」起跳，內建的要清得由使用者明確按按鈕。
詳細的實測數字見 `test_part_accumulation.py`。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.cardparts import next_part_path  # noqa: E402


class TestNextPartPath(unittest.TestCase):
    def _dirs(self, stack, bundled_names):
        parts = os.path.join(stack, "parts")
        bundled = os.path.join(stack, "defaults")
        os.makedirs(parts)
        os.makedirs(bundled)
        for name in bundled_names:
            for d in (parts, bundled):
                with open(os.path.join(d, name), "wb") as f:
                    f.write(b"x")
        return parts, bundled

    def test_bundled_templates_do_not_use_up_the_quota(self):
        with tempfile.TemporaryDirectory() as tmp:
            parts, bundled = self._dirs(tmp, [f"suit_S_{i}.png" for i in range(1, 9)])
            path, stale = next_part_path(parts, bundled, "suit", "S", max_own=8)
            self.assertIsNotNone(path, "內建樣板佔滿名額，使用者永遠存不進自己的花色")
            self.assertEqual(stale, [], "儲存流程不該再刪任何檔案（見模組說明）")
            # 新檔的編號要跳過內建佔用的 1~8，否則下次載入會把它誤判成內建的
            self.assertEqual(os.path.basename(path), "suit_S_9.png")

    def test_own_templates_do_use_up_the_quota(self):
        with tempfile.TemporaryDirectory() as tmp:
            parts, bundled = self._dirs(tmp, [])
            for i in range(1, 9):
                with open(os.path.join(parts, f"suit_S_{i}.png"), "wb") as f:
                    f.write(b"x")
            path, stale = next_part_path(parts, bundled, "suit", "S", max_own=8)
            self.assertIsNone(path)
            self.assertEqual(stale, [])

    def test_filename_never_collides_with_an_existing_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            parts, bundled = self._dirs(tmp, ["rank_2_1.png", "rank_2_2.png"])
            with open(os.path.join(parts, "rank_2_5.png"), "wb") as f:
                f.write(b"x")
            path, _ = next_part_path(parts, bundled, "rank", "2", max_own=8)
            self.assertFalse(os.path.exists(path))
            self.assertTrue(os.path.basename(path).startswith("rank_2_"))

    def test_nothing_is_deleted_for_any_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            parts, bundled = self._dirs(tmp, ["suit_S_1.png", "suit_C_1.png"])
            before = sorted(os.listdir(parts))
            _, stale = next_part_path(parts, bundled, "suit", "S")
            self.assertEqual(stale, [])
            self.assertEqual(sorted(os.listdir(parts)), before)


if __name__ == "__main__":
    unittest.main()
