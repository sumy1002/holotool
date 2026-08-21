"""「已達最高獲得金額」畫面：唯一一個沒有內建樣板的標記，終於有了。

## 為什麼以前偵測不到（2026-08-21 使用者實機回報）

    上限=無

不是分數低 —— 是 `card_templates\\ui_max_win.png` **根本不存在**。
它是七個畫面標記裡唯一沒有內建預設圖的一個（那個畫面的原生像素一直拿不到，
只有貼在對話裡、被重新壓縮過的截圖）。沒有 PNG 的話：

* `state_machine._score()` 回 `-1`，狀態列印「無」；
* `hit("max_win_marker")` 永遠 False → `FrameInfo.is_max_win` 永遠 False；
* 負責按下去的 `Bot._handle_max_win()` **一次都沒有執行過**；
* 連 `max_win_count` 都不會累加，所以第二次達標也不會自動收工。

使用者用主控台的「存一張目前畫面（PNG）」抓到 4 張 1065x599 的原生截圖之後，
樣板就做得出來了。

## 這個測試守的三件事

1. **內建樣板存在**（`defaults/ui/ui_max_win.png`）—— 少了它就回到上面那個狀態。
2. **它真的分得開**：實機 4 張上限畫面 0.810~0.988、另外 21 張其他畫面最高
   0.616，門檻 0.71 落在中間。
3. **它會被送到 `card_templates\\`**：那個資料夾不進版控也不進更新包，
   所以一定要有人在啟動時把缺的補過去（`prepare_runtime()`）。
   這一條沒守住的話，新樣板永遠到不了使用者手上。
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import unittest

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import paths  # noqa: E402
from src.config import DEFAULT_CONFIG  # noqa: E402
from src.defaults_layout import BUNDLED_MARKER_HEIGHT, SCREENSHOT_LAYOUT, UI_MARKER_FILES  # noqa: E402
from src.geometry import content_height  # noqa: E402
from src.recognize import marker_score  # noqa: E402
from src.state_machine import DEFAULT_MARKER_THRESHOLDS, _expand_region  # noqa: E402

BUNDLED = os.path.join(ROOT, "defaults", "ui", "ui_max_win.png")
CAPTURES = os.path.join(ROOT, "debug_captures")
CONFIG = os.path.join(ROOT, "config", "config.json")

# 上限畫面：使用者 2026-08-21 23:47 抓的四張（16:9 1065x599）
MAX_WIN_SHOTS = "shot_1065x599_"


class TestBundledTemplateExists(unittest.TestCase):
    def test_the_default_image_is_shipped(self):
        self.assertTrue(
            os.path.exists(BUNDLED),
            "defaults/ui/ui_max_win.png 不見了 —— 上限畫面會退回「上限=無」，"
            "自動按「結束／再玩一次」與每日次數統計全部失效",
        )

    def test_it_is_registered_as_a_marker_file(self):
        self.assertEqual(UI_MARKER_FILES.get("regions.max_win_marker"), "ui_max_win.png")
        self.assertIn("ui_max_win.png",
                      DEFAULT_CONFIG["templates"]["max_win_marker_image"])

    def test_missing_marker_images_are_restored_on_startup(self):
        """`card_templates\\` 不進版控、也不進更新包（PROTECTED_DIRS）。

        所以新增一張內建標記圖之後，一定要有人在啟動時把「缺的」補過去，
        否則它只會躺在 `defaults\\ui\\` 裡，程式永遠讀不到。
        **一定是 overwrite=False** —— 自己重新框選過的人不能被蓋掉。
        """
        source = inspect.getsource(paths.prepare_runtime)
        self.assertIn("install_default_ui_templates", source)
        self.assertIn("overwrite=False", source)


class TestThreshold(unittest.TestCase):
    def test_the_threshold_sits_between_the_measured_populations(self):
        """實測正例最低 0.810、反例最高 0.616。舊的 0.78 會擦邊擋掉正例。"""
        value = DEFAULT_MARKER_THRESHOLDS["max_win_marker"]
        self.assertGreater(value, 0.616)
        self.assertLess(value, 0.810)
        self.assertEqual(DEFAULT_CONFIG["marker_thresholds"]["max_win_marker"], value)

    def test_the_default_region_matches_where_the_text_actually_is(self):
        """舊的預設框是目測估的，左右各多框了二三十像素的背景。"""
        region = SCREENSHOT_LAYOUT["regions"]["max_win_marker"]
        self.assertAlmostEqual(region["x"], 0.4140, places=3)
        self.assertAlmostEqual(region["w"], 0.1714, places=3)


@unittest.skipUnless(os.path.exists(BUNDLED) and os.path.isdir(CAPTURES),
                     "沒有樣板或截圖")
class TestSeparationOnRealCaptures(unittest.TestCase):
    """拿實機截圖驗證，而不是只確認檔案存在。"""

    @classmethod
    def setUpClass(cls):
        cls.tmpl = cv2.imread(BUNDLED)
        cls.cfg = json.load(open(CONFIG, encoding="utf-8")) if os.path.exists(CONFIG) else None
        cls.shots = sorted(f for f in os.listdir(CAPTURES)
                           if f.startswith("shot_") and f.endswith(".png"))

    def setUp(self):
        if self.tmpl is None or self.cfg is None or not self.shots:
            self.skipTest("缺少樣板、設定檔或截圖")

    def _region(self, width: int, height: int) -> dict:
        label = "16:9" if abs(width / height - 16 / 9) < 0.05 else "21:9"
        for profile in self.cfg.get("calibration_profiles") or []:
            if profile["label"] == label:
                return profile["regions"]["max_win_marker"]
        self.skipTest(f"設定檔裡沒有 {label} 的校準")

    def _score(self, path: str) -> float:
        image = cv2.imread(path)
        height, width = image.shape[:2]
        pad = tuple(self.cfg["marker_pads"]["max_win_marker"])
        expanded = _expand_region(self._region(width, height), pad_x=pad[0], pad_y=pad[1])
        x, y = round(expanded["x"] * width), round(expanded["y"] * height)
        w, h = round(expanded["w"] * width), round(expanded["h"] * height)
        roi = image[max(0, y): y + h, max(0, x): x + w]
        scale = content_height(width, height) / BUNDLED_MARKER_HEIGHT
        return marker_score(roi, self.tmpl, expected_scale=scale)

    def test_every_max_win_screen_is_over_the_threshold(self):
        shots = [f for f in self.shots if MAX_WIN_SHOTS in f]
        if not shots:
            self.skipTest("沒有上限畫面的截圖")
        threshold = DEFAULT_MARKER_THRESHOLDS["max_win_marker"]
        for shot in shots:
            score = self._score(os.path.join(CAPTURES, shot))
            self.assertGreater(score, threshold, f"{shot} 只有 {score:.3f}")

    def test_no_other_screen_is_mistaken_for_it(self):
        """誤判的代價是在**還能繼續玩**的時候記一次上限、甚至提早收工。"""
        shots = [f for f in self.shots if MAX_WIN_SHOTS not in f]
        if len(shots) < 5:
            self.skipTest("反例太少，這個測試等於沒跑")
        threshold = DEFAULT_MARKER_THRESHOLDS["max_win_marker"]
        for shot in shots:
            score = self._score(os.path.join(CAPTURES, shot))
            self.assertLess(score, threshold, f"{shot} 被誤判成上限畫面（{score:.3f}）")

    def test_the_transition_frame_counts_too(self):
        """金額還在跳動、按鈕還沒出現的那張也要算數。

        那是最先出現的影格，也是分數最低的一張（0.810）—— 舊門檻 0.78
        只差 0.03 就會把它擋掉，而擋掉就代表要多等好幾拍。
        """
        first = os.path.join(CAPTURES, "shot_1065x599_20260821-234700.png")
        if not os.path.exists(first):
            self.skipTest("沒有那張過場影格")
        self.assertGreater(self._score(first),
                           DEFAULT_MARKER_THRESHOLDS["max_win_marker"])


if __name__ == "__main__":
    unittest.main()
