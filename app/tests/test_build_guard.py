"""打包失敗時，訊息要直接指出原因。

2026-08-21 踩到的：`release.bat` 跑到 PyInstaller 這一步丟了「結束碼 1」，
訊息只說「往回捲一下就看得到」。實際情況是 Analysis / PYZ / EXE **全都成功**，
在最後的 COLLECT 階段才失敗 —— 因為 `--noconfirm` 要把整個 `dist\\HoloTool\\`
砍掉重建，而 HoloTool.exe 還開著，執行中的 exe / dll 刪不掉。

前面幾百行輸出早就被終端機的回捲緩衝區蓋掉了，等於一個查不出原因的失敗。
所以現在：

1. 開工前先探測 `dist\\HoloTool\\` 有沒有檔案被鎖住，有就**立刻**中止並講清楚，
   不要先花三分鐘做 Analysis 再換一個結束碼 1；
2. PyInstaller 的輸出一邊即時印、一邊完整寫進 log 檔，失敗時附上最後 30 行。
"""
from __future__ import annotations

import builtins
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
PACKAGING = os.path.join(ROOT, "packaging")
if PACKAGING not in sys.path:
    sys.path.insert(0, PACKAGING)

import build_exe as be  # noqa: E402

BUSY = {"busy.exe", "deep.dll"}


class _PretendBusy:
    """讓指定檔名在以 "r+b" 開啟時丟 PermissionError，模擬「正在執行中」。

    Windows 上執行中的 exe / dll 映像檔是以 FILE_SHARE_READ 開著的：讀得到，
    但開來寫就會失敗。這正是 `_locked_files` 用來判斷的訊號。
    """

    def __enter__(self):
        self._real = builtins.open

        def fake_open(path, mode="r", *args, **kwargs):
            if mode == "r+b" and os.path.basename(str(path)) in BUSY:
                raise PermissionError(32, "another process is using this file")
            return self._real(path, mode, *args, **kwargs)

        builtins.open = fake_open
        return self

    def __exit__(self, *_exc):
        builtins.open = self._real
        return False


class LockedFilesTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        for name in ("fine.dll", "busy.exe", "notes.txt", "mod.pyd"):
            with open(os.path.join(self.folder, name), "wb") as f:
                f.write(b"x")
        os.makedirs(os.path.join(self.folder, "sub"))
        with open(os.path.join(self.folder, "sub", "deep.dll"), "wb") as f:
            f.write(b"x")


class TestLockedFiles(LockedFilesTestCase):
    def test_finds_busy_binaries_including_nested_ones(self):
        with _PretendBusy():
            found = sorted(os.path.basename(p) for p in be._locked_files(self.folder))
        self.assertEqual(found, ["busy.exe", "deep.dll"])

    def test_nothing_locked_when_everything_is_writable(self):
        self.assertEqual(be._locked_files(self.folder), [])

    def test_only_binaries_are_probed(self):
        """設定檔、統計檔一直被別的程式開著是正常的，不該被當成打包障礙。"""
        with _PretendBusy():
            names = [os.path.basename(p) for p in be._locked_files(self.folder)]
        self.assertNotIn("notes.txt", names)

    def test_limit_stops_the_walk_early(self):
        with _PretendBusy():
            self.assertEqual(len(be._locked_files(self.folder, limit=1)), 1)

    def test_missing_folder_is_not_an_error(self):
        """第一次打包時 dist\\HoloTool 還不存在，不該因此爆掉。"""
        self.assertEqual(be._locked_files(os.path.join(self.folder, "nope")), [])


class TestShortPath(unittest.TestCase):
    """路徑要好讀，但**絕對不能在印錯誤訊息的時候自己爆掉**。

    2026-08-21 實機踩到：`tempfile` 的暫存資料夾在 `C:`、專案在 `F:`，
    `os.path.relpath` 直接丟 `ValueError: path is on mount 'C:', start on
    mount 'F:'` —— 於是「打包為什麼失敗」被換成一個完全不相干的 traceback。
    """

    def test_relative_when_the_path_is_under_the_project(self):
        inside = os.path.join(be.ROOT, "dist", "HoloTool", "HoloTool.exe")
        self.assertEqual(be._short(inside),
                         os.path.join("dist", "HoloTool", "HoloTool.exe"))

    def test_falls_back_to_the_absolute_path_across_drives(self):
        target = os.path.join("C:", os.sep, "Temp", "busy.exe")
        with patch.object(os.path, "relpath",
                          side_effect=ValueError("path is on mount 'C:'")):
            self.assertEqual(be._short(target), target)


class TestAbortMessage(LockedFilesTestCase):
    def test_abort_message_survives_a_cross_drive_folder(self):
        """暫存資料夾在別的磁碟時，中止訊息還是要印得出來。"""
        original = be.DIST_DIR
        be.DIST_DIR = self.folder
        try:
            with _PretendBusy(), patch.object(
                os.path, "relpath", side_effect=ValueError("different mount")
            ):
                with self.assertRaises(SystemExit) as caught:
                    be._abort_if_output_locked()
        finally:
            be.DIST_DIR = original
        self.assertIn("正在使用中", str(caught.exception))
        self.assertIn("busy.exe", str(caught.exception))

    def test_abort_says_what_to_close(self):
        original = be.DIST_DIR
        be.DIST_DIR = self.folder
        try:
            with _PretendBusy():
                with self.assertRaises(SystemExit) as caught:
                    be._abort_if_output_locked()
        finally:
            be.DIST_DIR = original
        message = str(caught.exception)
        self.assertIn("正在使用中", message)
        # 迷你懸浮視窗沒有標題列也不在工作列，是最容易忘記關的那一個
        self.assertIn("迷你視窗", message)
        self.assertIn("busy.exe", message)

    def test_nothing_locked_means_no_abort(self):
        original = be.DIST_DIR
        be.DIST_DIR = self.folder
        try:
            be._abort_if_output_locked()      # 不該丟任何東西
        finally:
            be.DIST_DIR = original


class TestPyInstallerLog(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._work = be.WORK_PATH
        be.WORK_PATH = os.path.join(self._tmp.name, "build")
        self.addCleanup(lambda: setattr(be, "WORK_PATH", self._work))

    @staticmethod
    def _run(argv):
        """跑起來會即時印出（那是刻意的），測試裡不需要看到，收進 buffer。"""
        with contextlib.redirect_stdout(io.StringIO()):
            return be._run_pyinstaller(argv)

    def test_exit_code_and_full_output_are_both_captured(self):
        code, log_path = self._run([
            sys.executable, "-c",
            "import sys\n"
            "for i in range(5): print('line', i)\n"
            "print('boom', file=sys.stderr)\n"
            "sys.exit(3)\n",
        ])
        self.assertEqual(code, 3)
        with open(log_path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("line 4", text)
        # stderr 也要進 log —— 真正的錯誤訊息幾乎都在那裡
        self.assertIn("boom", text)

    def test_tail_returns_the_last_lines(self):
        _code, log_path = self._run([
            sys.executable, "-c", "[print('l', i) for i in range(50)]",
        ])
        tail = be._tail(log_path, 3)
        self.assertEqual(tail.splitlines(), ["l 47", "l 48", "l 49"])

    def test_tail_of_a_missing_log_is_empty(self):
        self.assertEqual(be._tail(os.path.join(self._tmp.name, "nope.log")), "")


if __name__ == "__main__":
    unittest.main()
