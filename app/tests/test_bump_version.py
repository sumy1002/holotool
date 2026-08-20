"""版本號自動修改：**只能改真正的那一行**。

`version.py` 的說明文字裡有一行縮排過的 `    __version__ = "1.0.1"` 當範例。
正則寫成 `__version__\\s*=` 而沒有錨在行首的話，就會改到說明文字、
真正的定義反而沒動 —— 表現出來就是「怎麼打包都是舊版本」。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
PACKAGING = os.path.join(ROOT, "packaging")
if PACKAGING not in sys.path:
    sys.path.insert(0, PACKAGING)

import bump_version as bv  # noqa: E402

SAMPLE = '''"""說明文字。

要發新版就改這一行：

    __version__ = "9.9.9"

改完之後三邊會跟著變。
"""
from __future__ import annotations

__version__ = "1.0.2"

GITHUB_OWNER = "sumy1002"
'''


class TestReadAndBump(unittest.TestCase):
    def test_reads_the_real_definition_not_the_docstring_example(self):
        self.assertEqual(bv.current_version(SAMPLE), "1.0.2")

    def test_exactly_one_definition_is_required(self):
        with self.assertRaises(SystemExit):
            bv.current_version('# 沒有任何 __version__ 定義\n')
        with self.assertRaises(SystemExit):
            bv.current_version('__version__ = "1.0.0"\n__version__ = "2.0.0"\n')

    def test_next_version(self):
        self.assertEqual(bv.next_version("1.0.2"), "1.0.3")
        self.assertEqual(bv.next_version("1.0.9"), "1.0.10")
        self.assertEqual(bv.next_version("1.2.3", "minor"), "1.3.0")
        self.assertEqual(bv.next_version("1.2.3", "major"), "2.0.0")

    def test_non_numeric_version_is_refused(self):
        with self.assertRaises(SystemExit):
            bv.next_version("latest")


class TestWrite(unittest.TestCase):
    def _write_sample(self, tmp: str) -> str:
        path = os.path.join(tmp, "version.py")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(SAMPLE)
        return path

    def test_only_the_real_line_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_sample(tmp)
            bv.write_version("1.0.3", path)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertIn('__version__ = "1.0.3"', text)
            # 說明文字裡那個範例必須原封不動
            self.assertIn('    __version__ = "9.9.9"', text)
            self.assertEqual(bv.current_version(text), "1.0.3")

    def test_the_rest_of_the_file_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_sample(tmp)
            bv.write_version("2.5.1", path)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertIn('GITHUB_OWNER = "sumy1002"', text)
            self.assertIn("from __future__ import annotations", text)

    def test_no_leftover_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_sample(tmp)
            bv.write_version("1.0.4", path)
            self.assertFalse(os.path.exists(path + ".tmp"))


class TestRealVersionFile(unittest.TestCase):
    def test_the_projects_own_version_file_is_readable(self):
        """真正的 version.py 也必須剛好有一個定義，否則發版腳本會炸。"""
        version = bv.current_version(bv.read_text())
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
