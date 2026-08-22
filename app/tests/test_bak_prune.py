"""打包時的 .bak 備份要輪替，不能無限堆積。

`build_exe._promote_runtime_data()` 每次發現兩邊內容不同就留一份 .bak ——
使用者一天發好幾版，config.json.bak、.bak1、.bak2、.bak3… 就一直長
（實機已經堆到 .bak3，data\\ 底下的統計檔也一樣）。留最近幾份就夠救急了。

唯一要小心的是**別把使用者手動留的備份當成我們的**：
`config.json.bak-before-profiles` 這種檔名不是輪替的一部分，一個都不能動。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGING = os.path.join(ROOT, "packaging")
for path in (ROOT, PACKAGING):
    if path not in sys.path:
        sys.path.insert(0, path)

import build_exe  # noqa: E402


def _write(folder, name, mtime_offset=0.0):
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(name)
    stamp = time.time() + mtime_offset
    os.utime(path, (stamp, stamp))
    return path


class TestBakRotation(unittest.TestCase):
    def test_only_the_newest_three_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = _write(tmp, "config.json")
            _write(tmp, "config.json.bak", -400)
            _write(tmp, "config.json.bak1", -300)
            _write(tmp, "config.json.bak2", -200)
            _write(tmp, "config.json.bak3", -100)
            removed = build_exe._prune_baks(target, keep=3)
            self.assertEqual(removed, ["config.json.bak"], "應該刪掉最舊的那一份")
            self.assertTrue(os.path.exists(os.path.join(tmp, "config.json.bak3")))
            self.assertTrue(os.path.exists(target), "本尊絕對不能被動到")

    def test_manual_backups_are_never_touched(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = _write(tmp, "config.json")
            manual = _write(tmp, "config.json.bak-before-profiles", -900)
            for index in range(6):
                _write(tmp, f"config.json.bak{index or ''}", -index * 10)
            build_exe._prune_baks(target, keep=2)
            self.assertTrue(os.path.exists(manual),
                            "使用者手動留的備份被輪替刪掉了")

    def test_other_files_baks_are_separate(self):
        """stats_A 的輪替不可以吃到 stats_B 的備份。"""
        with tempfile.TemporaryDirectory() as tmp:
            a = _write(tmp, "stats_2026-08-20.json")
            _write(tmp, "stats_2026-08-20.json.bak", -50)
            b_bak = _write(tmp, "stats_2026-08-21.json.bak", -60)
            build_exe._prune_baks(a, keep=1)
            self.assertTrue(os.path.exists(b_bak))

    def test_keep_backup_creates_then_rotates(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = _write(tmp, "config.json")
            for _ in range(build_exe.KEEP_BAK_PER_FILE + 3):
                build_exe._keep_backup(target)
                time.sleep(0.01)   # 讓 mtime 排得出先後
            baks = [n for n in os.listdir(tmp)
                    if build_exe._BAK_SUFFIX.fullmatch(n[len("config.json"):] or "")
                    and n.startswith("config.json")]
            self.assertEqual(len(baks), build_exe.KEEP_BAK_PER_FILE,
                             f"應該只留 {build_exe.KEEP_BAK_PER_FILE} 份，實際 {sorted(baks)}")


if __name__ == "__main__":
    unittest.main()
