"""滑鼠操作封裝，以及全域熱鍵監聽（F9 啟動/停止、F10 緊急停止）。"""
from __future__ import annotations

import random
import threading
import time

import keyboard
import pyautogui

from .capture import GameCapture


class MouseController:
    """滑鼠點擊。

    重點是「按下去要停一下再放開」。pyautogui.click() 的按下與放開幾乎沒有間隔，
    遊戲若是每一幀輪詢一次滑鼠狀態，這種瞬間點擊很容易整個被跳過 —— 表現出來就是
    「程式以為點了，但遊戲沒反應」。改成 mouseDown → 停 hold_range → mouseUp，
    讓按鍵狀態至少橫跨好幾幀。
    """

    def __init__(
        self,
        capture: GameCapture,
        click_delay_range=(0.08, 0.2),
        hold_range=(0.06, 0.12),
        move_duration: float = 0.10,
    ):
        self.capture = capture
        self.click_delay_range = click_delay_range
        self.hold_range = hold_range
        self.move_duration = move_duration

    def click_point(self, point: dict, jitter: int = 2) -> None:
        """點擊比例座標點 (x, y 皆為 0~1)，會依視窗當下的位置與大小即時換算。"""
        abs_x, abs_y = self.capture.ratio_point_to_absolute(point)
        if jitter:
            abs_x += random.randint(-jitter, jitter)
            abs_y += random.randint(-jitter, jitter)
        pyautogui.moveTo(abs_x, abs_y, duration=self.move_duration)
        # 先讓游標停一下，有些 UI 要先偵測到 hover 才吃得到點擊
        time.sleep(random.uniform(*self.click_delay_range))
        pyautogui.mouseDown()
        time.sleep(random.uniform(*self.hold_range))
        pyautogui.mouseUp()

    def click_region_center(self, region: dict) -> None:
        """點擊比例區域的中心點。"""
        center = {"x": region["x"] + region["w"] / 2, "y": region["y"] + region["h"] / 2}
        self.click_point(center)


class HotkeyManager:
    """管理 F9 (啟動/停止切換) 與 F10 (緊急停止) 全域熱鍵。"""

    def __init__(self, on_toggle, on_emergency_stop=None, toggle_key: str = "f9", stop_key: str = "f10"):
        self.on_toggle = on_toggle
        self.on_emergency_stop = on_emergency_stop
        self.toggle_key = toggle_key
        self.stop_key = stop_key
        self._registered = False

    def start(self) -> None:
        keyboard.add_hotkey(self.toggle_key, self.on_toggle)
        if self.on_emergency_stop:
            keyboard.add_hotkey(self.stop_key, self.on_emergency_stop)
        self._registered = True

    def stop(self) -> None:
        if self._registered:
            try:
                keyboard.remove_hotkey(self.toggle_key)
                if self.on_emergency_stop:
                    keyboard.remove_hotkey(self.stop_key)
            except KeyError:
                pass
            self._registered = False

    def wait_forever(self) -> None:
        keyboard.wait()
