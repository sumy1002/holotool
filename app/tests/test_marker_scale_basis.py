"""畫面標記的縮放基準：`capture_client_width/height` 對不上就整排偏低。

## 實機事故（2026-08-21）

使用者回報「湊到牌的畫面有時候識別不出來、會卡住」。用他的實機截圖量出來：

    過關標記　基準 1024x438（設定檔裡的值）→ 0.69   ← 門檻 0.80，過不了
    過關標記　基準 1365x576（圖真正的來源）→ 1.00

他的 `card_templates\\` 裡七張標記與 `defaults\\ui\\` **位元組完全相同**，
也就是全部都是內建的（來源 1365x576），但設定檔還寫著 1024x438 ——
所有標記都用錯 33% 的倍率在比對。牌桌 logo 餘裕大所以還撐得住，
過關（0.69/0.80）、翻倍、選牌就整排掉下來。

## 為什麼會變成這樣

`_sync_bundled_marker_size()` 本來只在 `old_version < 6` 時跑一次，於是這個
順序就漏掉了：**設定檔先升到 6 → 之後才把內建圖複製進 `card_templates\\`**。
升級那一刻圖還是他自己的，所以沒同步；等圖換成內建的，已經沒有人會再檢查了。

修法是把它改成**每次載入都檢查**。這個函式本身很保守（七張全部都是內建原封
複本才會動），所以無條件跑沒有風險，而且順序再怎麼顛倒都能自己收斂。
"""
from __future__ import annotations

import inspect
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import config as config_mod  # noqa: E402
from src.defaults_layout import BUNDLED_MARKER_HEIGHT, BUNDLED_MARKER_WIDTH  # noqa: E402


class TestSyncRunsEveryLoad(unittest.TestCase):
    def test_it_is_not_gated_on_the_config_version(self):
        """`if old_version < 6` 就是那個漏洞 —— 只跑一次，跑的時機還太早。"""
        source = inspect.getsource(config_mod.load_config)
        line = next(l for l in source.splitlines() if "_sync_bundled_marker_size" in l)
        self.assertNotIn("old_version", line,
                         "同步又被綁回版本號了；換圖與升級的先後順序一顛倒就會失效")


class TestSyncIsConservative(unittest.TestCase):
    """無條件跑的前提是它夠保守：有任何一張是使用者自己抓的就整組不碰。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bundled = os.path.join(self.tmp, "defaults", "ui")
        self.live = os.path.join(self.tmp, "card_templates")
        os.makedirs(self.bundled)
        os.makedirs(self.live)
        self._patch = (config_mod.default_ui_dir, config_mod.resolve_data_path)
        config_mod.default_ui_dir = lambda: self.bundled
        config_mod.resolve_data_path = lambda rel: os.path.join(self.tmp, rel)

    def tearDown(self):
        config_mod.default_ui_dir, config_mod.resolve_data_path = self._patch
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name: str, data: bytes, live: bytes | None = None):
        with open(os.path.join(self.bundled, name), "wb") as f:
            f.write(data)
        with open(os.path.join(self.live, name), "wb") as f:
            f.write(data if live is None else live)

    def _cfg(self, width=1024, height=438) -> dict:
        return {
            "templates": {
                "table_marker_image": "card_templates/table_marker.png",
                "max_win_marker_image": "card_templates/ui_max_win.png",
                "capture_client_width": width,
                "capture_client_height": height,
            }
        }

    def test_all_bundled_gets_synced(self):
        self._write("table_marker.png", b"aaa")
        self._write("ui_max_win.png", b"bbb")
        cfg = self._cfg()
        self.assertTrue(config_mod._sync_bundled_marker_size(cfg))
        self.assertEqual(cfg["templates"]["capture_client_width"], BUNDLED_MARKER_WIDTH)
        self.assertEqual(cfg["templates"]["capture_client_height"], BUNDLED_MARKER_HEIGHT)

    def test_one_own_capture_stops_the_whole_thing(self):
        """使用者自己抓的那個數字是對的，蓋掉會把他的樣板全部縮放錯。"""
        self._write("table_marker.png", b"aaa", live=b"my own screenshot")
        self._write("ui_max_win.png", b"bbb")
        cfg = self._cfg(width=1600, height=900)
        self.assertFalse(config_mod._sync_bundled_marker_size(cfg))
        self.assertEqual(cfg["templates"]["capture_client_width"], 1600)

    def test_already_correct_is_a_no_op(self):
        self._write("table_marker.png", b"aaa")
        cfg = self._cfg(width=BUNDLED_MARKER_WIDTH, height=BUNDLED_MARKER_HEIGHT)
        self.assertFalse(config_mod._sync_bundled_marker_size(cfg),
                         "沒有變動卻回報有變動，會害 load_config 每次都存檔")

    def test_marker_source_mix_reports_both_sides(self):
        self._write("table_marker.png", b"aaa", live=b"my own screenshot")
        self._write("ui_max_win.png", b"bbb")
        bundled, own = config_mod.marker_source_mix(self._cfg())
        self.assertEqual(bundled, ["ui_max_win.png"])
        self.assertEqual(own, ["table_marker.png"])


if __name__ == "__main__":
    unittest.main()
