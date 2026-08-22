"""蓋著的深色牌背不讀牌：投注畫面的假陽性與白做工，一次解掉。

背景（2026-08-21 的「兩角一致」文件裡記錄過但一直沒修）：投注畫面五張全是
蓋著的牌背，本來就沒有牌面可讀，但舊版照樣對每一格跑完整的角落判讀 ——
牌背花紋偶爾會被硬湊成一張真牌（實測有 1 格會被讀成 AS）。這個假陽性目前
無害（`is_draw` 要五格全中、`looks_like_betting` 是靠亮度另外判的），但它離
`draw_prompt_soft_threshold` 那條「五張都認得出來就當成選牌畫面」的路只差
幾步，值得連根拔掉。順帶把 bot 停留最久的 idle 狀態的 CPU 開銷砍掉一半。

規則：slot 的中位亮度 ≤ IDLE_SLOT_MAX_VALUE（實測投注畫面最亮 133、
其他畫面最暗 205）就直接回報 None，不跑角落判讀；量不到亮度（-1）不算暗，
照常去讀。
"""
from __future__ import annotations

import json
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import DEFAULT_CONFIG  # noqa: E402
from src.recognize import CardReader  # noqa: E402
from src.state_machine import IDLE_SLOT_MAX_VALUE, detect_frame  # noqa: E402

WIDTH, HEIGHT = 800, 400


class FakeCapture:
    def __init__(self, img: np.ndarray):
        self._img = img
        self._frame = None

    def begin_frame(self):
        self._frame = self._img
        return self._frame

    def grab_region(self, region):
        frame = self._frame if self._frame is not None else self.begin_frame()
        fh, fw = frame.shape[:2]
        x = max(0, min(int(round(region["x"] * fw)), fw - 1))
        y = max(0, min(int(round(region["y"] * fh)), fh - 1))
        w = max(1, min(int(round(region["w"] * fw)), fw - x))
        h = max(1, min(int(round(region["h"] * fh)), fh - y))
        return frame[y:y + h, x:x + w]

    def get_client_size(self):
        return self._img.shape[1], self._img.shape[0]


class SpyReader(CardReader):
    """記錄 read() 被叫了幾次。回傳值固定，測的是「有沒有去讀」。"""

    def __init__(self, answer=("AS", 0.9)):
        super().__init__(part_templates={"rank": {"A": [np.zeros((32, 24), np.uint8)]},
                                         "suit": {"S": [np.zeros((24, 24), np.uint8)]}})
        self.read_calls = 0
        self._answer = answer

    def read(self, roi, expected_w=0, expected_h=0):
        self.read_calls += 1
        return self._answer

    def read_rightmost(self, strip, expected_w, expected_h):
        return None


def _cfg() -> dict:
    return json.loads(json.dumps(DEFAULT_CONFIG))


def _image(slot_brightness: list[int]) -> np.ndarray:
    """整張畫面塗中性灰，五個手牌框各自塗指定亮度（BGR 同值 → HSV V 同值）。"""
    img = np.full((HEIGHT, WIDTH, 3), 40, np.uint8)
    for region, value in zip(DEFAULT_CONFIG["regions"]["card_slots"], slot_brightness):
        x = int(round(region["x"] * WIDTH))
        y = int(round(region["y"] * HEIGHT))
        w = int(round(region["w"] * WIDTH))
        h = int(round(region["h"] * HEIGHT))
        img[y:y + h, x:x + w] = value
    return img


def _detect(img: np.ndarray, reader: SpyReader):
    return detect_frame(FakeCapture(img), _cfg(), reader, {})


class TestDarkSlotsAreSkipped(unittest.TestCase):
    def test_betting_screen_reads_nothing(self):
        """五張全蓋著（暗）→ 一格都不該去讀，全部回 None。"""
        reader = SpyReader()
        frame = _detect(_image([85, 112, 133, 125, 82]), reader)
        self.assertEqual(reader.read_calls, 0)
        self.assertEqual(frame.slot_cards, [None] * 5)
        # 亮度照量，looks_like_betting 不受影響
        self.assertEqual(len(frame.slot_values), 5)
        self.assertTrue(frame.looks_like_betting())

    def test_bright_slots_are_still_read(self):
        """選牌畫面（亮）→ 五格照讀。"""
        reader = SpyReader()
        frame = _detect(_image([255, 250, 255, 245, 255]), reader)
        self.assertEqual(reader.read_calls, 5)
        self.assertEqual([s and s[0] for s in frame.slot_cards], ["AS"] * 5)

    def test_mixed_slots_read_only_the_bright_ones(self):
        """翻倍對話框：第一格被立繪蓋住變暗（88），其他亮 → 只讀亮的四格。"""
        reader = SpyReader()
        frame = _detect(_image([88, 240, 240, 240, 240]), reader)
        self.assertEqual(reader.read_calls, 4)
        self.assertIsNone(frame.slot_cards[0])

    def test_threshold_boundary(self):
        """剛好等於門檻算暗（跳過）；高一階就要讀。"""
        reader = SpyReader()
        _detect(_image([IDLE_SLOT_MAX_VALUE] * 5), reader)
        self.assertEqual(reader.read_calls, 0)
        reader2 = SpyReader()
        _detect(_image([IDLE_SLOT_MAX_VALUE + 1] * 5), reader2)
        self.assertEqual(reader2.read_calls, 5)

    def test_card_back_false_positive_is_gone(self):
        """就算讀取器硬要把牌背認成 AS（歷史上真的發生過），
        暗格也拿不到讀取的機會 —— 假陽性從源頭消失。"""
        reader = SpyReader(answer=("AS", 0.95))
        frame = _detect(_image([97, 102, 100, 97, 95]), reader)
        self.assertEqual(frame.slot_cards, [None] * 5)
        # 所以「五張都認得出來就當成選牌畫面」那條寬鬆路也絕對不會被牌背觸發
        self.assertFalse(frame.is_draw)


if __name__ == "__main__":
    unittest.main()
