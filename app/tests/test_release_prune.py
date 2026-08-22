"""發版產物的自動清理：一版 76 MB，不清會長到好幾 GB。

每發一版，`app\\dist\\` 就多一個整包 zip（76 MB）＋差分包；`release.bat`
又會在磁碟根目錄留一份 `holotool-test-<版本>\\`（約 120 MB 的完整 build）。
差分功能只需要「最近的上一版」當基準，所以各保留最近兩版就夠了。

清理最怕誤刪，所以規則收得很緊：
  · 整包：檔名必須是 HoloTool-<版本>.zip，保留版本最新的 N 個
  · 差分：目標版本不在保留名單裡才刪
  · 測試資料夾：名稱要能解析出版本、資料夾裡要真的有 HoloTool.exe
  · 剛打包出來的一定是最新版 → 永遠不會被清掉
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGING = os.path.join(ROOT, "packaging")
for path in (ROOT, PACKAGING):
    if path not in sys.path:
        sys.path.insert(0, path)

import make_release  # noqa: E402


def _touch(folder, name, payload=b"x"):
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(payload)
    return path


class TestPruneOldReleases(unittest.TestCase):
    def test_keeps_the_newest_two_and_their_shas(self):
        with tempfile.TemporaryDirectory() as tmp:
            for version in ("1.0.19", "1.0.20", "1.0.21", "1.0.22"):
                _touch(tmp, f"HoloTool-{version}.zip")
                _touch(tmp, f"HoloTool-{version}.zip.sha256")
            removed = make_release.prune_old_releases(tmp, keep=2)
            left = sorted(os.listdir(tmp))
            self.assertEqual(left, [
                "HoloTool-1.0.21.zip", "HoloTool-1.0.21.zip.sha256",
                "HoloTool-1.0.22.zip", "HoloTool-1.0.22.zip.sha256",
            ])
            self.assertEqual(len(removed), 4)

    def test_patches_follow_their_target_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            for version in ("1.0.21", "1.0.22"):
                _touch(tmp, f"HoloTool-{version}.zip")
            _touch(tmp, "HoloTool-1.0.22-patch-from-1.0.21.zip")       # 目標還在 → 留
            _touch(tmp, "HoloTool-1.0.20-patch-from-1.0.19.zip")       # 目標已清 → 刪
            _touch(tmp, "HoloTool-1.0.20-patch-from-1.0.19.zip.sha256")
            make_release.prune_old_releases(tmp, keep=2)
            left = sorted(os.listdir(tmp))
            self.assertIn("HoloTool-1.0.22-patch-from-1.0.21.zip", left)
            self.assertNotIn("HoloTool-1.0.20-patch-from-1.0.19.zip", left)
            self.assertNotIn("HoloTool-1.0.20-patch-from-1.0.19.zip.sha256", left)

    def test_unrelated_files_are_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            for version in ("1.0.20", "1.0.21", "1.0.22"):
                _touch(tmp, f"HoloTool-{version}.zip")
            keepers = ("HoloToolSetup.exe", "notes.txt", "HoloTool-something.txt")
            for name in keepers:
                _touch(tmp, name)
            make_release.prune_old_releases(tmp, keep=2)
            left = os.listdir(tmp)
            for name in keepers:
                self.assertIn(name, left)
            self.assertNotIn("HoloTool-1.0.20.zip", left)

    def test_keep_less_than_one_does_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            _touch(tmp, "HoloTool-1.0.20.zip")
            self.assertEqual(make_release.prune_old_releases(tmp, keep=0), [])
            self.assertTrue(os.listdir(tmp))


class TestPruneTestCopies(unittest.TestCase):
    def _make_copy(self, base, version, with_exe=True):
        folder = os.path.join(base, f"holotool-test-{version}")
        os.makedirs(folder)
        if with_exe:
            _touch(folder, "HoloTool.exe")
        return folder

    def test_keeps_newest_two_builds(self):
        with tempfile.TemporaryDirectory() as tmp:
            for version in ("1.0.19", "1.0.20", "1.0.21"):
                self._make_copy(tmp, version)
            removed = make_release.prune_test_copies(keep=2, neighbours=tmp)
            self.assertEqual(removed, ["holotool-test-1.0.19"])
            left = sorted(os.listdir(tmp))
            self.assertEqual(left, ["holotool-test-1.0.20", "holotool-test-1.0.21"])

    def test_never_touches_folders_that_are_not_builds(self):
        """沒有 HoloTool.exe 的不敢刪 —— 那可能是使用者自己的資料夾。"""
        with tempfile.TemporaryDirectory() as tmp:
            for version in ("1.0.20", "1.0.21", "1.0.22"):
                self._make_copy(tmp, version)
            odd = self._make_copy(tmp, "1.0.1", with_exe=False)   # 不是 build
            weird = os.path.join(tmp, "holotool-test-notes")       # 版本解析不出來
            os.makedirs(weird)
            make_release.prune_test_copies(keep=2, neighbours=tmp)
            self.assertTrue(os.path.isdir(odd))
            self.assertTrue(os.path.isdir(weird))
            self.assertFalse(os.path.isdir(os.path.join(tmp, "holotool-test-1.0.20")))

    def test_missing_folder_is_not_an_error(self):
        self.assertEqual(
            make_release.prune_test_copies(keep=2, neighbours="/no/such/place"), [])


if __name__ == "__main__":
    unittest.main()
