"""單次畫面偵測：把螢幕截圖轉換成「目前看到了什麼」的結構化資訊。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from .recognize import CardReader, marker_score

if TYPE_CHECKING:  # 只在型別檢查時需要；執行時不要 import，
    from .capture import GameCapture  # 這樣離線工具不必安裝 mss / pywin32

# 六個畫面標記各自的預設門檻。舊版全部共用 table_marker_threshold(0.80)，
# 但它們的對比度、背景複雜度差很多，共用一個門檻一定會有人過不了、有人誤判。
DEFAULT_MARKER_THRESHOLDS = {
    "table_marker": 0.82,
    "draw_prompt": 0.78,
    "congrats_marker": 0.80,
    "challenge_marker": 0.80,
    "fail_marker": 0.82,
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

    @property
    def any_dialog(self) -> bool:
        """任何一個對話框畫面成立時為 True。

        這些畫面會把整個牌桌（含左上角 High&Low logo）模糊掉，
        不能拿來當作「已經離開牌桌」的證據。
        """
        return (self.is_congrats or self.is_challenge or self.is_fail
                or self.is_poker_fail or self.is_max_win)


def expected_marker_scale(cfg: dict, client_width: int) -> float:
    """目前視窗寬 ÷ 擷取畫面標記樣板當時的視窗寬。

    畫面標記樣板是校準當下從畫面切下來的點陣圖，帶著當時的解析度。
    內建的預設樣板 (defaults/ui/) 是從 1024×438 的截圖切出來的，
    如果實機視窗是 1937×817，樣板要放大 1.89 倍才對得起來。
    """
    tmpl_cfg = cfg.get("templates", {}) or {}
    try:
        ref_w = int(tmpl_cfg.get("capture_client_width") or 0)
    except (TypeError, ValueError):
        ref_w = 0
    if ref_w <= 0:
        ref_w = 1024  # 內建預設樣板的來源解析度
    if client_width <= 0:
        return 1.0
    return client_width / float(ref_w)


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
        client_w, _client_h = capture.get_client_size()
    except Exception:
        client_w = 0
    scale = expected_marker_scale(cfg, client_w)

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
    for slot_region in regions["card_slots"]:
        if slot_region.get("w", 0) <= 0:
            slot_cards.append(None)
            continue
        try:
            roi = capture.grab_region(slot_region)
        except Exception:
            slot_cards.append(None)
            continue
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
