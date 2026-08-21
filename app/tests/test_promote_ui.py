"""升級「內建畫面標記樣板」的兩個必要條件。

背景：內建標記樣板是從 1024 寬的縮圖裁的，而使用者的視窗是 1365 寬 ——
比對前要把樣板**放大** 1.33 倍，放大就糊，實測選牌 37% / 過關 21% / 翻倍 23%，
門檻 0.80 一個都過不了。修法是拿原生解析度的截圖重裁。

這裡守兩件事：

1. **裁出來的位置要對**（`cut` 用比例座標，任何解析度都適用）。
2. **來源解析度一定要跟著更新**。只換圖不改 `BUNDLED_MARKER_WIDTH/HEIGHT`
   會用錯倍率去縮放，換了樣板反而比原本更慘 —— 這是最容易漏的一步。

以及一個獨立性檢查：`BUNDLED_MARKER_*`（內建標記的來源解析度）跟
`SCREENSHOT_CLIENT_*`（校準座標的參考尺寸）是兩件事，升級標記不可以動到後者。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

sys.path.insert(0, os.path.join(ROOT, "tools"))

import promote_ui_templates as promote  # noqa: E402
from src import defaults_layout  # noqa: E402


class TestCut(unittest.TestCase):
    def test_cuts_the_right_pixels_at_any_resolution(self):
        """比例座標的意義：同一塊內容，不管截圖多大都要裁到同一塊。"""
        region = {"x": 0.25, "y": 0.50, "w": 0.25, "h": 0.25}
        for width, height in ((1024, 438), (1365, 576), (2048, 876)):
            img = np.zeros((height, width, 3), np.uint8)
            x0, y0 = round(0.25 * width), round(0.50 * height)
            x1, y1 = round(0.50 * width), round(0.75 * height)
            img[y0:y1, x0:x1] = 255           # 只有目標區塊是白的
            piece = promote.cut(img, region)
            self.assertEqual(piece.shape[:2], (y1 - y0, x1 - x0), (width, height))
            # 裁出來整塊都是白的 = 位置對了
            self.assertEqual(int(piece.min()), 255, (width, height))

    def test_bigger_screenshot_gives_a_bigger_template(self):
        """重點就是這個：原生解析度越大，樣板像素越多，比對時才不用放大。"""
        region = {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}
        small = promote.cut(np.zeros((438, 1024, 3), np.uint8), region)
        big = promote.cut(np.zeros((576, 1365, 3), np.uint8), region)
        self.assertGreater(big.shape[1], small.shape[1])
        self.assertGreater(big.shape[0], small.shape[0])

    def test_never_returns_an_empty_slice(self):
        """座標貼邊或算出零寬時，寧可回一個 1 像素也不要回空陣列。"""
        img = np.zeros((100, 200, 3), np.uint8)
        for region in ({"x": 0.999, "y": 0.999, "w": 0.001, "h": 0.001},
                       {"x": 0.5, "y": 0.5, "w": 0.0, "h": 0.0},
                       {"x": 0.9, "y": 0.9, "w": 0.5, "h": 0.5}):
            piece = promote.cut(img, region)
            self.assertGreater(piece.size, 0, region)


class TestBundledSizeIsUpdated(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "defaults_layout.py")
        shutil.copy2(os.path.join(ROOT, "src", "defaults_layout.py"), self.path)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _read(self) -> str:
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def test_updates_both_numbers(self):
        # 刻意用一組「絕對不等於目前值」的數字 —— 這個常數本身會隨著每次
        # 升級內建樣板而改變，寫死 1365 的話升級一次測試就壞。
        self.assertTrue(promote.bump_bundled_size(1920, 810, self.path))
        text = self._read()
        self.assertIn("BUNDLED_MARKER_WIDTH = 1920", text)
        self.assertIn("BUNDLED_MARKER_HEIGHT = 810", text)

    def test_does_not_touch_the_calibration_reference_size(self):
        """`SCREENSHOT_CLIENT_*` 是校準座標的參考尺寸，是另一件事。

        混用的話一定有一邊是錯的：改它會影響「校準」分頁把比例換回像素。
        """
        before = self._read()
        promote.bump_bundled_size(1920, 810, self.path)
        after = self._read()
        for line in ("SCREENSHOT_CLIENT_WIDTH = 1024", "SCREENSHOT_CLIENT_HEIGHT = 438"):
            self.assertIn(line, before)
            self.assertIn(line, after)

    def test_only_rewrites_line_starts(self):
        """說明文字裡如果提到這個常數，不能被一起改掉。"""
        with open(self.path, "a", encoding="utf-8") as f:
            f.write('\n_EXAMPLE = "    BUNDLED_MARKER_WIDTH = 1024"\n')
        promote.bump_bundled_size(2048, 876, self.path)
        self.assertIn('_EXAMPLE = "    BUNDLED_MARKER_WIDTH = 1024"', self._read())

    def test_leaves_a_backup(self):
        promote.bump_bundled_size(1920, 810, self.path)
        self.assertTrue(os.path.exists(self.path + ".bak"))

    def test_reports_when_nothing_changed(self):
        promote.bump_bundled_size(1920, 810, self.path)
        self.assertFalse(promote.bump_bundled_size(1920, 810, self.path))


class TestMarkerKeys(unittest.TestCase):
    def test_every_marker_has_a_file_name(self):
        """七個標記（含從來沒有圖的 max_win）都要能被這個工具處理。"""
        for key in ("table_marker", "draw_prompt", "congrats_marker",
                    "challenge_marker", "fail_marker", "poker_fail_marker",
                    "max_win_marker"):
            self.assertIn(key, promote.MARKER_KEYS, key)
            self.assertTrue(promote.MARKER_KEYS[key].endswith(".png"))

    def test_keys_match_the_config_region_names(self):
        """裁圖是用 cfg['regions'][key]，名字對不上就會裁到錯的地方。"""
        regions = defaults_layout.SCREENSHOT_LAYOUT["regions"]
        for key in promote.MARKER_KEYS:
            self.assertIn(key, regions, key)


class TestConstantsAreWiredUp(unittest.TestCase):
    """`capture_client_width` 的預設值必須來自 BUNDLED_MARKER_*。

    否則升級內建樣板之後，新安裝的人會用 1024 的倍率去比對 1365 的圖。
    """

    def test_default_config_uses_the_bundled_marker_size(self):
        from src.config import DEFAULT_CONFIG
        templates = DEFAULT_CONFIG["templates"]
        self.assertEqual(templates["capture_client_width"],
                         defaults_layout.BUNDLED_MARKER_WIDTH)
        self.assertEqual(templates["capture_client_height"],
                         defaults_layout.BUNDLED_MARKER_HEIGHT)

    def test_marker_scale_falls_back_to_the_bundled_size(self):
        """舊 config 沒有這兩個欄位時，退回值也要是內建常數，不能寫死 1024。"""
        from src.state_machine import expected_marker_scale
        cfg = {"templates": {}}
        w, h = defaults_layout.BUNDLED_MARKER_WIDTH, defaults_layout.BUNDLED_MARKER_HEIGHT
        self.assertAlmostEqual(expected_marker_scale(cfg, w, h), 1.0, places=6)


if __name__ == "__main__":
    unittest.main()


class TestBundledSizeSync(unittest.TestCase):
    """升級時「來源解析度」要不要跟著改，取決於樣板是誰的。

    2026-08-21 把內建標記圖從 1024 縮圖換成 1365 原生截圖。設定檔裡的
    `capture_client_width` 若還停在 1024，比對會用錯倍率 ——
    **換了樣板反而比原本更慘**，所以一定要同步。

    但**不能無條件覆蓋**：使用者自己在實機重新框選過標記時，那個數字就是
    他自己的截圖解析度，蓋掉等於把他所有樣板都縮放錯。
    """

    def setUp(self):
        from src import config as config_mod
        self.config_mod = config_mod
        self.dir = tempfile.mkdtemp()
        self.bundled = os.path.join(self.dir, "defaults", "ui")
        self.mine = os.path.join(self.dir, "card_templates")
        os.makedirs(self.bundled)
        os.makedirs(self.mine)
        self._orig_ui = config_mod.default_ui_dir
        self._orig_resolve = config_mod.resolve_data_path
        config_mod.default_ui_dir = lambda: self.bundled
        config_mod.resolve_data_path = lambda rel: os.path.join(self.dir, rel)

    def tearDown(self):
        self.config_mod.default_ui_dir = self._orig_ui
        self.config_mod.resolve_data_path = self._orig_resolve
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, folder, name, data: bytes):
        with open(os.path.join(folder, name), "wb") as f:
            f.write(data)

    def _cfg(self, width=1024, height=438):
        from src.defaults_layout import UI_MARKER_FILES
        names = list(UI_MARKER_FILES.values())
        return {"templates": {
            **{f"m{i}_image": f"card_templates/{n}" for i, n in enumerate(names)},
            "capture_client_width": width,
            "capture_client_height": height,
        }}

    def _seed(self, identical=True):
        from src.defaults_layout import UI_MARKER_FILES
        for i, name in enumerate(UI_MARKER_FILES.values()):
            self._write(self.bundled, name, b"bundled-%d" % i)
            self._write(self.mine, name, b"bundled-%d" % i if identical else b"mine-%d" % i)

    def test_syncs_when_every_marker_is_still_bundled(self):
        from src.defaults_layout import BUNDLED_MARKER_HEIGHT, BUNDLED_MARKER_WIDTH
        self._seed(identical=True)
        cfg = self._cfg()
        self.assertTrue(self.config_mod._sync_bundled_marker_size(cfg))
        self.assertEqual(cfg["templates"]["capture_client_width"], BUNDLED_MARKER_WIDTH)
        self.assertEqual(cfg["templates"]["capture_client_height"], BUNDLED_MARKER_HEIGHT)

    def test_leaves_the_users_own_capture_size_alone(self):
        """一張是他自己抓的就整組不動 —— 這是最重要的那條。"""
        self._seed(identical=False)
        cfg = self._cfg(width=1843, height=778)
        self.assertFalse(self.config_mod._sync_bundled_marker_size(cfg))
        self.assertEqual(cfg["templates"]["capture_client_width"], 1843)

    def test_one_own_template_among_bundled_ones_is_enough_to_stop_it(self):
        from src.defaults_layout import UI_MARKER_FILES
        self._seed(identical=True)
        first = list(UI_MARKER_FILES.values())[0]
        self._write(self.mine, first, b"my own capture")
        cfg = self._cfg(width=1843, height=778)
        self.assertFalse(self.config_mod._sync_bundled_marker_size(cfg))
        self.assertEqual(cfg["templates"]["capture_client_width"], 1843)

    def test_compares_content_not_file_names(self):
        """檔名一模一樣、內容不同 —— 一定要看得出來是他自己的。

        比檔名這件事在點數/花色樣板上踩過：內建檔名是 suit_D_1..8，
        使用者自己抓的也會被寫成那種名字，比檔名就會把他的成果當成內建處理。
        """
        self._seed(identical=False)     # 檔名全同、內容全不同
        cfg = self._cfg(width=1600, height=676)
        self.assertFalse(self.config_mod._sync_bundled_marker_size(cfg))

    def test_no_marker_files_at_all_changes_nothing(self):
        cfg = self._cfg()
        self.assertFalse(self.config_mod._sync_bundled_marker_size(cfg))
        self.assertEqual(cfg["templates"]["capture_client_width"], 1024)

    def test_already_in_sync_reports_no_change(self):
        from src.defaults_layout import BUNDLED_MARKER_HEIGHT, BUNDLED_MARKER_WIDTH
        self._seed(identical=True)
        cfg = self._cfg(width=BUNDLED_MARKER_WIDTH, height=BUNDLED_MARKER_HEIGHT)
        self.assertFalse(self.config_mod._sync_bundled_marker_size(cfg))


class TestRetunedThresholds(unittest.TestCase):
    def test_config_default_matches_state_machine_default(self):
        """兩邊各有一份門檻表，不同步的話「改了沒效果」會很難查。"""
        from src.config import DEFAULT_CONFIG
        from src.state_machine import DEFAULT_MARKER_THRESHOLDS
        self.assertEqual(DEFAULT_CONFIG["marker_thresholds"],
                         {k: v for k, v in DEFAULT_MARKER_THRESHOLDS.items()})

    def test_version_6_forces_the_new_thresholds(self):
        """只改預設值是沒用的 —— `_deep_merge` 會讓使用者的舊值贏。"""
        from src.config import CONFIG_VERSION, DEFAULT_CONFIG, RETUNED_ON_UPGRADE
        self.assertIn("marker_thresholds", RETUNED_ON_UPGRADE[6])
        self.assertGreaterEqual(CONFIG_VERSION, 6)
        th = DEFAULT_CONFIG["marker_thresholds"]
        # 這兩個是實測重新量出來的（正例最低 0.70 / 0.81）
        self.assertLessEqual(th["draw_prompt"], 0.70)
        self.assertLessEqual(th["fail_marker"], 0.81)
