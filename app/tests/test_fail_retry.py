"""湊牌失敗後應點「再一次」，而不是卡著或去點「投注並開始」。"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import DEFAULT_CONFIG
from src.recognize import region_similarity
from src.state_machine import FrameInfo


def _cfg() -> dict:
    return json.loads(json.dumps(DEFAULT_CONFIG))


def _idle_frame(**kwargs) -> FrameInfo:
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
        ui_scores={"table": 0.9, "draw": 0.1, "congrats": 0.1, "challenge": 0.1, "fail": 0.33, "poker_fail": 0.1},
    )
    data.update(kwargs)
    return FrameInfo(**data)


class TestRegionSimilarity(unittest.TestCase):
    def test_fail_marker_still_matches_when_shifted_on_different_background(self):
        path = os.path.join(ROOT, "card_templates", "ui_fail.png")
        tmpl = cv2.imread(path)
        self.assertIsNotNone(tmpl)
        th, tw = tmpl.shape[:2]
        canvas = np.full((th + 40, tw + 80, 3), (160, 70, 150), dtype=np.uint8)
        canvas[18 : 18 + th, 30 : 30 + tw] = tmpl
        score = region_similarity(canvas, tmpl)
        self.assertGreaterEqual(score, 0.80, f"位移後的失敗標記分數只有 {score:.2f}")


class TestFailRetry(unittest.TestCase):
    def _make_bot(self):
        with patch("src.bot.GameCapture") as Cap, patch("src.bot.MouseController") as Mouse, patch(
            "src.bot.DailyStats"
        ):
            Cap.return_value.is_window_valid.return_value = True
            from src.bot import Bot

            bot = Bot(_cfg(), dry_run=False)
            bot.mouse = Mouse.return_value
            bot.capture = Cap.return_value
            bot.capture.is_window_valid.return_value = True
            bot.running = True
            return bot

    def _watch_clicks(self, bot) -> list[str]:
        keys: list[str] = []
        orig = bot._click_point

        def wrapped(key: str, *args, **kwargs):
            keys.append(key)
            return orig(key, *args, **kwargs)

        bot._click_point = wrapped
        return keys

    def test_detected_fail_clicks_retry(self):
        bot = self._make_bot()
        keys = self._watch_clicks(bot)
        frame = _idle_frame(is_fail=True, ui_scores={"table": 0.4, "draw": 0.1, "congrats": 0.1, "challenge": 0.1, "fail": 0.92})
        with patch("src.bot.detect_frame", return_value=frame):
            bot._tick()
        self.assertIn("retry_button", keys)
        self.assertNotIn("start_round", keys)

    def test_slow_screen_after_draw_is_never_treated_as_poker_fail(self):
        """按完「替換」之後畫面慢，不可以自己判定湊牌失敗去點「再一次」。

        使用者回報過：明明已經湊到牌、也進了比大小畫面，程式卻因為等太久
        就先按了「再一次」，把已經過關的一局打掉。湊牌失敗一定會出現
        「要再玩一次撲克嗎？」的標記，沒看到那個標記就只能繼續等。
        """
        bot = self._make_bot()
        keys = self._watch_clicks(bot)
        bot._awaiting_draw_result = True
        bot._draw_confirm_at = time.time() - 3.0
        frame = _idle_frame(is_fail=False)
        with patch("src.bot.detect_frame", return_value=frame):
            bot._tick()
        self.assertEqual(keys, [], f"畫面只是慢，卻點了 {keys}")

    def test_real_poker_fail_marker_still_clicks_retry(self):
        """真的看到「要再玩一次撲克嗎？」才點再一次。"""
        bot = self._make_bot()
        keys = self._watch_clicks(bot)
        bot._awaiting_draw_result = True
        bot._draw_confirm_at = time.time() - 3.0
        frame = _idle_frame(is_fail=False, is_poker_fail=True)
        with patch("src.bot.detect_frame", return_value=frame):
            bot._tick()
        self.assertIn("retry_button", keys)

    def test_draw_wait_gives_up_quietly_after_timeout(self):
        """等超過上限就回到一般偵測，但仍然不可以點「再一次」。"""
        bot = self._make_bot()
        keys = self._watch_clicks(bot)
        bot._awaiting_draw_result = True
        bot._draw_confirm_at = time.time() - 999.0
        frame = _idle_frame(is_fail=False)
        with patch("src.bot.detect_frame", return_value=frame):
            bot._tick()
        self.assertFalse(bot._awaiting_draw_result)
        self.assertNotIn("retry_button", keys)

    def test_fail_click_retries_if_dialog_still_there(self):
        bot = self._make_bot()
        keys = self._watch_clicks(bot)
        frame = _idle_frame(is_fail=True)
        with patch("src.bot.detect_frame", return_value=frame):
            bot._tick()
            # 假裝已經過了重試秒數：畫面還停在失敗對話框 = 遊戲沒收到，要再點
            bot._acted_at = time.time() - 10.0
            bot._tick()
        self.assertGreaterEqual(keys.count("retry_button"), 2)

    def test_poker_fail_screen_clicks_retry_not_start(self):
        bot = self._make_bot()
        keys = self._watch_clicks(bot)
        frame = _idle_frame(is_poker_fail=True, ui_scores={"table": 0.76, "draw": 0.56, "congrats": 0.35, "challenge": 0.49, "fail": 0.72, "poker_fail": 0.95})
        with patch("src.bot.detect_frame", return_value=frame):
            bot._tick()
        self.assertIn("retry_button", keys)
        self.assertNotIn("start_round", keys)


class TestNoFalseAutoStop(unittest.TestCase):
    """對話框會把牌桌 logo 模糊掉，不可以因此誤判「已達每日上限」而自動停止。"""

    def _make_bot(self):
        with patch("src.bot.GameCapture") as Cap, patch("src.bot.MouseController"), patch(
            "src.bot.DailyStats"
        ):
            from src.bot import Bot

            bot = Bot(_cfg(), dry_run=True)
            bot.capture = Cap.return_value
            bot.capture.is_window_valid.return_value = True
            bot.ui_templates["table_marker"] = np.zeros((4, 4, 3), np.uint8)
            bot.running = True
            bot._logo_ever_matched = True
            return bot

    def test_long_dialog_does_not_stop_the_bot(self):
        bot = self._make_bot()
        frame = _idle_frame(on_table=False, table_marker_score=0.66, is_challenge=True)
        with patch("src.bot.detect_frame", return_value=frame):
            for _ in range(60):
                bot._tick()
        self.assertTrue(bot.running, "翻倍對話框停留久了就被誤判成離開牌桌")

    def test_really_leaving_the_table_still_stops(self):
        bot = self._make_bot()
        frame = _idle_frame(on_table=False, table_marker_score=0.20)
        with patch("src.bot.detect_frame", return_value=frame):
            for _ in range(int(_cfg()["exit_table_ticks"]) + 2):
                bot._tick()
        self.assertFalse(bot.running, "真的離開牌桌時應該要自動停止")


class FakeCapture:
    def __init__(self, bgr):
        self._img = bgr
        self._frame = bgr
        self.hwnd = 1

    def begin_frame(self):
        self._frame = self._img
        return self._frame

    def get_client_size(self):
        h, w = self._img.shape[:2]
        return w, h

    def grab_region(self, region):
        from src.geometry import ratio_region_to_pixels

        h, w = self._img.shape[:2]
        x, y, rw, rh = ratio_region_to_pixels(region, w, h)
        x = max(0, min(x, w - 1))
        y = max(0, min(y, h - 1))
        rw = max(1, min(rw, w - x))
        rh = max(1, min(rh, h - y))
        return self._img[y : y + rh, x : x + rw]


class TestDetectPokerFail(unittest.TestCase):
    def test_screenshot_is_poker_fail_not_idle(self):
        from src.state_machine import detect_frame

        path = os.path.join(ROOT, "tests", "fixtures", "poker_fail_screen.png")
        img = cv2.imread(path)
        self.assertIsNotNone(img)
        tmpl = cv2.imread(os.path.join(ROOT, "card_templates", "ui_poker_fail.png"))
        self.assertIsNotNone(tmpl)
        cfg = _cfg()
        frame = detect_frame(FakeCapture(img), cfg, {}, {"poker_fail": tmpl})
        self.assertTrue(frame.is_poker_fail, f"湊牌失敗分數只有 {frame.ui_scores.get('poker_fail')}")
        self.assertFalse(frame.is_fail)


if __name__ == "__main__":
    unittest.main()
