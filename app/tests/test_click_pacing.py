"""點擊節奏：漏收要會重試、動畫期間不能亂點。

實機 log 顯示的兩個症狀：

1. 翻倍對話框停在畫面上好幾分鐘，bot 完全不動 —— 因為舊版用一個
   「這個對話框處理過了」的旗標，點過就再也不點，遊戲漏收就永遠卡住。
2. 按完「大」一秒後又跑去點「投注並開始」—— 遊戲還在跑動畫，畫面認不出來，
   舊版就直接當成待機亂點。
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
        ui_scores={"table": 0.9, "draw": 0.1, "congrats": 0.1,
                   "challenge": 0.1, "fail": 0.1, "poker_fail": 0.1},
    )
    data.update(kwargs)
    return FrameInfo(**data)


class PacingTestCase(unittest.TestCase):
    def _make_bot(self, **cfg_over):
        with patch("src.bot.GameCapture") as Cap, patch("src.bot.MouseController"), patch(
            "src.bot.DailyStats"
        ):
            from src.bot import Bot

            bot = Bot(_cfg(**cfg_over), dry_run=True)
            bot.capture = Cap.return_value
            bot.capture.is_window_valid.return_value = True
            bot.ui_templates["table_marker"] = np.zeros((4, 4, 3), np.uint8)
            bot.running = True
            return bot

    def _watch(self, bot) -> list:
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


class TestStuckDialogIsRetried(PacingTestCase):
    def test_challenge_dialog_is_clicked_again_when_game_misses_it(self):
        """翻倍對話框一直在 = 遊戲沒收到，必須再點，不能永遠不動。"""
        bot = self._make_bot(action_cooldown_sec=0.05, action_retry_sec=0.15)
        keys = self._watch(bot)
        frame = _frame(is_challenge=True, on_table=False)
        for _ in range(4):
            self._run(bot, frame)
            time.sleep(0.2)
        self.assertGreaterEqual(
            keys.count("challenge_button"), 3,
            f"畫面卡住卻只點了 {keys.count('challenge_button')} 次",
        )

    def test_congrats_screen_is_clicked_again_when_game_misses_it(self):
        bot = self._make_bot(action_cooldown_sec=0.05, action_retry_sec=0.15)
        keys = self._watch(bot)
        frame = _frame(is_congrats=True)
        for _ in range(3):
            self._run(bot, frame)
            time.sleep(0.2)
        self.assertGreaterEqual(keys.count("click_continue"), 2)

    def test_does_not_spam_within_the_cooldown(self):
        """冷卻時間內不能連點 —— 遊戲需要時間反應。"""
        bot = self._make_bot(action_cooldown_sec=5.0, action_retry_sec=5.0)
        keys = self._watch(bot)
        self._run(bot, _frame(is_challenge=True, on_table=False), ticks=10)
        self.assertEqual(keys.count("challenge_button"), 1)


class TestNoStrayClicksDuringAnimation(PacingTestCase):
    def test_idle_does_not_fire_right_after_an_action(self):
        """按完「大」之後遊戲在跑動畫，這時候不能去點「投注並開始」。"""
        bot = self._make_bot()
        keys = self._watch(bot)
        # 先讓它做一個動作（比大小按下去）
        bot._acted_state = "highlow:6C"
        bot._acted_at = time.time()
        # 接著幾個 tick 都是「認不出來的畫面」
        self._run(bot, _frame(on_table=True), ticks=5)
        self.assertNotIn(
            "start_round", keys,
            "動作後的動畫期間仍然誤點了「投注並開始」",
        )

    def test_idle_requires_a_sustained_unrecognised_screen(self):
        """畫面只是短暫認不出來時不動作；持續夠久才點投注。"""
        bot = self._make_bot(idle_confirm_sec=0.3, action_cooldown_sec=0.0)
        keys = self._watch(bot)
        self._run(bot, _frame(), ticks=2)
        self.assertNotIn("start_round", keys, "才第一眼認不出來就急著點")
        time.sleep(0.35)
        self._run(bot, _frame())
        self.assertIn("start_round", keys, "持續認不出來卻一直不點")

    def test_recognised_screen_resets_the_idle_timer(self):
        bot = self._make_bot(idle_confirm_sec=0.3, action_cooldown_sec=0.0)
        keys = self._watch(bot)
        self._run(bot, _frame())          # 開始計時
        time.sleep(0.35)
        self._run(bot, _frame(is_challenge=True, on_table=False))  # 認出畫面 → 歸零
        self._run(bot, _frame())          # 重新開始計時
        self.assertNotIn("start_round", keys, "待機計時沒有因為認出畫面而歸零")


class TestHighLowRetry(PacingTestCase):
    def test_same_card_still_showing_means_the_click_was_missed(self):
        bot = self._make_bot(action_cooldown_sec=0.05, action_retry_sec=0.15)
        bot.dry_run = True
        keys = self._watch(bot)
        frame = _frame(highlow_card=("6C", 0.95))
        # 前兩次讀到同一張牌才會動作（畫面穩定確認）
        for _ in range(5):
            self._run(bot, frame)
            time.sleep(0.2)
        presses = keys.count("high_button") + keys.count("low_button")
        self.assertGreaterEqual(presses, 2, f"比大小卡住卻只按了 {presses} 次")


class TestMouseHold(unittest.TestCase):
    def test_click_holds_the_button_down_long_enough(self):
        """按下與放開之間要有停頓，否則每幀輪詢的遊戲會整個跳過。"""
        from src.controller import MouseController

        events = []
        with patch("src.controller.pyautogui") as pg:
            pg.moveTo.side_effect = lambda *a, **k: events.append(("move", time.time()))
            pg.mouseDown.side_effect = lambda *a, **k: events.append(("down", time.time()))
            pg.mouseUp.side_effect = lambda *a, **k: events.append(("up", time.time()))

            class FakeCapture:
                def ratio_point_to_absolute(self, point):
                    return 100, 200

            MouseController(FakeCapture(), click_delay_range=(0.0, 0.0),
                            hold_range=(0.06, 0.12), move_duration=0.0).click_point(
                {"x": 0.5, "y": 0.5})

        kinds = [e[0] for e in events]
        self.assertEqual(kinds, ["move", "down", "up"])
        held = events[2][1] - events[1][1]
        self.assertGreaterEqual(held, 0.05, f"按住時間只有 {held * 1000:.0f} ms，太短")


if __name__ == "__main__":
    unittest.main()
