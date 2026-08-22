"""螢幕擷取工具：以遊戲視窗用戶端座標為基準，擷取指定區域畫面。"""
from __future__ import annotations

from typing import Optional

import mss
import numpy as np

from . import window as win_mod
from .geometry import ratio_point_to_pixels, ratio_region_to_pixels


class GameCapture:
    """代表一個「已定位遊戲視窗」的擷取器。

    所有 region / point 都是比例值 (0~1)，會在每次擷取時依「視窗當下的實際尺寸」
    即時換算成螢幕絕對座標，因此視窗移動或縮放都不需要重新校準。
    """

    def __init__(self, window_title_substring: str):
        self.window_title_substring = window_title_substring
        self.hwnd: Optional[int] = None
        self._sct = mss.mss()
        self._frame = None

    def locate(self) -> bool:
        """尋找視窗，成功回傳 True。找不到時 hwnd 會是 None。"""
        self.hwnd = win_mod.find_window_by_title(self.window_title_substring)
        return self.hwnd is not None

    def is_window_valid(self) -> bool:
        if self.hwnd is None:
            return False
        import win32gui
        return win32gui.IsWindow(self.hwnd) and win32gui.IsWindowVisible(self.hwnd)

    def get_window_rect(self) -> "win_mod.WindowRect":
        if self.hwnd is None:
            raise RuntimeError("尚未定位到遊戲視窗，請先呼叫 locate()")
        return win_mod.get_client_rect_on_screen(self.hwnd)

    def get_client_size(self) -> tuple[int, int]:
        rect = self.get_window_rect()
        return rect.width, rect.height

    def begin_frame(self) -> np.ndarray:
        """擷取一整張用戶端畫面，同一輪 detect 重複裁切都用這一張。"""
        self._frame = self.grab_full_client()
        return self._frame

    def grab_region(self, region: dict) -> np.ndarray:
        """擷取指定比例區域 (x, y, w, h 皆為 0~1)，回傳 BGR numpy 陣列 (cv2 格式)。"""
        frame = self._frame if self._frame is not None else self.begin_frame()
        fh, fw = frame.shape[:2]
        x, y, w, h = ratio_region_to_pixels(region, fw, fh)
        x = max(0, min(x, fw - 1))
        y = max(0, min(y, fh - 1))
        w = max(1, min(w, fw - x))
        h = max(1, min(h, fh - y))
        return frame[y : y + h, x : x + w]

    def grab_full_client(self) -> np.ndarray:
        if self.hwnd is not None:
            try:
                from .overlay import grab_client_bgr

                img = grab_client_bgr(self.hwnd)
                if img is not None and img.size and float(img.mean()) > 6:
                    return img
            except Exception:
                pass
        rect = self.get_window_rect()
        abs_box = {"left": rect.left, "top": rect.top, "width": rect.width, "height": rect.height}
        shot = self._sct.grab(abs_box)
        arr = np.array(shot)
        # BGRA 切掉 alpha 之後是「非連續」的視圖，後面每一次 cvtColor /
        # matchTemplate 都得自己再複製一次。這裡一次轉成連續記憶體，
        # 同一張畫面在一個 tick 裡會被裁十幾次，划得來。
        return np.ascontiguousarray(arr[:, :, :3])

    def ratio_point_to_absolute(self, point: dict) -> tuple[int, int]:
        """把比例座標點 (x, y 皆為 0~1) 轉成螢幕絕對座標（點擊用）。"""
        rect = self.get_window_rect()
        x, y = ratio_point_to_pixels(point, rect.width, rect.height)
        return rect.left + x, rect.top + y
