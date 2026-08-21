"""差分更新包：76 MB 的更新裡有 99.9% 是一模一樣的東西。

## 為什麼要做（2026-08-22 量的）

| | |
|---|---|
| `git clone` 整個 repo | 909 KB、1.3 秒 |
| `HoloTool-1.0.19.zip` | **76.5 MB** |
| 1.0.13 → 1.0.19 六個版本，zip 大小總共只差 | **54 KB** |

使用者回報「拉更新超級慢，每一台電腦都一樣」。慢的不是 git，是那 76 MB ——
而且每次都在重抓一模一樣的 numpy / opencv / tcl-tk / python3xx.dll。
真正會變的只有 `HoloTool.exe`（裡面包著程式碼）跟 `base_library.zip`。

## 基準線＝「上一版的東西」，兩種來源都吃

不另外維護一份清單檔 —— 少一個「這個檔不要刪」的坑，而且對**已經發出去的
舊版**也能回溯生效。找基準的順序：

1. `app\\dist\\HoloTool-<舊版>.zip` —— 上一次發版的整包
2. `<磁碟>\\holotool-test-<舊版>\\` —— `release.bat` 發版前留的那份 build

第 2 條不是備胎而是必要的：一個 zip 76 MB，**使用者清掉 dist 裡的舊 zip 是
很正常的事**（實際發生過，1.0.21 那次就因此沒產出差分包）。
資料夾那條一定要套用跟打包相同的排除規則，否則使用者的 `config\\`、
`card_templates\\` 會被算成「有變動」而塞進差分包。

比對用 CRC32 + 大小（zip 的中央目錄本來就存著），不解壓、不算 SHA256 ——
這裡要回答的是「我自己這兩次打包一不一樣」，不是防篡改。

## 設計上唯一重要的決定：基底版本寫在**檔名**裡

`HoloTool-<新版>-patch-from-<舊版>.zip`

updater 在**還沒下載任何東西**的時候就要能判斷「這包我用不用得上」——
如果要先下載一個 metadata 才知道，就等於多一個來回，而省時間正是重點。
對不上就安靜地走整包那條路。

## 安全性

差分包套上去的前提是「你現在正好是那個基底版本」。拿錯基底會生出混版安裝，
比不更新還糟。所以有兩道關卡：**檔名比對**（選的時候）＋
**`patch.json` 裡的 base 再比一次**（解開之後）。任何一關不過就整條放棄、
改抓整包 —— 而放棄的時候安裝目錄一個位元組都還沒被動到。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import updater  # noqa: E402
from src.updater import PATCH_MARKER, SENTINEL, UpdateError, extract_update  # noqa: E402
from src.version import (  # noqa: E402
    asset_name,
    parse_patch_asset_name,
    patch_asset_name,
)


class TestAssetNaming(unittest.TestCase):
    def test_round_trip(self):
        name = patch_asset_name("1.0.21", "1.0.20")
        self.assertEqual(name, "HoloTool-1.0.21-patch-from-1.0.20.zip")
        self.assertEqual(parse_patch_asset_name(name), ("1.0.21", "1.0.20"))

    def test_a_full_zip_is_not_mistaken_for_a_patch(self):
        for name in (asset_name("1.0.21"), "HoloToolSetup.exe", "random.zip",
                     "HoloTool-1.0.21.zip.sha256"):
            self.assertIsNone(parse_patch_asset_name(name), name)


def _release_json(version: str, assets: list) -> dict:
    return {
        "tag_name": f"v{version}",
        "body": "",
        "html_url": "https://example.invalid/r",
        "assets": [{"name": n, "size": s,
                    "browser_download_url": f"https://example.invalid/{n}"}
                   for n, s in assets],
    }


class TestAssetSelection(unittest.TestCase):
    """整包與差分包長得很像（都是 `HoloTool-*.zip`），選錯很致命。"""

    def _parse(self, assets, installed="1.0.20"):
        data = _release_json("1.0.21", assets)
        info = updater._parse_release(data)
        # _parse_release 會用真正的 __version__，測試裡要能指定「目前安裝的版本」
        info.patch_url = info.patch_name = info.patch_base = ""
        info.patch_size = 0
        patches = [a for a in data["assets"]
                   if parse_patch_asset_name(a["name"]) is not None]
        updater._attach_patch(info, patches, data["assets"], "", installed=installed)
        return info

    def test_the_patch_is_never_chosen_as_the_full_package(self):
        """只有差分包時，整包**不能**退而求其次挑到它。

        挑錯的話解開之後最上層沒有 HoloTool.exe，會以一個很難懂的錯誤收場。
        """
        with self.assertRaises(UpdateError):
            updater._parse_release(_release_json(
                "1.0.21", [(patch_asset_name("1.0.21", "1.0.20"), 8_000_000)]))

    def test_both_are_picked_up(self):
        info = self._parse([(asset_name("1.0.21"), 76_000_000),
                            (patch_asset_name("1.0.21", "1.0.20"), 8_000_000)])
        self.assertEqual(info.zip_name, asset_name("1.0.21"))
        self.assertTrue(info.has_patch)
        self.assertEqual(info.patch_base, "1.0.20")
        self.assertEqual(info.download_size, 8_000_000)

    def test_a_patch_for_a_different_base_is_ignored(self):
        """跨了好幾版的人只能抓整包。"""
        info = self._parse([(asset_name("1.0.21"), 76_000_000),
                            (patch_asset_name("1.0.21", "1.0.19"), 8_000_000)],
                           installed="1.0.16")
        self.assertFalse(info.has_patch)
        self.assertEqual(info.download_size, 76_000_000)

    def test_no_patch_at_all_still_works(self):
        info = self._parse([(asset_name("1.0.21"), 76_000_000)])
        self.assertFalse(info.has_patch)
        self.assertEqual(info.download_size, 76_000_000)


class TestWrongVersionAssetIsRefused(unittest.TestCase):
    """上傳錯版本的整包，寧可中止也不要降級。

    ## 實機事故（2026-08-22）

    `v1.0.22` 的資產裡放的是 `HoloTool-1.0.20.zip`（上傳時挑錯檔）。
    舊的退路「開頭是 HoloTool- 的第一個 zip」會挑到它，然後：

    1. 把 1.0.20 的檔案蓋到使用者的安裝上 —— **降級**
    2. exe 之後自報 1.0.20，又看到「有 1.0.22」→ **無限更新迴圈**
    3. `.sha256` 也是配對的，驗證會通過，**全程沒有任何錯誤訊息**

    只有 1.0.21 的人會安然無恙（他們走差分那條路）—— 那更糟，
    問題只發生在一部分機器上，最難查。
    """

    def test_a_zip_for_another_version_is_not_used(self):
        with self.assertRaises(UpdateError) as ctx:
            updater._parse_release(_release_json(
                "1.0.22", [(asset_name("1.0.20"), 76_000_000)]))
        message = str(ctx.exception)
        self.assertIn(asset_name("1.0.22"), message)   # 該有的是哪一個
        self.assertIn(asset_name("1.0.20"), message)   # 實際看到的是哪一個
        self.assertIn("降級", message)

    def test_the_patch_alone_does_not_rescue_a_wrong_full_zip(self):
        """差分包對得上不代表整包可以將就 —— 跨版的人還是會被降級。"""
        with self.assertRaises(UpdateError):
            updater._parse_release(_release_json("1.0.22", [
                (asset_name("1.0.20"), 76_000_000),
                (patch_asset_name("1.0.22", "1.0.21"), 6_700_000),
            ]))

    def test_the_correct_zip_still_works(self):
        info = updater._parse_release(_release_json(
            "1.0.22", [(asset_name("1.0.20"), 1), (asset_name("1.0.22"), 2)]))
        self.assertEqual(info.zip_name, asset_name("1.0.22"))


class TestPatchExtraction(unittest.TestCase):
    def _zip(self, tmp: str, names: list, marker: dict | None) -> str:
        path = os.path.join(tmp, "p.zip")
        with zipfile.ZipFile(path, "w") as zf:
            for name in names:
                zf.writestr(name, b"x")
            if marker is not None:
                zf.writestr(PATCH_MARKER, json.dumps(marker))
        return path

    def test_a_patch_does_not_need_the_exe_at_the_top(self):
        """差分包只含變動的檔案 —— 不能沿用整包那條「一定要有 HoloTool.exe」。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = self._zip(tmp, ["app/src/bot.pyc"], {"base": "1.0.20"})
            out = extract_update(path, os.path.join(tmp, "stage"),
                                 expect_patch_from="1.0.20")
            self.assertTrue(os.path.exists(os.path.join(out, "app", "src", "bot.pyc")))

    def test_the_marker_itself_is_not_copied_into_the_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._zip(tmp, [SENTINEL], {"base": "1.0.20"})
            out = extract_update(path, os.path.join(tmp, "stage"),
                                 expect_patch_from="1.0.20")
            self.assertFalse(os.path.exists(os.path.join(out, PATCH_MARKER)))

    def test_the_wrong_base_is_refused(self):
        """套錯基底會生出混版安裝，比不更新還糟。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = self._zip(tmp, [SENTINEL], {"base": "1.0.11"})
            with self.assertRaises(UpdateError) as ctx:
                extract_update(path, os.path.join(tmp, "stage"),
                               expect_patch_from="1.0.20")
            self.assertIn("1.0.11", str(ctx.exception))

    def test_a_missing_marker_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._zip(tmp, [SENTINEL], None)
            with self.assertRaises(UpdateError):
                extract_update(path, os.path.join(tmp, "stage"),
                               expect_patch_from="1.0.20")

    def test_a_refused_patch_leaves_nothing_behind(self):
        """放棄的時候暫存區要清乾淨，不能留半套讓下一步誤用。"""
        with tempfile.TemporaryDirectory() as tmp:
            stage = os.path.join(tmp, "stage")
            path = self._zip(tmp, [SENTINEL], {"base": "1.0.11"})
            with self.assertRaises(UpdateError):
                extract_update(path, stage, expect_patch_from="1.0.20")
            self.assertFalse(os.path.exists(stage))

    def test_the_full_package_still_requires_the_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._zip(tmp, ["app/src/bot.pyc"], None)
            with self.assertRaises(UpdateError):
                extract_update(path, os.path.join(tmp, "stage"))


class TestBaselineDiscovery(unittest.TestCase):
    """一個 zip 76 MB，使用者清掉 `app\\dist\\` 裡的舊 zip 很正常。

    實際發生過：1.0.21 那次 dist 裡只剩當版的 zip，差分包因此沒有產出。
    所以 `release.bat` 留在隔壁的 `holotool-test-<舊版>\\` 也要算數。
    """

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "packaging"))
        import make_release
        self.mr = make_release
        self.tmp = tempfile.mkdtemp()
        self.project = os.path.join(self.tmp, "holotool")
        self.dist = os.path.join(self.project, "dist")
        os.makedirs(self.dist)
        self._saved = make_release.PROJECT
        make_release.PROJECT = self.project

    def tearDown(self):
        self.mr.PROJECT = self._saved
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _zip(self, version: str):
        path = os.path.join(self.dist, asset_name(version))
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(SENTINEL, b"x")
        return path

    def _folder(self, version: str):
        path = os.path.join(self.tmp, f"holotool-test-{version}")
        os.makedirs(os.path.join(path, "app"))
        with open(os.path.join(path, SENTINEL), "wb") as f:
            f.write(b"x")
        return path

    def test_a_previous_zip_is_used(self):
        self._zip("1.0.20")
        base, reader, path = self.mr._previous_bundle(self.dist, "1.0.21")
        self.assertEqual(base, "1.0.20")
        self.assertIs(reader, self.mr._zip_fingerprints)

    def test_the_release_bat_folder_is_used_when_the_zips_are_gone(self):
        self._folder("1.0.20")
        base, reader, path = self.mr._previous_bundle(self.dist, "1.0.21")
        self.assertEqual(base, "1.0.20")
        self.assertIs(reader, self.mr._dir_fingerprints)

    def test_the_newest_previous_version_wins(self):
        self._folder("1.0.18")
        self._zip("1.0.20")
        base, _reader, _path = self.mr._previous_bundle(self.dist, "1.0.21")
        self.assertEqual(base, "1.0.20")

    def test_the_current_and_newer_versions_are_never_the_baseline(self):
        self._zip("1.0.21")
        self._zip("1.0.22")
        self.assertIsNone(self.mr._previous_bundle(self.dist, "1.0.21"))

    def test_a_patch_zip_is_never_the_baseline(self):
        path = os.path.join(self.dist, patch_asset_name("1.0.20", "1.0.19"))
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(SENTINEL, b"x")
        self.assertIsNone(self.mr._previous_bundle(self.dist, "1.0.21"))

    def test_user_data_in_the_old_folder_is_ignored(self):
        """那個資料夾是完整的安裝，裡面有校準與樣板 —— 一個都不能算進來。"""
        folder = self._folder("1.0.20")
        os.makedirs(os.path.join(folder, "app", "config"))
        with open(os.path.join(folder, "app", "config", "config.json"), "w") as f:
            f.write("{}")
        prints = self.mr._dir_fingerprints(folder)
        self.assertNotIn("app/config/config.json", prints)
        self.assertIn(SENTINEL, prints)


if __name__ == "__main__":
    unittest.main()
