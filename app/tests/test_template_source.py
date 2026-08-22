"""卡牌樣板隨程式發布：主機吃本機蒐集的，其他電腦吃內建的。

需求（2026-08-22）：「不會保留使用者本地端的卡牌樣式，會吃我這邊的資料」——
其他電腦不該再用自己蒐集的點數/花色樣板，一律用開發者打包進 defaults\\parts\\
的那一套（隨程式更新）。主機（旁邊有原始碼專案的那份安裝）維持原行為：
GUI 蒐集立即生效、繼續累積，因為那正是發布的資料來源。

三個要守住的性質：

1. **主機判斷要準**：直接跑原始碼＝主機；exe 住在原始碼樹裡（往上兩層找得到
   packaging\\build_exe.py）＝主機；zip/安裝檔裝的副本＝其他電腦。
2. **其他電腦完全不吃本機樣板**——連「本機有、內建沒有」的標籤也不吃
   （那正是「使用者自己亂抓」會混進來的洞）。
3. **不刪任何本機檔案**：只是不用。`card_template_source` = "local" 隨時可以
   把行為切回來。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import cv2  # noqa: E402

from src import paths as paths_mod  # noqa: E402
from src import recognize  # noqa: E402
from src.cardparts import PART_SIZES  # noqa: E402


def blob(size, seed=0):
    rng = np.random.default_rng(seed)
    big = (rng.random((size[1] * 2, size[0] * 2)) > 0.4).astype(np.uint8) * 255
    return cv2.resize(big, size, interpolation=cv2.INTER_AREA)


def write_part(folder, fname, seed):
    kind = fname.split("_")[0]
    cv2.imwrite(os.path.join(folder, fname), blob(PART_SIZES[kind], seed))


class TestMasterDetection(unittest.TestCase):
    def test_running_from_source_is_master(self):
        # 測試本身就是從原始碼跑的（沒有 frozen）
        self.assertTrue(paths_mod.is_master_install())

    def test_frozen_inside_the_source_tree_is_master(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe_dir = os.path.join(tmp, "app", "dist", "HoloTool")
            os.makedirs(exe_dir)
            os.makedirs(os.path.join(tmp, "app", "packaging"))
            with open(os.path.join(tmp, "app", "packaging", "build_exe.py"), "w") as f:
                f.write("# probe")
            with patch.object(sys, "frozen", True, create=True), \
                    patch.object(sys, "executable",
                                 os.path.join(exe_dir, "HoloTool.exe")):
                self.assertTrue(paths_mod.is_master_install())

    def test_frozen_standalone_install_is_not_master(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe_dir = os.path.join(tmp, "HoloTool")   # 例如 D:\HoloTool
            os.makedirs(exe_dir)
            with patch.object(sys, "frozen", True, create=True), \
                    patch.object(sys, "executable",
                                 os.path.join(exe_dir, "HoloTool.exe")):
                self.assertFalse(paths_mod.is_master_install())


class TestResolvePartSource(unittest.TestCase):
    def test_auto_on_master_uses_local_collection(self):
        with patch.object(recognize, "is_master_install", return_value=True):
            source, bundled, mode = recognize.resolve_part_source({})
        self.assertEqual(mode, "local")
        self.assertEqual(source, recognize.parts_dir())
        self.assertEqual(bundled, recognize.default_parts_dir())

    def test_auto_on_a_plain_install_uses_the_bundle(self):
        with patch.object(recognize, "is_master_install", return_value=False):
            source, bundled, mode = recognize.resolve_part_source({})
        self.assertEqual(mode, "bundled")
        self.assertEqual(source, recognize.default_parts_dir())
        self.assertIsNone(bundled)

    def test_explicit_override_beats_detection(self):
        with patch.object(recognize, "is_master_install", return_value=False):
            _s, _b, mode = recognize.resolve_part_source(
                {"card_template_source": "local"})
            self.assertEqual(mode, "local")
        with patch.object(recognize, "is_master_install", return_value=True):
            _s, _b, mode = recognize.resolve_part_source(
                {"card_template_source": "bundled"})
            self.assertEqual(mode, "bundled")

    def test_garbage_value_falls_back_to_auto(self):
        with patch.object(recognize, "is_master_install", return_value=False):
            _s, _b, mode = recognize.resolve_part_source(
                {"card_template_source": "???"})
        self.assertEqual(mode, "bundled")


class TestBundledModeIgnoresLocalFiles(unittest.TestCase):
    """其他電腦：本機蒐集的樣板完全不參與比對（但檔案原封不動）。"""

    def _load(self, master: bool, cfg=None):
        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, "card_templates", "parts")
            bundled = os.path.join(tmp, "defaults", "parts")
            os.makedirs(local)
            os.makedirs(bundled)
            write_part(local, "rank_5_1.png", seed=1)    # 本機才有的標籤
            write_part(bundled, "rank_A_1.png", seed=2)  # 內建才有的標籤
            # 正常安裝的狀態：prepare_runtime 會把內建樣板補進 card_templates
            #（overwrite=False），主機的載入來源是 card_templates 這一份。
            import shutil
            shutil.copy2(os.path.join(bundled, "rank_A_1.png"),
                         os.path.join(local, "rank_A_1.png"))
            with patch.object(recognize, "parts_dir", return_value=local), \
                    patch.object(recognize, "default_parts_dir", return_value=bundled), \
                    patch.object(recognize, "is_master_install", return_value=master):
                loaded = recognize.load_part_templates(cfg=cfg)
            local_files = sorted(os.listdir(local))
        return loaded, local_files

    def test_plain_install_sees_only_the_bundle(self):
        loaded, local_files = self._load(master=False)
        self.assertIn("A", loaded["rank"])
        self.assertNotIn("5", loaded["rank"],
                         "其他電腦竟然吃到了本機蒐集的樣板")
        self.assertEqual(local_files, ["rank_5_1.png", "rank_A_1.png"],
                         "本機檔案不可以被動到（只是不用，不是刪掉）")

    def test_master_still_sees_its_own_collection(self):
        loaded, _ = self._load(master=True)
        self.assertIn("5", loaded["rank"])
        self.assertIn("A", loaded["rank"])   # 內建墊背照舊

    def test_local_override_reenables_collection_on_a_plain_install(self):
        loaded, _ = self._load(master=False,
                               cfg={"card_template_source": "local"})
        self.assertIn("5", loaded["rank"])


if __name__ == "__main__":
    unittest.main()
