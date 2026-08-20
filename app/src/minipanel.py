"""迷你懸浮控制窗：釘在最上層、只有「啟動 / 停止」。

## 為什麼要有這個

只有一個螢幕的人沒有地方擺主視窗 —— 主視窗蓋著遊戲，遊戲蓋著主視窗，
要按啟動就得先 Alt+Tab 切回來，切回來的瞬間遊戲又失去焦點。
所以做一個小到可以塞在工作列上方角落的視窗，只留最必要的兩顆操作。

F9 / F10 全域熱鍵本來就在（`HotkeyManager`），這個視窗是給
「不想背快捷鍵、想看到目前是跑還是停」的人用的。

## 幾個刻意的設計

* `overrideredirect(True)`：拿掉標題列，才能真的做到「超小」。代價是不能用
  系統的拖曳，所以自己綁滑鼠事件實作拖曳。
* 位置記在 config 裡（`mini_panel.x/y`），下次打開還在同一個角落。
* 顯示前一定要 **夾回螢幕範圍內**。使用者換螢幕或改解析度之後，存下來的座標
  很可能落在畫面外 —— 那時視窗會「開了但看不到」，而且因為沒有標題列也沒有
  工作列項目，完全找不回來。
* 主視窗收起來（`withdraw`）時，還原按鈕是唯一的回頭路，所以那顆按鈕永遠存在，
  而且不會因為 bot 正在跑就變成停用。
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional

from .geometry import clamp_window_to_screen

WIDTH = 168
HEIGHT = 42

# 距離螢幕右下角的預設留白。往上留 56px 大約就落在工作列上方一點。
MARGIN_RIGHT = 24
MARGIN_BOTTOM = 56

BG = "#1f2430"
FG = "#e6e6e6"
DOT_RUNNING = "#3ddc84"
DOT_STOPPED = "#8a8f98"


class MiniPanel(tk.Toplevel):
    """超小的懸浮控制窗。

    參數都是 callback，這個類別不認識 bot 也不認識 config，方便單獨測試。
    """

    def __init__(self, master: tk.Misc, *,
                 on_start: Callable[[], None],
                 on_stop: Callable[[], None],
                 on_restore: Callable[[], None],
                 on_move: Optional[Callable[[int, int], None]] = None,
                 position: Optional[tuple[int, int]] = None):
        super().__init__(master)
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_restore = on_restore
        self._on_move = on_move
        self._running = False
        self._drag_origin: Optional[tuple[int, int]] = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=BG)
        self.resizable(False, False)

        x, y = self._resolve_position(position)
        self.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

        body = tk.Frame(self, bg=BG)
        body.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        self.dot = tk.Label(body, text="●", bg=BG, fg=DOT_STOPPED,
                            font=("Segoe UI", 11))
        self.dot.pack(side=tk.LEFT, padx=(8, 2))

        self.toggle_btn = tk.Button(
            body, text="啟動", command=self._toggle, relief=tk.FLAT,
            bg="#2d7d46", fg="white", activebackground="#359351",
            activeforeground="white", font=("Microsoft JhengHei", 10, "bold"),
            width=6, cursor="hand2", borderwidth=0,
        )
        self.toggle_btn.pack(side=tk.LEFT, padx=2, pady=6)

        restore = tk.Button(
            body, text="▣", command=self._on_restore, relief=tk.FLAT,
            bg="#3a4150", fg=FG, activebackground="#4a5263",
            activeforeground="white", font=("Segoe UI", 10),
            width=2, cursor="hand2", borderwidth=0,
        )
        restore.pack(side=tk.RIGHT, padx=(2, 6), pady=6)

        # 拖曳：標題列被拿掉了，所以底色與那顆點都當作把手。
        for widget in (self, body, self.dot):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._drag_end)

        self.dot.configure(cursor="fleur")
        self._add_tip(restore, "還原主視窗")
        self._add_tip(self.dot, "拖曳這裡可以移動")

    # ------------------------------------------------------------ 位置

    def _resolve_position(self, position: Optional[tuple[int, int]]) -> tuple[int, int]:
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        if position is None:
            return (screen_w - WIDTH - MARGIN_RIGHT, screen_h - HEIGHT - MARGIN_BOTTOM)
        return clamp_to_screen(position[0], position[1], screen_w, screen_h)

    def _drag_start(self, event: "tk.Event") -> None:
        self._drag_origin = (event.x_root - self.winfo_x(),
                             event.y_root - self.winfo_y())

    def _drag_move(self, event: "tk.Event") -> None:
        if self._drag_origin is None:
            return
        x = event.x_root - self._drag_origin[0]
        y = event.y_root - self._drag_origin[1]
        x, y = clamp_to_screen(x, y, self.winfo_screenwidth(), self.winfo_screenheight())
        self.geometry(f"+{x}+{y}")

    def _drag_end(self, _event: "tk.Event") -> None:
        self._drag_origin = None
        if self._on_move is not None:
            self._on_move(self.winfo_x(), self.winfo_y())

    # ------------------------------------------------------------ 狀態

    def _toggle(self) -> None:
        # 依「目前顯示的狀態」決定要做什麼。真正的狀態由呼叫端透過
        # set_running() 回報，所以這裡按下去之後不自己翻轉旗標 ——
        # 啟動失敗（例如找不到遊戲視窗）時，按鈕不應該假裝已經在跑。
        if self._running:
            self._on_stop()
        else:
            self._on_start()

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        if self._running:
            self.dot.config(fg=DOT_RUNNING)
            self.toggle_btn.config(text="停止", bg="#a33a3a", activebackground="#bf4444")
        else:
            self.dot.config(fg=DOT_STOPPED)
            self.toggle_btn.config(text="啟動", bg="#2d7d46", activebackground="#359351")

    # ------------------------------------------------------------ 小提示

    def _add_tip(self, widget: tk.Widget, text: str) -> None:
        """極簡 tooltip。視窗沒有標題列，不講一句人話使用者會不知道能拖。"""
        tip: dict[str, Optional[tk.Toplevel]] = {"win": None}

        def show(_event: "tk.Event") -> None:
            if tip["win"] is not None:
                return
            win = tk.Toplevel(self)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            tk.Label(win, text=text, bg="#ffffe1", fg="#222",
                     font=("Microsoft JhengHei", 9), padx=6, pady=2,
                     borderwidth=1, relief=tk.SOLID).pack()
            win.geometry(f"+{widget.winfo_rootx()}+{widget.winfo_rooty() - 26}")
            tip["win"] = win

        def hide(_event: "tk.Event") -> None:
            if tip["win"] is not None:
                tip["win"].destroy()
                tip["win"] = None

        widget.bind("<Enter>", show, add="+")
        widget.bind("<Leave>", hide, add="+")
        # 視窗被銷毀時 tooltip 也要跟著走，否則會留下一塊黃色浮在畫面上
        widget.bind("<Destroy>", hide, add="+")


def clamp_to_screen(x, y, screen_w: int, screen_h: int,
                    width: int = WIDTH, height: int = HEIGHT) -> tuple[int, int]:
    """`geometry.clamp_window_to_screen` 套上迷你視窗的尺寸。

    真正的邏輯放在 geometry.py（那邊沒有 tkinter 依賴，測試才跑得起來）。
    """
    return clamp_window_to_screen(x, y, width, height, screen_w, screen_h)
