"""HoloTool 圖形介面。

執行方式：
    .venv\\Scripts\\python.exe gui.py

四個分頁：
  主控台 — 選擇遊戲視窗、啟動/停止自動遊玩、看即時 log 與今日統計、檢查更新
  校準   — 逐項框選畫面位置，右邊會顯示每一項的完成狀態
  點數/花色樣板 — 讀取畫面自動切出左上角，確認後儲存（只要 13 點數 + 4 花色）
  設定   — 調整辨識門檻與策略參數
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _locate_app_dir() -> str:
    """找出放程式與資料的那個子資料夾（預設叫 app\\）。

    最外層只留 gui.py / README.md / requirements.txt，其餘全部收進一個子資料夾。
    這裡不寫死資料夾名字，而是找「裡面有 src\\paths.py 的那一個」——
    這樣你之後想把 app\\ 改成別的名字也不會壞掉。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isfile(os.path.join(here, "src", "paths.py")):
        return here                      # 打包成 exe 之後是平的，不用再往下找
    for name in sorted(os.listdir(here)):
        candidate = os.path.join(here, name)
        if os.path.isfile(os.path.join(candidate, "src", "paths.py")):
            return candidate
    return here


if not getattr(sys, "frozen", False):
    _APP_DIR = _locate_app_dir()
    if _APP_DIR not in sys.path:
        sys.path.insert(0, _APP_DIR)

import cv2
from PIL import Image, ImageTk

from src import logger
from src import window as win_mod
from src.bot import Bot
from src.capture import GameCapture
from src.config import (
    CONFIG_VERSION,
    DEFAULT_CONFIG,
    apply_screenshot_layout,
    get_by_path,
    load_config,
    save_config,
    set_by_path,
    set_template_capture_size,
)
from src.defaults_layout import UI_MARKER_FILES
from src.controller import HotkeyManager
from src import calibguide
from src import profiles as profiles_mod
from src import reconcile as reconcile_mod
from src.settings_layout import (
    INT_SETTINGS,
    OTHER_FIELDS,
    SETTING_FIELDS,
    SETTING_SECTIONS,
)
from src.minipanel import MiniPanel
from src.geometry import (
    aspect_ratio_delta,
    pixels_point_to_ratio,
    pixels_region_to_ratio,
    ratio_point_to_pixels,
    ratio_region_to_pixels,
    scale_factor,
)
from src.handeval import Card, normalize_label_input
from src.overlay import CalibrationPreview, select_point, select_region
from src import cardparts
from src.cardparts import (
    MIN_OWN_TO_DROP_BUNDLED,
    bundled_copies_present,
    extract_parts,
    joker_template_count,
    missing_parts,
    next_part_path,
    parse_part_name,
    part_inventory,
    rightmost_card_rect,
    unusable_parts,
)
from src.paths import (
    default_parts_dir,
    parts_dir,
    prepare_runtime,
    project_root,
    resolve_data_path,
    template_dir,
)
from src.recognize import CardReader, load_part_templates, part_sources
from src.state_machine import detect_frame
from src.stats import DailyStats
from src import updater
from src.version import __version__ as APP_VERSION

TEMPLATE_DIR = template_dir()
PARTS_DIR = parts_dir()
PROJECT_ROOT = project_root()

# (kind, path, name, hint, group_title)
# group_title 用來在「依序校準」時提示使用者先切到對應畫面
CALIB_TARGETS: list[tuple[str, str, str, str, str]] = [
    ("region", "regions.table_marker", "牌桌標記：High & Low Logo",
     "框選左上角「High & Low」標題（整場小遊戲都看得到，離開後會消失）",
     "請切到『投注並開始』畫面（五張牌背面、下方有紫色按鈕）"),
    ("point", "points.start_round", "「投注並開始」按鈕",
     "點擊畫面下方紫色按鈕「投注並開始」",
     "請切到『投注並開始』畫面（五張牌背面、下方有紫色按鈕）"),
    *[
        ("region", f"regions.card_slots.{i}", f"第 {i + 1} 張手牌區域",
         f"框選中間那排從左數第 {i + 1} 張牌（貼齊白邊；背面或正面都可以，位置相同）",
         "請切到『投注並開始』或『選擇要保留的牌』畫面（中間有五張牌）")
        for i in range(5)
    ],
    ("region", "regions.draw_prompt", "選牌提示文字",
     "框選「選擇要保留的牌吧！」這一行字",
     "請切到『選擇要保留的牌』畫面（五張牌正面、下方有「替換」）"),
    *[
        ("point", f"points.hold_toggles.{i}", f"第 {i + 1} 張「保留」點擊位置",
         f"點第 {i + 1} 張牌的卡面中央（點下去會出現粉紅「剩餘」標籤；沒點的牌才會被替換）",
         "請切到『選擇要保留的牌』畫面（五張牌正面、下方有「替換」）")
        for i in range(5)
    ],
    ("point", "points.draw_confirm", "「替換」按鈕",
     "點擊下方紫色按鈕「替換」",
     "請切到『選擇要保留的牌』畫面（五張牌正面、下方有「替換」）"),
    ("region", "regions.congrats_marker", "過關畫面標記",
     "框選「Congratulations !」或底部「點擊繼續」這幾個字",
     "請切到過關畫面（出現 Congratulations、獲得金額、點擊繼續）"),
    ("point", "points.click_continue", "「點擊繼續」",
     "點一下畫面中間或「點擊繼續」這行字（過關後要按一下才會出現翻倍對話框）",
     "請切到過關畫面（出現 Congratulations、獲得金額、點擊繼續）"),
    ("region", "regions.challenge_marker", "翻倍對話框標記",
     "框選「要挑戰嗎？」或「翻倍機會！／成功！」這幾個字",
     "請切到翻倍對話框（左側角色、右側有「取消」「進行挑戰」）"),
    ("point", "points.cashout_button", "對話框「取消」（兌現）",
     "點擊白色按鈕「取消」（不繼續加倍、收下目前金額）",
     "請切到翻倍對話框（左側角色、右側有「取消」「進行挑戰」）"),
    ("point", "points.challenge_button", "「進行挑戰」按鈕",
     "點擊紫色按鈕「進行挑戰」",
     "請切到翻倍對話框（左側角色、右側有「取消」「進行挑戰」）"),
    ("region", "regions.highlow_card", "比大小：目前已翻開的牌",
     "框選緊鄰「背面牌」左側、完整露臉的那張牌（第一輪就是左邊那張；不要框到牌背）",
     "請切到比大小畫面（左邊有翻開的牌、右邊有「大」「小」按鈕）"),
    ("point", "points.high_button", "「大」按鈕",
     "點擊右側紫色「大」按鈕",
     "請切到比大小畫面（左邊有翻開的牌、右邊有「大」「小」按鈕）"),
    ("point", "points.low_button", "「小」按鈕",
     "點擊右側白色「小」按鈕",
     "請切到比大小畫面（左邊有翻開的牌、右邊有「大」「小」按鈕）"),
    ("region", "regions.poker_fail_marker", "湊牌失敗標記",
     "框選「要再玩一次撲克嗎？」這一行（不要框到金額）",
     "請切到湊牌失敗畫面（好可惜、要再玩一次撲克嗎、取消／再一次）"),
    ("point", "points.retry_button", "「再一次」按鈕",
     "點擊紫色按鈕「再一次」（湊牌失敗與比大小失敗都是這顆）",
     "請切到湊牌失敗或比大小失敗畫面（右下有「取消」「再一次」）"),
    ("region", "regions.fail_marker", "比大小失敗標記",
     "框選「失敗」這兩個大字（這是比大小輸了才會出現，不是湊牌失敗）",
     "請切到比大小失敗畫面（標題「失敗」、按鈕「取消」「再一次」）"),
    ("region", "regions.max_win_marker", "已達最高獲得金額標記",
     "框選畫面底部中央「已達最高獲得金額，遊戲結束」這一行（不要框到旁邊的按鈕）",
     "請切到達到上限的結算畫面（顯示獲得硬幣／獲勝牌型／翻倍次數，右下有「再玩一次」）"),
    ("point", "points.max_win_retry", "上限畫面的「再玩一次」",
     "點擊右下角的「再玩一次」（這顆的位置跟失敗畫面那顆不一樣，要分開校準）",
     "請切到達到上限的結算畫面（顯示獲得硬幣／獲勝牌型／翻倍次數，右下有「再玩一次」）"),
]

# 設定分頁的欄位分組（比對門檻／動作時間／其餘）放在 src/settings_layout.py，
# 那邊沒有 tkinter 依賴，測試才驗得到「每個欄位都被歸到某一組、路徑真的存在」。


class CollapsibleSection(ttk.Frame):
    """可以收合的一區設定。

    標題那一列是一顆按鈕（`▼ 標題` / `▶ 標題`），按下去把內容 pack_forget()。
    ttk 沒有現成的可收合容器，自己包一層最單純，也不會影響外層的捲動區
    —— 內容縮回去之後 `<Configure>` 會重算 scrollregion，捲軸自己會變短。

    要往裡面塞東西時用 `section.body`，不要直接用 section。
    """

    def __init__(self, parent, title: str, subtitle: str = "", expanded: bool = True):
        super().__init__(parent)
        self._expanded = bool(expanded)
        self._title = title

        header = ttk.Frame(self)
        header.pack(fill=tk.X)
        self._button = ttk.Button(header, width=30, command=self.toggle)
        self._button.pack(side=tk.LEFT)
        if subtitle:
            ttk.Label(header, text=subtitle, foreground="#777",
                      wraplength=420, justify="left").pack(side=tk.LEFT, padx=(8, 0))

        self.body = ttk.Frame(self)
        if self._expanded:
            self.body.pack(fill=tk.X, padx=(16, 0), pady=(4, 0))
        self._sync()

    def _sync(self) -> None:
        arrow = "▼" if self._expanded else "▶"
        self._button.config(text=f"{arrow}　{self._title}")

    def toggle(self) -> None:
        self._expanded = not self._expanded
        if self._expanded:
            self.body.pack(fill=tk.X, padx=(16, 0), pady=(4, 0))
        else:
            self.body.pack_forget()
        self._sync()

    @property
    def expanded(self) -> bool:
        return self._expanded


class HoloToolGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"HoloTool v{APP_VERSION} — Hololive Dreams High & Low 自動化工具")
        # 視窗要能縮得很小：分頁內容過長的用捲軸裝，固定高度的元件也都調小了，
        # 所以下限可以壓到 680×430（大約是 1366×768 螢幕的一半）。
        self.geometry("1040x700")
        self.minsize(680, 430)

        self.cfg = load_config()
        self.log_queue: queue.Queue[str] = queue.Queue()
        logger.subscribe(self.log_queue.put)

        self.bot: Bot | None = None
        self.bot_thread: threading.Thread | None = None
        self.hotkeys: HotkeyManager | None = None

        # 檢查更新是在背景執行緒做的（不然按下去 GUI 會卡住不動），
        # 結果一律透過 queue 交回主執行緒 —— tkinter 元件只能在主執行緒動。
        self._update_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._update_busy = False
        # 已經決定要為了更新關閉程式。_pump 看到這個旗標就不再碰任何元件 ——
        # 元件已經被 destroy() 掉了，繼續碰會丟 TclError，而 windowed 模式下
        # 那個錯誤看不到，只會表現成「視窗關了、行程還在」。
        self._quitting = False

        self._preview_running = False
        self._preview_queue: queue.Queue[str] = queue.Queue()
        self._preview_imgtk = None
        self._calib_overlay = CalibrationPreview(self)
        self._hover_calib_iid: str | None = None
        self._calib_preview_all = False

        # 上一次自動偵測到的用戶端尺寸。用來判斷「視窗被拉成別的比例了」，
        # 沒變就什麼都不做（否則 _pump 會每兩秒寫一次 config.json）。
        self._detected_size: tuple[int, int] | None = None
        # 拖曳中量到的中間尺寸。要連續兩次量到同一個值才視為「拖完了」。
        self._settling_size: tuple[int, int] | None = None
        self._detect_tick = 0

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._refresh_windows()
        self._autodetect_profile(announce=False)
        self._refresh_calib_status()
        self._refresh_template_panel()
        self.after(200, self._pump)

    # ------------------------------------------------------------------ UI

    @staticmethod
    def _scrollable(parent) -> ttk.Frame:
        """在 parent 裡放一個可上下捲動的區域，回傳「要往裡面塞東西」的 Frame。

        分頁內容比視窗高的時候（設定分頁有二十幾列），沒有捲軸就等於強迫
        視窗不能縮小 —— 縮了就有東西被切掉還按不到。包一層 Canvas 之後，
        視窗想縮多小都行，內容自己捲。
        """
        canvas = tk.Canvas(parent, highlightthickness=0, borderwidth=0)
        bar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        bar.pack(side=tk.RIGHT, fill=tk.Y)

        inner = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _fit(_event=None):
            # 寬度一律跟著 canvas，只捲上下，不要出現橫向捲軸。
            #
            # 高度取「內容要求的高度」與「canvas 目前高度」的**較大值**：
            #   * 內容比視窗高 → 用內容高度，於是 scrollregion 超出可視範圍、捲軸有用
            #   * 內容比視窗短 → 撐滿 canvas，裡面 expand=True 的元件（主控台的
            #     執行紀錄、校準分頁的清單）才會跟著視窗長大
            # 少了後面那一半，包進捲動區的分頁在大視窗下會變成上面擠一堆、
            # 下面一大片空白 —— 等於為了小視窗能捲，犧牲了大視窗的可用性。
            width = canvas.winfo_width()
            height = max(inner.winfo_reqheight(), canvas.winfo_height())
            current = (canvas.itemcget(window, "width"), canvas.itemcget(window, "height"))
            if current != (str(width), str(height)):
                canvas.itemconfigure(window, width=width, height=height)
            canvas.configure(scrollregion=canvas.bbox("all"))

        inner.bind("<Configure>", _fit)
        canvas.bind("<Configure>", _fit)

        def _on_wheel(event):
            # 內容比視窗還短時不要捲，否則會出現「捲到一半彈回來」的怪動作
            first, last = canvas.yview()
            if first <= 0.0 and last >= 1.0:
                return
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

        def _grab_wheel(_event=None):
            canvas.bind_all("<MouseWheel>", _on_wheel)

        def _release_wheel(_event=None):
            # Tk 會在「滑鼠從 canvas 移到它的子元件上」時也送一個 <Leave>。
            # 舊版直接 unbind，結果滑鼠一停在任何一個輸入框或標籤上，
            # 滾輪就完全失效 —— 只有指到空白邊緣才捲得動。
            # 所以先確認指標真的離開這塊區域了才交還滾輪。
            x, y = canvas.winfo_pointerxy()
            left, top = canvas.winfo_rootx(), canvas.winfo_rooty()
            inside = (left <= x < left + canvas.winfo_width()
                      and top <= y < top + canvas.winfo_height())
            if not inside:
                canvas.unbind_all("<MouseWheel>")

        # 只在滑鼠進到這塊區域時接管滾輪，才不會影響其他分頁。
        # inner 也要綁：從外面直接進到子元件上時不會經過 canvas 的 <Enter>。
        canvas.bind("<Enter>", _grab_wheel)
        canvas.bind("<Leave>", _release_wheel)
        inner.bind("<Enter>", _grab_wheel, add="+")
        return inner

    @staticmethod
    def _keep_wheel_local(widget: tk.Widget) -> None:
        """讓這個元件自己處理滾輪，不要被外層的捲動區搶走。

        給有自己捲軸的元件用（例如樣板檔清單）。回傳 "break" 中斷事件傳遞，
        所以 `bind_all` 那一層不會再收到。
        """
        def _on_wheel(event):
            widget.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"

        widget.bind("<MouseWheel>", _on_wheel, add="+")

    @staticmethod
    def _wrap_to_width(label: ttk.Label, container: tk.Widget, margin: int = 40) -> None:
        """讓說明文字的換行寬度跟著容器走，不要寫死一個數字。

        寫死 wraplength 兩邊都難看：視窗縮小時文字被切掉，
        視窗放大時右邊留一大片空白。
        """
        def _on_configure(event):
            width = max(240, event.width - margin)
            if int(label.cget("wraplength") or 0) != width:
                label.config(wraplength=width)

        container.bind("<Configure>", _on_configure, add="+")

    def _build_ui(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.tab_main = ttk.Frame(self.notebook)
        self.tab_calib = ttk.Frame(self.notebook)
        self.tab_tmpl = ttk.Frame(self.notebook)
        self.tab_settings = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_main, text="  主控台  ")
        self.notebook.add(self.tab_calib, text="  校準  ")
        self.notebook.add(self.tab_tmpl, text="  點數/花色樣板  ")
        self.notebook.add(self.tab_settings, text="  設定  ")

        self._build_main_tab()
        self._build_calib_tab()
        self._build_template_tab()
        self._build_settings_tab()

    # ---------- 主控台 ----------

    def _build_main_tab(self) -> None:
        # 主控台有六塊（遊戲視窗、視窗比例、自動遊玩、今日統計、版本與更新、執行紀錄），
        # 視窗一縮小最下面的執行紀錄與「檢查更新」就被切掉、而且捲不過去。
        # 整頁包進捲動區；內容比視窗短時 _scrollable 會撐滿高度，
        # 所以大視窗下執行紀錄照樣會跟著長大。
        frame = self._scrollable(self.tab_main)

        win_box = ttk.LabelFrame(frame, text="遊戲視窗")
        win_box.pack(fill=tk.X, padx=8, pady=6)

        self.window_combo = ttk.Combobox(win_box, state="readonly", width=28)
        self.window_combo.grid(row=0, column=0, padx=6, pady=6, sticky="we")
        ttk.Button(win_box, text="重新整理清單", command=self._refresh_windows).grid(row=0, column=1, padx=4)
        ttk.Button(win_box, text="使用此視窗", command=self._use_selected_window).grid(row=0, column=2, padx=4)
        win_box.columnconfigure(0, weight=1)

        self.window_status = ttk.Label(win_box, text="", foreground="#555",
                                       wraplength=760, justify="left")
        self.window_status.grid(row=1, column=0, columnspan=3, padx=6, pady=(0, 6), sticky="w")

        # 長寬比原本放在「校準」分頁，但那是本末倒置：使用者選好視窗的那一刻
        # 就已經決定了是哪個比例，不該再要求他自己去另一個分頁挑一次。
        # 現在選視窗（或視窗被拉成別的比例）就自動偵測並套用對應的那組校準，
        # 想手動指定的人再從下拉選單改。校準分頁只顯示「目前正在校準哪一組」。
        ratio_row = ttk.Frame(win_box)
        ratio_row.grid(row=2, column=0, columnspan=3, padx=6, pady=(0, 4), sticky="we")
        ttk.Label(ratio_row, text="視窗比例").pack(side=tk.LEFT)
        self.profile_combo = ttk.Combobox(ratio_row, state="readonly", width=22, values=[])
        self.profile_combo.pack(side=tk.LEFT, padx=(6, 4))
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_choice)
        ttk.Button(ratio_row, text="重新偵測",
                   command=lambda: self._autodetect_profile(force=True)).pack(side=tk.LEFT, padx=4)
        ttk.Button(ratio_row, text="另存為目前比例的校準",
                   command=self._save_profile_for_current).pack(side=tk.LEFT, padx=4)
        ttk.Button(ratio_row, text="刪除這一組",
                   command=self._delete_selected_profile).pack(side=tk.LEFT, padx=4)

        self.profile_status = ttk.Label(win_box, text="", foreground="#06c", justify="left",
                                       wraplength=760)
        self.profile_status.grid(row=3, column=0, columnspan=3, padx=6, pady=(0, 8), sticky="w")

        # 「把目前這個畫面原封不動存成 PNG」。
        # 為什麼需要這顆：截圖貼進對話會被重新壓縮縮小，內建標記樣板就是這樣變成
        # 1024 寬的糊圖（實機 1365 → 比對時放大 1.33 倍 → 選牌分數只有 37%）。
        # 自己用截圖工具裁也很容易連標題列一起裁進去、或是被系統縮放動過。
        # 由程式直接抓「用戶端區域」存檔，尺寸一定正確，檔名也帶著解析度。
        shot_row = ttk.Frame(win_box)
        shot_row.grid(row=4, column=0, columnspan=3, padx=6, pady=(0, 8), sticky="we")
        ttk.Button(shot_row, text="存一張目前畫面（PNG）",
                   command=self._save_debug_shot).pack(side=tk.LEFT)
        self.shot_status = ttk.Label(shot_row, text="", foreground="#555", justify="left",
                                     wraplength=520)
        self.shot_status.pack(side=tk.LEFT, padx=(8, 0))

        ctrl_box = ttk.LabelFrame(frame, text="自動遊玩")
        ctrl_box.pack(fill=tk.X, padx=8, pady=6)

        self.state_label = ttk.Label(ctrl_box, text="狀態：停止中", font=("Microsoft JhengHei", 14, "bold"))
        self.state_label.grid(row=0, column=0, padx=10, pady=8, sticky="w")

        self.start_btn = ttk.Button(ctrl_box, text="▶ 啟動 (F9)", command=self._start_bot)
        self.start_btn.grid(row=0, column=1, padx=6, pady=8)
        self.stop_btn = ttk.Button(ctrl_box, text="■ 停止", command=self._stop_bot, state=tk.DISABLED)
        self.stop_btn.grid(row=0, column=2, padx=6, pady=8)

        ttk.Button(ctrl_box, text="⤡ 縮成迷你視窗", command=self._show_mini_panel
                   ).grid(row=0, column=3, padx=6, pady=8)

        self.dry_run_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ctrl_box, text="除錯模式（只判斷、不實際點擊。每次按啟動會依目前勾選狀態套用）",
                        variable=self.dry_run_var).grid(row=1, column=0, columnspan=3, padx=10, sticky="w")

        self.preview_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ctrl_box, text="顯示即時辨識結果", variable=self.preview_var,
                        command=self._toggle_preview).grid(row=2, column=0, columnspan=3, padx=10,
                                                           pady=(0, 6), sticky="w")

        self.preview_label = ttk.Label(ctrl_box, text="即時辨識：未啟用", foreground="#0a6")
        self.preview_label.grid(row=3, column=0, columnspan=3, padx=10, pady=(0, 8), sticky="w")

        ttk.Label(
            ctrl_box,
            text="流程：自己進入牌桌畫面 → 按「啟動」或熱鍵 F9 → 達每日上限被踢出牌桌時會自動停止。"
                 "緊急停止：F10，或把滑鼠移到螢幕角落。",
            foreground="#666", wraplength=520, justify="left",
        ).grid(row=4, column=0, columnspan=3, padx=10, pady=(0, 8), sticky="w")

        stats_box = ttk.LabelFrame(frame, text="今日統計")
        stats_box.pack(fill=tk.X, padx=8, pady=6)
        self.stats_label = ttk.Label(stats_box, text="—", font=("Consolas", 10))
        self.stats_label.pack(padx=10, pady=(8, 2), anchor="w")

        recon_row = ttk.Frame(stats_box)
        recon_row.pack(fill=tk.X, padx=10, pady=(0, 4))
        self.reconcile_btn = ttk.Button(recon_row, text="補算未記錄的數值",
                                       command=self._reconcile_stats)
        self.reconcile_btn.pack(side=tk.LEFT)
        ttk.Label(recon_row,
                  text="從執行紀錄與其他安裝的統計檔找出還沒算進機率模型的牌（可以重複按，不會重複計算）",
                  foreground="#777", wraplength=560, justify="left").pack(side=tk.LEFT, padx=8)

        self.reconcile_status = ttk.Label(stats_box, text="", foreground="#06c",
                                         wraplength=760, justify="left")
        self.reconcile_status.pack(padx=10, pady=(0, 8), anchor="w")

        upd_box = ttk.LabelFrame(frame, text="版本與更新")
        upd_box.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(upd_box, text=f"目前版本　v{APP_VERSION}",
                  font=("Microsoft JhengHei", 10, "bold")).grid(
            row=0, column=0, padx=10, pady=(8, 2), sticky="w")
        self.update_btn = ttk.Button(upd_box, text="檢查更新", command=self._check_update)
        self.update_btn.grid(row=0, column=1, padx=6, pady=(8, 2))
        self.update_status = ttk.Label(upd_box, text="尚未檢查", foreground="#555",
                                       wraplength=640, justify="left")
        self.update_status.grid(row=1, column=0, columnspan=2, padx=10,
                                pady=(0, 8), sticky="w")
        upd_box.columnconfigure(0, weight=1)

        log_box = ttk.LabelFrame(frame, text="執行紀錄")
        log_box.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        self.log_text = tk.Text(log_box, height=6, wrap="word", state=tk.DISABLED,
                                font=("Consolas", 9), bg="#1e1e1e", fg="#dcdcdc")
        scroll = ttk.Scrollbar(log_box, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0), pady=6)
        scroll.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 6), pady=6)
        # 主控台現在整頁包在捲動區裡，而捲動區是用 bind_all 接管滾輪的。
        # 不特別處理的話，滑鼠停在執行紀錄上轉滾輪會捲整頁而不是捲紀錄 ——
        # 這塊有自己的捲軸，滾輪本來就該歸它。
        self._keep_wheel_local(self.log_text)

    # ---------- 校準 ----------

    def _build_calib_tab(self) -> None:
        # 同樣包進捲動區：上面那段說明加上五排按鈕就佔掉不少高度，
        # 視窗縮小時校準清單與預覽會被壓到看不見。
        frame = self._scrollable(self.tab_calib)

        ttk.Label(
            frame,
            text="依序校準會照遊戲流程分組，每換一組會先跳出提示，請把遊戲切到該畫面再開始框選。\n"
                 "實際流程：投注並開始 → 點要保留的牌 → 替換 → Congratulations 點擊繼續 → 進行挑戰/取消 → 大/小 → 失敗再一次。\n"
                 "框選時：畫面角落有一張半透明「範例圖」告訴你這一項該框到哪（按 H 可隱藏），"
                 "遊戲畫面上的虛線框／十字則是建議位置，照著描或自己調整都可以。\n"
                 "半透明瞄點不是當機；HoloTool 會留在工作列。Esc 跳過這一項，Q / 右鍵結束校準。\n"
                 "也可以先按「套用截圖預設框選」，之後再只微調對不準的項目。\n"
                 "滑鼠移到清單項目上，右邊會顯示遊戲截圖與框選位置（不會蓋住遊戲）。",
            foreground="#555",
            justify="left",
        ).pack(anchor="w", padx=10, pady=8)

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X, padx=10)
        ttk.Button(btn_row, text="依序校準全部項目", command=self._calibrate_all).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="校準選取項目", command=self._calibrate_selected).pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_row, text="套用截圖預設框選", command=self._apply_screenshot_defaults).pack(side=tk.LEFT, padx=6)
        self.preview_all_btn = ttk.Button(btn_row, text="預覽全部框選", command=self._toggle_preview_all)
        self.preview_all_btn.pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_row, text="重新整理狀態", command=self._refresh_calib_status).pack(side=tk.LEFT, padx=6)

        # 比例的挑選與管理都在「主控台」——這裡只講「你現在校準的是哪一組」，
        # 因為那是校準時唯一需要知道的事（校準結果只會寫進這一組）。
        self.calib_profile_banner = ttk.Label(
            frame, text="", foreground="#06c", justify="left", wraplength=900,
            font=("Microsoft JhengHei", 10, "bold"))
        self.calib_profile_banner.pack(anchor="w", padx=10, pady=(10, 0))

        self.calib_progress = ttk.Label(frame, text="滑鼠移到項目上即可預覽框選位置",
                                        font=("Microsoft JhengHei", 11, "bold"), foreground="#06c")
        self.calib_progress.pack(anchor="w", padx=10, pady=(8, 0))

        body = ttk.Frame(frame)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

        columns = ("name", "status", "value")
        self.calib_tree = ttk.Treeview(body, columns=columns, show="headings", height=8)
        self.calib_tree.heading("name", text="項目")
        self.calib_tree.heading("status", text="狀態")
        self.calib_tree.heading("value", text="已記錄的比例座標")
        self.calib_tree.column("name", width=200, minwidth=110)
        self.calib_tree.column("status", width=60, minwidth=44, anchor="center", stretch=False)
        self.calib_tree.column("value", width=200, minwidth=90)
        self.calib_tree.grid(row=0, column=0, sticky="nsew")
        self.calib_tree.bind("<Double-1>", lambda _e: self._calibrate_selected())
        self.calib_tree.bind("<Motion>", self._on_calib_hover)
        self.calib_tree.bind("<<TreeviewSelect>>", self._on_calib_select)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self.calib_tree.tag_configure("done", foreground="#0a6")
        self.calib_tree.tag_configure("todo", foreground="#c33")
        # 校準清單有十幾項、自己會捲，滾輪歸它（同上，捲動區用 bind_all 接管滾輪）
        self._keep_wheel_local(self.calib_tree)

        preview_box = ttk.LabelFrame(body, text="框選預覽（遊戲截圖）")
        preview_box.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.calib_preview_label = tk.Label(
            preview_box,
            text="點選或滑過左邊項目，這裡會顯示遊戲畫面與框的位置",
            anchor="center",
            justify="center",
            wraplength=420,
            bg="#111111",
            fg="#dddddd",
        )
        self.calib_preview_label.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self._calib_overlay.attach(self.calib_preview_label)

        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

    # ---------- 點數 / 花色樣板 ----------

    # 一格「抓到的牌」大約要多寬（含 padding）。用來算視窗縮小時要排成幾欄。
    _PART_CELL_WIDTH = 92

    def _build_template_tab(self) -> None:
        # 舊版是「左邊六格、右邊蒐集進度」左右並排的固定 grid，視窗一縮小
        # 六格就被切掉、而且沒有捲軸可以捲過去 —— 等於整個功能消失。
        # 現在整頁包進捲動區，六格會依寬度自動換行，蒐集進度改放最下面並可收合。
        frame = self._scrollable(self.tab_tmpl)

        self.tmpl_help = ttk.Label(
            frame,
            text="辨識牌面只需要「13 個點數 + 4 個花色」，不用蒐集 52 張整卡。"
                 "在選牌畫面按「讀取目前畫面」，程式會自動把五張手牌的左上角切出來並猜出牌面；"
                 "確認無誤就按「全部儲存」，猜錯的自己改掉再存。玩一兩局就能湊齊。"
                 "代號格式：點數 + 花色，例如 10H、AS、QD。"
                 "花色 S=黑桃 H=紅心 D=方塊 C=梅花；不要的那格清空即可。"
                 "鬼牌（中間印著 JOKER 那張）代號填 JK —— 它沒有花色，"
                 "只會存左上角那個「$」當點數樣板，但一定要抓，"
                 "否則抽到鬼牌那一格永遠認不出來、選牌畫面卡在 4/5。"
                 "代號欄只吃英數字（大小寫都一樣），並且關掉了輸入法，不必再切中英文。",
            foreground="#555",
            justify="left",
            wraplength=620,
        )
        self.tmpl_help.pack(anchor="w", padx=10, pady=8, fill=tk.X)
        self._wrap_to_width(self.tmpl_help, frame)

        bar = ttk.Frame(frame)
        bar.pack(fill=tk.X, padx=10)
        ttk.Button(bar, text="讀取目前畫面", command=self._capture_parts).pack(side=tk.LEFT)
        ttk.Button(bar, text="全部儲存", command=self._save_parts).pack(side=tk.LEFT, padx=6)
        self.parts_hint = ttk.Label(bar, text="", foreground="#777", wraplength=320,
                                    justify="left")
        self.parts_hint.pack(side=tk.LEFT, padx=10)

        cards_box = ttk.LabelFrame(frame, text="這一輪抓到的牌")
        cards_box.pack(fill=tk.X, expand=False, padx=10, pady=6)
        self._parts_grid = ttk.Frame(cards_box)
        self._parts_grid.pack(fill=tk.X, padx=4, pady=4)

        self._part_slots = []
        self._part_cells = []
        for i in range(6):
            cell = ttk.Frame(self._parts_grid)
            # 手牌 1~5 只存在於選牌畫面，比大小那張只存在於比大小畫面 ——
            # 兩個畫面不可能同時出現。所以名稱要講清楚是哪個畫面的，
            # 而且 _capture_parts() 只會填「目前這個畫面」那一組（見那邊的說明）。
            name = f"手牌{i + 1}" if i < 5 else "★ 比大小"
            colour = "#666" if i < 5 else "#a05000"
            ttk.Label(cell, text=name, foreground=colour).pack()
            preview = ttk.Label(cell, text="—", anchor="center", relief="groove", width=6)
            preview.pack(pady=2, ipady=12)
            var = tk.StringVar()
            entry = ttk.Entry(cell, width=5, textvariable=var,
                              font=("Consolas", 11), justify="center")
            entry.pack()
            self._restrict_to_label_chars(var)
            self._detach_ime(entry)
            self._part_cells.append(cell)
            self._part_slots.append({"preview": preview, "var": var, "img": None, "parts": None})

        self._parts_columns = 0
        self._parts_grid.bind("<Configure>", self._reflow_part_cells)
        self._reflow_part_cells()

        progress = CollapsibleSection(
            frame, "蒐集進度",
            "看還缺哪些點數/花色，以及刪掉不要的樣板檔。", expanded=False)
        progress.pack(fill=tk.X, padx=10, pady=(6, 0))
        self.tmpl_section = progress
        right = progress.body

        self.tmpl_progress = ttk.Label(right, text="", font=("Microsoft JhengHei", 10, "bold"),
                                       wraplength=420, justify="left")
        self.tmpl_progress.pack(anchor="w", padx=10, pady=(8, 2), fill=tk.X)
        self.tmpl_missing = tk.Text(right, height=3, wrap="word", state=tk.DISABLED,
                                    font=("Consolas", 9), background="#f7f7f7")
        self.tmpl_missing.pack(fill=tk.X, padx=10, pady=4)

        ttk.Label(right, text="已存在的樣板檔：").pack(anchor="w", padx=10, pady=(6, 2))
        list_wrap = ttk.Frame(right)
        list_wrap.pack(fill=tk.X, padx=10, pady=(0, 6))
        self.tmpl_list = tk.Listbox(list_wrap, font=("Consolas", 9), height=8)
        list_scroll = ttk.Scrollbar(list_wrap, command=self.tmpl_list.yview)
        self.tmpl_list.configure(yscrollcommand=list_scroll.set)
        self.tmpl_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        list_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        # 清單自己有捲軸，滾輪不要被外層的捲動區搶走
        self._keep_wheel_local(self.tmpl_list)

        btns = ttk.Frame(right)
        btns.pack(anchor="w", padx=10, pady=(0, 10))
        ttk.Button(btns, text="刪除選取的樣板檔", command=self._delete_template).pack(side=tk.LEFT)
        ttk.Button(btns, text="清掉所有內建樣板", command=self._delete_bundled_templates).pack(
            side=tk.LEFT, padx=(6, 0))
        ttk.Button(btns, text="清掉裁切壞的樣板", command=self._delete_junk_templates).pack(
            side=tk.LEFT, padx=(6, 0))

    def _reflow_part_cells(self, _event=None) -> None:
        """依目前寬度決定六格排成幾欄。視窗縮小就換行，不會被切掉。"""
        width = self._parts_grid.winfo_width()
        if width <= 1:                     # 還沒 layout 過，先用一整排
            columns = len(self._part_cells)
        else:
            columns = max(1, min(len(self._part_cells), width // self._PART_CELL_WIDTH))
        if columns == self._parts_columns:
            return                          # 欄數沒變就不要重排（Configure 會連發）
        self._parts_columns = columns
        for index, cell in enumerate(self._part_cells):
            cell.grid(row=index // columns, column=index % columns,
                      padx=2, pady=4, sticky="n")

    # ------------------------------------------------- 代號輸入框

    def _restrict_to_label_chars(self, var: tk.StringVar) -> None:
        """代號欄只吃英數字，小寫自動變大寫，全形自動折成半角。

        用 trace 而不是 validatecommand：validate 只能「接受或拒絕」，沒辦法把
        使用者打的小寫改成大寫；而中文輸入法是「一次上一整串字」，
        攔在變數層最乾淨 —— 貼上、輸入法上字、程式自己 set，三種都涵蓋。

        正規化是 idempotent 的（normalize(normalize(x)) == normalize(x)），
        所以「改完再觸發一次 trace」最多只會多跑一輪就停，不會無限遞迴。
        """
        def on_write(*_args):
            current = var.get()
            cleaned = normalize_label_input(current)
            if cleaned != current:
                var.set(cleaned)

        var.trace_add("write", on_write)

    def _detach_ime(self, widget: tk.Widget) -> None:
        """在這個輸入框上關掉輸入法（只有 Windows 有效）。

        症狀：每次按「讀取目前畫面」，焦點移到代號欄，Windows 就把輸入法切回
        中文 —— 剛剛用 Shift 切成英文的狀態**不會跟著焦點走**，所以每抓一輪牌
        都要重切一次。

        `ImmAssociateContext(hwnd, 0)` 把這個視窗跟輸入法解除關聯，之後打什麼
        字就直接進來，跟輸入法目前是中文還是英文完全無關。焦點回來時再做一次，
        因為 Tk 重建視窗（unmap/map）會讓關聯復原。

        失敗就安靜跳過（不是 Windows、或 Tk 沒給這個 widget 自己的 HWND）——
        上面那層 trace 仍然會把非英數字擋掉，只是使用者得自己切輸入法。
        """
        if os.name != "nt":
            return

        def detach(_event=None):
            try:
                import ctypes
                ctypes.windll.imm32.ImmAssociateContext(widget.winfo_id(), 0)
            except Exception:   # noqa: BLE001
                pass            # 這只是便利功能，絕對不值得讓 GUI 起不來

        detach()
        widget.bind("<FocusIn>", detach, add="+")

    # ---------- 設定 ----------

    def _build_settings_tab(self) -> None:
        # 二十幾列設定，一定超過小視窗的高度，整頁包進捲動區
        frame = self._scrollable(self.tab_settings)
        ttk.Label(frame, text="改完記得按最下面的「儲存設定」。下次啟動時生效。",
                  foreground="#555", wraplength=560, justify="left").pack(
                      anchor="w", padx=10, pady=8)

        self.setting_vars: dict[str, tk.StringVar] = {}
        self.setting_sections: dict[str, CollapsibleSection] = {}

        for title, subtitle, fields, expanded in SETTING_SECTIONS:
            section = CollapsibleSection(frame, title, subtitle, expanded=expanded)
            section.pack(fill=tk.X, padx=10, pady=(6, 0))
            self.setting_sections[title] = section
            self._build_setting_rows(section.body, fields)

        # 其餘欄位直接攤開。一定要有自己的標題 —— 不然它們緊接在收合起來的
        # 「動作時間設定」下面，看起來像是那一區的內容。
        ttk.Label(frame, text="其他設定", font=("Microsoft JhengHei", 10, "bold")).pack(
            anchor="w", padx=12, pady=(14, 0))
        box = ttk.Frame(frame)
        box.pack(fill=tk.X, padx=26, pady=(4, 0))
        self._build_setting_rows(box, OTHER_FIELDS)

        # 分組之後多了一種很難發現的壞法：欄位在完整清單裡、但沒有被任何一區畫出來，
        # 於是它在畫面上不存在、存檔時也不會被寫。這裡直接對一次帳。
        missing = [key for key, _n, _h in SETTING_FIELDS if key not in self.setting_vars]
        if missing:
            logger.log(f"[警告] 這些設定欄位沒有被畫出來，存檔時會被忽略：{missing}")

        opt_box = ttk.Frame(frame)
        opt_box.pack(fill=tk.X, padx=26)
        self.ace_high_var = tk.BooleanVar(value=bool(self.cfg.get("ace_high", True)))
        self.failsafe_var = tk.BooleanVar(value=bool(self.cfg.get("pyautogui_failsafe", True)))
        ttk.Checkbutton(opt_box, text="比大小時 A 視為最大牌",
                        variable=self.ace_high_var).pack(anchor="w", padx=4, pady=4)
        ttk.Checkbutton(opt_box, text="啟用滑鼠移到螢幕角落緊急中止（建議開啟）",
                        variable=self.failsafe_var).pack(anchor="w", padx=4, pady=4)

        ttk.Label(frame,
                  text="小數點打不進去的話（中文輸入法常會吃掉數字鍵盤那顆「.」），"
                       "直接打「。」「，」或全形數字都可以，存檔時會自動換成半角。",
                  foreground="#777", wraplength=560, justify="left").pack(
                      anchor="w", padx=10, pady=(10, 0))

        btn_row = ttk.Frame(frame)
        btn_row.pack(anchor="w", padx=14, pady=12)
        ttk.Button(btn_row, text="儲存設定", command=self._save_settings).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="全部還原成預設值",
                   command=self._reset_settings_to_default).pack(side=tk.LEFT, padx=(8, 0))

    def _build_setting_rows(self, parent, fields) -> None:
        """把一組 (key, 名稱, 說明) 排成三欄。所有 StringVar 都收進 setting_vars，
        存檔與「還原預設值」不必知道欄位被分到哪一區。"""
        for row, (key, name, hint) in enumerate(fields):
            ttk.Label(parent, text=name).grid(row=row, column=0, sticky="w", padx=4, pady=4)
            try:
                current = get_by_path(self.cfg, key)
            except (KeyError, IndexError, TypeError):
                current = ""
            var = tk.StringVar(value=str(current))
            self.setting_vars[key] = var
            entry = ttk.Entry(parent, textvariable=var, width=12)
            entry.grid(row=row, column=1, padx=4, pady=4)
            self._allow_numpad_decimal(entry)
            ttk.Label(parent, text=hint, foreground="#777", wraplength=380,
                      justify="left").grid(row=row, column=2, sticky="w", padx=8)
        parent.columnconfigure(2, weight=1)

    def _allow_numpad_decimal(self, entry: tk.Widget) -> None:
        """讓數字鍵盤那顆小數點在中文輸入法下也能用。

        Tk 收到的 keysym 是 KP_Decimal，但輸入法可能把字元換成「。」或整個吃掉。
        直接綁 keysym 自己插入一個半角句號，就跟輸入法狀態無關了。
        """
        def insert_dot(_event=None):
            try:
                entry.insert(tk.INSERT, ".")
            except tk.TclError:
                return None
            return "break"

        for sequence in ("<KP_Decimal>", "<KP_Separator>"):
            try:
                entry.bind(sequence, insert_dot)
            except tk.TclError:
                pass   # 某些 Tk 版本沒有這個 keysym，忽略即可

    def _reset_settings_to_default(self) -> None:
        """把設定欄位填回程式內建預設值（**只改畫面上的欄位，不會存檔**）。

        刻意不直接寫進 config：使用者可以先看到預設值是多少，決定要不要按儲存。
        校準座標與樣板完全不在這個範圍內，不會被動到。
        """
        if not messagebox.askyesno(
            "還原成預設值？",
            "會把上面每一個數值欄位填回程式內建的預設值。\n\n"
            "校準座標與卡牌樣板不會被動到。\n"
            "填回來之後還要按「儲存設定」才會真的生效，不想要就直接切走分頁。",
        ):
            return
        restored = 0
        for key, var in self.setting_vars.items():
            try:
                var.set(str(get_by_path(DEFAULT_CONFIG, key)))
                restored += 1
            except (KeyError, IndexError, TypeError):
                continue
        self.ace_high_var.set(bool(DEFAULT_CONFIG.get("ace_high", True)))
        self.failsafe_var.set(bool(DEFAULT_CONFIG.get("pyautogui_failsafe", True)))
        logger.log(f"已把 {restored} 個設定欄位填回預設值（還沒存檔）")

    # ------------------------------------------------------- 視窗 / 狀態

    def _refresh_windows(self) -> None:
        self._windows = [(hwnd, title) for hwnd, title in win_mod.list_visible_windows()]
        titles = [title for _hwnd, title in self._windows]
        self.window_combo["values"] = titles

        current = self.cfg.get("window_title_substring", "")
        if current:
            for i, title in enumerate(titles):
                if current.lower() in title.lower():
                    self.window_combo.current(i)
                    break
        self._update_window_status()

    def _use_selected_window(self) -> None:
        idx = self.window_combo.current()
        if idx < 0:
            messagebox.showwarning("尚未選擇", "請先從清單挑選遊戲視窗。")
            return
        _hwnd, title = self._windows[idx]
        self.cfg["window_title_substring"] = title
        self.cfg["config_version"] = CONFIG_VERSION
        # 這裡**故意不動** cfg["calibration"]。
        # 舊版會把「目前視窗尺寸」直接寫成校準尺寸，但座標一個都沒重新量 ——
        # 結果是把視窗調成別的長寬比、按一下「使用此視窗」，長寬比警告就消失了，
        # 而所有框選其實還是上一個比例的值。沉默地壞掉比報錯難查得多。
        # 校準尺寸只有在真的重新框選（_run_calibration）時才該更新。
        save_config(self.cfg)
        logger.log(f"已設定遊戲視窗：{title}")
        # 選好視窗的那一刻就把比例抓出來、套上對應的那組校準。
        # 沒有對應的那一組時 select_for_window() 會從最接近的**精確換算**一組出來
        # （遊戲的 UI 排在置中的 16:9 內容框裡，換算是像素級精確的）。
        self._detected_size = None
        self._autodetect_profile()
        self._update_window_status()

    def _find_hwnd(self) -> int | None:
        title = self.cfg.get("window_title_substring", "")
        return win_mod.find_window_by_title(title) if title else None

    def _update_window_status(self) -> None:
        title = self.cfg.get("window_title_substring", "")
        if not title:
            self.window_status.config(text="尚未設定遊戲視窗（請從上方清單選擇後按「使用此視窗」）",
                                      foreground="#c33")
            return
        hwnd = self._find_hwnd()
        if hwnd is None:
            self.window_status.config(text=f"設定為「{title}」，但目前找不到這個視窗（遊戲沒開？）",
                                      foreground="#c33")
            return
        rect = win_mod.get_client_rect_on_screen(hwnd)
        ref = self.cfg.get("calibration", {})
        ref_w, ref_h = ref.get("client_width", 0), ref.get("client_height", 0)
        aspect_label = profiles_mod.label_for(rect.width, rect.height)
        msg = f"已鎖定「{title}」  目前尺寸 {rect.width}x{rect.height}（{aspect_label}）"
        color = "#0a6"

        # 目前這個長寬比有沒有專屬的校準？沒有的話一定要講，因為位置會歪。
        match, delta_profile = profiles_mod.find_match(
            self.cfg, rect.width, rect.height,
            self.cfg.get("aspect_ratio_tolerance", 0.02))
        tolerance = self.cfg.get("aspect_ratio_tolerance", 0.02)
        if match is None:
            msg += "  ⚠ 還沒有任何校準"
            color = "#c33"
        elif delta_profile > tolerance:
            msg += f"  ⚠ 沒有 {aspect_label} 的校準，借用「{match.get('label')}」（差 {delta_profile:.1%}）"
            color = "#c33"
        elif match.get("seeded_from"):
            msg += f"  ⚠ {aspect_label} 這組是從「{match['seeded_from']}」複製的，尚未校準"
            color = "#c60"
        else:
            msg += f"  |  使用 {match.get('label')} 的校準"

        if ref_w > 0 and ref_h > 0:
            scale = scale_factor(rect.width, ref_w)
            msg += f"（量於 {ref_w}x{ref_h}，縮放 {scale:.2f} 倍）"
            if color == "#0a6" and scale < 0.6:
                msg += "  ⚠ 視窗偏小，辨識率可能下降"
                color = "#c60"
        self.window_status.config(text=msg, foreground=color)

    # ----------------------------------------------------------- 校準

    def _refresh_calib_status(self) -> None:
        self._refresh_profiles()
        self.calib_tree.delete(*self.calib_tree.get_children())
        for i, (kind, path, name, _hint, _group) in enumerate(CALIB_TARGETS):
            value = get_by_path(self.cfg, path)
            if kind == "region":
                done = value.get("w", 0) > 0 and value.get("h", 0) > 0
                shown = (f"x={value['x']:.4f} y={value['y']:.4f} "
                         f"w={value['w']:.4f} h={value['h']:.4f}") if done else ""
            else:
                done = value.get("x", 0) > 0 or value.get("y", 0) > 0
                shown = f"x={value['x']:.4f} y={value['y']:.4f}" if done else ""
            self.calib_tree.insert(
                "", tk.END, iid=str(i),
                values=(name, "已完成" if done else "未校準", shown),
                tags=("done" if done else "todo",),
            )

    def _set_calib_progress(self, text: str) -> None:
        self.calib_progress.config(text=text)
        try:
            self.update_idletasks()
        except tk.TclError:
            pass

    def _on_tab_changed(self, _event=None) -> None:
        current = self.notebook.select()
        if current != str(self.tab_calib):
            self._calib_preview_all = False
            self._hover_calib_iid = None
            if hasattr(self, "preview_all_btn"):
                self.preview_all_btn.config(text="預覽全部框選")
            self._hide_calib_preview()

    def _on_calib_hover(self, event) -> None:
        row = self.calib_tree.identify_row(event.y)
        if not row:
            return
        if row == self._hover_calib_iid:
            return
        self._hover_calib_iid = row
        try:
            index = int(row)
        except ValueError:
            index = self.calib_tree.index(row)
        if self._calib_preview_all:
            self._show_calib_preview(list(range(len(CALIB_TARGETS))), highlight_index=index)
        else:
            self._show_calib_preview([index], highlight_index=index)

    def _on_calib_select(self, _event=None) -> None:
        selection = self.calib_tree.selection()
        if not selection:
            return
        row = selection[0]
        self._hover_calib_iid = row
        try:
            index = int(row)
        except ValueError:
            index = self.calib_tree.index(row)
        if self._calib_preview_all:
            self._show_calib_preview(list(range(len(CALIB_TARGETS))), highlight_index=index)
        else:
            self._show_calib_preview([index], highlight_index=index)

    def _toggle_preview_all(self) -> None:
        self._calib_preview_all = not self._calib_preview_all
        if self._calib_preview_all:
            self.preview_all_btn.config(text="關閉全部預覽")
            self._show_calib_preview(list(range(len(CALIB_TARGETS))))
        else:
            self.preview_all_btn.config(text="預覽全部框選")
            self._hide_calib_preview()

    def _hide_calib_preview(self) -> None:
        if hasattr(self, "_calib_overlay"):
            self._calib_overlay.hide()

    def _show_calib_preview(self, indices: list[int], highlight_index: int | None = None) -> None:
        hwnd = self._find_hwnd()
        if hwnd is None:
            self._set_calib_progress("找不到遊戲視窗，無法預覽（請先在主控台選好視窗）")
            self._hide_calib_preview()
            return

        items: list[tuple[str, str, dict, str]] = []
        missing_name = None
        for i in indices:
            kind, path, name, _hint, _group = CALIB_TARGETS[i]
            value = get_by_path(self.cfg, path)
            if kind == "region":
                ready = value.get("w", 0) > 0 and value.get("h", 0) > 0
            else:
                ready = bool(value.get("x") or value.get("y"))
            if not ready:
                if highlight_index == i:
                    missing_name = name
                continue
            if i == highlight_index:
                color = "#ffff33"
            elif kind == "region":
                color = "#00ff88"
            else:
                color = "#66ddff"
            items.append((kind, name, value, color))

        if not items:
            msg = f"「{missing_name}」還沒校準，沒有可預覽的框" if missing_name else "沒有可預覽的校準項目"
            self._set_calib_progress(msg)
            self._hide_calib_preview()
            return

        if highlight_index is not None:
            kind, _path, name, _hint, _group = CALIB_TARGETS[highlight_index]
            self._set_calib_progress(f"預覽中：{name}（{'框選區域' if kind == 'region' else '點擊位置'}）")
        elif self._calib_preview_all:
            self._set_calib_progress(f"預覽全部框選（{len(items)} 項）。再按一次按鈕可關閉。")
        self._calib_overlay.show(hwnd, items)

    def _safe_restore_window(self) -> None:
        """把主視窗叫回最前面。校準過程不再隱藏視窗，但仍保留這個保險。"""
        try:
            self.deiconify()
            self.state("normal")
            self.lift()
            self.attributes("-topmost", True)
            self.after(200, lambda: self.attributes("-topmost", False))
            self.update()
        except tk.TclError:
            pass

    def _calibrate_selected(self) -> None:
        selection = self.calib_tree.selection()
        if not selection:
            messagebox.showinfo("請先選擇", "請在清單中點選一個要校準的項目。")
            return
        index = self.calib_tree.index(selection[0])
        self._run_calibration([CALIB_TARGETS[index]])

    def _calibrate_all(self) -> None:
        self._run_calibration(CALIB_TARGETS)

    # ------------------------------------------------- 長寬比校準組

    AUTO_CHOICE = "自動偵測（建議）"

    def _client_size(self) -> tuple[int, int] | None:
        hwnd = self._find_hwnd()
        if hwnd is None:
            return None
        try:
            rect = win_mod.get_client_rect_on_screen(hwnd)
        except Exception:  # noqa: BLE001
            return None
        if rect.width <= 0 or rect.height <= 0:
            return None
        return rect.width, rect.height

    def _autodetect_profile(self, force: bool = False, announce: bool = True) -> None:
        """依視窗當下的尺寸挑（或換算出）對應比例的校準並套用。

        `force=False` 時只在尺寸真的變了、**而且已經穩定下來**才做事：

        * 每次都重跑 select_for_window 等於每兩秒寫一次 config.json。
        * 更重要的是，慢慢用滑鼠把視窗拖大的過程會經過一堆怪比例（2.08:1、
          1.95:1…），每一種都會生出一組換算來的 profile，把 profile 數量頂到上限
          之後就開始淘汰。所以要等「連續兩次量到同一個尺寸」才動作 ——
          拖曳過程中的中間值不會留下任何東西。

        使用者手動指定過比例（`auto_detect_profile` = False）就完全不介入，
        除非他自己按「重新偵測」（那顆會 force=True 並把自動偵測打開）。
        """
        size = self._client_size()
        if size is None:
            return
        if force:
            self.cfg["auto_detect_profile"] = True
        elif not self.cfg.get("auto_detect_profile", True):
            return
        elif size == getattr(self, "_detected_size", None):
            self._settling_size = None
            return
        elif size != getattr(self, "_settling_size", None):
            self._settling_size = size    # 還在拖，下次再看看有沒有停下來
            return

        self._settling_size = None
        self._detected_size = size
        try:
            selection = profiles_mod.select_for_window(self.cfg, size[0], size[1])
        except Exception as e:  # noqa: BLE001
            logger.log(f"[警告] 自動偵測視窗比例失敗：{e!r}")
            return
        save_config(self.cfg)
        if announce or selection.get("switched"):
            logger.log(f"視窗 {size[0]}x{size[1]} → " + profiles_mod.summarize_selection(selection))
            if selection.get("switched") and self.bot is not None and self.bot.running:
                logger.log("[注意] 執行中換了校準組。若點擊位置變得不準，請停止後重新啟動。")
        self._refresh_calib_status()
        self._update_window_status()

    def _on_profile_choice(self, _event=None) -> None:
        """使用者從下拉選單手動指定比例。

        手動指定之後就關掉自動偵測 —— 不然視窗尺寸一變（或下次啟動）馬上就被
        自動偵測改回去，使用者會覺得這個選單根本沒用。
        """
        choice = self.profile_combo.get()
        if choice == self.AUTO_CHOICE:
            # 先存檔再偵測：遊戲沒開的時候 _autodetect_profile() 量不到尺寸會直接
            # 返回，如果把「打開自動偵測」交給它做，這個選擇就悄悄不見了 ——
            # 下一次 _refresh_profiles() 還會把選單跳回原本那一組。
            self.cfg["auto_detect_profile"] = True
            save_config(self.cfg)
            self._detected_size = None
            self._autodetect_profile(force=True)
            logger.log("視窗比例已改回自動偵測")
            self._refresh_profiles()
            return
        profile = profiles_mod.find_by_label(self.cfg, choice)
        if profile is None:
            return
        self.cfg["auto_detect_profile"] = False
        profiles_mod.activate(self.cfg, profile)
        save_config(self.cfg)
        logger.log(f"已手動指定使用「{choice}」的校準（自動偵測已關閉，"
                   "要恢復請把下拉選單改回「自動偵測」）")
        self._refresh_calib_status()
        self._update_window_status()

    def _refresh_profiles(self) -> None:
        if not hasattr(self, "profile_status"):
            return
        lines = profiles_mod.describe(self.cfg)
        labels = [p.get("label", "?") for p in profiles_mod.get_profiles(self.cfg)]
        auto = bool(self.cfg.get("auto_detect_profile", True))
        self.profile_combo.config(values=[self.AUTO_CHOICE] + labels)
        wanted = self.AUTO_CHOICE if auto else (self.cfg.get("active_profile") or "")
        if self.profile_combo.get() != wanted:
            self.profile_combo.set(wanted)

        head = ""
        size = self._client_size()
        if size is not None:
            width, height = size
            label = profiles_mod.label_for(width, height)
            tolerance = self.cfg.get("aspect_ratio_tolerance", 0.02)
            match, delta = profiles_mod.find_match(self.cfg, width, height, tolerance)
            mode = "自動偵測" if auto else "手動指定"
            if not auto:
                head = (f"{mode}：使用「{self.cfg.get('active_profile') or '（無）'}」這一組"
                        f"（目前視窗 {width}x{height} = {label}）\n")
            elif match is not None and delta <= tolerance:
                head = f"{mode}：目前視窗 {width}x{height} = {label}，使用「{match.get('label')}」這一組\n"
            else:
                borrowed = match.get("label") if match else "（無）"
                head = (f"{mode}：目前視窗 {width}x{height} = {label}，"
                        f"還沒有這個比例的校準（最接近的是「{borrowed}」）\n")
        body = "\n".join(f"　• {line}" for line in lines) if lines else "　（還沒有任何校準組）"
        self.profile_status.config(text=head + body)
        self._refresh_calib_banner()

    def _refresh_calib_banner(self) -> None:
        """校準分頁上方那一行：現在校準的是哪一組。"""
        if not hasattr(self, "calib_profile_banner"):
            return
        active = self.cfg.get("active_profile")
        cal = self.cfg.get("calibration") or {}
        width, height = cal.get("client_width") or 0, cal.get("client_height") or 0
        size = f"{width}x{height}" if width and height else "尺寸未紀錄"
        if active:
            text = f"正在校準：{active}（量於 {size}）　—　框選結果只會寫進這一組"
        else:
            pending = self.cfg.get(profiles_mod.PENDING_LABEL_KEY)
            text = (f"正在校準：{pending or '未知比例'}（{size}，尚未建立校準組，"
                    "存檔時會自動建立）")
        self.calib_profile_banner.config(
            text=text + "　—　比例的偵測與切換在「主控台」分頁")

    def _save_profile_for_current(self) -> None:
        hwnd = self._find_hwnd()
        if hwnd is None:
            messagebox.showerror("找不到遊戲視窗",
                                 "請先在「主控台」分頁選擇遊戲視窗，並確認遊戲正在執行。")
            return
        rect = win_mod.get_client_rect_on_screen(hwnd)
        label = profiles_mod.label_for(rect.width, rect.height)
        tolerance = self.cfg.get("aspect_ratio_tolerance", 0.02)
        existing, delta = profiles_mod.find_match(self.cfg, rect.width, rect.height, tolerance)
        if existing is not None and delta <= tolerance:
            if not messagebox.askyesno(
                "覆蓋這一組校準？",
                f"「{existing.get('label')}」已經有一組校準（量於 "
                f"{existing.get('client_width')}x{existing.get('client_height')}）。\n\n"
                "要用目前畫面上的座標覆蓋它嗎？\n"
                "（其他比例的校準不會被動到）",
            ):
                return
        profiles_mod.save_as(self.cfg, rect.width, rect.height, label)
        save_config(self.cfg)
        self._refresh_profiles()
        self._update_window_status()
        logger.log(f"已建立／更新「{label}」的校準組（{rect.width}x{rect.height}）。"
                   "接著請針對這個比例重新框選一次，框完的結果只會存進這一組。")

    def _delete_selected_profile(self) -> None:
        label = self.profile_combo.get()
        if label == self.AUTO_CHOICE:
            # 下拉選單停在「自動偵測」時，要刪的是目前正在生效的那一組
            label = self.cfg.get("active_profile") or ""
        if not label:
            messagebox.showinfo("尚未選擇", "請先從下拉選單選一組要刪除的比例。")
            return
        if not messagebox.askyesno(
            "刪除校準組？",
            f"要刪掉「{label}」這一組校準嗎？\n\n"
            "這組裡面的框選座標會一起消失，無法復原（其他比例不受影響）。",
        ):
            return
        if profiles_mod.remove(self.cfg, label):
            save_config(self.cfg)
            # 刪掉的可能就是生效中的那一組，回到自動偵測重新挑一組來用，
            # 否則畫面上會停在「沒有生效中的校準」這種半死狀態。
            self.cfg["auto_detect_profile"] = True
            self._detected_size = None
            self._autodetect_profile()
            self._refresh_profiles()
            self._update_window_status()
            logger.log(f"已刪除「{label}」的校準組。")

    # --------------------------------------------------- 校準的圖示提示

    def _calib_example(self, path: str):
        """這一項的範例圖（PIL Image）。做不出來就回 None，校準照舊能跑。

        範例圖只是提示，任何一步失敗（檔案缺了、PIL 出問題）都不該讓校準中斷 ——
        使用者正在對著遊戲畫面等，這時候丟一個例外是最糟的結果。
        """
        try:
            return calibguide.example_image(path)
        except Exception as e:  # noqa: BLE001
            logger.log(f"[提示] 讀取「{path}」的範例圖失敗，改用純文字說明：{e!r}")
            return None

    def _calib_guide_rect(self, kind: str, path: str, rect) -> dict | None:
        """把建議座標換算成螢幕絕對座標，交給遮罩畫成虛線框／十字。"""
        try:
            value = calibguide.suggested_value(self.cfg, path)
            if not value:
                return None
            if kind == "region":
                x, y, w, h = ratio_region_to_pixels(value, rect.width, rect.height)
                return {"kind": "region", "rect": (rect.left + x, rect.top + y, w, h)}
            x, y = ratio_point_to_pixels(value, rect.width, rect.height)
            radius = max(14, round(rect.width * 0.018))
            return {"kind": "point",
                    "rect": (rect.left + x - radius, rect.top + y - radius,
                             radius * 2, radius * 2)}
        except Exception as e:  # noqa: BLE001
            logger.log(f"[提示] 算不出「{path}」的建議位置，只顯示文字說明：{e!r}")
            return None

    def _run_calibration(self, targets: list[tuple[str, str, str, str, str]]) -> None:
        hwnd = self._find_hwnd()
        if hwnd is None:
            messagebox.showerror("找不到遊戲視窗",
                                 "請先在「主控台」分頁選擇遊戲視窗，並確認遊戲正在執行。")
            return

        self._hide_calib_preview()
        self._calib_preview_all = False
        if hasattr(self, "preview_all_btn"):
            self.preview_all_btn.config(text="預覽全部框選")

        rect = win_mod.get_client_rect_on_screen(hwnd)
        self.cfg["calibration"] = {"client_width": rect.width, "client_height": rect.height}

        done = skipped = 0
        aborted = False
        total = len(targets)
        last_group = None

        for index, (kind, path, name, hint, group) in enumerate(targets, 1):
            if group != last_group:
                last_group = group
                self._safe_restore_window()
                if not messagebox.askokcancel("切換遊戲畫面", f"{group}\n\n準備好後按「確定」開始框選這一組。\n按「取消」則結束校準。"):
                    aborted = True
                    break

            self._set_calib_progress(f"校準中 {index}/{total}：{name}（請看全螢幕半透明瞄點畫面）")
            try:
                self.lower()
                win_mod.bring_to_foreground(hwnd)
                self.update()
                time.sleep(0.2)
                rect = win_mod.get_client_rect_on_screen(hwnd)

                example = self._calib_example(path)
                guide = self._calib_guide_rect(kind, path, rect)
                instruction = f"【第 {index}/{total} 項】{name}\n{hint}"
                if example is None:
                    instruction += "\n（這一項沒有內建範例圖，請照上面的文字說明框選）"
                picker = select_region if kind == "region" else select_point
                result = picker(
                    instruction, self,
                    example=example,
                    example_caption=calibguide.caption_for(path),
                    guide=guide,
                    window_rect=(rect.left, rect.top, rect.width, rect.height),
                )
            except Exception as e:  # noqa: BLE001
                logger.log(f"[錯誤] 校準「{name}」時發生問題：{e!r}")
                self._safe_restore_window()
                continue
            finally:
                self._safe_restore_window()

            if result.status == "abort":
                logger.log("已中止校準流程")
                aborted = True
                break
            if result.status == "skip" or result.value is None:
                logger.log(f"校準已跳過：{name}")
                skipped += 1
                continue

            picked = result.value
            if kind == "region":
                value = pixels_region_to_ratio(
                    picked["x"] - rect.left, picked["y"] - rect.top,
                    picked["w"], picked["h"], rect.width, rect.height,
                )
            else:
                value = pixels_point_to_ratio(
                    picked["x"] - rect.left, picked["y"] - rect.top,
                    rect.width, rect.height,
                )
            set_by_path(self.cfg, path, value)
            save_config(self.cfg)
            logger.log(f"已校準 {name}")
            if path in UI_MARKER_FILES:
                self._save_one_ui_marker(path)
            done += 1
            self._refresh_calib_status()

        summary = f"校準結束：完成 {done} 項、跳過 {skipped} 項" + ("（已中止）" if aborted else "")
        self._set_calib_progress(summary)
        logger.log(summary)

        # 沒有單獨校準保留點時，預設用手牌區域中心
        for i in range(5):
            point = self.cfg["points"]["hold_toggles"][i]
            slot = self.cfg["regions"]["card_slots"][i]
            if not (point["x"] or point["y"]) and slot["w"] > 0:
                self.cfg["points"]["hold_toggles"][i] = {
                    "x": slot["x"] + slot["w"] / 2,
                    "y": slot["y"] + slot["h"] / 2,
                }
        # 「再一次」若跳過，沿用「進行挑戰」（兩個都是右下紫色按鈕）
        retry = self.cfg["points"].get("retry_button", {})
        challenge = self.cfg["points"].get("challenge_button", {})
        if not (retry.get("x") or retry.get("y")) and (challenge.get("x") or challenge.get("y")):
            self.cfg["points"]["retry_button"] = dict(challenge)
        save_config(self.cfg)
        self._refresh_calib_status()
        self._update_window_status()

    def _apply_screenshot_defaults(self) -> None:
        if not messagebox.askyesno(
            "套用截圖預設框選",
            "會用截圖量好的預設範圍覆蓋目前的校準座標，並還原畫面標記樣板。\n"
            "視窗標題不會改。之後仍可逐項微調。\n\n確定要套用嗎？",
        ):
            return
        apply_screenshot_layout(self.cfg, install_templates=True)
        save_config(self.cfg)
        self._refresh_calib_status()
        logger.log("已套用截圖預設框選，並還原畫面標記樣板")
        messagebox.showinfo("完成", "已套用預設框選。若有對不準的項目，在清單裡選取後再單獨校準即可。")

    def _save_debug_shot(self) -> None:
        """把遊戲視窗的用戶端畫面原封不動存成 PNG。

        用途有兩個：
        1. 回報問題時附一張沒有被壓縮過的原圖；
        2. 升級「內建畫面標記樣板」—— 內建那批是 1024 寬的縮圖，實機視窗通常
           更大，比對時要放大就會糊。有原生解析度的截圖，就能用
           `tools/promote_ui_templates.py` 依校準座標把標記裁出來。

        **一定要由程式來抓**：手動截圖很容易連視窗邊框／標題列一起裁進去，
        或是被系統顯示縮放動過，那樣裁出來的樣板座標與尺寸都會對不上。
        """
        capture = GameCapture(self.cfg.get("window_title_substring", ""))
        if not capture.locate():
            self.shot_status.config(text="找不到遊戲視窗", foreground="#c00")
            messagebox.showerror("找不到遊戲視窗",
                                 "請先在上方清單選好遊戲視窗，並確認遊戲正在執行。")
            return
        try:
            frame = capture.grab_full_client()
        except Exception as e:  # noqa: BLE001
            self.shot_status.config(text=f"擷取失敗：{e!r}", foreground="#c00")
            return
        if frame is None or getattr(frame, "size", 0) == 0:
            self.shot_status.config(text="擷取到空白畫面", foreground="#c00")
            return
        height, width = frame.shape[:2]
        folder = os.path.join(PROJECT_ROOT, "debug_captures")
        os.makedirs(folder, exist_ok=True)
        name = f"shot_{width}x{height}_{time.strftime('%Y%m%d-%H%M%S')}.png"
        path = os.path.join(folder, name)
        try:
            ok = cv2.imwrite(path, frame)
        except Exception as e:  # noqa: BLE001
            ok = False
            logger.log(f"[錯誤] 存畫面失敗：{e!r}")
        if not ok:
            self.shot_status.config(text="寫檔失敗（磁碟空間或權限）", foreground="#c00")
            return
        self.shot_status.config(text=f"已存 {name} → debug_captures", foreground="#0a6")
        logger.log(f"已存畫面截圖 debug_captures\\{name}（{width}x{height}）")

    def _save_one_ui_marker(self, path: str) -> None:
        """框選標記區域的當下就存樣板，避免最後停在別的畫面時把樣板存錯。"""
        fname = UI_MARKER_FILES.get(path)
        if not fname:
            return
        region_key = path.split(".")[-1]
        capture = GameCapture(self.cfg.get("window_title_substring", ""))
        if not capture.locate():
            logger.log(f"[警告] 找不到遊戲視窗，暫不更新 {fname}")
            return
        region = self.cfg["regions"].get(region_key, {})
        if region.get("w", 0) <= 0:
            return
        os.makedirs(TEMPLATE_DIR, exist_ok=True)
        try:
            roi = capture.grab_region(region)
            cv2.imwrite(os.path.join(TEMPLATE_DIR, fname), roi)
            # 樣板帶著擷取當下的解析度，一定要一起記下來，比對時才知道要縮放幾倍
            cw, ch = capture.get_client_size()
            set_template_capture_size(self.cfg, cw, ch)
            save_config(self.cfg)
            logger.log(f"已更新畫面標記樣板 {fname}（擷取解析度 {cw}x{ch}）")
        except Exception as e:  # noqa: BLE001
            logger.log(f"[錯誤] 儲存 {fname} 失敗：{e!r}")

    def _save_ui_markers(self) -> None:
        """把用來判斷畫面狀態的區域存成樣板圖。"""
        mapping = [
            ("table_marker", "table_marker.png"),
            ("draw_prompt", "ui_draw_prompt.png"),
            ("congrats_marker", "ui_congrats.png"),
            ("challenge_marker", "ui_challenge.png"),
            ("fail_marker", "ui_fail.png"),
            ("poker_fail_marker", "ui_poker_fail.png"),
        ]
        capture = GameCapture(self.cfg["window_title_substring"])
        if not capture.locate():
            return
        os.makedirs(TEMPLATE_DIR, exist_ok=True)
        saved = False
        for region_key, fname in mapping:
            region = self.cfg["regions"].get(region_key, {})
            if region.get("w", 0) <= 0:
                continue
            try:
                roi = capture.grab_region(region)
                cv2.imwrite(os.path.join(TEMPLATE_DIR, fname), roi)
                saved = True
                logger.log(f"已更新畫面標記樣板 {fname}")
            except Exception as e:  # noqa: BLE001
                logger.log(f"[錯誤] 儲存 {fname} 失敗：{e!r}")
        if saved:
            try:
                cw, ch = capture.get_client_size()
                set_template_capture_size(self.cfg, cw, ch)
                save_config(self.cfg)
                logger.log(f"樣板擷取解析度已記錄為 {cw}x{ch}")
            except Exception as e:  # noqa: BLE001
                logger.log(f"[錯誤] 記錄樣板擷取解析度失敗：{e!r}")

    # ------------------------------------------------------- 點數 / 花色樣板

    def _slot_regions(self) -> list[tuple[str, dict]]:
        out = [(f"slot{i}", r) for i, r in enumerate(self.cfg["regions"]["card_slots"])]
        out.append(("highlow", self.cfg["regions"].get("highlow_card", {})))
        return out

    def _looks_like_highlow(self, capture, reader) -> bool:
        """現在畫面上是「比大小」還是「選牌」？

        刻意**不用**畫面標記的分數來判斷。使用者的標記樣板還是 1024 寬的內建圖，
        分數常常在門檻邊緣徘徊（實機 log：選牌 37%、失敗 49%）——
        拿一個本身就不可靠的訊號來決定要讀哪幾格，只會把問題往下傳。

        改用「哪一組真的切得出牌角」這個直接證據：選牌畫面五格通常有 4~5 格
        切得出來；比大小畫面的手牌框壓在淺色面板上，最多誤中一兩格。
        所以三格以上成功就當成選牌畫面。
        """
        hand_hits = 0
        for _name, region in self._slot_regions()[:5]:
            if not region or region.get("w", 0) <= 0:
                continue
            try:
                roi = capture.grab_region(region)
            except Exception:
                continue
            h, w = roi.shape[:2]
            try:
                if extract_parts(roi, w, h) is not None:
                    hand_hits += 1
            except Exception:
                continue
        if hand_hits >= 3:
            return False

        region = self.cfg["regions"].get("highlow_card", {})
        if not region or region.get("w", 0) <= 0:
            return False
        try:
            roi = capture.grab_region(region)
        except Exception:
            return False
        h, w = roi.shape[:2]
        try:
            return rightmost_card_rect(roi, w, h) is not None
        except Exception:
            return False

    def _capture_parts(self) -> None:
        capture = GameCapture(self.cfg.get("window_title_substring", ""))
        if not capture.locate():
            messagebox.showerror("找不到遊戲視窗", "請確認遊戲正在執行，並已在主控台選好視窗。")
            return
        try:
            capture.begin_frame()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("擷取失敗", f"擷取畫面時發生錯誤：{e!r}")
            return

        templates = load_part_templates()
        reader = CardReader(part_templates=templates)
        found = 0
        # 「手牌 1~5」與「比大小」不可能同時出現在畫面上：前者只在選牌畫面、
        # 後者只在比大小畫面。舊版不管在哪個畫面都把六格全部讀一遍，於是
        # **永遠有一半是垃圾** —— 在選牌畫面時「比大小」那格會去讀牌桌背景或
        # 別人的牌角，在比大小畫面時五格手牌全是背景。
        #
        # 使用者填好正確的那幾格按「全部儲存」，垃圾那格如果也被填了就一起存進去，
        # 這正是 card_templates/parts 裡那幾個「只切到一小角」的廢檔的來源，
        # 也是他看到「位子看起來太偏」警告的原因（警告本身是對的，錯在讀了不該讀的格子）。
        #
        # 所以先判斷「現在是哪個畫面」，只填那一組，另一組明確標成「非此畫面」。
        on_highlow = self._looks_like_highlow(capture, reader)
        for index, (slot, (_name, region)) in enumerate(
                zip(self._part_slots, self._slot_regions())):
            slot["parts"] = None
            slot["img"] = None
            slot["var"].set("")
            slot["preview"].config(image="", text="—")
            wanted = (_name == "highlow") if on_highlow else (_name != "highlow")
            if not wanted:
                slot["preview"].config(text="非此畫面")
                continue
            if not region or region.get("w", 0) <= 0:
                continue
            try:
                roi = capture.grab_region(region)
            except Exception:
                continue
            h, w = roi.shape[:2]
            if _name == "highlow":
                # 比大小畫面是一排疊在一起的牌，要先定位出最右邊那張
                rect = rightmost_card_rect(roi, w, h)
                parts = extract_parts(roi, w, h, rect=rect) if rect else None
                guess = reader.read_rightmost(roi, w, h)
            else:
                parts = extract_parts(roi, w, h)
                guess = reader.read(roi, w, h)
            if parts is None:
                slot["preview"].config(text="讀不到")
                continue
            found += 1
            slot["parts"] = parts
            if guess:
                slot["var"].set(guess[0])
            # 預覽：左上角原圖 + 切出來的點數/花色
            corner = parts["corner"]
            scale = max(1, min(5, int(90 / max(1, corner.shape[1]))))
            shown = cv2.resize(corner, (corner.shape[1] * scale, corner.shape[0] * scale),
                               interpolation=cv2.INTER_NEAREST)
            image = Image.fromarray(cv2.cvtColor(shown, cv2.COLOR_BGR2RGB))
            photo = ImageTk.PhotoImage(image)
            slot["img"] = photo          # 保住參考，否則會被回收變空白
            slot["preview"].config(image=photo, text="")

        screen = "比大小畫面" if on_highlow else "選牌畫面"
        other = "手牌 1~5" if on_highlow else "比大小"
        self.parts_hint.config(
            text=f"偵測到「{screen}」，讀到 {found} 張；確認代號後按「全部儲存」。"
                 f"（{other} 那幾格標成「非此畫面」是正常的 —— 兩個畫面不會同時出現，"
                 "硬讀只會存到垃圾樣板）")

    def _save_parts(self) -> None:
        saved = 0
        skipped: list[str] = []
        written: list[str] = []
        os.makedirs(PARTS_DIR, exist_ok=True)
        for slot in self._part_slots:
            parts = slot.get("parts")
            label = slot["var"].get().strip().upper()
            if parts is None or not label:
                continue
            try:
                card = Card.from_label(label)
            except ValueError:
                skipped.append(f"{label}（格式不對）")
                continue

            # 鬼牌：**要蒐集**，而且只蒐集點數那一格。
            #
            # 這裡本來寫著「鬼牌沒有點數/花色，不用蒐集」直接跳過 —— 那句話是錯的，
            # 而且代價很大：鬼牌左上角印的是一個「$」，對 13 個點數的分數全部落在
            # 0.6 上下（實機 log：`點數 8=0.64 2=0.62 ←分數未達 0.72`），
            # 於是那一格永遠認不出來，選牌畫面永遠只有 4/5，bot 就整晚停在那裡。
            # 版面跟普通牌完全一樣（右下角也印一次、轉 180 度），所以只要把 JK
            # 當成第 14 個點數標籤存起來就好。
            # 花色與中央大圖案不存：鬼牌沒有花色，中央那隻怪物存進 pip 只會去
            # 干擾黑桃/梅花的判斷。
            if card.rank == cardparts.JOKER_RANK:
                saved += self._write_part("rank", cardparts.JOKER_RANK,
                                          parts["rank"], written)
                if parts.get("rank2") is not None:
                    saved += self._write_part("rank", cardparts.JOKER_RANK,
                                              parts["rank2"], written)
                continue

            rank, suit = label[:-1], label[-1]
            if parts["is_red"] != (suit in "HD"):
                skipped.append(f"{label}（顏色對不上：畫面上是{'紅' if parts['is_red'] else '黑'}色）")
                continue
            saved += self._write_part("rank", rank, parts["rank"], written)
            saved += self._write_part("suit", suit, parts["suit"], written)
            if rank not in ("J", "Q", "K") and parts.get("pip") is not None:
                saved += self._write_part("pip", suit, parts["pip"], written)
            # 右下角那一組（轉正過的）也存起來：同一張牌的第二次取樣，
            # 讓 2/5/8、黑桃/梅花這種容易打結的組合多一份參考
            if parts.get("rank2") is not None:
                saved += self._write_part("rank", rank, parts["rank2"], written)
            if parts.get("suit2") is not None:
                saved += self._write_part("suit", suit, parts["suit2"], written)
        if saved:
            logger.log(f"已新增 {saved} 個點數/花色樣板")
        # 剛存進去的有沒有裁壞？以前這件事完全不講，載入時安靜跳過就算了 ——
        # 結果使用者存了 5 個「只切到花色一角」的小點，一直以為自己有樣板，
        # 而那正是方塊被認成愛心的原因。現在當場講。
        fresh_junk = [n for n, _cov in unusable_parts(PARTS_DIR) if n in written]
        if fresh_junk:
            messagebox.showwarning(
                "這次有幾張裁切壞了",
                "下面這幾張只切到符號的一小角，**不會被拿來比對**：\n\n"
                + "  ".join(fresh_junk)
                + "\n\n通常是校準的手牌框沒對準這個視窗比例。\n"
                  "請確認主控台的「視窗比例」正確，或在「校準」分頁重新框選手牌區域，"
                  "再重新抓一次。\n\n"
                  "（壞掉的檔案留在磁碟上沒有刪，可以用「清掉裁切壞的樣板」一次清掉。）")
        if skipped:
            messagebox.showwarning("有幾格沒存", "\n".join(skipped))
        elif not saved and not fresh_junk:
            messagebox.showinfo("沒有東西可存", "請先按「讀取目前畫面」，並填好代號。")
        self._refresh_template_panel()

    _MAX_PER_LABEL = 8

    def _write_part(self, kind: str, key: str, image,
                    written: Optional[list] = None) -> int:
        """存一個點數/花色小樣板。上限**只算你自己抓的**，內建的不佔位子。

        這裡曾經有一個很難發現的 bug：內建樣板每個花色都剛好 8 張
        （suit_S_1~8、suit_C_1~8…），上限也是 8，於是「已經滿了」——
        按幾次「全部儲存」都不會有任何花色被存下來，畫面也不會報錯。
        結果就是花色永遠只能用內建那組糊圖，怎麼調都認不準。

        現在改成：內建樣板不算在上限裡；而且一旦你為某個標籤存了自己的樣板，
        那個標籤的內建檔就直接刪掉（反正比對時本來就會被忽略）。
        """
        path, stale = next_part_path(PARTS_DIR, default_parts_dir(), kind, key,
                                     self._MAX_PER_LABEL)
        if path is None:
            return 0
        for old in stale:
            try:
                os.remove(old)
            except OSError:
                pass
        cv2.imwrite(path, image)
        if written is not None:
            written.append(os.path.basename(path))
        return 1

    def _delete_template(self) -> None:
        selection = self.tmpl_list.curselection()
        if not selection:
            messagebox.showinfo("請先選擇", "請在右邊清單選擇要刪除的樣板檔。")
            return
        fname = self.tmpl_list.get(selection[0]).replace("（內建）", "")
        if not messagebox.askyesno("確認刪除", f"確定要刪除 {fname} 嗎？"):
            return
        try:
            os.remove(os.path.join(PARTS_DIR, fname))
            logger.log(f"已刪除樣板 {fname}")
        except OSError as e:
            messagebox.showerror("刪除失敗", str(e))
        self._refresh_template_panel()

    def _bundled_filenames(self) -> set:
        """`parts/` 裡「內容真的還是內建樣板」的檔名。

        **不可以只比檔名。** 內建檔叫 suit_D_1.png，而使用者自己抓的樣板也可能
        被寫進同一個檔名（見 cardparts.is_bundled_copy 的說明）——
        只比檔名的話，「清掉所有內建樣板」會把他自己抓的 19 個一起刪掉，
        而按鈕上明明寫著「你自己抓的不會動」。
        """
        return set(bundled_copies_present(PARTS_DIR, default_parts_dir()))

    def _delete_bundled_templates(self) -> None:
        """把還留在 card_templates/parts 的內建樣板一次清掉。

        內建樣板是從縮圖放大來的，比實機糊一圈，是「2/5/8 一直問號」「黑桃梅花
        分不清」的主因。有自己的樣板之後程式會自動不用內建的，但整組刪掉更乾淨。
        """
        present = bundled_copies_present(PARTS_DIR, default_parts_dir())
        if not present:
            messagebox.showinfo("沒有內建樣板", "目前的樣板全部都是你自己抓的，不用清。")
            return
        sources = part_sources()
        orphan = []
        for fname in present:
            parsed = parse_part_name(fname)
            if parsed and not sources.get(parsed[0], {}).get(parsed[1], False):
                orphan.append(f"{parsed[0]}_{parsed[1]}")
        warn = ""
        if orphan:
            warn = ("\n\n注意：這些還沒有你自己的樣板，刪掉之後會完全認不出來，"
                    "要先自己抓一次：\n" + "  ".join(sorted(set(orphan))))
        if not messagebox.askyesno(
            "確認清除",
            f"要刪掉 {len(present)} 個內建樣板嗎？（你自己抓的不會動）{warn}",
        ):
            return
        removed = 0
        for fname in present:
            try:
                os.remove(os.path.join(PARTS_DIR, fname))
                removed += 1
            except OSError:
                pass
        logger.log(f"已清除 {removed} 個內建樣板")
        self._refresh_template_panel()

    def _delete_junk_templates(self) -> None:
        """刪掉「只切到符號一角」的樣板檔。

        這些檔案載入時本來就會被跳過（`part_is_usable`），留著不影響辨識，
        但會讓「我有 3 張樣板」的印象跟「實際只有 1 張能用」對不起來 ——
        使用者就是這樣一路以為自己有在累積樣板的。
        """
        junk = unusable_parts(PARTS_DIR)
        if not junk:
            messagebox.showinfo("沒有壞掉的樣板", "每一張樣板都切得好好的，不用清。")
            return
        listing = "\n".join(f"  {n}（只佔 {cov:.0%}）" for n, cov in junk)
        if not messagebox.askyesno(
            "確認刪除",
            f"要刪掉這 {len(junk)} 個裁切壞掉的樣板嗎？\n\n{listing}\n\n"
            "它們現在也沒有被拿來比對，刪掉只是讓數量對得上。\n"
            "刪完之後請重新抓一次那幾個花色／點數。",
        ):
            return
        removed = 0
        for name, _cov in junk:
            try:
                os.remove(os.path.join(PARTS_DIR, name))
                removed += 1
            except OSError:
                pass
        logger.log(f"已刪除 {removed} 個裁切壞掉的樣板")
        self._refresh_template_panel()

    def _refresh_template_panel(self) -> None:
        templates = load_part_templates()
        miss_rank, miss_suit = missing_parts(templates)
        n_rank = len(templates.get("rank") or {})
        n_suit = len(templates.get("suit") or {})
        n_pip = len(templates.get("pip") or {})
        n_joker = joker_template_count(templates)
        # 鬼牌另外算：它不在 13 個點數裡，但沒有它的話抽到鬼牌就整個卡住，
        # 所以要單獨顯示，不能被「點數 13/13 ✓」蓋掉。
        self.tmpl_progress.config(
            text=f"點數 {n_rank - (1 if n_joker else 0)}/13　花色 {n_suit}/4　"
                 f"中央大圖案 {n_pip}/4　鬼牌 {'✓' if n_joker else '✗'}（{n_joker} 張）")

        lines = []
        if miss_rank:
            lines.append("還缺點數：" + "  ".join(miss_rank))
        if miss_suit:
            lines.append("還缺花色：" + "  ".join(miss_suit))
        if not n_joker:
            lines.append("⚠ 還沒有鬼牌（JK）樣板 —— 抽到鬼牌時那一格會永遠認不出來，"
                         "選牌畫面只有 4/5，bot 會停在那裡。")
            lines.append("　→ 下次畫面上出現鬼牌時按「讀取目前畫面」，那一格代號填 "
                         "JK 再儲存即可（鬼牌沒有花色，只會存點數那一格）。")

        # 每個標籤實際的樣板數量。以前這裡只顯示「有沒有自己的樣板」，
        # 於是「有 3 個檔案但 2 個是裁壞的小點、真正拿來比對只有 1 張」
        # 這種狀況完全看不出來 —— 而那正是方塊被認成愛心的原因。
        inventory = part_inventory(PARTS_DIR, default_parts_dir())
        thin, junky = [], []
        for kind, title in (("rank", "點數"), ("suit", "花色"), ("pip", "中央大圖案")):
            for key in sorted(inventory.get(kind, {}), key=lambda k: (len(k), k)):
                row = inventory[kind][key]
                # 鬼牌不進這一列：這一列講的是「還在跟內建糊圖混著比對」，
                # 而內建樣板裡從來就沒有鬼牌，寫進去只會給錯的說明。
                if key == cardparts.JOKER_RANK:
                    continue
                if row["own"] < MIN_OWN_TO_DROP_BUNDLED:
                    thin.append(f"{title}{key}({row['own']}/{MIN_OWN_TO_DROP_BUNDLED})")
                if row["junk"]:
                    junky.append(f"{title}{key}×{row['junk']}")
        if thin:
            lines.append(f"自己抓的樣板還不到 {MIN_OWN_TO_DROP_BUNDLED} 張的："
                         + "  ".join(thin))
            lines.append("　→ 這些會「你的 + 內建」混著比對。抓滿之後程式就只用你的，"
                         "辨識率明顯提升。")
        if junky:
            lines.append("⚠ 有裁切壞掉的樣板（只切到符號一角，不會拿來比對）："
                         + "  ".join(junky))
            lines.append("　→ 按下面的「清掉裁切壞的樣板」刪掉，再重新抓一次。")
        if not lines:
            lines.append("已經蒐集齊全，而且每個標籤都有足夠的自己抓的樣板，"
                         "52 張牌加鬼牌都認得出來了！")
        lines.append("（中央大圖案是數字牌中間那個大花色，用來把黑桃跟梅花分清楚，"
                     "存數字牌時會自動一起存。鬼牌代號填 JK，只會存點數那一格。）")
        self.tmpl_missing.config(state=tk.NORMAL)
        self.tmpl_missing.delete("1.0", tk.END)
        self.tmpl_missing.insert("1.0", "\n".join(lines))
        self.tmpl_missing.config(state=tk.DISABLED)

        bundled = self._bundled_filenames()
        junk_files = dict(unusable_parts(PARTS_DIR))
        self.tmpl_list.delete(0, tk.END)
        if os.path.isdir(PARTS_DIR):
            for fname in sorted(os.listdir(PARTS_DIR)):
                if not fname.lower().endswith(".png"):
                    continue
                if fname in junk_files:
                    tag = f"　← 裁切壞掉（只佔 {junk_files[fname]:.0%}），不會用"
                elif fname in bundled:
                    tag = "（內建）"
                else:
                    tag = ""
                self.tmpl_list.insert(tk.END, fname + tag)

    # ----------------------------------------------------------- 設定

    _INT_SETTINGS = INT_SETTINGS

    # 全形句號、頓號、逗號、間隔號都當成小數點。
    #
    # 中文輸入法開著的時候，數字鍵盤那顆「.」常常打出「。」或根本吃不進去，
    # 使用者只能改按主鍵盤上「>」那一顆。與其要求他先切輸入法，不如全部接受 ——
    # 這些字元在數字欄位裡不可能有別的意思。
    _DECIMAL_ALIASES = str.maketrans({
        "。": ".", "．": ".", "、": ".", "，": ".", ",": ".", "·": ".", "‧": ".",
        "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
        "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
    })

    @classmethod
    def _normalize_number(cls, raw: str) -> str:
        return raw.strip().translate(cls._DECIMAL_ALIASES)

    def _save_settings(self) -> None:
        for key, var in self.setting_vars.items():
            raw = self._normalize_number(var.get())
            if raw != var.get().strip():
                var.set(raw)          # 讓使用者看到程式實際收到什麼
            try:
                value = int(raw) if key in self._INT_SETTINGS else float(raw)
            except ValueError:
                messagebox.showerror("數值格式錯誤", f"「{key}」的值「{raw}」不是有效數字。")
                return
            try:
                set_by_path(self.cfg, key, value)
            except (KeyError, IndexError, TypeError):
                messagebox.showerror("設定路徑錯誤", f"找不到設定欄位「{key}」。")
                return
        self.cfg["ace_high"] = self.ace_high_var.get()
        self.cfg["pyautogui_failsafe"] = self.failsafe_var.get()
        save_config(self.cfg)
        logger.log("設定已儲存")
        if self.bot is not None:
            messagebox.showinfo("設定已儲存", "設定已存檔，停止後再重新啟動即會套用新設定。")
        else:
            messagebox.showinfo("設定已儲存", "設定已存檔。")

    # ------------------------------------------------------- Bot 控制

    def _start_bot(self) -> None:
        if self.bot is not None and self.bot.running:
            return
        self.cfg = load_config()
        if not self.cfg.get("window_title_substring"):
            messagebox.showerror("尚未設定視窗", "請先在主控台選擇遊戲視窗。")
            return
        if self._find_hwnd() is None:
            messagebox.showerror("找不到遊戲視窗", "請確認遊戲正在執行。")
            return

        if self.bot is None:
            self.bot = Bot(self.cfg, dry_run=self.dry_run_var.get())
            self.bot_thread = threading.Thread(target=self.bot.run_forever, daemon=True)
            self.bot_thread.start()
            self.hotkeys = HotkeyManager(on_toggle=self._hotkey_toggle,
                                         on_emergency_stop=self._hotkey_stop)
            try:
                self.hotkeys.start()
            except Exception as e:  # noqa: BLE001
                logger.log(f"[警告] 熱鍵註冊失敗（可能需要系統管理員權限）：{e!r}")
        else:
            self.bot.reload(self.cfg, dry_run=self.dry_run_var.get())
        if not self.bot.running:
            self.bot.toggle()
        self._sync_buttons()

    def _stop_bot(self) -> None:
        if self.bot is not None and self.bot.running:
            self.bot.toggle()
        self._sync_buttons()

    def _hotkey_toggle(self) -> None:
        if self.bot is not None:
            self.bot.toggle()

    def _hotkey_stop(self) -> None:
        if self.bot is not None:
            self.bot.emergency_stop()

    def _shutdown_bot(self) -> None:
        if self.hotkeys is not None:
            self.hotkeys.stop()
            self.hotkeys = None
        if self.bot is not None:
            self.bot.running = False
            self.bot.stop_program()
            self.bot = None
        if self.bot_thread is not None:
            self.bot_thread.join(timeout=2.0)
            self.bot_thread = None

    def _sync_buttons(self) -> None:
        running = self.bot is not None and self.bot.running
        mode = "除錯模式" if (self.bot and self.bot.dry_run) else "正式模式"
        self.state_label.config(text=f"狀態：{'執行中（' + mode + '）' if running else '停止中'}",
                                foreground="#0a6" if running else "#666")
        self.start_btn.config(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_btn.config(state=tk.NORMAL if running else tk.DISABLED)
        mini = getattr(self, "_mini_panel", None)
        if mini is not None and mini.winfo_exists():
            mini.set_running(running)

    # --------------------------------------------------- 迷你懸浮視窗

    def _show_mini_panel(self) -> None:
        """把主視窗收起來，只留一顆啟動/停止釘在最上層。

        給只有一個螢幕的人用：主視窗會蓋住遊戲，而 Alt+Tab 切回來的瞬間
        遊戲就失去焦點了。
        """
        saved = self.cfg.get("mini_panel") or {}
        position = None
        if isinstance(saved.get("x"), int) and isinstance(saved.get("y"), int):
            position = (saved["x"], saved["y"])

        existing = getattr(self, "_mini_panel", None)
        if existing is not None and existing.winfo_exists():
            existing.destroy()

        self._mini_panel = MiniPanel(
            self,
            on_start=self._start_bot,
            on_stop=self._stop_bot,
            on_restore=self._restore_from_mini,
            on_move=self._remember_mini_position,
            position=position,
        )
        self._mini_panel.set_running(self.bot is not None and self.bot.running)

        # 先確認迷你視窗真的畫出來了，才敢把主視窗收起來。
        # 萬一它沒顯示（overrideredirect 在某些環境會出怪事），收起主視窗之後
        # 使用者就會兩個窗都沒有 —— 沒有標題列、沒有工作列項目，只剩 F9 能按。
        self.update_idletasks()
        self.update()
        if not self._mini_panel.winfo_ismapped():
            self._mini_panel.destroy()
            self._mini_panel = None
            messagebox.showwarning(
                "迷你視窗無法顯示",
                "這台電腦上的迷你視窗沒有正常出現，主視窗維持開啟以免你找不回來。\n"
                "可以改用全域熱鍵 F9 啟動／停止、F10 緊急停止。",
            )
            return
        self.withdraw()

    def _restore_from_mini(self) -> None:
        mini = getattr(self, "_mini_panel", None)
        if mini is not None and mini.winfo_exists():
            mini.destroy()
        self._mini_panel = None
        self.deiconify()
        self.lift()

    def _remember_mini_position(self, x: int, y: int) -> None:
        """拖曳結束才存，不是每個 motion 事件都存 —— 拖一次會觸發上百次事件，
        每次都寫檔會讓拖曳變得一頓一頓的，而且 config.json 被反覆改寫。"""
        self.cfg["mini_panel"] = {"x": int(x), "y": int(y)}
        save_config(self.cfg)

    # --------------------------------------------------- 即時辨識預覽

    def _toggle_preview(self) -> None:
        if self.preview_var.get():
            if self._find_hwnd() is None:
                messagebox.showerror("找不到遊戲視窗", "請先在主控台選擇遊戲視窗。")
                self.preview_var.set(False)
                return
            self._preview_running = True
            threading.Thread(target=self._preview_worker, daemon=True).start()
        else:
            self._preview_running = False
            self.preview_label.config(text="即時辨識：未啟用")

    def _preview_worker(self) -> None:
        capture = GameCapture(self.cfg.get("window_title_substring", ""))
        capture.locate()
        probe = Bot(self.cfg, dry_run=True)
        templates = probe.reader
        ui = probe.ui_templates

        while self._preview_running:
            try:
                if not capture.is_window_valid() and not capture.locate():
                    self._preview_queue.put("即時辨識：找不到遊戲視窗")
                    time.sleep(1.0)
                    continue
                frame = detect_frame(capture, self.cfg, templates, ui)
                slots = " ".join(
                    (c[0] if c else "??").rjust(3) for c in frame.slot_cards
                )
                highlow = frame.highlow_card[0] if frame.highlow_card else "??"
                flags = []
                if frame.is_draw:
                    flags.append("選牌")
                if frame.is_congrats:
                    flags.append("過關")
                if frame.is_challenge:
                    flags.append("翻倍對話")
                if frame.is_poker_fail:
                    flags.append("湊牌失敗")
                if frame.is_max_win:
                    flags.append("已達上限")
                if frame.is_fail:
                    flags.append("比大小失敗")
                phase = " ".join(flags) if flags else "待機/其他"
                on_table = "在牌桌" if frame.on_table else "不在牌桌"
                scores = frame.ui_scores
                self._preview_queue.put(
                    f"即時辨識：{phase}  手牌 [{slots}]  比大小牌 [{highlow}]  "
                    f"{on_table}({frame.table_marker_score:.2f})  "
                    f"樣板倍率 {scores.get('_scale', 1.0):.2f}x  "
                    f"選牌{scores.get('draw', 0):.2f} 過關{scores.get('congrats', 0):.2f} "
                    f"翻倍{scores.get('challenge', 0):.2f} 失敗{scores.get('fail', 0):.2f} "
                    f"湊牌失敗{scores.get('poker_fail', 0):.2f}"
                )
            except Exception as e:  # noqa: BLE001
                self._preview_queue.put(f"即時辨識：發生錯誤 {e!r}")
            time.sleep(1.0)

    # ------------------------------------------------------- 定時更新

    def _pump(self) -> None:
        if self._quitting:
            return
        # log
        drained = 0
        while drained < 200:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, line + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
            drained += 1

        while True:
            try:
                text = self._preview_queue.get_nowait()
            except queue.Empty:
                break
            self.preview_label.config(text=text)

        while True:
            try:
                kind, payload = self._update_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_update_event(kind, payload)
            if self._quitting:
                # 已經在收尾了，不要再回頭去碰下面那些元件
                return

        self._sync_buttons()
        self._refresh_stats()

        # 每 4 次 pump（約兩秒）看一次視窗尺寸有沒有變。使用者把遊戲從 21:9
        # 拉成 16:9 之後，不需要自己想起來要去按什麼按鈕。
        self._detect_tick = (self._detect_tick + 1) % 4
        if self._detect_tick == 0:
            self._autodetect_profile(announce=False)

        self.after(500, self._pump)

    def _refresh_stats(self) -> None:
        try:
            stats = DailyStats()
        except Exception:
            return
        data = stats.data
        observed = sum(data.get("card_counts", {}).values())
        guesses = sum(1 for e in data.get("events", []) if e.get("kind") == "highlow_guess")
        self.stats_label.config(
            text=(f"日期 {data.get('date', '-')}　已觀察牌數 {observed}　"
                  f"開始局數 {data.get('rounds_started', 0)}　比大小猜測 {guesses} 次")
        )

    def _reconcile_stats(self) -> None:
        """把 log 與其他安裝的統計檔裡「還沒算進機率模型」的牌補進來。

        機率模型（`src/stats.py`）吃的是 card_counts，而那份計數只有在 bot 正常
        跑到選牌／比大小時才會 +1。exe 版與原始碼版各有一份 data\\、只開即時辨識
        看畫面、程式中途被關掉 —— 這幾種情況的牌都只留在 log 裡。
        補進來之後今天的估計才會真的貼近今天的牌堆。
        """
        self.reconcile_btn.config(state=tk.DISABLED)
        self.reconcile_status.config(text="正在檢查…", foreground="#555")
        self.update_idletasks()
        try:
            report = reconcile_mod.reconcile(days=7, cfg=self.cfg)
        except Exception as e:  # noqa: BLE001
            self.reconcile_status.config(text=f"補算失敗：{e!r}", foreground="#c33")
            logger.log(f"[錯誤] 補算未記錄的數值失敗：{e!r}")
            return
        finally:
            self.reconcile_btn.config(state=tk.NORMAL)

        summary = report.summary()
        self.reconcile_status.config(
            text=summary, foreground="#0a6" if report.total_added else "#777")
        logger.log("[補算] " + summary)
        if report.total_added:
            self._refresh_stats()
            messagebox.showinfo("已補算", summary + "\n\n機率模型下一局就會用新的數字。")
        else:
            messagebox.showinfo(
                "無其餘資料",
                "沒有找到還沒記錄到的牌。\n\n"
                "檢查過：今天與前 6 天的執行紀錄、"
                f"以及 {len(reconcile_mod.candidate_data_dirs(self.cfg))} 個其他 data 資料夾。")

    # ------------------------------------------------------- 檢查更新

    def _set_update_status(self, text: str, colour: str = "#555") -> None:
        self.update_status.config(text=text, foreground=colour)

    def _update_idle(self) -> None:
        self._update_busy = False
        self.update_btn.config(state=tk.NORMAL)

    def _check_update(self) -> None:
        """按下「檢查更新」：只問 GitHub 版本號，不下載任何東西。"""
        if self._update_busy:
            return
        self._update_busy = True
        self.update_btn.config(state=tk.DISABLED)
        self._set_update_status("正在向 GitHub 查詢最新版本…")

        def worker() -> None:
            try:
                result = updater.check_for_update()
            except Exception as exc:                      # 背景執行緒不可以讓例外逃走
                self._update_queue.put(("error", f"檢查更新時發生意外錯誤：{exc}"))
                return
            self._update_queue.put(("checked", result))

        threading.Thread(target=worker, daemon=True, name="update-check").start()

    def _handle_update_event(self, kind: str, payload) -> None:
        """在主執行緒處理背景執行緒回報的更新事件。"""
        if kind == "progress":
            self._set_update_status(str(payload))
        elif kind == "checked":
            self._on_update_checked(payload)
        elif kind == "ready":
            self._on_update_ready(payload)
        elif kind == "error":
            self._update_idle()
            self._set_update_status(str(payload), "#c00")
            messagebox.showerror("更新失敗", str(payload))

    def _on_update_checked(self, result) -> None:
        self._update_idle()
        if result.error:
            self._set_update_status(result.message, "#c00")
            return
        if not result.available:
            self._set_update_status(result.message, "#0a6")
            return

        release = result.release
        self._set_update_status(result.message, "#c60")
        notes = (release.notes or "（這一版沒有寫說明）").strip()
        if len(notes) > 1000:
            notes = notes[:1000] + "\n…"
        proceed = messagebox.askyesno(
            f"發現新版本 v{release.version}",
            f"目前版本：v{result.current}\n"
            f"最新版本：v{release.version}\n\n"
            f"{notes}\n\n"
            "── 這次更新會動到什麼 ──\n"
            "只覆蓋程式檔案（HoloTool.exe 與 app\\ 裡的程式內容）。\n"
            "你的校準 config\\、自己抓的樣板 card_templates\\、統計 data\\\n"
            "完全不會被動到，更新前還會先壓縮備份一份到 backups\\。\n\n"
            "要現在下載並更新嗎？",
        )
        if not proceed:
            self._set_update_status(
                f"有新版 v{release.version}，你選擇稍後再更新。"
                f"手動下載：{release.page_url}", "#c60")
            return

        if self.bot is not None:
            messagebox.showwarning(
                "請先停止自動遊玩",
                "更新會關閉並重新啟動 HoloTool，請先按「停止」（或 F10）"
                "讓自動遊玩結束，再按一次「檢查更新」。",
            )
            self._set_update_status("請先停止自動遊玩再更新。", "#c60")
            return

        self._update_busy = True
        self.update_btn.config(state=tk.DISABLED)
        self._set_update_status("正在準備更新…")

        def worker() -> None:
            try:
                info = updater.prepare_update(
                    release,
                    progress=lambda text: self._update_queue.put(("progress", text)),
                )
            except updater.UpdateError as exc:
                self._update_queue.put(("error", str(exc)))
                return
            except Exception as exc:
                self._update_queue.put(("error", f"更新時發生意外錯誤：{exc}"))
                return
            self._update_queue.put(("ready", (release, info)))

        threading.Thread(target=worker, daemon=True, name="update-download").start()

    def _on_update_ready(self, payload) -> None:
        """檔案下載、驗證、備份、解壓都完成了，只剩「關掉自己讓外部腳本置換」。"""
        release, info = payload
        self._update_idle()
        backup = info.get("backup") or ""
        backup_line = (f"更新前備份：{backup}\n\n" if backup
                       else "（沒有找到需要備份的校準或樣板）\n\n")
        proceed = messagebox.askyesno(
            "準備完成，要現在重新啟動嗎？",
            f"v{release.version} 已下載並通過檔案校驗。\n\n"
            f"{backup_line}"
            "按「是」之後 HoloTool 會關閉，由一個小的批次檔完成置換再自動\n"
            "重新開啟 —— 執行中的程式不能覆蓋自己，所以一定要先關掉。\n"
            "過程大約十秒，畫面可能會閃一下命令列視窗，請不要手動關它。\n\n"
            "按「否」則取消這次更新，已下載的檔案留在系統暫存區，不會生效。",
        )
        if not proceed:
            self._set_update_status(
                f"v{release.version} 已下載但尚未套用。下次按「檢查更新」可以再選一次。",
                "#c60")
            return
        try:
            updater.launch_apply_script(info["script"])
        except Exception as exc:
            self._set_update_status(f"無法啟動更新程式：{exc}", "#c00")
            messagebox.showerror(
                "無法啟動更新程式",
                f"{exc}\n\n請到 Release 頁面手動下載安裝：\n{release.page_url}",
            )
            return
        logger.log(f"[更新] 即將關閉並套用 v{release.version}（PID {os.getpid()}）")
        self._quitting = True
        # 排到下一個空檔再關，讓這一輪 _pump 先乾淨地結束
        self.after(50, self._quit_for_update)

    def _quit_for_update(self) -> None:
        """為了更新關閉程式。**一定要真的讓行程結束。**

        置換用的 .bat 在等這個 PID 從 tasklist 消失才敢動手，所以
        「視窗關了但行程還活著」對更新來說跟沒關一樣 —— 畫面會停在一個
        什麼都不做的黑視窗上（實際踩到過這個坑）。
        所以最後一定要走 hard_exit()，不能只靠 mainloop 自然返回。
        """
        try:
            self._on_close()
        except Exception:
            pass
        updater.hard_exit(0)

    # ----------------------------------------------------------- 關閉

    def _on_close(self) -> None:
        self._preview_running = False
        mini = getattr(self, "_mini_panel", None)
        if mini is not None and mini.winfo_exists():
            mini.destroy()
        self._hide_calib_preview()
        if hasattr(self, "_calib_overlay"):
            self._calib_overlay.destroy()
        self._shutdown_bot()
        logger.unsubscribe(self.log_queue.put)
        self.destroy()


def main() -> None:
    prepare_runtime()
    if getattr(sys, "frozen", False):
        def _gui_excepthook(exc_type, exc, tb):
            import traceback
            detail = "".join(traceback.format_exception(exc_type, exc, tb))
            try:
                messagebox.showerror("HoloTool 發生錯誤", detail[-2500:])
            except Exception:
                pass
            sys.__excepthook__(exc_type, exc, tb)

        sys.excepthook = _gui_excepthook
    app = HoloToolGUI()
    logger.log(f"HoloTool v{APP_VERSION} GUI 已啟動")
    app.mainloop()


if __name__ == "__main__":
    main()
