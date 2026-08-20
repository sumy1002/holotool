"""尋找遊戲視窗並取得其用戶端區域在螢幕上的絕對座標。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import win32con
import win32gui


@dataclass
class WindowRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


def list_visible_windows() -> list[tuple[int, str]]:
    """列出目前所有有標題的可見視窗，格式為 (hwnd, title)，方便校準時挑選。"""
    results: list[tuple[int, str]] = []

    def _enum(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title.strip():
                results.append((hwnd, title))

    win32gui.EnumWindows(_enum, None)
    return results


def find_window_by_title(substring: str) -> Optional[int]:
    """依標題「部分字串」尋找視窗，回傳 hwnd（找不到回傳 None）。大小寫不敏感。"""
    if not substring:
        return None
    substring_lower = substring.lower()
    for hwnd, title in list_visible_windows():
        if substring_lower in title.lower():
            return hwnd
    return None


def get_client_rect_on_screen(hwnd: int) -> WindowRect:
    """取得視窗「用戶端區域」（不含標題列/邊框）在螢幕上的絕對座標。"""
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    left, top = win32gui.ClientToScreen(hwnd, (left, top))
    right, bottom = win32gui.ClientToScreen(hwnd, (right, bottom))
    return WindowRect(left, top, right, bottom)


def bring_to_foreground(hwnd: int) -> None:
    """盡可能把視窗叫到最前面。

    Windows 對「搶奪焦點」有限制，SetForegroundWindow 常會失敗，
    所以這裡依序嘗試多種方式，至少讓視窗變成可見、不被其他視窗遮住。
    """
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception:
        pass
    try:
        win32gui.BringWindowToTop(hwnd)
    except Exception:
        pass
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        # 焦點搶奪被系統阻擋時會失敗，屬正常情況，不影響擷取畫面
        pass
