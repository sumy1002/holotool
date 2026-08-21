"""範例圖擺哪一個角落。

這段幾何很容易悄悄算錯，而錯的表現正好是最糟的那種：**範例圖蓋住了使用者
現在要瞄的地方**。而且它不會報錯，只會讓人覺得「這個提示很煩」。

`choose_panel_corner` 刻意寫成模組層函式（不吃 self、不碰 tkinter），
所以在沒有 GUI 的環境也測得到。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.geometry import PANEL_MARGIN as EXAMPLE_MARGIN  # noqa: E402
from src.geometry import choose_panel_corner  # noqa: E402

SCREEN = (1920, 1080)
PANEL = (500, 260)


def corner(*obstacles):
    return choose_panel_corner(PANEL[0], PANEL[1], SCREEN[0], SCREEN[1], list(obstacles))


class TestChoosePanelCorner(unittest.TestCase):
    def test_top_right_when_nothing_is_in_the_way(self):
        self.assertEqual(corner(),
                         (SCREEN[0] - PANEL[0] - EXAMPLE_MARGIN, EXAMPLE_MARGIN))

    def test_moves_away_from_an_obstacle_in_the_top_right(self):
        """「大」按鈕就在右上角時，範例圖不能停在預設位置。"""
        got = corner((1700, 40, 1800, 140))
        self.assertNotEqual(got, (SCREEN[0] - PANEL[0] - EXAMPLE_MARGIN, EXAMPLE_MARGIN))
        self.assertEqual(_overlap(got, (1700, 40, 1800, 140)), 0)

    def test_avoids_both_the_guide_and_the_instruction_text(self):
        guide = (1600, 60, 1880, 300)               # 右上
        header = (300, 40, 1600, 260)               # 上方那一大塊說明文字
        got = corner(guide, header)
        self.assertEqual(_overlap(got, guide) + _overlap(got, header), 0)

    def test_falls_back_to_the_least_bad_corner_when_all_overlap(self):
        """四個角落都被佔住時要選重疊最小的那個，而不是丟例外。"""
        big = (0, 0, SCREEN[0], SCREEN[1])
        small_top_right = (SCREEN[0] - 60, 0, SCREEN[0], 60)
        got = choose_panel_corner(PANEL[0], PANEL[1], SCREEN[0], SCREEN[1],
                                  [big, small_top_right])
        self.assertIn(got, [
            (SCREEN[0] - PANEL[0] - EXAMPLE_MARGIN, EXAMPLE_MARGIN),
            (EXAMPLE_MARGIN, EXAMPLE_MARGIN),
            (SCREEN[0] - PANEL[0] - EXAMPLE_MARGIN, SCREEN[1] - PANEL[1] - EXAMPLE_MARGIN),
            (EXAMPLE_MARGIN, SCREEN[1] - PANEL[1] - EXAMPLE_MARGIN),
        ])
        # 右上角多疊了一塊，所以不該是右上角
        self.assertNotEqual(got, (SCREEN[0] - PANEL[0] - EXAMPLE_MARGIN, EXAMPLE_MARGIN))

    def test_panel_always_stays_on_screen(self):
        for obstacles in ([], [(0, 0, 100, 100)], [(1800, 1000, 1920, 1080)]):
            x, y = choose_panel_corner(PANEL[0], PANEL[1], SCREEN[0], SCREEN[1], obstacles)
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + PANEL[0], SCREEN[0])
            self.assertLessEqual(y + PANEL[1], SCREEN[1])


def _overlap(origin, rect) -> int:
    x, y = origin
    ox0, oy0, ox1, oy1 = rect
    dx = max(0, min(x + PANEL[0], ox1) - max(x, ox0))
    dy = max(0, min(y + PANEL[1], oy1) - max(y, oy0))
    return dx * dy


if __name__ == "__main__":
    unittest.main()
