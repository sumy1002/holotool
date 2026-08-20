"""自動更新：**絕對不可以動到使用者的校準與樣板**，而且不能覆蓋執行中的 exe。

這個檔案裡的每一個測試都對應一個具體的災難：

  · 版本比較看不懂 → 對舊版跳「有新版」或對新版跳「已是最新」
  · 抓錯資產       → 下載到別的 zip 甚至安裝檔
  · 校驗跳過       → 半截檔案被解開覆蓋程式
  · 解壓沒過濾     → 更新包裡的 config/ 蓋掉逐格校準的座標（就是那個坑）
  · 備份只留一代   → 下一次動作把唯一那份好的蓋掉，救不回來
  · 置換腳本寫錯   → robocopy /PURGE 把整個資料目錄清空
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import updater  # noqa: E402
from src import version as ver  # noqa: E402


class TestVersionCompare(unittest.TestCase):
    def test_parses_common_shapes(self):
        self.assertEqual(ver.version_tuple("v1.2.3"), (1, 2, 3))
        self.assertEqual(ver.version_tuple("1.2"), (1, 2, 0))
        self.assertEqual(ver.version_tuple("1.2.3-beta1"), (1, 2, 3))
        self.assertEqual(ver.version_tuple("1.0.0"), (1, 0, 0))

    def test_newer_and_older(self):
        self.assertTrue(ver.is_newer("1.0.1", "1.0.0"))
        self.assertTrue(ver.is_newer("v1.1.0", "1.0.9"))
        self.assertTrue(ver.is_newer("2.0.0", "1.99.99"))
        self.assertFalse(ver.is_newer("1.0.0", "1.0.0"))
        self.assertFalse(ver.is_newer("1.0.0", "1.0.1"))

    def test_double_digit_segments_are_not_compared_as_text(self):
        # 字串比較會說 "1.9" > "1.10"，那是錯的
        self.assertTrue(ver.is_newer("1.10.0", "1.9.0"))
        self.assertFalse(ver.is_newer("1.9.0", "1.10.0"))

    def test_garbage_never_reports_an_update(self):
        """看不懂就當作沒有新版 —— 反過來會叫使用者去下載一包不知道是什麼的東西。"""
        for junk in ("", "latest", "v", "release-2026", None):
            self.assertFalse(ver.is_newer(junk, "1.0.0"), junk)


class TestReleaseParsing(unittest.TestCase):
    def _release(self, **kwargs) -> dict:
        data = {
            "tag_name": "v1.2.0",
            "body": "修好了花色辨識",
            "html_url": "https://example.invalid/releases/v1.2.0",
            "assets": [],
        }
        data.update(kwargs)
        return data

    @staticmethod
    def _asset(name: str, size: int = 1234) -> dict:
        return {"name": name, "size": size,
                "browser_download_url": f"https://example.invalid/{name}"}

    def test_prefers_the_exactly_named_zip(self):
        data = self._release(assets=[
            self._asset("other.zip"),
            self._asset("HoloTool-1.2.0.zip"),
            self._asset("HoloToolSetup.exe"),
        ])
        info = updater._parse_release(data)
        self.assertEqual(info.zip_name, "HoloTool-1.2.0.zip")
        self.assertEqual(info.version, "1.2.0")
        self.assertEqual(info.tag, "v1.2.0")

    def test_never_picks_an_exe(self):
        """安裝檔跟更新包是兩回事，抓錯的話置換流程會整個對不上。"""
        data = self._release(assets=[
            self._asset("HoloToolSetup.exe"),
            self._asset("HoloTool-1.2.0.zip"),
        ])
        self.assertTrue(updater._parse_release(data).zip_name.endswith(".zip"))

    def test_missing_zip_is_an_error(self):
        data = self._release(assets=[self._asset("HoloToolSetup.exe")])
        with self.assertRaises(updater.UpdateError):
            updater._parse_release(data)

    def test_sha256_can_come_from_the_release_notes(self):
        sha = "a" * 64
        data = self._release(body=f"修好了花色辨識\n\nsha256: {sha}\n",
                             assets=[self._asset("HoloTool-1.2.0.zip")])
        self.assertEqual(updater._parse_release(data).sha256, sha)

    def test_bad_sha_in_notes_is_ignored_rather_than_trusted(self):
        data = self._release(body="sha256: 不是雜湊",
                             assets=[self._asset("HoloTool-1.2.0.zip")])
        self.assertEqual(updater._parse_release(data).sha256, "")

    def test_no_tag_is_an_error(self):
        with self.assertRaises(updater.UpdateError):
            updater._parse_release(self._release(tag_name=""))


class TestProtectedPaths(unittest.TestCase):
    def test_user_data_dirs_are_recognised_at_both_layouts(self):
        for name in (
            "config/config.json",                 # 舊版攤平的安裝
            "app/config/config.json",             # 新版
            "app/card_templates/parts/suit_S_1.png",
            "card_templates/table_marker.png",
            "app/data/stats_2026-08-20.json",
            "app/logs/bot_2026-08-20.log",
            "app/debug_captures/purple_start.png",
        ):
            self.assertTrue(updater._is_protected(name), name)

    def test_program_content_is_not_protected(self):
        for name in (
            "HoloTool.exe",
            "app/base_library.zip",
            "app/defaults/parts/rank_2_1.png",     # 內建樣板是程式內容
            "app/defaults/ui/table_marker.png",
            "app/cv2/cv2.pyd",
        ):
            self.assertFalse(updater._is_protected(name), name)


class TestExtract(unittest.TestCase):
    def _zip(self, tmp: str, entries: dict) -> str:
        path = os.path.join(tmp, "update.zip")
        with zipfile.ZipFile(path, "w") as zf:
            for name, content in entries.items():
                zf.writestr(name, content)
        return path

    def test_extracts_program_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = self._zip(tmp, {
                "HoloTool.exe": "new exe",
                "app/python3.dll": "dll",
                "app/defaults/parts/rank_2_b1.png": "png",
            })
            out = updater.extract_update(src, os.path.join(tmp, "stage"))
            self.assertTrue(os.path.exists(os.path.join(out, "HoloTool.exe")))
            self.assertTrue(os.path.exists(
                os.path.join(out, "app", "defaults", "parts", "rank_2_b1.png")))

    def test_user_data_inside_the_zip_is_dropped(self):
        """這是那個坑的最後一道防線。

        正常的更新包裡根本不會有 config/ 與 card_templates/（make_release.py
        排除掉了，而且會自我檢查）。萬一有人手動打了一包錯的上去，
        解壓這一層也要把它擋掉，不能讓它有機會走到 robocopy。
        """
        with tempfile.TemporaryDirectory() as tmp:
            src = self._zip(tmp, {
                "HoloTool.exe": "new exe",
                "app/config/config.json": '{"regions": "壞掉的預設值"}',
                "app/card_templates/parts/suit_S_1.png": "覆蓋你的花色樣板",
                "app/data/stats.json": "{}",
            })
            out = updater.extract_update(src, os.path.join(tmp, "stage"))
            self.assertFalse(os.path.exists(os.path.join(out, "app", "config")))
            self.assertFalse(os.path.exists(os.path.join(out, "app", "card_templates")))
            self.assertFalse(os.path.exists(os.path.join(out, "app", "data")))
            self.assertTrue(os.path.exists(os.path.join(out, "HoloTool.exe")))

    def test_path_traversal_is_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = self._zip(tmp, {
                "HoloTool.exe": "new exe",
                "../evil.txt": "跳出暫存區",
                "../../evil2.txt": "跳更遠",
            })
            stage = os.path.join(tmp, "stage")
            updater.extract_update(src, stage)
            self.assertFalse(os.path.exists(os.path.join(tmp, "evil.txt")))
            self.assertFalse(os.path.exists(
                os.path.join(os.path.dirname(tmp), "evil2.txt")))

    def test_zip_without_the_exe_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = self._zip(tmp, {"readme.txt": "這不是更新包"})
            stage = os.path.join(tmp, "stage")
            with self.assertRaises(updater.UpdateError):
                updater.extract_update(src, stage)
            # 失敗時不留垃圾
            self.assertFalse(os.path.isdir(stage))


class TestVerify(unittest.TestCase):
    def _good_zip(self, tmp: str) -> str:
        path = os.path.join(tmp, "HoloTool-1.2.0.zip")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("HoloTool.exe", "new exe")
        return path

    def test_matching_sha_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._good_zip(tmp)
            actual = updater.sha256_of(path)
            self.assertEqual(updater.verify_download(path, actual), actual)

    def test_wrong_sha_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._good_zip(tmp)
            with self.assertRaises(updater.UpdateError):
                updater.verify_download(path, "b" * 64)

    def test_uppercase_sha_still_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._good_zip(tmp)
            actual = updater.sha256_of(path)
            self.assertEqual(updater.verify_download(path, actual.upper()), actual)

    def test_not_a_zip_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "broken.zip")
            with open(path, "wb") as f:
                f.write("這不是 zip".encode("utf-8"))
            with self.assertRaises(updater.UpdateError):
                updater.verify_download(path, "")

    def test_zip_without_the_exe_is_refused_even_with_a_matching_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "wrong.zip")
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("readme.txt", "hi")
            with self.assertRaises(updater.UpdateError):
                updater.verify_download(path, updater.sha256_of(path))


class TestBackup(unittest.TestCase):
    def _make_install(self, root: str) -> None:
        os.makedirs(os.path.join(root, "config"), exist_ok=True)
        os.makedirs(os.path.join(root, "card_templates", "parts"), exist_ok=True)
        os.makedirs(os.path.join(root, "logs"), exist_ok=True)
        with open(os.path.join(root, "config", "config.json"), "w",
                  encoding="utf-8") as f:
            f.write('{"regions": {"card_slots": "逐格校準過的"}}')
        with open(os.path.join(root, "card_templates", "parts", "suit_S_1.png"),
                  "wb") as f:
            f.write(b"png-bytes")
        with open(os.path.join(root, "logs", "bot.log"), "w", encoding="utf-8") as f:
            f.write("不需要備份的東西")

    def test_backup_contains_config_and_templates(self):
        with tempfile.TemporaryDirectory() as root:
            self._make_install(root)
            path = updater.backup_user_data(root=root, version="1.0.0")
            self.assertTrue(path and os.path.exists(path))
            with zipfile.ZipFile(path) as zf:
                names = {n.replace("\\", "/") for n in zf.namelist()}
            self.assertIn("config/config.json", names)
            self.assertIn("card_templates/parts/suit_S_1.png", names)
            # log 不備份（沒有價值，只會讓備份變大）
            self.assertFalse(any(n.startswith("logs/") for n in names))

    def test_nothing_to_back_up_leaves_no_empty_zip(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(updater.backup_user_data(root=root), "")
            backups = os.path.join(root, "backups")
            self.assertTrue(not os.path.isdir(backups) or not os.listdir(backups))

    def test_keeps_multiple_generations(self):
        """只留一代等於白備份 —— 下一次動作就會把唯一那份好的蓋掉。"""
        with tempfile.TemporaryDirectory() as root:
            out = os.path.join(root, "backups")
            os.makedirs(out)
            for i in range(9):
                name = f"pre-update-1.0.0-2026082{i}-120000.zip"
                with open(os.path.join(out, name), "w", encoding="utf-8") as f:
                    f.write(str(i))
            removed = updater._prune_backups(out, keep=5)
            left = sorted(os.listdir(out))
            self.assertEqual(len(left), 5)
            self.assertEqual(len(removed), 4)
            # 留下來的是「最近的五份」
            self.assertEqual(left[-1], "pre-update-1.0.0-20260828-120000.zip")
            self.assertEqual(left[0], "pre-update-1.0.0-20260824-120000.zip")


class TestApplyScript(unittest.TestCase):
    def _script(self, tmp: str) -> str:
        root = os.path.join(tmp, "HoloTool")
        stage = os.path.join(tmp, "stage")
        os.makedirs(root)
        os.makedirs(stage)
        with patch.object(updater, "log_dir", lambda: os.path.join(tmp, "logs")):
            path = updater.write_apply_script(stage, root, zip_path="", pid=4242,
                                              script_dir=tmp)
        with open(path, encoding="ascii") as f:
            return f.read()

    def test_waits_for_the_running_exe_before_touching_anything(self):
        """執行中的 exe 不能覆蓋自己 —— 必須等主程式真的結束。"""
        with tempfile.TemporaryDirectory() as tmp:
            body = self._script(tmp)
            self.assertIn("tasklist", body)
            self.assertIn("4242", body)
            # 等待迴圈一定要排在 robocopy 之前
            self.assertLess(body.index("tasklist"), body.index("robocopy"))

    def test_never_purges(self):
        """/PURGE 或 /MIR 會把「來源沒有、目的地有」的檔案刪掉 ——
        更新包裡沒有 config\\ 與 card_templates\\，等於整組清空。"""
        with tempfile.TemporaryDirectory() as tmp:
            # 只看真的會被執行的那些行；註解裡提到 /PURGE 是在說明為什麼不用它
            commands = "\n".join(
                line for line in self._script(tmp).upper().splitlines()
                if not line.strip().startswith("REM")
            )
            self.assertNotIn("/PURGE", commands)
            self.assertNotIn("/MIR", commands)

    def test_excludes_every_protected_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = self._script(tmp)
            for folder in updater.PROTECTED_DIRS:
                self.assertIn(os.path.join("app", folder), body, folder)
            self.assertIn("backups", body)

    def test_force_kills_a_process_that_will_not_close(self):
        """踩過的坑：GUI 的視窗關了、行程卻還活著，.bat 就一直等一個永遠不會
        消失的 PID，畫面停在一個什麼都不做的黑視窗上。等不到就要強制結束。"""
        with tempfile.TemporaryDirectory() as tmp:
            body = self._script(tmp)
            self.assertIn("taskkill", body)
            # 強制結束要排在複製之前，否則 exe 還鎖著自己
            self.assertLess(body.index("taskkill"), body.index("robocopy"))
            # 但也不能無限等下去
            self.assertIn("aborting, nothing changed", body)

    def test_gives_up_before_touching_anything(self):
        """真的關不掉時，放棄的那條路徑必須完全不碰安裝目錄。"""
        with tempfile.TemporaryDirectory() as tmp:
            lines = self._script(tmp).splitlines()
            abort = next(i for i, line in enumerate(lines)
                         if "aborting, nothing changed" in line)
            copy = next(i for i, line in enumerate(lines)
                        if line.strip().startswith("robocopy"))
            # 放棄的分支在複製指令之前，而且是 goto finish（跳過複製）
            self.assertLess(abort, copy)
            self.assertTrue(any("goto finish" in line
                                for line in lines[abort:copy]))

    def test_restarts_the_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = self._script(tmp)
            self.assertIn("HoloTool.exe", body)
            self.assertIn("start", body)

    def test_script_is_pure_ascii(self):
        """cmd 預設不是 UTF-8，帶中文的批次檔在某些機器上會整行解析失敗。"""
        with tempfile.TemporaryDirectory() as tmp:
            body = self._script(tmp)
            body.encode("ascii")   # 不該丟例外


class TestHardExit(unittest.TestCase):
    def test_hard_exit_really_ends_the_process(self):
        """destroy() 之後行程沒死透，更新就會卡住。這裡用子行程驗證它真的會結束。"""
        import subprocess
        code = (
            "import sys, threading, time;"
            f"sys.path.insert(0, {ROOT!r});"
            # 故意留一個不會結束的非 daemon 執行緒 —— 正常 sys.exit() 會被它卡住
            "t = threading.Thread(target=lambda: time.sleep(300), daemon=False);"
            "t.start();"
            "from src import updater;"
            "updater.hard_exit(0)"
        )
        proc = subprocess.run([sys.executable, "-c", code], timeout=30)
        self.assertEqual(proc.returncode, 0)


class TestDevMode(unittest.TestCase):
    def test_install_root_refuses_in_dev_mode(self):
        """跑原始碼時沒有 exe 可以換，寧可明確拒絕也不要亂搬檔案。"""
        with patch.object(updater.sys, "frozen", False, create=True):
            with self.assertRaises(updater.UpdateError):
                updater.install_root()


class TestCheckForUpdate(unittest.TestCase):
    def test_network_failure_is_reported_not_raised(self):
        """檢查更新失敗只該顯示一行紅字，不該讓 GUI 噴 traceback。"""
        def _boom(*_a, **_k):
            raise updater.UpdateError("連不上網路")
        with patch.object(updater, "fetch_latest_release", _boom):
            result = updater.check_for_update()
        self.assertFalse(result.available)
        self.assertIn("連不上網路", result.message)
        self.assertTrue(result.error)

    def test_same_version_reports_up_to_date(self):
        info = updater.ReleaseInfo(
            version=ver.__version__, tag=f"v{ver.__version__}", notes="",
            zip_url="https://example.invalid/x.zip", zip_name="x.zip", size=1)
        with patch.object(updater, "fetch_latest_release", lambda **_k: info):
            result = updater.check_for_update()
        self.assertFalse(result.available)
        self.assertIn("最新版", result.message)

    def test_newer_version_is_offered(self):
        info = updater.ReleaseInfo(
            version="999.0.0", tag="v999.0.0", notes="很多修正",
            zip_url="https://example.invalid/x.zip", zip_name="x.zip", size=1)
        with patch.object(updater, "fetch_latest_release", lambda **_k: info):
            result = updater.check_for_update()
        self.assertTrue(result.available)
        self.assertIn("999.0.0", result.message)


if __name__ == "__main__":
    unittest.main()
