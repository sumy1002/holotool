"""迷你懸浮視窗的位置夾取。

看起來很無聊，但它是唯一的救命索：迷你視窗沒有標題列、不出現在工作列，
位置一旦落在畫面外就**完全找不回來**，而主視窗那時已經被收起來了。
所以「存下來的座標在別台螢幕上會不會跑出畫面」這件事要有測試。

刻意從 `geometry` 匯入而不是 `minipanel` —— 後者 import tkinter，
在沒有 GUI 的機器上（CI、這個開發容器）整個測試檔會直接 ImportError 收不到。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.geometry import clamp_window_to_screen  # noqa: E402

# 測試用的視窗尺寸。取自 src/minipanel.py 目前的值，但被夾取的函式本身是通用的，
# 所以迷你視窗日後改大改小都不需要動這個測試。
WIDTH, HEIGHT = 168, 42


def clamp_to_screen(x, y, screen_w, screen_h):
    return clamp_window_to_screen(x, y, WIDTH, HEIGHT, screen_w, screen_h)


class TestClamp(unittest.TestCase):
    def test_a_normal_position_is_left_alone(self):
        self.assertEqual(clamp_to_screen(300, 400, 1920, 1080), (300, 400))

    def test_position_saved_on_a_bigger_screen_comes_back(self):
        # 在 3840 寬的螢幕存的位置，換到 1920 的筆電上不能留在畫面外
        x, y = clamp_to_screen(3600, 2000, 1920, 1080)
        self.assertLessEqual(x + WIDTH, 1920)
        self.assertLessEqual(y + HEIGHT, 1080)

    def test_negative_coordinates_are_pulled_back(self):
        self.assertEqual(clamp_to_screen(-500, -80, 1920, 1080), (0, 0))

    def test_the_whole_window_stays_visible(self):
        x, y = clamp_to_screen(1919, 1079, 1920, 1080)
        self.assertEqual((x, y), (1920 - WIDTH, 1080 - HEIGHT))

    def test_screen_smaller_than_the_panel_does_not_go_negative(self):
        # 極端情況：別回傳負數座標，那會讓視窗跑到左上角外面
        x, y = clamp_to_screen(50, 50, 100, 20)
        self.assertEqual((x, y), (0, 0))

    def test_floats_are_accepted(self):
        self.assertEqual(clamp_to_screen(10.7, 20.2, 1920, 1080), (10, 20))


if __name__ == "__main__":
    unittest.main()
