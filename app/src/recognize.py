"""畫面辨識：卡牌樣板比對 + UI 畫面標記比對。

兩種比對的性質完全不同，所以分開處理：

* **卡牌**（card_slots / highlow_card）：位置固定、尺寸等於校準框，用「整張卡面」
  比對，但允許小幅位移搜尋，避免動畫或幾像素的偏移就認不出來。
* **畫面標記**（table_marker / draw_prompt / ...）：是一段文字或圖示，樣板是校準
  當下從畫面切下來的，**帶著當時的解析度**。實機視窗若不是當初擷取樣板的尺寸，
  必須先把樣板縮放到正確倍率才比對得起來。這個倍率由呼叫端算好傳進來
  （見 state_machine.expected_marker_scale），不要在這裡亂猜。

樣板檔放在 card_templates/，卡牌檔名格式為「點數+花色.png」，例如 10H.png、AS.png、
QD.png；同一張牌可以有多個樣板（10H_1.png、10H_2.png），比對時取最高分。
"""
from __future__ import annotations

import os
import math
from typing import Optional

import cv2
import numpy as np

from . import cardparts
from .paths import default_parts_dir, parts_dir, template_dir

TEMPLATE_DIR = template_dir()

# 這些檔名不是卡牌，是畫面標記樣板，載入卡牌時必須排除
RESERVED_NAMES = {
    "back",
    "table_marker",
    "ui_draw_prompt",
    "ui_congrats",
    "ui_challenge",
    "ui_fail",
    "ui_poker_fail",
}


def _is_reserved(name: str) -> bool:
    """判斷檔名（不含副檔名）是不是畫面標記樣板。

    注意：舊版是用 name.split("_")[0] 去比對，"ui_congrats" 會被切成 "ui"，
    既不在 RESERVED_NAMES 裡、也不是以 "ui_" 開頭，於是六張 UI 圖全部被當成
    「卡牌」載入（標籤變成 ui / table），一旦命中就會在 Card.from_label() 丟出
    ValueError 把整個主迴圈的 tick 吃掉。這裡改成用完整檔名判斷。
    """
    low = name.lower()
    if low in RESERVED_NAMES or low.startswith("ui_"):
        return True
    # 允許同一張牌多個樣板：10H_1 -> 10H；但要先確認底名不是保留字
    return low.split("_")[0] in RESERVED_NAMES


def card_label_from_filename(name: str) -> str:
    """10H_2.png -> 10H；AS.png -> AS。"""
    return name.split("_")[0].upper()


def load_card_templates(template_dir: str = TEMPLATE_DIR) -> dict[str, list]:
    """載入卡牌樣板，回傳 {牌面標籤: [樣板圖, ...]}。"""
    templates: dict[str, list] = {}
    if not os.path.isdir(template_dir):
        return templates
    for fname in os.listdir(template_dir):
        if not fname.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
            continue
        name = os.path.splitext(fname)[0]
        if _is_reserved(name):
            continue
        img = cv2.imread(os.path.join(template_dir, fname), cv2.IMREAD_COLOR)
        if img is None:
            continue
        templates.setdefault(card_label_from_filename(name), []).append(img)
    return templates


def load_part_templates(directory: Optional[str] = None) -> dict:
    """載入 card_templates/parts/ 裡的點數與花色樣板。

    同時把 defaults/parts/ 一起傳進去，讓「已經有自己樣板的標籤」不再混進內建的糊圖。
    """
    return cardparts.load_part_templates(directory or parts_dir(), default_parts_dir())


def part_sources(directory: Optional[str] = None) -> dict:
    """每個點數／花色目前用的是自己蒐集的樣板還是內建的。"""
    return cardparts.part_sources(directory or parts_dir(), default_parts_dir())


def load_single_template(path: str) -> Optional[np.ndarray]:
    if not os.path.exists(path):
        return None
    return cv2.imread(path, cv2.IMREAD_COLOR)


def is_blank_region(roi: np.ndarray, std_threshold: float = 8.0) -> bool:
    """判斷該區域是否為空（沒有牌、純色背景等），避免誤判。"""
    if roi is None or roi.size == 0:
        return True
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi
    return float(gray.std()) < std_threshold


def _as_gray(img: np.ndarray) -> np.ndarray:
    if img is None:
        return img
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _safe_match(img: np.ndarray, tmpl: np.ndarray) -> float:
    """TM_CCOEFF_NORMED，樣板必須小於等於搜尋圖，否則回 0。"""
    ih, iw = img.shape[:2]
    th, tw = tmpl.shape[:2]
    if th > ih or tw > iw or th < 4 or tw < 4:
        return 0.0
    try:
        result = cv2.matchTemplate(img, tmpl, cv2.TM_CCOEFF_NORMED)
    except Exception:
        return 0.0
    value = float(np.nanmax(result))
    return value if math.isfinite(value) else 0.0


def _resize(tmpl: np.ndarray, scale: float) -> Optional[np.ndarray]:
    nw, nh = int(round(tmpl.shape[1] * scale)), int(round(tmpl.shape[0] * scale))
    if nw < 6 or nh < 6:
        return None
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    return cv2.resize(tmpl, (nw, nh), interpolation=interp)


# --------------------------------------------------------------- 畫面標記

# 預期倍率附近要試的乘數。樣板是從某個區域切下來的，區域之後若被微調過，
# 實際倍率會跟推算值有點出入，所以往下多留一點餘裕。
_SCALE_MULTIPLIERS = (0.68, 0.78, 0.86, 0.93, 1.0, 1.07, 1.15)


def marker_score(
    roi: np.ndarray,
    template: np.ndarray,
    expected_scale: float = 1.0,
    multipliers: tuple = _SCALE_MULTIPLIERS,
) -> float:
    """在 ROI 內搜尋畫面標記樣板，回傳 0~1 的最高相似度。

    expected_scale = 目前視窗寬 ÷ 擷取樣板當時的視窗寬。
    只在這個倍率附近搜尋，不要像舊版那樣用一串固定倍率
    (0.45, 0.55, 0.7, 0.85, 1.0, 1.15, 1.3, max_scale) —— 那串在 1.3 和
    max_scale（常常 3 以上）之間有巨大斷層，視窗放大到 1.3 倍以上時
    「正確的比對尺寸」根本不會被試到，分數必然掉下來。
    """
    if roi is None or template is None or roi.size == 0 or template.size == 0:
        return 0.0
    img = _as_gray(roi)
    tmpl0 = _as_gray(template)
    if img.shape[0] < 8 or img.shape[1] < 8:
        return 0.0
    if not math.isfinite(expected_scale) or expected_scale <= 0:
        expected_scale = 1.0

    best = 0.0
    tried = 0
    for m in multipliers:
        resized = _resize(tmpl0, expected_scale * m)
        if resized is None:
            continue
        if resized.shape[0] > img.shape[0] or resized.shape[1] > img.shape[1]:
            continue
        tried += 1
        best = max(best, _safe_match(img, resized))
    if tried == 0:
        # 樣板在任何倍率下都塞不進 ROI，退而求其次：縮到剛好塞得下
        fit = min(img.shape[1] / tmpl0.shape[1], img.shape[0] / tmpl0.shape[0])
        resized = _resize(tmpl0, fit)
        if resized is not None:
            best = _safe_match(img, resized)
    return best


def region_similarity(roi: np.ndarray, template: np.ndarray, expected_scale: float = 1.0) -> float:
    """相容舊呼叫方式的別名。"""
    return marker_score(roi, template, expected_scale)


# ----------------------------------------------------------------- 卡牌

def _card_scores(roi: np.ndarray, templates: dict[str, list]) -> dict[str, float]:
    """把每個樣板縮放到略小於 ROI 再比對，允許幾像素的位移。"""
    ih, iw = roi.shape[:2]
    scores: dict[str, float] = {}
    for label, imgs in templates.items():
        best = 0.0
        for tmpl in imgs:
            th, tw = tmpl.shape[:2]
            scale = min(iw / tw, ih / th) * 0.94
            t = _resize(tmpl, scale)
            if t is None:
                continue
            best = max(best, _safe_match(roi, t))
        scores[label] = best
    return scores


def recognize_card(
    roi: np.ndarray,
    templates: dict[str, list],
    threshold: float = 0.83,
    min_margin: float = 0.02,
) -> Optional[tuple[str, float]]:
    """回傳 (牌面標籤如 "10H", 相似度分數)，辨識不出或該區域是空的時回傳 None。

    除了「最高分是否超過 threshold」，還要求最高分與第二名至少差 min_margin，
    避免把相近的牌認錯（寧可辨識失敗，也不要猜錯牌）。
    """
    if roi is None or roi.size == 0 or is_blank_region(roi):
        return None
    if not templates:
        return None

    label_scores = _card_scores(roi, templates)
    if not label_scores:
        return None

    ranked = sorted(label_scores.items(), key=lambda kv: kv[1], reverse=True)
    best_label, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else -1.0

    if best_score < threshold:
        return None
    if second_score >= 0 and (best_score - second_score) < min_margin:
        return None
    return best_label, best_score


class CardReader:
    """統一的牌面辨識入口。

    有兩套機制，優先用角落：

    1. **角落（點數 + 花色）** —— 只要 13 + 4 = 17 個小樣板就能認得全部 52 張牌，
       而且比大小畫面裡被疊住、只露出左上角的歷史牌也認得出來。
    2. **整張卡面** —— 舊做法，需要 52 張樣板。角落認不出來時當備援。
    """

    def __init__(
        self,
        card_templates: Optional[dict] = None,
        part_templates: Optional[dict] = None,
        threshold: float = 0.83,
        min_margin: float = 0.02,
        part_min_score: float = cardparts.DEFAULT_MIN_SCORE,
        part_min_margin: float = cardparts.DEFAULT_MIN_MARGIN,
    ):
        self.card_templates = card_templates or {}
        self.part_templates = part_templates or {"rank": {}, "suit": {}}
        self.threshold = threshold
        self.min_margin = min_margin
        self.part_min_score = part_min_score
        self.part_min_margin = part_min_margin

    @property
    def has_parts(self) -> bool:
        return bool(self.part_templates.get("rank")) and bool(self.part_templates.get("suit"))

    @property
    def has_cards(self) -> bool:
        return bool(self.card_templates)

    @property
    def ready(self) -> bool:
        return self.has_parts or self.has_cards

    def read(self, roi, expected_w: int = 0, expected_h: int = 0):
        """回傳 (牌面標籤, 分數) 或 None。"""
        if roi is None or getattr(roi, "size", 0) == 0:
            return None
        if self.has_parts:
            hit = cardparts.recognize_by_corner(
                roi, self.part_templates, expected_w, expected_h,
                min_score=self.part_min_score, min_margin=self.part_min_margin,
            )
            if hit is not None:
                return hit
        if self.has_cards:
            return recognize_card(roi, self.card_templates, self.threshold, self.min_margin)
        return None

    def explain(self, roi, expected_w: int = 0, expected_h: int = 0) -> str:
        """辨識失敗時說明原因，方便使用者知道該補哪個樣板。"""
        if not self.has_parts:
            return "沒有點數/花色樣板"
        parts = cardparts.extract_parts(roi, expected_w, expected_h)
        if parts is None:
            return "切不出左上角（卡面可能被蓋住，或校準框沒對準卡片）"
        return cardparts.explain_parts(
            parts, self.part_templates,
            min_score=self.part_min_score, min_margin=self.part_min_margin,
        )

    def read_rightmost(self, strip, expected_w: int, expected_h: int):
        """比大小畫面：在一條水平長條裡讀出「最右邊那張完整露出的牌」。

        版面有兩種：牌堆右邊接著一張蓋著的牌（等你猜大小），或是剛翻開的那張
        單獨落在右邊一小段距離外。兩種情況下「要比的牌」都是**最右邊那張正面朝上
        的牌**，所以取最右邊的白色卡身區塊，而不是面積最大的那塊 —— 剛翻開的
        那一張是單獨一塊，面積往往比左邊那一整排小。蓋著的牌是紫色的卡背，
        不是白的，不會被選到。
        """
        if strip is None or getattr(strip, "size", 0) == 0:
            return None
        rect = cardparts.rightmost_card_rect(strip, expected_w, expected_h)
        if rect is None:
            return None
        if self.has_parts:
            hit = cardparts.recognize_by_corner(
                strip, self.part_templates, expected_w, expected_h,
                min_score=self.part_min_score, min_margin=self.part_min_margin,
                rect=rect,
            )
            if hit is not None:
                return hit
        if self.has_cards:
            x, y, w, h = rect
            card = strip[y: y + h, x: x + w]
            if card.size:
                return recognize_card(card, self.card_templates, self.threshold, self.min_margin)
        return None


def find_rightmost_card(
    strip: np.ndarray,
    templates: dict[str, list],
    card_w: int,
    card_h: int,
    threshold: float = 0.83,
    min_margin: float = 0.02,
) -> Optional[tuple[str, float, int]]:
    """在一條水平長條畫面裡找「最右邊那張認得出來的牌」。

    比大小畫面的歷史牌是往左層層堆疊的，只露出左上角一小條，右邊那張完整
    露出的才是目前要比的牌。舊版是用固定步距（約卡寬的 28%）一格一格滑動再
    取最右，那個誤差大到足以讓整張卡面比對失敗；這裡改成一次 matchTemplate
    掃過整條，直接拿到每張牌的最佳分數與 x 位置，位置精確到 1 像素。

    回傳 (標籤, 分數, 在 strip 內的 x 座標)。
    """
    if strip is None or strip.size == 0 or not templates:
        return None
    sh, sw = strip.shape[:2]
    if card_w < 8 or card_h < 8 or card_w > sw or card_h > sh:
        return None

    band = strip[:card_h, :]
    hits: list[tuple[int, str, float]] = []  # (x, label, score)
    for label, imgs in templates.items():
        best_score, best_x = 0.0, -1
        for tmpl in imgs:
            t = cv2.resize(tmpl, (card_w, card_h), interpolation=cv2.INTER_AREA)
            if t.shape[0] > band.shape[0] or t.shape[1] > band.shape[1]:
                continue
            try:
                result = cv2.matchTemplate(band, t, cv2.TM_CCOEFF_NORMED)
            except Exception:
                continue
            _, mx, _, mloc = cv2.minMaxLoc(result)
            if math.isfinite(mx) and mx > best_score:
                best_score, best_x = float(mx), int(mloc[0])
        if best_x >= 0:
            hits.append((best_x, label, best_score))

    confident = [h for h in hits if h[2] >= threshold]
    if not confident:
        return None

    # 取最右邊的位置；同一個位置可能有多張牌都過門檻，要再看領先幅度
    confident.sort(key=lambda h: h[0])
    rightmost_x = confident[-1][0]
    near = [h for h in confident if rightmost_x - h[0] <= max(4, card_w // 6)]
    near.sort(key=lambda h: h[2], reverse=True)
    best_x, best_label, best_score = near[0]
    if len(near) > 1 and (best_score - near[1][2]) < min_margin:
        return None  # 同一個位置有兩張牌分數太接近，不敢採信
    return best_label, best_score, best_x


def median_value(image: "np.ndarray") -> int:
    """回傳一塊畫面的「中位亮度」(HSV 的 V，0~255)。

    用來區分「蓋著的深色牌背」與「白色牌面／淺色面板」，判斷目前是不是
    等你下注的投注畫面。取中位數而不是平均：牌面上的黑色花色圖案、牌背上的
    亮色邊框都是少數像素，中位數不會被它們拉走。
    """
    if image is None or getattr(image, "size", 0) == 0:
        return -1
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    return int(np.median(hsv[:, :, 2]))
