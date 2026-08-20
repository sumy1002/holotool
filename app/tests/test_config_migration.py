"""設定檔升級：程式改好了，參數不可以還停在舊值。

背景：使用者的 config.json 是自己校準過的，不能整個覆蓋。但 _deep_merge 會讓
「舊設定檔裡的舊值」蓋掉「程式的新預設」—— 於是辨識演算法明明重新調校過，
part_min_score 還卡在 0.80，所有 0.72~0.79 分的正確答案全被擋掉，
log 就會出現「點數 2=0.77 3=0.66 ←分數未達 0.8」這種正解領先卻被擋掉的情形。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import config as cfg_mod  # noqa: E402


class TestConfigMigration(unittest.TestCase):
    def _load_with(self, stored: dict) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(stored, f)
            with patch.object(cfg_mod, "CONFIG_PATH", path):
                loaded = cfg_mod.load_config()
                with open(path, encoding="utf-8") as f:
                    on_disk = json.load(f)
        return loaded, on_disk

    def test_retuned_fields_are_forced_to_the_new_default(self):
        stored = {
            "config_version": 2,
            "part_min_score": 0.80,
            "part_min_margin": 0.03,
            "capture_client_width": 1843,
            "capture_client_height": 778,
        }
        loaded, on_disk = self._load_with(stored)
        self.assertEqual(loaded["part_min_score"], cfg_mod.DEFAULT_CONFIG["part_min_score"])
        self.assertEqual(loaded["part_min_margin"], cfg_mod.DEFAULT_CONFIG["part_min_margin"])
        self.assertEqual(loaded["config_version"], cfg_mod.CONFIG_VERSION)
        # 升級結果要寫回檔案，下次開啟不用再跑一次
        self.assertEqual(on_disk["part_min_score"], cfg_mod.DEFAULT_CONFIG["part_min_score"])

    def test_calibration_is_never_touched_by_the_upgrade(self):
        stored = {
            "config_version": 2,
            "part_min_score": 0.80,
            "capture_client_width": 1843,
            "capture_client_height": 778,
            "points": {"high_button": {"x": 0.1234, "y": 0.5678}},
            "regions": {"table_marker": {"x": 0.11, "y": 0.22, "w": 0.33, "h": 0.44}},
        }
        loaded, _ = self._load_with(stored)
        self.assertEqual(loaded["capture_client_width"], 1843)
        self.assertEqual(loaded["points"]["high_button"], {"x": 0.1234, "y": 0.5678})
        self.assertEqual(loaded["regions"]["table_marker"],
                         {"x": 0.11, "y": 0.22, "w": 0.33, "h": 0.44})

    def test_already_current_config_is_left_alone(self):
        stored = {
            "config_version": cfg_mod.CONFIG_VERSION,
            # 使用者升級後自己再調過的值，不可以被搶回預設
            "part_min_score": 0.66,
        }
        loaded, _ = self._load_with(stored)
        self.assertEqual(loaded["part_min_score"], 0.66)

    def test_new_keys_arrive_without_wiping_old_ones(self):
        loaded, _ = self._load_with({"config_version": 2, "capture_client_width": 1843})
        self.assertIn("draw_result_wait_sec", loaded)
        self.assertEqual(loaded["capture_client_width"], 1843)


if __name__ == "__main__":
    unittest.main()
