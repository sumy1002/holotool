"""補算未記錄的數值：可以重複按、而且只增不減。

這個功能的價值全部押在兩件事上：

1. **重複按不會重複計算。** 使用者一定會按第二次確認，如果每按一次就把同一批
   牌再加一遍，機率模型會被自己的按鈕搞歪，而且完全沒有徵兆。
2. **只認「狀態變了才印一次」的 log 行。** `偵測中 ... 比大小=6C` 每個 tick
   都印一次，一張牌在畫面上停三秒就是七八行 —— 算進去等於憑空多出七張牌。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import reconcile as rec  # noqa: E402
from src import stats as stats_mod  # noqa: E402

DAY = "2026-08-21"

HAND_LINE = ("[2026-08-21 10:00:01] [選牌階段] 手牌=('JS', '6D', '9C', '4H', 'QS') "
             "目前牌型=無對子 | 點擊保留=[1, 2]（沒點的會被替換）| "
             "換牌後門票(>=兩對)機率=12.3% 預期牌型=一對")
HIGHLOW_LINE = ("[2026-08-21 10:00:05] [比大小階段] 目前牌=10S 建議=LOW 預估勝率=61.9% "
                "(連續第 1 次)。同點數會重抽，不計勝負。")
TICK_LINE = ("[2026-08-21 10:00:06] 偵測中 牌桌=90% 選牌=10% 過關=10% 翻倍=10% "
             "失敗=10% 湊牌失敗=10% 上限=無 手牌=0/5 比大小=10S")


class ReconcileTestCase(unittest.TestCase):
    """把 data/ 與 logs/ 都導到暫存資料夾，絕對不碰使用者的真實統計檔。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.data = os.path.join(self.root, "data")
        self.logs = os.path.join(self.root, "logs")
        os.makedirs(self.data)
        os.makedirs(self.logs)
        self._patches = [
            patch.object(stats_mod, "DATA_DIR", self.data),
            patch.object(rec, "log_dir", lambda: self.logs),
            patch.object(rec, "data_dir", lambda: self.data),
            patch.object(rec, "project_root", lambda: self.root),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(self._tmp.cleanup)
        for p in self._patches:
            self.addCleanup(p.stop)

    def write_log(self, *lines: str, day: str = DAY) -> None:
        with open(os.path.join(self.logs, f"bot_{day}.log"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def counts(self, day: str = DAY) -> dict:
        path = os.path.join(self.data, f"stats_{day}.json")
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in (data.get("card_counts") or {}).items() if v}


class TestLogParsing(ReconcileTestCase):
    def test_hand_and_highlow_lines_are_counted(self):
        labels, total = rec.parse_log_cards("\n".join([HAND_LINE, HIGHLOW_LINE]))
        self.assertEqual(sorted(labels), sorted(["JS", "6D", "9C", "4H", "QS", "10S"]))
        self.assertEqual(total, 2)

    def test_per_tick_detection_lines_are_ignored(self):
        """`偵測中 ... 比大小=10S` 每個 tick 印一次，算進去會讓同一張牌暴增。"""
        labels, _ = rec.parse_log_cards("\n".join([TICK_LINE] * 8))
        self.assertEqual(labels, [])

    def test_start_line_skips_what_was_already_counted(self):
        text = "\n".join([HAND_LINE, HIGHLOW_LINE])
        labels, total = rec.parse_log_cards(text, start_line=1)
        self.assertEqual(labels, ["10S"])
        self.assertEqual(total, 2)


class TestReconcileDay(ReconcileTestCase):
    def test_log_cards_are_added_once_only(self):
        self.write_log(HAND_LINE, HIGHLOW_LINE)
        first = rec.reconcile_day(DAY)
        self.assertEqual(first.total_added, 6)
        self.assertEqual(self.counts(), {"JS": 1, "6D": 1, "9C": 1, "4H": 1, "QS": 1, "10S": 1})

        second = rec.reconcile_day(DAY)
        self.assertEqual(second.total_added, 0)
        self.assertIn("無其餘資料", second.summary())
        self.assertEqual(self.counts(), {"JS": 1, "6D": 1, "9C": 1, "4H": 1, "QS": 1, "10S": 1})

    def test_new_lines_appended_later_are_picked_up(self):
        self.write_log(HAND_LINE)
        rec.reconcile_day(DAY)
        self.write_log(HAND_LINE, HIGHLOW_LINE)
        again = rec.reconcile_day(DAY)
        self.assertEqual(again.total_added, 1)
        self.assertEqual(self.counts()["10S"], 1)
        self.assertEqual(self.counts()["JS"], 1)   # 舊的那一行沒有被再算一次

    def test_truncated_log_does_not_recount_from_the_top(self):
        """log 被清掉之後行數變少。從頭重掃會把整批舊資料再加一次。"""
        self.write_log(HAND_LINE, HIGHLOW_LINE)
        rec.reconcile_day(DAY)
        self.write_log(HAND_LINE)          # 只剩一行了
        after = rec.reconcile_day(DAY)
        self.assertEqual(after.total_added, 0)
        self.assertEqual(self.counts()["JS"], 1)

    def test_existing_counts_are_never_reduced(self):
        stats = stats_mod.DailyStats(DAY)
        stats.data["card_counts"]["AS"] = 5
        stats.save()
        self.write_log(HAND_LINE)
        rec.reconcile_day(DAY)
        self.assertEqual(self.counts()["AS"], 5)

    def test_days_without_data_do_not_create_empty_files(self):
        """往回掃七天時，沒玩過的日子不該生出空的 stats 檔 ——
        那會把 _load_recent_history 的天數額度吃掉，歷史反而變短。"""
        self.write_log(HAND_LINE)
        rec.reconcile(days=7, today=DAY)
        self.assertEqual(sorted(os.listdir(self.data)), [f"stats_{DAY}.json"])

    def test_past_day_file_records_its_own_date(self):
        self.write_log(HAND_LINE, day="2026-08-19")
        rec.reconcile_day("2026-08-19")
        with open(os.path.join(self.data, "stats_2026-08-19.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["date"], "2026-08-19")


class TestMergeOtherInstall(ReconcileTestCase):
    def _write_external(self, counts: dict, folder: str = "exe") -> str:
        path = os.path.join(self.root, folder, "data")
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, f"stats_{DAY}.json"), "w", encoding="utf-8") as f:
            json.dump({"date": DAY, "card_counts": counts}, f)
        return path

    def test_extra_data_dirs_are_merged_once(self):
        folder = self._write_external({"AS": 2, "3H": 1})
        cfg = {"extra_data_dirs": [folder]}
        first = rec.reconcile_day(DAY, cfg)
        self.assertEqual(first.total_added, 3)
        self.assertEqual(self.counts(), {"AS": 2, "3H": 1})

        second = rec.reconcile_day(DAY, cfg)
        self.assertEqual(second.total_added, 0)
        self.assertEqual(self.counts(), {"AS": 2, "3H": 1})

    def test_growth_on_the_other_side_is_picked_up(self):
        folder = self._write_external({"AS": 2})
        cfg = {"extra_data_dirs": [folder]}
        rec.reconcile_day(DAY, cfg)
        self._write_external({"AS": 5})          # exe 版又玩了三張 A
        after = rec.reconcile_day(DAY, cfg)
        self.assertEqual(after.total_added, 3)
        self.assertEqual(self.counts()["AS"], 5)

    def test_shrinking_on_the_other_side_adds_nothing(self):
        folder = self._write_external({"AS": 5})
        cfg = {"extra_data_dirs": [folder]}
        rec.reconcile_day(DAY, cfg)
        self._write_external({"AS": 1})          # 對面的統計被清過
        after = rec.reconcile_day(DAY, cfg)
        self.assertEqual(after.total_added, 0)
        self.assertEqual(self.counts()["AS"], 5)   # 只增不減

    def test_own_data_dir_is_never_treated_as_external(self):
        self.assertNotIn(os.path.abspath(self.data),
                         rec.candidate_data_dirs({"extra_data_dirs": [self.data]}))

    def test_dist_folders_are_found_automatically(self):
        exe_data = os.path.join(self.root, "dist", "HoloTool", "app", "data")
        os.makedirs(exe_data)
        self.assertIn(os.path.abspath(exe_data), rec.candidate_data_dirs())


if __name__ == "__main__":
    unittest.main()
