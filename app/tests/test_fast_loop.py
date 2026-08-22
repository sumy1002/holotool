"""自適應偵測節奏：有事發生時拍快一點，反應快但不犧牲任何保護。

三個要守住的性質：

1. **快拍只出現在「本來就在忙」的時候**（等結果、等第二拍確認、畫面剛換、
   剛點過按鈕），其餘時間維持基本節奏 —— 平均 CPU 幾乎不變。
2. **離桌判定量的是時間，不是拍數。** 快拍模式下 tick 比較密，直接數拍數
   會讓「25 拍 ≈ 10 秒」縮成三四秒而提早停機。改用權重（這一拍代表的牆鐘
   時間 ÷ 基本間隔）累計，無論節奏怎麼切換，門檻對應同一段真實時間。
3. **快拍下的兩次確認取樣不能靠太近。** 「連續兩拍相同才動作」在 0.4s 節奏
   下兩次取樣天然隔 0.4 秒；0.15s 快拍會讓兩次都落在同一段過渡動畫裡的機率
   變高，所以額外要求距第一次讀到至少 0.25 秒。慢拍行為完全不變 ——
   既有測試直接呼叫 _tick()（權重 1、非快拍）就是在驗這件事。
"""
from __future__ import annotations

import json
import os
import sys
import time
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
        on_table=True,
        table_marker_score=0.9,
        slot_cards=[None] * 5,
        highlow_card=None,
        is_draw=False,
        is_congrats=False,
        is_challenge=False,
        is_fail=False,
        is_poker_fail=False,
        is_max_win=False,
        ui_scores={"table": 0.9, "draw": 0.1, "congrats": 0.1, "challenge": 0.1,
                   "fail": 0.1, "poker_fail": 0.1, "max_win": 0.1},
    )
    data.update(kwargs)
    return FrameInfo(**data)


class FakeStats:
    def __init__(self):
        self.data = {}
        self.events = []

    def bump(self, field, delta=1):
        self.data[field] = self.data.get(field, 0) + delta

    def record_event(self, kind, detail):
        self.events.append((kind, detail))

    def record_card(self, label):
        pass

    def get_card_probabilities(self, **_kw):
        from src.handeval import full_deck
        deck = full_deck()
        return {c.label: 1.0 / len(deck) for c in deck}

    def get_rank_probabilities(self, **_kw):
        from src.handeval import RANKS
        return {r: 1.0 / len(RANKS) for r in RANKS}


class FastLoopTestCase(unittest.TestCase):
    def _make_bot(self, **cfg_over):
        with patch("src.bot.GameCapture") as Cap, patch("src.bot.MouseController"), patch(
            "src.bot.DailyStats"
        ):
            from src.bot import Bot

            bot = Bot(_cfg(**cfg_over), dry_run=True)
            bot.capture = Cap.return_value
            bot.capture.is_window_valid.return_value = True
            bot.stats = FakeStats()
            bot.ui_templates["table_marker"] = np.zeros((4, 4, 3), np.uint8)
            bot.running = True
            return bot

    def _run(self, bot, frame, ticks=1):
        with patch("src.bot.detect_frame", return_value=frame):
            for _ in range(ticks):
                bot._tick()


class TestFastTickChooser(FastLoopTestCase):
    def test_idle_bot_stays_on_the_base_interval(self):
        bot = self._make_bot()
        self.assertFalse(bot._wants_fast_tick(), "沒事發生就不該用快拍")

    def test_awaiting_draw_result_is_fast(self):
        bot = self._make_bot()
        bot._awaiting_draw_result = True
        self.assertTrue(bot._wants_fast_tick())

    def test_waiting_for_the_second_confirming_read_is_fast(self):
        bot = self._make_bot()
        bot._pending_slot_count = 1
        bot._pending_slot_since = time.time()
        self.assertTrue(bot._wants_fast_tick())
        bot._pending_slot_count = 0
        bot._pending_highlow_count = 1
        bot._pending_highlow_since = time.time()
        self.assertTrue(bot._wants_fast_tick())

    def test_a_stale_pending_read_does_not_pin_fast_mode(self):
        """畫面中途切走，留下 pending==1 的殘骸 —— 不可以把快拍釘死。"""
        bot = self._make_bot()
        bot._pending_slot_count = 1
        bot._pending_slot_since = time.time() - 10.0
        self.assertFalse(bot._wants_fast_tick())

    def test_recent_screen_change_is_fast_then_relaxes(self):
        bot = self._make_bot()
        bot._last_state_change_at = time.time()
        self.assertTrue(bot._wants_fast_tick())
        bot._last_state_change_at = time.time() - bot.FAST_STATE_WINDOW_SEC - 0.5
        self.assertFalse(bot._wants_fast_tick())

    def test_a_recent_action_is_fast(self):
        """剛點完按鈕的那兩秒要拍快一點，結果畫面才接得住。"""
        bot = self._make_bot()
        bot._acted_at = time.time()
        self.assertTrue(bot._wants_fast_tick())

    def test_a_tick_records_state_changes(self):
        bot = self._make_bot()
        self._run(bot, _frame(is_challenge=True))
        self.assertEqual(bot._last_seen_state, "challenge")
        first_change = bot._last_state_change_at
        self.assertGreater(first_change, 0.0)
        self._run(bot, _frame(is_challenge=True), ticks=2)
        self.assertEqual(bot._last_state_change_at, first_change,
                         "同一個畫面不該一直刷新變動時間，快拍窗會關不掉")


class TestExitTableCountsTimeNotTicks(FastLoopTestCase):
    def _leave_table_frame(self):
        return _frame(on_table=False, table_marker_score=0.2)

    def test_fast_ticks_do_not_shorten_the_exit_timer(self):
        """快拍（權重 0.375 = 0.15/0.4）下，25 個門檻單位需要 66+ 拍，
        跟慢拍的 25 拍對應到**同一段真實時間**（約 10 秒）。"""
        bot = self._make_bot()
        bot._logo_ever_matched = True
        bot._tick_weight = 0.375
        ticks_needed = int(_cfg()["exit_table_ticks"])
        self._run(bot, self._leave_table_frame(), ticks=ticks_needed + 2)
        self.assertTrue(bot.running,
                        "快拍讓離桌判定提早觸發了 —— 25 拍在快拍下不到 10 秒")
        # 補到等值的時間量就要停（25 / 0.375 ≈ 67 拍）
        self._run(bot, self._leave_table_frame(), ticks=50)
        self.assertFalse(bot.running, "累計夠久了還不停")

    def test_default_weight_keeps_the_old_tick_semantics(self):
        """權重 1（慢拍、以及所有直接呼叫 _tick 的既有測試）行為與以前相同。"""
        bot = self._make_bot()
        bot._logo_ever_matched = True
        self._run(bot, self._leave_table_frame(),
                  ticks=int(_cfg()["exit_table_ticks"]) + 2)
        self.assertFalse(bot.running)


class TestFastConfirmGap(FastLoopTestCase):
    HAND = [("2H", 0.9), ("7D", 0.9), ("9C", 0.9), ("QS", 0.9), ("KD", 0.9)]

    def _draw_frame(self):
        return _frame(is_draw=True, slot_cards=list(self.HAND))

    def test_two_fast_reads_too_close_do_not_act(self):
        bot = self._make_bot(monte_carlo_samples=50)
        bot._tick_was_fast = True
        self._run(bot, self._draw_frame(), ticks=2)
        self.assertIsNone(bot._last_slot_signature,
                          "快拍下兩次取樣只隔幾毫秒就動作了")
        # 湊滿最短間隔之後，第三拍照常確認
        time.sleep(bot.CONFIRM_MIN_GAP_SEC + 0.05)
        self._run(bot, self._draw_frame())
        self.assertEqual(bot._last_slot_signature,
                         tuple(label for label, _s in self.HAND))

    def test_slow_ticks_confirm_on_the_second_read_like_before(self):
        bot = self._make_bot(monte_carlo_samples=50)
        self._run(bot, self._draw_frame(), ticks=2)   # _tick_was_fast 預設 False
        self.assertEqual(bot._last_slot_signature,
                         tuple(label for label, _s in self.HAND),
                         "慢拍的既有行為（第二拍就確認）被改壞了")

    def test_highlow_gap_works_the_same_way(self):
        bot = self._make_bot()
        bot._tick_was_fast = True
        frame = _frame(highlow_card=("9C", 0.9))
        self._run(bot, frame, ticks=2)
        self.assertIsNone(bot._last_highlow_label,
                          "比大小在快拍下兩次取樣靠太近就動作了")
        time.sleep(bot.CONFIRM_MIN_GAP_SEC + 0.05)
        self._run(bot, frame)
        self.assertEqual(bot._last_highlow_label, "9C")


if __name__ == "__main__":
    unittest.main()
