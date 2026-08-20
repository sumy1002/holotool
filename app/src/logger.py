"""紀錄輸出：同時印到終端機、附加寫入當日 log 檔，並可推送給 GUI 顯示。"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Callable

from .paths import log_dir

LOG_DIR = log_dir()

# GUI 等外部程式可以訂閱 log，每產生一行就會被呼叫一次
_subscribers: list[Callable[[str], None]] = []


def subscribe(callback: Callable[[str], None]) -> None:
    if callback not in _subscribers:
        _subscribers.append(callback)


def unsubscribe(callback: Callable[[str], None]) -> None:
    if callback in _subscribers:
        _subscribers.remove(callback)


def log(message: str) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}"

    try:
        print(line)
    except UnicodeEncodeError:
        # 某些終端機編碼無法顯示中文時，不要因此中斷主流程
        print(line.encode("ascii", "replace").decode("ascii"))

    path = os.path.join(LOG_DIR, f"bot_{datetime.now().date().isoformat()}.log")
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")

    for callback in list(_subscribers):
        try:
            callback(line)
        except Exception:
            # 訂閱者出錯不應影響 log 本身
            pass
