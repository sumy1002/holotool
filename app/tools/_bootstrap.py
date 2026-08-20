"""讓 tools\\ 底下的工具找得到專案根目錄的 src 套件。

這些工具搬進子資料夾之後，Python 的 sys.path[0] 會變成 tools\\，
`from src.xxx import ...` 就會找不到。這個檔案只做一件事：
把專案根目錄插到 sys.path 最前面。

用法：在工具的 import 區塊最上面寫一行

    import _bootstrap  # noqa: F401
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
