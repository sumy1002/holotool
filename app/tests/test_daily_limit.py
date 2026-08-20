"""每日上限：「已達最高獲得金額，遊戲結束」畫面的處理。

遊戲規則：金額翻倍超過上限就強制結束，每天可以達成兩次。
第 1 次 → 畫面停在結算頁，按「再玩一次」還能玩第二輪。
第 2 次 → 遊戲直接關掉牌桌，該收工了。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import DEFAULT_CONFIG  # noqa: E402
from src.state_machine import FrameInfo  # noqa: E402


def _cfg(**over) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg.update(over)
    return cfg


def _frame(**kwargs) -> FrameInfo:
    data = dict(
        on_table=False,
        table_marker_score=0.5,
        slot_cards=[None] * 5,
        highlow_card=None,
        is_draw=False,
        is_congrats=False,
        is_challenge=False,
        is_fail=False,
        is_poker_fail=False,
        is_max_win=False,
        ui_scores={"table": 0.5, "draw": 0.1, "congrats": 0.1, "challenge": 0.1,
                   "fail": 0.1, "poker_fail": 0.1, "max_win": 0.1},
    )
    data.update(kwargs)
    return FrameInfo(**data)


class FakeStats:
    """只記在記憶體裡的統計，行為與 DailyStats 相同的那幾個方法。"""

    def __init__(self, start: int = 0):
        self.data = {"max_win_count": start}
        self.events = []

    def bump(self, field, delta=1):
        self.data[field] = self.data.get(field, 0) + delta

    def record_event(self, kind, detail):
        self.events.append((kind, detail))

    def record_card(self, label):
        pass


class DailyLimitTestCase(unittest.TestCase):
    def _make_bot(self, already_done: int = 0, **cfg_over):
        with patch("src.bot.GameCapture") as Cap, patch("src.bot.MouseController"), patch(
            "src.bot.DailyStats"
        ):
            from src.bot import Bot

            bot = Bot(_cfg(**cfg_over), dry_run=True)
            bot.capture = Cap.return_value
            bot.capture.is_window_valid.return_value = True
            bot.stats = FakeStats(already_done)
            bot.ui_templates["table_marker"] = np.zeros((4, 4, 3), np.uint8)
            bot.ui_templates["max_win"] = np.zeros((4, 4, 3), np.uint8)
            bot.running = True
            return bot

    def _watch(self, bot):
        keys = []
        original = bot._click_point

        def wrapped(key, *a, **kw):
            keys.append(key)
            return original(key, *a, **kw)

        bot._click_point = wrapped
        return keys

    def _run(self, bot, frame, ticks=1):
        with patch("src.bot.detect_frame", return_value=frame):
            for _ in range(ticks):
                bot._tick()


class TestFirstTime(DailyLimitTestCase):
    def test_clicks_play_again_and_keeps_running(self):
        bot = self._make_bot(already_done=0)
        keys = self._watch(bot)
        self._run(bot, _frame(is_max_win=True))
        self.assertIn("max_win_retry", keys, "第一次達到上限應該按「再玩一次」")
        self.assertNotIn("retry_button", keys, "按錯成失敗畫面的那顆「再一次」")
        self.assertTrue(bot.running, "第一次就停掉了，第二輪不用玩了嗎")
        self.assertEqual(bot.stats.data["max_win_count"], 1)

    def test_counts_the_screen_only_once_even_across_many_ticks(self):
        bot = self._make_bot(already_done=0)
        self._run(bot, _frame(is_max_win=True), ticks=10)
        self.assertEqual(
            bot.stats.data["max_win_count"], 1,
            "同一個上限畫面被重複計數了",
        )


class TestSecondTime(DailyLimitTestCase):
    def test_stops_without_clicking(self):
        bot = self._make_bot(already_done=1)
        keys = self._watch(bot)
        self._run(bot, _frame(is_max_win=True))
        self.assertEqual(bot.stats.data["max_win_count"], 2)
        self.assertFalse(bot.running, "第二次達到上限應該自動停止")
        self.assertNotIn("max_win_retry", keys, "已經沒額度了還去按「再玩一次」")

    def test_stops_immediately_when_the_quota_was_already_used_up(self):
        """程式重開之後，當天額度若已用完，一看到上限畫面就要停。"""
        bot = self._make_bot(already_done=2)
        keys = self._watch(bot)
        self._run(bot, _frame(is_max_win=True))
        self.assertFalse(bot.running)
        self.assertEqual(keys, [])

    def test_records_the_stop_reason(self):
        bot = self._make_bot(already_done=1)
        self._run(bot, _frame(is_max_win=True))
        kinds = [k for k, _ in bot.stats.events]
        self.assertIn("auto_stop_daily_limit", kinds)


class TestPriorityAndSafety(DailyLimitTestCase):
    def test_max_win_wins_over_other_markers_on_the_same_frame(self):
        """上限畫面同時有結算面板，其他標記擦邊命中時不能被當成一般失敗。"""
        bot = self._make_bot(already_done=0)
        keys = self._watch(bot)
        self._run(bot, _frame(is_max_win=True, is_fail=True, is_poker_fail=True))
        self.assertIn("max_win_retry", keys)
        self.assertNotIn("retry_button", keys)

    def test_max_win_counts_as_a_dialog_so_the_table_timer_resets(self):
        """這個畫面也會把牌桌 logo 蓋掉，不能被誤判成「離開牌桌」。"""
        bot = self._make_bot(already_done=0)
        bot._logo_ever_matched = True
        self._run(bot, _frame(is_max_win=True), ticks=40)
        self.assertEqual(bot._missing_table_ticks, 0)

    def test_without_the_template_nothing_happens(self):
        """還沒校準這個標記時，is_max_win 永遠是 False，不會亂停。"""
        bot = self._make_bot(already_done=0)
        keys = self._watch(bot)
        self._run(bot, _frame(is_max_win=False), ticks=3)
        self.assertNotIn("max_win_retry", keys)
        self.assertTrue(bot.running)
        self.assertEqual(bot.stats.data["max_win_count"], 0)


class TestStatsPersistence(unittest.TestCase):
    def test_count_survives_a_restart(self):
        from src.stats import DailyStats

        with tempfile.TemporaryDirectory() as tmp:
            with patch("src.stats.DATA_DIR", tmp):
                first = DailyStats(day="2026-08-20")
                first.path = os.path.join(tmp, "stats_2026-08-20.json")
                first.bump("max_win_count")
                self.assertEqual(first.data["max_win_count"], 1)

                second = DailyStats(day="2026-08-20")
                second.path = os.path.join(tmp, "stats_2026-08-20.json")
                second.data = second._load()
                self.assertEqual(second.data.get("max_win_count"), 1,
                                 "重開程式後次數沒有被記住")


if __name__ == "__main__":
    unittest.main()
