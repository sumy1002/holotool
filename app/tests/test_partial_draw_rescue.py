"""選牌畫面「五張沒認齊」不可以讓 bot 永遠停在那裡。

## 這個測試在守什麼

`_tick` 原本寫的是：

    if len(recognized) == 5:
        self._handle_draw_phase(frame)
    elif self._status_ticks % 8 == 1:
        self._explain_missing_cards(frame)

**沒有 else。** 只要有一格認不出來，bot 就安靜地卡在選牌畫面，每隔幾秒印一次
「只認出 4/5」，直到有人來按 F9。使用者 2026-08-21 遇到的就是這個：一張鬼牌，
整晚的任務停擺。

鬼牌那個特定原因已經修好（見 test_joker.py），但結構性的問題還在 ——
反光、動畫殘影、遊戲改版換一張新牌，都會再次造成同樣的死結。所以加一道
有時限的自救：同一組「認不齊」的判讀連續維持 `partial_draw_rescue_sec` 秒，
就只用認得出來的牌做最保守的決定（有對子留對子，否則全換）並按替換。

## 兩邊都要守

* **不能太早動手** —— 發牌動畫、翻牌過渡影格本來就會有一兩秒認不出來。
  只要有任何一格的判讀改變，計時就要重新開始。
* **不能永遠不動手** —— 那就是原本的 bug。
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import DEFAULT_CONFIG  # noqa: E402
from src.state_machine import FrameInfo  # noqa: E402


def _cfg(**overrides) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg.update(overrides)
    return cfg


def _draw_frame(labels) -> FrameInfo:
    """labels 裡的 None 代表那一格認不出來。"""
    return FrameInfo(
        on_table=True,
        table_marker_score=0.9,
        slot_cards=[None if l is None else (l, 0.9) for l in labels],
        highlow_card=None,
        is_draw=True,
        is_congrats=False,
        is_challenge=False,
        is_fail=False,
        is_poker_fail=False,
        ui_scores={},
    )


class _RescueCase(unittest.TestCase):
    def _make_bot(self, **cfg_overrides):
        with patch("src.bot.GameCapture") as Cap, patch("src.bot.MouseController") as Mouse, \
                patch("src.bot.DailyStats"):
            Cap.return_value.is_window_valid.return_value = True
            from src.bot import Bot

            bot = Bot(_cfg(**cfg_overrides), dry_run=False)
            bot.mouse = Mouse.return_value
            bot.capture = Cap.return_value
            bot.capture.is_window_valid.return_value = True
            bot.running = True
            return bot

    def _clicks(self, bot) -> list:
        keys = []
        bot._click_point = lambda key, *a, **k: keys.append(key) or True
        return keys


class TestDoesNotFireTooEarly(_RescueCase):
    def test_nothing_happens_before_the_timeout(self):
        bot = self._make_bot()
        frame = _draw_frame(["2H", None, "AH", "QS", "3D"])
        for _ in range(5):
            bot._handle_partial_draw(frame)
        bot.mouse.click_point.assert_not_called()

    def test_the_timer_restarts_whenever_any_slot_changes(self):
        """發牌動畫期間每一格的判讀都在跳動，那不是「卡住」。"""
        bot = self._make_bot(partial_draw_rescue_sec=0.3)
        bot._handle_partial_draw(_draw_frame([None, None, None, None, None]))
        time.sleep(0.2)
        bot._handle_partial_draw(_draw_frame(["2H", None, None, None, None]))
        time.sleep(0.2)   # 距離最早那次已經 0.4 秒 > 0.3，但判讀中途變過
        bot._handle_partial_draw(_draw_frame(["2H", None, None, None, None]))
        bot.mouse.click_point.assert_not_called()

    def test_disabled_when_the_setting_is_zero(self):
        bot = self._make_bot(partial_draw_rescue_sec=0)
        frame = _draw_frame(["2H", None, "AH", "QS", "3D"])
        bot._handle_partial_draw(frame)
        time.sleep(0.05)
        bot._handle_partial_draw(frame)
        bot.mouse.click_point.assert_not_called()

    def test_a_full_hand_clears_the_timer(self):
        """認齊之後又認不齊，要從頭數，不能延用上一次的計時。"""
        bot = self._make_bot(partial_draw_rescue_sec=0.3)
        bot._handle_partial_draw(_draw_frame(["2H", None, "AH", "QS", "3D"]))
        time.sleep(0.35)
        bot._clear_partial_draw_timer()   # 模擬中途認齊了一次（_tick 會這樣做）
        bot._handle_partial_draw(_draw_frame(["2H", None, "AH", "QS", "3D"]))
        bot.mouse.click_point.assert_not_called()


class TestFiresAfterTheTimeout(_RescueCase):
    def _run_until_rescue(self, labels, wait=0.2):
        bot = self._make_bot(partial_draw_rescue_sec=wait)
        frame = _draw_frame(labels)
        bot._handle_partial_draw(frame)
        time.sleep(wait + 0.05)
        bot._handle_partial_draw(frame)
        return bot

    def test_it_presses_replace_instead_of_waiting_forever(self):
        bot = self._run_until_rescue(["2H", None, "AH", "QS", "3D"])
        self.assertTrue(bot.mouse.click_point.called,
                        "逾時之後還是沒有任何動作 —— bot 又會卡死")

    def test_a_pair_among_the_known_cards_is_kept(self):
        bot = self._run_until_rescue(["8H", None, "8C", "QS", "3D"])
        points = self.cfg_points(bot)
        # 兩張 8 的保留鍵 + 替換，共三次點擊
        self.assertEqual(bot.mouse.click_point.call_count, 3)
        self.assertIn(points["hold_toggles"][0], self.called_args(bot))
        self.assertIn(points["hold_toggles"][2], self.called_args(bot))
        self.assertIn(points["draw_confirm"], self.called_args(bot))

    def test_without_a_pair_everything_is_discarded(self):
        bot = self._run_until_rescue(["2H", None, "AH", "QS", "3D"])
        # 只按「替換」，一張都不留
        self.assertEqual(bot.mouse.click_point.call_count, 1)
        self.assertIn(self.cfg_points(bot)["draw_confirm"], self.called_args(bot))

    def test_a_joker_is_not_mistaken_for_a_jack(self):
        """"JK"[:-1] == "J" —— 直接切最後一個字元會讓鬼牌變成一張 J，
        然後跟真的 J 配成一個根本不存在的對子。"""
        bot = self._run_until_rescue(["JK", None, "JD", "QS", "3D"])
        self.assertEqual(bot.mouse.click_point.call_count, 1)  # 沒有對子

    def test_a_real_pair_with_a_joker_on_the_table_still_works(self):
        bot = self._run_until_rescue(["JK", None, "JD", "JS", "3D"])
        # 兩張 J 是真的對子；鬼牌不算進去
        self.assertEqual(bot.mouse.click_point.call_count, 3)

    def test_dry_run_only_logs(self):
        with patch("src.bot.GameCapture") as Cap, patch("src.bot.MouseController") as Mouse, \
                patch("src.bot.DailyStats"):
            Cap.return_value.is_window_valid.return_value = True
            from src.bot import Bot
            bot = Bot(_cfg(partial_draw_rescue_sec=0.2), dry_run=True)
            bot.mouse = Mouse.return_value
            bot.running = True
        frame = _draw_frame(["2H", None, "AH", "QS", "3D"])
        bot._handle_partial_draw(frame)
        time.sleep(0.25)
        bot._handle_partial_draw(frame)
        bot.mouse.click_point.assert_not_called()

    # ---- helpers ----
    @staticmethod
    def cfg_points(bot):
        return bot.cfg["points"]

    @staticmethod
    def called_args(bot):
        return [c.args[0] for c in bot.mouse.click_point.call_args_list]


if __name__ == "__main__":
    unittest.main()
