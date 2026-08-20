"""用實機截圖驗證六個畫面標記：該認的要認得出來，不該認的不能誤判。

而且要**在不同視窗大小下都成立** —— 樣板是點陣圖，帶著擷取當下的解析度，
比對前必須依「目前視窗寬 ÷ 樣板擷取寬」縮放。這一組測試就是在守住這件事：
同一張截圖縮放成 1024 / 1440 / 1937 三種寬度，判斷結果必須完全一樣。

截圖放在 debug_captures/purple_*.png（1024×438）。找不到就自動跳過。
"""
from __future__ import annotations

import json
import os
import sys
import unittest

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import DEFAULT_CONFIG  # noqa: E402
from src.geometry import ratio_region_to_pixels  # noqa: E402
from src.recognize import load_single_template  # noqa: E402
from src.state_machine import detect_frame  # noqa: E402

SHOTS = {
    "purple_start": {"on_table": True},
    "purple_draw": {"on_table": True, "is_draw": True},
    "purple_highlow": {"on_table": True},
    "purple_congrats": {"on_table": True, "is_congrats": True},
    "purple_challenge": {"is_challenge": True},
    "purple_fail": {"is_fail": True, "is_poker_fail": True},
}
FLAGS = ("is_draw", "is_congrats", "is_challenge", "is_fail", "is_poker_fail")
WIDTHS = (1024, 1440, 1937)


class FakeCapture:
    def __init__(self, bgr):
        self._img = bgr

    def begin_frame(self):
        return self._img

    def get_client_size(self):
        h, w = self._img.shape[:2]
        return w, h

    def grab_region(self, region):
        h, w = self._img.shape[:2]
        x, y, rw, rh = ratio_region_to_pixels(region, w, h)
        x = max(0, min(x, w - 1))
        y = max(0, min(y, h - 1))
        rw = max(1, min(rw, w - x))
        rh = max(1, min(rh, h - y))
        return self._img[y : y + rh, x : x + rw]


def _cfg() -> dict:
    return json.loads(json.dumps(DEFAULT_CONFIG))


def _ui_templates(cfg: dict) -> dict:
    mapping = {
        "table_marker": "table_marker_image",
        "draw_prompt": "draw_prompt_image",
        "congrats": "congrats_marker_image",
        "challenge": "challenge_marker_image",
        "fail": "fail_marker_image",
        "poker_fail": "poker_fail_marker_image",
    }
    out = {}
    for key, cfg_key in mapping.items():
        rel = cfg["templates"].get(cfg_key, "")
        out[key] = load_single_template(os.path.join(ROOT, rel)) if rel else None
    return out


def _load(name: str, width: int):
    path = os.path.join(ROOT, "debug_captures", name + ".png")
    img = cv2.imread(path)
    if img is None:
        return None
    if img.shape[1] != width:
        height = int(round(img.shape[0] * width / img.shape[1]))
        img = cv2.resize(img, (width, height))
    return img


class TestScreenDetection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = _cfg()
        cls.templates = _ui_templates(cls.cfg)
        missing = [k for k, v in cls.templates.items() if v is None]
        if missing:
            raise unittest.SkipTest(f"缺少畫面標記樣板：{missing}")

    def _detect(self, name: str, width: int):
        img = _load(name, width)
        if img is None:
            self.skipTest(f"找不到截圖 debug_captures/{name}.png")
        return detect_frame(FakeCapture(img), self.cfg, {}, self.templates)

    def test_each_screen_is_classified_correctly(self):
        for name, expected in SHOTS.items():
            for width in WIDTHS:
                with self.subTest(shot=name, width=width):
                    frame = self._detect(name, width)
                    for flag in FLAGS:
                        want = expected.get(flag, False)
                        got = getattr(frame, flag)
                        self.assertEqual(
                            got, want,
                            f"{name}@{width} 的 {flag} 應該是 {want}，實際 {got}"
                            f"（分數 {frame.ui_scores}）",
                        )

    def test_table_marker_visible_on_table_screens(self):
        for name, expected in SHOTS.items():
            if not expected.get("on_table"):
                continue
            for width in WIDTHS:
                with self.subTest(shot=name, width=width):
                    frame = self._detect(name, width)
                    self.assertTrue(
                        frame.on_table,
                        f"{name}@{width} 應該看得到牌桌 logo，分數只有 "
                        f"{frame.table_marker_score:.2f}",
                    )

    def test_dialog_screens_are_not_treated_as_leaving_the_table(self):
        """對話框會把牌桌模糊掉，logo 認不出來屬正常，不能拿來判定已離開牌桌。"""
        for name in ("purple_challenge", "purple_fail"):
            for width in WIDTHS:
                with self.subTest(shot=name, width=width):
                    frame = self._detect(name, width)
                    self.assertTrue(
                        frame.any_dialog,
                        f"{name}@{width} 沒有被判定成對話框，離桌計數器不會被重置",
                    )

    def test_results_are_identical_across_window_sizes(self):
        for name in SHOTS:
            base = None
            for width in WIDTHS:
                frame = self._detect(name, width)
                current = tuple(getattr(frame, f) for f in FLAGS) + (frame.on_table,)
                if base is None:
                    base = current
                else:
                    self.assertEqual(
                        current, base,
                        f"{name} 在 {width} 寬時的判斷結果和 {WIDTHS[0]} 寬不一致",
                    )


class TestHighLowStripScan(unittest.TestCase):
    """比大小畫面：歷史牌往左堆疊只露出一小條，要取最右邊那張完整的牌。"""

    CW, CH = 60, 90

    def _cards(self):
        import numpy as np

        rng = np.random.default_rng(0)
        return {
            label: [rng.integers(0, 255, (self.CH, self.CW, 3), dtype=np.uint8)]
            for label in ("AS", "KH", "9C", "7D")
        }

    def test_picks_the_rightmost_fully_visible_card(self):
        import numpy as np

        from src.recognize import find_rightmost_card

        cards = self._cards()
        strip = np.full((self.CH, 420, 3), 200, np.uint8)
        strip[:, 0:18] = cards["AS"][0][:, :18]      # 被蓋住的歷史牌
        strip[:, 18:36] = cards["KH"][0][:, :18]     # 被蓋住的歷史牌
        strip[:, 36:96] = cards["9C"][0]             # 目前要比的牌
        rng = np.random.default_rng(1)
        strip[:, 110:170] = rng.integers(0, 255, (self.CH, 60, 3), dtype=np.uint8)  # 卡背，無樣板

        hit = find_rightmost_card(strip, cards, self.CW, self.CH, 0.83, 0.02)
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0], "9C")

    def test_returns_none_when_no_card_present(self):
        import numpy as np

        from src.recognize import find_rightmost_card

        strip = np.full((self.CH, 420, 3), 200, np.uint8)
        self.assertIsNone(find_rightmost_card(strip, self._cards(), self.CW, self.CH, 0.83, 0.02))


class TestCardTemplateLoading(unittest.TestCase):
    def test_ui_markers_are_not_loaded_as_cards(self):
        """card_templates 裡的 ui_*.png / table_marker.png 不可以被當成卡牌。"""
        from src.recognize import load_card_templates

        templates = load_card_templates(os.path.join(ROOT, "card_templates"))
        for label in templates:
            self.assertNotIn(
                label.lower(), {"ui", "table", "back"},
                f"畫面標記樣板被誤當成卡牌載入（標籤 {label}）",
            )


if __name__ == "__main__":
    unittest.main()
