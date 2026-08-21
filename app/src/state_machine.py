"""單次畫面偵測：把螢幕截圖轉換成「目前看到了什麼」的結構化資訊。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from .defaults_layout import BUNDLED_MARKER_HEIGHT, BUNDLED_MARKER_WIDTH
from .geometry import content_height
from .recognize import CardReader, marker_score, median_value

if TYPE_CHECKING:  # 只在型別檢查時需要；執行時不要 import，
    from .capture import GameCapture  # 這樣離線工具不必安裝 mss / pywin32

# 六個畫面標記各自的預設門檻。舊版全部共用 table_marker_threshold(0.80)，
# 但它們的對比度、背景複雜度差很多，共用一個門檻一定會有人過不了、有人誤判。
# draw_prompt 與 fail_marker 這兩個是 2026-08-21 換成原生解析度樣板時重新量的
# （27 個實機畫面：1365 原生 21 張 + 1024 舊圖 6 張，正例最低分 vs 反例最高分）：
#
#   標記               正例最低  反例最高  舊門檻  新門檻
#   draw_prompt          0.70     0.64    0.78 ✗   0.67
#   fail_marker          0.81     0.77    0.82 ✗   0.79
#
# 這兩個框裡都有「會變的東西」：選牌提示是淡入淡出的，而且框內含大片背景漸層；
# 失敗那格的大標題本身會變（失敗／無對子／一對…）。所以餘裕天生就小，
# 不是解析度造成的。判斷「這一局結束了」請以 poker_fail_marker 為主（餘裕 0.45）。
DEFAULT_MARKER_THRESHOLDS = {
    "table_marker": 0.82,
    "draw_prompt": 0.67,
    "congrats_marker": 0.80,
    "challenge_marker": 0.80,
    "fail_marker": 0.79,
    "poker_fail_marker": 0.74,
    "max_win_marker": 0.78,
}

# 各標記搜尋時往外擴張的量，單位是「該區域自身的寬/高比例」。
# 舊版是用「整個視窗」的比例 (0.05 / 0.12)，在 1937 寬的視窗下等於左右各加
# 97 ~ 232 像素，搜尋範圍暴增，多尺度掃描取 max() 必然掃出假陽性。
DEFAULT_MARKER_PADS = {
    "table_marker": (0.20, 0.25),
    "draw_prompt": (0.30, 0.80),
    "congrats_marker": (0.20, 0.60),
    "challenge_marker": (0.35, 0.90),
    "fail_marker": (0.35, 0.60),
    "poker_fail_marker": (0.25, 0.80),
    "max_win_marker": (0.25, 0.80),
}


# 「投注畫面」的判斷門檻：五個牌位的中位亮度都要低於這個值。
#
# 實測（三種長寬比、20 幾張實機截圖）：
#   投注畫面（五張深色牌背）  V = [85, 112, 133, 125, 82]
#   選牌畫面（五張白牌面）    V = [255, 255, 255, 255, 253]
#   比大小                    V = [255, 205, 247, 247, 251]
#   湊牌失敗 / 翻倍對話框     至少四個 >= 205
# 投注畫面最亮的是 133，其他畫面最暗的（不算被立繪蓋住的那一格）是 205，
# 中間空一大段，170 放在中間非常安全。
IDLE_SLOT_MAX_VALUE = 170


@dataclass
class FrameInfo:
    on_table: bool
    table_marker_score: float
    slot_cards: list = field(default_factory=list)
    highlow_card: Optional[tuple] = None
    is_draw: bool = False
    is_congrats: bool = False
    is_challenge: bool = False
    is_fail: bool = False
    is_poker_fail: bool = False
    is_max_win: bool = False
    ui_scores: dict = field(default_factory=dict)

    # 五個牌位的中位亮度。用來確認「這真的是投注畫面」再去點投注並開始，
    # 空 list 代表沒量到（校準框無效或擷取失敗），此時視為無法確認。
    slot_values: list = field(default_factory=list)

    def looks_like_betting(self, max_value: float = IDLE_SLOT_MAX_VALUE) -> bool:
        """五個牌位是否都是「蓋著的深色牌背」= 這是等你下注的畫面。

        必須**五個都**成立。翻倍對話框的第一格會被立繪蓋住而變暗（V=88），
        只要求「大部分很暗」就會把對話框誤判成投注畫面。
        """
        if len(self.slot_values) < 5:
            return False
        return all(v <= max_value for v in self.slot_values)

    @property
    def any_dialog(self) -> bool:
        """任何一個對話框畫面成立時為 True。

        這些畫面會把整個牌桌（含左上角 High&Low logo）模糊掉，
        不能拿來當作「已經離開牌桌」的證據。
        """
        return (self.is_congrats or self.is_challenge or self.is_fail
                or self.is_poker_fail or self.is_max_win)


def expected_marker_scale(cfg: dict, client_width: int,
                          client_height: int = 0) -> float:
    """畫面標記樣板要放大／縮小幾倍才對得上目前的畫面。

    樣板是校準當下從畫面切下來的點陣圖，帶著當時的解析度。內建預設樣板
    (defaults/ui/) 來自 1024×438 的截圖。

    **倍率取決於「內容框的高度」，不是視窗寬度。** 遊戲把一個 16:9 的內容框
    置中放進視窗，UI 全部排在裡面（詳見 geometry.content_box）。實測：

        畫面              實測倍率   內容框高度比   舊的視窗寬度比
        16:9 1474x829      1.894       1.893          1.439  ← 差 24%
        4:3  1269x952      1.632       1.630          1.239  ← 差 24%
        21:9 1367x574      1.308       1.311          1.335  ← 差 2%

    舊公式只在接近 21:9 時剛好差不多，所以一直沒被發現；一換成 16:9 或 4:3
    就用錯 24% 的倍率去比對，所有畫面標記的分數整排掉下來。

    拿不到視窗高度時退回舊公式 —— 不準，但比回傳 1.0 好。
    """
    tmpl_cfg = cfg.get("templates", {}) or {}

    def _int(key: str, fallback: int) -> int:
        try:
            value = int(tmpl_cfg.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        return value if value > 0 else fallback

    # 內建預設樣板的來源解析度。設定檔沒有這一項時退回內建常數 ——
    # 寫死 1024 的話，升級內建樣板之後舊 config 會用錯倍率。
    ref_w = _int("capture_client_width", BUNDLED_MARKER_WIDTH)
    ref_h = _int("capture_client_height", BUNDLED_MARKER_HEIGHT)

    if client_width <= 0:
        return 1.0
    if client_height <= 0:
        return client_width / float(ref_w)

    ref_box = content_height(ref_w, ref_h)
    if ref_box <= 0:
        return client_width / float(ref_w)
    return content_height(client_width, client_height) / ref_box


def _expand_region(region: dict, pad_x: float = 0.25, pad_y: float = 0.5) -> dict:
    """依「區域自身尺寸」的比例往外擴張，並限制最多不超過整個視窗的 8%。"""
    if not region or region.get("w", 0) <= 0:
        return region
    px = min(float(region["w"]) * pad_x, 0.08)
    py = min(float(region["h"]) * pad_y, 0.08)
    x = max(0.0, float(region["x"]) - px)
    y = max(0.0, float(region["y"]) - py)
    right = min(1.0, float(region["x"]) + float(region["w"]) + px)
    bottom = min(1.0, float(region["y"]) + float(region["h"]) + py)
    return {"x": x, "y": y, "w": right - x, "h": bottom - y}


def _score(
    capture: "GameCapture",
    region: dict,
    template,
    scale: float,
    pad: tuple = (0.25, 0.5),
) -> float:
    if template is None or not region or region.get("w", 0) <= 0:
        return -1.0
    try:
        roi = capture.grab_region(_expand_region(region, pad_x=pad[0], pad_y=pad[1]))
    except Exception:
        return -1.0
    return marker_score(roi, template, expected_scale=scale)


def _recognize_highlow_card(capture: "GameCapture", cfg: dict, reader):
    """比大小時歷史牌會往左堆，最右邊那張完整露出的才是目前要比的牌。

    做法：以校準框的高度切出一條水平長條交給 CardReader，它會找出白色卡身的
    右邊界、往左量一張卡的寬度，再讀那張卡的左上角。
    """
    region = cfg["regions"].get("highlow_card", {})
    if region.get("w", 0) <= 0 or reader is None or not reader.ready:
        return None

    x0 = float(region["x"])
    width = float(region["w"])
    scan_right = float(cfg.get("highlow_scan_right", 0.62))
    if scan_right <= x0 + width:
        scan_right = min(1.0, x0 + width * 2.5)

    strip = {"x": x0, "y": float(region["y"]), "w": scan_right - x0, "h": float(region["h"])}
    try:
        roi = capture.grab_region(strip)
    except Exception:
        return None

    sh, sw = roi.shape[:2]
    card_w = max(8, int(round(sw * width / strip["w"])))
    return reader.read_rightmost(roi, card_w, sh)


def detect_frame(
    capture: "GameCapture",
    cfg: dict,
    card_templates,
    ui_templates: dict,
) -> FrameInfo:
    """card_templates 可以是 CardReader，也可以是舊的 {標籤: [圖]} 字典。"""
    if hasattr(capture, "begin_frame"):
        capture.begin_frame()

    regions = cfg["regions"]
    threshold = cfg.get("match_threshold", 0.83)
    min_margin = cfg.get("min_match_margin", 0.02)

    reader = card_templates if isinstance(card_templates, CardReader) else CardReader(
        card_templates=card_templates or {}, threshold=threshold, min_margin=min_margin
    )

    try:
        client_w, client_h = capture.get_client_size()
    except Exception:
        client_w = client_h = 0
    scale = expected_marker_scale(cfg, client_w, client_h)

    thresholds = dict(DEFAULT_MARKER_THRESHOLDS)
    thresholds.update(cfg.get("marker_thresholds", {}) or {})
    pads = dict(DEFAULT_MARKER_PADS)
    for key, value in (cfg.get("marker_pads", {}) or {}).items():
        if isinstance(value, (list, tuple)) and len(value) == 2:
            pads[key] = (float(value[0]), float(value[1]))

    marker_map = [
        ("table_marker", "table_marker"),
        ("draw_prompt", "draw_prompt"),
        ("congrats_marker", "congrats"),
        ("challenge_marker", "challenge"),
        ("fail_marker", "fail"),
        ("poker_fail_marker", "poker_fail"),
        ("max_win_marker", "max_win"),
    ]
    scores: dict[str, float] = {}
    for region_key, tmpl_key in marker_map:
        scores[region_key] = _score(
            capture,
            regions.get(region_key, {}),
            ui_templates.get(tmpl_key),
            scale,
            pad=pads.get(region_key, (0.25, 0.5)),
        )

    def hit(key: str) -> bool:
        value = scores.get(key, -1.0)
        return value >= 0 and value >= thresholds.get(key, 0.80)

    table_score = scores["table_marker"]
    on_table = True if table_score < 0 else hit("table_marker")

    slot_cards = []
    slot_values: list[int] = []
    for slot_region in regions["card_slots"]:
        if slot_region.get("w", 0) <= 0:
            slot_cards.append(None)
            continue
        try:
            roi = capture.grab_region(slot_region)
        except Exception:
            slot_cards.append(None)
            continue
        slot_values.append(median_value(roi))
        h, w = roi.shape[:2]
        slot_cards.append(reader.read(roi, w, h))

    highlow_result = _recognize_highlow_card(capture, cfg, reader)

    # 「選擇要保留的牌吧！」這行字後面的背景光暈每一局都不太一樣，實測分數會在
    # 0.57 ~ 0.99 之間跳動。門檻設高會漏掉，設低又會在投注畫面誤判而卡住。
    # 折衷：分數夠高就直接算數；分數普通但「五張手牌都認得出來」時也算數
    #（投注畫面是五張蓋著的牌，認不出來，所以不會被誤判）。
    is_draw = hit("draw_prompt")
    if not is_draw and sum(1 for s in slot_cards if s is not None) == 5:
        soft = float(cfg.get("draw_prompt_soft_threshold", 0.50))
        is_draw = scores["draw_prompt"] >= soft

    return FrameInfo(
        on_table=on_table,
        table_marker_score=table_score,
        slot_cards=slot_cards,
        highlow_card=highlow_result,
        is_draw=is_draw,
        is_congrats=hit("congrats_marker"),
        is_challenge=hit("challenge_marker"),
        is_fail=hit("fail_marker"),
        is_poker_fail=hit("poker_fail_marker"),
        is_max_win=hit("max_win_marker"),
        slot_values=slot_values,
        ui_scores={
            "table": table_score,
            "draw": scores["draw_prompt"],
            "congrats": scores["congrats_marker"],
            "challenge": scores["challenge_marker"],
            "fail": scores["fail_marker"],
            "poker_fail": scores["poker_fail_marker"],
            "max_win": scores["max_win_marker"],
            "_scale": scale,
        },
    )
