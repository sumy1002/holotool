"""可重用的全螢幕框選遮罩，以及校準預覽（畫在截圖上，不蓋住遊戲）。

在整個螢幕蓋上一層半透明遮罩，讓使用者用滑鼠框選一塊區域或點選一個座標。
可以獨立執行（自己建立 Tk root），也可以掛在既有的 GUI 視窗底下（Toplevel）。
"""
from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from typing import Optional


@dataclass
class SelectionResult:
    """框選結果。

    status="ok"    使用者完成選取，value 內含座標
    status="skip"  使用者按 Esc 跳過這一項
    status="abort" 使用者按 Q 或滑鼠右鍵，要求中止整個校準流程
    """

    status: str
    value: Optional[dict] = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class ScreenSelector:
    """回傳的座標一律為「螢幕絕對座標」。

    mode="region" 時 value 為 {"x","y","w","h"}；mode="point" 時為 {"x","y"}。

    重要：如果程式已經有一個 tkinter 主視窗（例如 gui.py），一定要把它當成 master
    傳進來，這樣才會用 Toplevel 掛在同一個 Tcl 解譯器底下。否則會產生第二個 Tk
    根視窗，關閉遮罩後主視窗會失去回應。
    """

    def __init__(self, instruction: str, mode: str = "region", master: Optional[tk.Misc] = None):
        self.mode = mode
        self.status = "skip"
        self.value: Optional[dict] = None
        self.start: Optional[tuple[int, int]] = None

        self._owns_root = master is None
        self.root = tk.Tk() if self._owns_root else tk.Toplevel(master)

        self.root.attributes("-fullscreen", True)
        self.root.attributes("-alpha", 0.35)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="black")
        self.root.config(cursor="crosshair")

        self.canvas = tk.Canvas(self.root, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        hint = "拖曳滑鼠框選一個區域" if mode == "region" else "點擊滑鼠左鍵選取一個座標點"
        self.canvas.create_text(
            self.root.winfo_screenwidth() // 2,
            70,
            text=(
                f"{instruction}\n\n"
                f"{hint}\n"
                "這層半透明畫面就是校準工具，HoloTool 主視窗不會關閉，選完會自動回來\n"
                "Esc = 跳過這一項　　Q 或滑鼠右鍵 = 結束校準"
            ),
            fill="white",
            font=("Microsoft JhengHei", 18, "bold"),
            justify="center",
        )
        self.rect_id = None

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_abort)
        self.root.bind("<Escape>", self._on_escape)
        self.root.bind("<KeyPress-q>", self._on_abort)
        self.root.bind("<KeyPress-Q>", self._on_abort)
        self.root.focus_force()

    def _to_canvas(self, screen_x: int, screen_y: int) -> tuple[int, int]:
        return screen_x - self.root.winfo_rootx(), screen_y - self.root.winfo_rooty()

    def _on_press(self, event):
        self.start = (event.x_root, event.y_root)
        if self.mode == "region":
            self.rect_id = self.canvas.create_rectangle(
                event.x, event.y, event.x, event.y, outline="red", width=3
            )

    def _on_drag(self, event):
        if self.mode == "region" and self.rect_id is not None and self.start:
            cx, cy = self._to_canvas(*self.start)
            self.canvas.coords(self.rect_id, cx, cy, event.x, event.y)

    def _on_release(self, event):
        if self.start is None:
            return
        end = (event.x_root, event.y_root)
        if self.mode == "point":
            self.value = {"x": end[0], "y": end[1]}
        else:
            x0, y0 = self.start
            x1, y1 = end
            if abs(x1 - x0) < 3 or abs(y1 - y0) < 3:
                return  # 框太小視為誤觸，繼續等待使用者重新框選
            self.value = {
                "x": min(x0, x1),
                "y": min(y0, y1),
                "w": abs(x1 - x0),
                "h": abs(y1 - y0),
            }
        self.status = "ok"
        self._close()

    def _on_escape(self, _event):
        self.status = "skip"
        self.value = None
        self._close()

    def _on_abort(self, _event):
        self.status = "abort"
        self.value = None
        self._close()

    def _close(self):
        try:
            if not self._owns_root:
                self.root.grab_release()
        except tk.TclError:
            pass
        self.root.destroy()

    def run(self) -> SelectionResult:
        if self._owns_root:
            self.root.mainloop()
        else:
            self.root.grab_set()
            self.root.wait_window()
        return SelectionResult(self.status, self.value)


def select_region(instruction: str, master: Optional[tk.Misc] = None) -> SelectionResult:
    return ScreenSelector(instruction, "region", master).run()


def select_point(instruction: str, master: Optional[tk.Misc] = None) -> SelectionResult:
    return ScreenSelector(instruction, "point", master).run()


def _hex_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _cjk_font(size: int):
    from PIL import ImageFont

    for path in (
        r"C:\Windows\Fonts\msjh.ttc",
        r"C:\Windows\Fonts\msjhbd.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\meiryo.ttc",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def grab_client_bgr(hwnd: int):
    """擷取遊戲視窗用戶端畫面。優先 PrintWindow（被擋住也能抓），失敗再截螢幕。"""
    import numpy as np
    import win32gui
    import win32ui
    from ctypes import windll

    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    width, height = right - left, bottom - top
    if width <= 1 or height <= 1:
        raise RuntimeError("視窗太小，無法預覽")

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    try:
        bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
        save_dc.SelectObject(bitmap)
        # 3 = PW_CLIENTONLY | PW_RENDERFULLCONTENT，比較吃得進 GPU 畫面
        printed = False
        for flag in (3, 2, 1, 0):
            if windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), flag):
                printed = True
                break
        bits = bitmap.GetBitmapBits(True)
        img = np.frombuffer(bits, dtype=np.uint8).reshape((height, width, 4))
        bgr = np.ascontiguousarray(img[:, :, :3])
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)

    if printed and float(bgr.mean()) > 6:
        return bgr

    import mss

    from . import window as win_mod

    rect = win_mod.get_client_rect_on_screen(hwnd)
    with mss.mss() as sct:
        shot = sct.grab(
            {"left": rect.left, "top": rect.top, "width": rect.width, "height": rect.height}
        )
    return np.array(shot)[:, :, :3]


def paint_calib_items(frame_bgr, items: list[tuple[str, str, dict, str]]):
    """在遊戲截圖上畫校準框／點，回傳 RGB 的 PIL Image。"""
    from PIL import Image, ImageDraw

    from .geometry import ratio_point_to_pixels, ratio_region_to_pixels

    h, w = frame_bgr.shape[:2]
    image = Image.fromarray(frame_bgr[:, :, ::-1].copy())
    draw = ImageDraw.Draw(image)
    font = _cjk_font(max(16, h // 28))
    for kind, name, value, color in items:
        rgb = _hex_rgb(color)
        if kind == "region":
            x, y, bw, bh = ratio_region_to_pixels(value, w, h)
            draw.rectangle([x, y, x + bw, y + bh], outline=rgb, width=3)
            draw.text((x + 4, max(0, y - 24)), name, fill=rgb, font=font)
        else:
            x, y = ratio_point_to_pixels(value, w, h)
            r = 14
            draw.ellipse([x - r, y - r, x + r, y + r], outline=rgb, width=3)
            draw.line([x - 20, y, x + 20, y], fill=rgb, width=3)
            draw.line([x, y - 20, x, y + 20], fill=rgb, width=3)
            draw.text((x + 16, y - 22), name, fill=rgb, font=font)
    return image


class CalibrationPreview:
    """把遊戲畫面截圖畫上框選，顯示在 GUI 裡（不要蓋住遊戲視窗）。"""

    def __init__(self, master: tk.Misc):
        self.master = master
        self.label: Optional[tk.Label] = None
        self._imgtk = None
        self._raw = None
        self._raw_hwnd = None
        self._raw_tick = 0.0

    def attach(self, label: tk.Label) -> None:
        self.label = label

    def show(self, hwnd: int, items: list[tuple[str, str, dict, str]], max_width: int = 560) -> None:
        import time

        from PIL import ImageTk

        if self.label is None:
            return
        now = time.monotonic()
        need_grab = (
            self._raw is None
            or self._raw_hwnd != hwnd
            or (now - self._raw_tick) > 0.4
        )
        if need_grab:
            try:
                self._raw = grab_client_bgr(hwnd)
                self._raw_hwnd = hwnd
                self._raw_tick = now
            except Exception:
                self.hide("擷取遊戲畫面失敗")
                return

        painted = paint_calib_items(self._raw, items)
        tw, th = painted.size
        if tw > max_width and tw > 0:
            nh = max(1, round(th * max_width / tw))
            painted = painted.resize((max_width, nh))
        self._imgtk = ImageTk.PhotoImage(painted)
        self.label.config(image=self._imgtk, text="")

    def hide(self, message: str = "滑鼠移到左邊項目上，這裡會顯示遊戲畫面與框選位置") -> None:
        self._raw = None
        self._raw_hwnd = None
        if self.label is not None:
            self.label.config(image="", text=message)
        self._imgtk = None

    def destroy(self) -> None:
        self.hide()
        self.label = None
