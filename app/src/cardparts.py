"""從卡面左上角讀出「點數 + 花色」。

為什麼要這樣做：

* 整張卡面比對要蒐集 **52 張** 樣板才算完整；改成讀左上角，只需要
  **13 個點數 + 4 個花色 = 17 個小圖**，玩一兩局就能湊齊。
* 比大小畫面的歷史牌是層層疊在一起的，只露出左上角那一小條，整張卡面比對
  一定失敗；左上角剛好就是唯一還看得到的地方。

流程：

    卡面區域 (ROI)
      → 找出白色卡身的外框（容忍校準框有幾像素偏移）
      → 從卡身左上角切出「角落」
      → 二值化出墨色像素（黑字或紅字）
      → 依橫向投影切成上下兩塊：上面是點數、下面是花色
      → 各自正規化成固定大小的小圖，跟樣板比對

花色先用顏色分成紅 (H/D) 與黑 (S/C) 兩組，再比形狀，這樣只要分辨兩者即可。
"""
from __future__ import annotations

import hashlib
import os
from typing import Optional

import cv2
import numpy as np

RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
SUITS = ("S", "H", "D", "C")
RED_SUITS = ("H", "D")
BLACK_SUITS = ("S", "C")

# 角落各部位佔整張卡的比例（以「白色卡身」的左上角為原點）。
# 這些數字是從實機截圖量出來的，跨多張截圖非常一致：
#   點數字 y 約 0.03~0.12、花色 y 約 0.125~0.19、兩者 x 都在 0.02~0.21。
# 直接依比例切，比「找墨色區塊再切」穩定 —— 畫面一糊，「2」「3」的橫筆會斷開，
# 區塊偵測就會把一個字誤判成兩塊，整個往下錯一格。
CORNER_X0, CORNER_X1 = 0.020, 0.215
RANK_Y0, RANK_Y1 = 0.025, 0.123
SUIT_Y0, SUIT_Y1 = 0.123, 0.195

# 卡面中央畫著跟花色一樣的大圖案（數字牌才有；J/Q/K 是人像）。
# 這個圖案比角落那顆小花色大三四倍，是分辨黑桃/梅花最可靠的依據。
CENTRE_X0, CENTRE_X1 = 0.14, 0.86
CENTRE_Y0, CENTRE_Y1 = 0.22, 0.90
NUMBER_RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10")

# 正規化後的樣板大小
RANK_SIZE = (24, 32)   # (w, h)
SUIT_SIZE = (24, 24)
PIP_SIZE = (32, 32)    # 卡面中央的大圖案

PARTS_SUBDIR = "parts"


# ------------------------------------------------------------ 影像處理

def _hsv(bgr: np.ndarray):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    return hsv[..., 0].astype(np.int16), hsv[..., 1].astype(np.int16), hsv[..., 2].astype(np.int16)


def card_body_rect(
    roi: np.ndarray,
    prefer: str = "largest",
    min_height_frac: float = 0.0,
    min_fill: float = 0.0,
) -> Optional[tuple[int, int, int, int]]:
    """找出 ROI 裡白色卡身的外框 (x, y, w, h)。找不到回傳 None。

    prefer="largest"   取面積最大的（單張卡用）
    prefer="rightmost" 取最右邊的（比大小畫面用：剛翻開的那張會單獨落在右邊，
                       面積不一定最大，但一定最右）
    min_height_frac    高度至少要有 ROI 高度的幾成才算數，用來濾掉小雜訊
    min_fill           面積至少要佔外框的幾成。蓋著的牌那圈淺色邊框會被連成一個
                       又高又寬、但中間全空的「環」，外框看起來像一張卡，
                       實際填充率只有 1%，靠這個濾掉
    """
    if roi is None or roi.size == 0:
        return None
    _h, s, v = _hsv(roi)
    white = ((v > 165) & (s < 70)).astype(np.uint8)
    if white.sum() < roi.shape[0] * roi.shape[1] * 0.02:
        return None
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, _labels, stats, _cent = cv2.connectedComponentsWithStats(white, 8)
    if n <= 1:
        return None

    min_h = roi.shape[0] * min_height_frac
    best = None
    best_key = None
    for i in range(1, n):
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y = int(stats[i, cv2.CC_STAT_TOP])
        w = int(stats[i, cv2.CC_STAT_WIDTH])
        h = int(stats[i, cv2.CC_STAT_HEIGHT])
        area = int(stats[i, cv2.CC_STAT_AREA])
        if w < 6 or h < 6 or h < min_h:
            continue
        if min_fill > 0 and area < w * h * min_fill:
            continue
        key = (x + w) if prefer == "rightmost" else area
        if best_key is None or key > best_key:
            best_key, best = key, (x, y, w, h)
    return best


def _ink_mask(bgr: np.ndarray) -> np.ndarray:
    """卡面是白的，墨色 = 很暗（黑字）或很鮮豔（紅字）。"""
    _h, s, v = _hsv(bgr)
    return (((v < 150) | ((s > 90) & (v > 80)))).astype(np.uint8)


def _row_bands(mask: np.ndarray, min_frac: float = 0.10) -> list[tuple[int, int]]:
    """依橫向投影把遮罩切成上下數塊。"""
    if mask.size == 0:
        return []
    need = max(1, int(round(mask.shape[1] * min_frac)))
    on = mask.sum(1) >= need
    bands: list[tuple[int, int]] = []
    start = None
    for i, flag in enumerate(on):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            bands.append((start, i))
            start = None
    if start is not None:
        bands.append((start, len(on)))
    bands = [b for b in bands if b[1] - b[0] >= 2]  # 濾掉只有一兩列的雜訊

    # 畫面糊的時候，「2」「3」這種數字的橫筆會斷開成兩塊，要先併回同一個字。
    gap_limit = max(2, int(round(mask.shape[0] * 0.10)))
    merged: list[tuple[int, int]] = []
    for band in bands:
        if merged and band[0] - merged[-1][1] <= gap_limit:
            merged[-1] = (merged[-1][0], band[1])
        else:
            merged.append(band)
    return merged


def _tight(mask: np.ndarray) -> Optional[np.ndarray]:
    cols = np.nonzero(mask.sum(0))[0]
    rows = np.nonzero(mask.sum(1))[0]
    if len(cols) == 0 or len(rows) == 0:
        return None
    return mask[rows.min(): rows.max() + 1, cols.min(): cols.max() + 1]


def _shift(img: np.ndarray, dx: int, dy: int) -> np.ndarray:
    if dx == 0 and dy == 0:
        return img
    matrix = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, matrix, (img.shape[1], img.shape[0]), borderValue=0)


def centre_mask(img: np.ndarray) -> np.ndarray:
    """把前景的「質心」移到畫布正中央。

    這一步是辨識率的關鍵。原本是用外接矩形置中，但外接矩形只能算到整數像素，
    同一個字在不同影格裡切出來的位置常常差一兩格 —— 而差一兩格，IoU 就會掉
    0.15~0.20 分。實測：不對位時正解分數中位數只有 0.78（門檻 0.80，幾乎全被
    擋掉）；質心置中 + ±1 微調之後升到 0.97，領先幅度也從 +0.15 拉大到 +0.30。
    """
    if img is None or img.size == 0:
        return img
    mask = img > 127
    if not mask.any():
        return img
    ys, xs = np.nonzero(mask)
    h, w = img.shape[:2]
    return _shift(img, int(round((w - 1) / 2.0 - xs.mean())),
                  int(round((h - 1) / 2.0 - ys.mean())))


# 點數小圖去雜點的門檻：面積小於「最大連通塊的這個比例」就丟掉。
#
# 為什麼只對點數做：角落的點數與花色是靠橫向投影切開的，切點不可能每次都完美，
# 常常把花色符號的一小角留在點數那一塊裡（實機樣板 rank_2_5、rank_3_5、rank_8_2
# 底下都看得到那顆小點）。質心置中會被那顆小點拉偏，整個字就歪掉。
#
# 花色與中央大圖案**不做**：梅花本身就是三個瓣、有些花色正規化後會斷成幾塊，
# 實測門檻拉到 10% 以上就開始把真正的花色瓣切掉（suit_H_9 被誤判成 D）。
#
# 「10」是兩塊（1 和 0），所以不能只留最大塊，要用比例。
# 實測 3%~12% 之間結果完全一樣（點數 59/62 → 60/62），取中間值。
RANK_SPECK_AREA_FRAC = 0.08


def drop_specks(mask: np.ndarray, min_area_frac: float) -> np.ndarray:
    """丟掉「明顯比主體小」的獨立小塊。全空或只有一塊時原樣回傳。"""
    if mask is None or mask.size == 0:
        return mask
    binary = (mask > 127).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if count <= 2:
        return mask
    areas = stats[1:, cv2.CC_STAT_AREA]
    if len(areas) == 0:
        return mask
    biggest = int(areas.max())
    keep = np.zeros_like(binary)
    for index, area in enumerate(areas, start=1):
        if area >= biggest * min_area_frac:
            keep[labels == index] = 1
    if keep.sum() == 0:
        return mask
    return keep * 255


def clean_part_mask(mask: np.ndarray, kind: str) -> np.ndarray:
    """依種類做該做的清理。目前只有點數需要去雜點。"""
    if kind == "rank":
        return drop_specks(mask, RANK_SPECK_AREA_FRAC)
    return mask


def _normalize(mask: np.ndarray, size: tuple[int, int]) -> Optional[np.ndarray]:
    """裁緊後等比例放進固定畫布，回傳 0/255 的灰階小圖。"""
    tight = _tight(mask)
    if tight is None or tight.shape[0] < 2 or tight.shape[1] < 2:
        return None
    tw, th = size
    scale = min((tw - 4) / tight.shape[1], (th - 4) / tight.shape[0])
    if scale <= 0:
        return None
    nw, nh = max(1, int(round(tight.shape[1] * scale))), max(1, int(round(tight.shape[0] * scale)))
    small = cv2.resize((tight * 255).astype(np.uint8), (nw, nh), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((th, tw), np.uint8)
    ox, oy = (tw - nw) // 2, (th - nh) // 2
    canvas[oy: oy + nh, ox: ox + nw] = small
    return centre_mask(canvas)


def _col_clusters(mask: np.ndarray, min_frac: float = 0.12, min_width: int = 4,
                  merge_gap: int = 6) -> list[tuple[int, int]]:
    """依直向投影把遮罩切成左右數塊（用來找出一排牌各自的點數字）。"""
    if mask.size == 0:
        return []
    need = max(1, int(round(mask.shape[0] * min_frac)))
    on = mask.sum(0) >= need
    out: list[tuple[int, int]] = []
    start = None
    for i, flag in enumerate(on):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(on)))
    out = [c for c in out if c[1] - c[0] >= min_width]
    merged: list[tuple[int, int]] = []
    for c in out:                      # 「10」是兩個字，要併成一個
        if merged and c[0] - merged[-1][1] <= merge_gap:
            merged[-1] = (merged[-1][0], c[1])
        else:
            merged.append(c)
    return merged


def rightmost_card_rect(
    strip: np.ndarray,
    expected_w: int,
    expected_h: int,
) -> Optional[tuple[int, int, int, int]]:
    """比大小畫面：在一條水平長條裡找出「最右邊那張正面朝上的牌」的位置。

    版面有兩種：牌堆右邊接著一張蓋著的牌，或剛翻開的那張單獨落在右邊。
    兩種情況下要比的都是最右邊那張正面朝上的牌。

    做法：先取最右邊的白色卡身區塊（蓋著的牌是紫色卡背，不會被選到），再在
    那一塊的上緣找出所有點數字，最右邊那個字就屬於目前這張牌 —— 用「字的位置」
    定位比用「區塊寬度去推算」可靠，因為疊了幾張牌是會變的。
    """
    if strip is None or strip.size == 0 or expected_w < 16 or expected_h < 20:
        return None
    blob = card_body_rect(strip, prefer="rightmost", min_height_frac=0.55, min_fill=0.35)
    if blob is None:
        return None
    bx, by, bw, bh = blob

    band_top = by + int(round(expected_h * RANK_Y0))
    band_bottom = by + int(round(expected_h * RANK_Y1))
    band_top = max(0, min(band_top, strip.shape[0] - 2))
    band_bottom = max(band_top + 2, min(band_bottom, strip.shape[0]))
    band = strip[band_top:band_bottom, bx: bx + bw]
    if band.size == 0:
        return None

    clusters = _col_clusters(_ink_mask(band), min_width=max(4, int(expected_w * 0.02)),
                             merge_gap=max(3, int(expected_w * 0.03)))
    # 貼著區塊最右緣的細長雜訊（卡片邊框）不算點數字
    clusters = [c for c in clusters if bw - c[1] > 2]
    if not clusters:
        return None

    glyph_x0 = clusters[-1][0]
    left = max(0, bx + glyph_x0 - int(round(expected_w * 0.045)))
    left = min(left, max(0, strip.shape[1] - 4))
    return left, by, expected_w, expected_h


def extract_parts(
    roi: np.ndarray,
    expected_w: int = 0,
    expected_h: int = 0,
    rect: Optional[tuple[int, int, int, int]] = None,
) -> Optional[dict]:
    """從卡面 ROI 取出點數與花色的小圖。

    回傳 {"rank": 灰階小圖, "suit": 灰階小圖, "is_red": bool, "corner": 角落彩圖}，
    取不到就回傳 None。

    expected_w / expected_h 是「完整一張卡在畫面上的像素大小」。比大小畫面的
    歷史牌只露出左邊一小條，偵測到的卡身寬度會遠小於實際卡寬，這時角落大小要
    依 expected 值算，不能依偵測到的寬度。
    """
    if rect is None:
        rect = card_body_rect(roi)
    if rect is None:
        return None
    bx, by, bw, bh = rect
    ref_w = expected_w if expected_w > 0 else bw
    ref_h = expected_h if expected_h > 0 else bh
    if ref_w < 24 or ref_h < 32:
        return None

    def _slice(y0: float, y1: float):
        x0 = bx + int(round(ref_w * CORNER_X0))
        x1 = bx + int(round(ref_w * CORNER_X1))
        ya = by + int(round(ref_h * y0))
        yb = by + int(round(ref_h * y1))
        x0 = max(0, min(x0, roi.shape[1] - 1))
        x1 = max(x0 + 4, min(x1, roi.shape[1]))
        ya = max(0, min(ya, roi.shape[0] - 1))
        yb = max(ya + 4, min(yb, roi.shape[0]))
        return roi[ya:yb, x0:x1]

    rank_rgb = _slice(RANK_Y0, RANK_Y1)
    suit_rgb = _slice(SUIT_Y0, SUIT_Y1)
    if rank_rgb.size == 0 or suit_rgb.size == 0:
        return None

    rank_mask = _ink_mask(rank_rgb)
    suit_mask = _ink_mask(suit_rgb)
    if rank_mask.sum() < 6 or suit_mask.sum() < 4:
        return None

    rank_img = _normalize(clean_part_mask(rank_mask, "rank"), RANK_SIZE)
    suit_img = _normalize(suit_mask, SUIT_SIZE)
    if rank_img is None or suit_img is None:
        return None

    # 花色顏色：用花色那一塊的墨色像素判斷紅/黑
    _h, s, v = _hsv(suit_rgb)
    ink = suit_mask.astype(bool)
    is_red = bool(np.median(s[ink]) > 90 and np.median(v[ink]) > 80)

    corner = _slice(RANK_Y0, SUIT_Y1)
    bottom = _extract_bottom_corner(roi, rect, ref_w, ref_h)
    return {
        "rank": rank_img,
        "suit": suit_img,
        "rank2": bottom[0] if bottom else None,
        "suit2": bottom[1] if bottom else None,
        "is_red": is_red,
        "corner": corner,
        "pip": extract_centre_pip(roi, rect, ref_w, ref_h),
    }


def _extract_bottom_corner(roi: np.ndarray, rect, ref_w: int, ref_h: int):
    """右下角那組（旋轉 180 度的）點數 + 花色，取不到就回 None。

    撲克牌的點數花色一定印兩次，右下角是左上角轉 180 度的複製品。
    整張牌看得到時就多讀一次，兩次分數平均，抵銷模糊與反光造成的雜訊。
    比大小畫面的歷史牌被疊住、只露左邊一條，這時 bw/bh 明顯小於整張牌，直接放棄。
    """
    bx, by, bw, bh = rect
    if bw < 0.88 * ref_w or bh < 0.88 * ref_h:
        return None

    def _slice(y0: float, y1: float):
        x1 = bx + bw - int(round(ref_w * CORNER_X0))
        x0 = bx + bw - int(round(ref_w * CORNER_X1))
        yb = by + bh - int(round(ref_h * y0))
        ya = by + bh - int(round(ref_h * y1))
        x0 = max(0, min(x0, roi.shape[1] - 1))
        x1 = max(x0 + 4, min(x1, roi.shape[1]))
        ya = max(0, min(ya, roi.shape[0] - 1))
        yb = max(ya + 4, min(yb, roi.shape[0]))
        return cv2.rotate(roi[ya:yb, x0:x1], cv2.ROTATE_180)

    rank_rgb = _slice(RANK_Y0, RANK_Y1)
    suit_rgb = _slice(SUIT_Y0, SUIT_Y1)
    if rank_rgb.size == 0 or suit_rgb.size == 0:
        return None
    rank_mask = _ink_mask(rank_rgb)
    suit_mask = _ink_mask(suit_rgb)
    if rank_mask.sum() < 6 or suit_mask.sum() < 4:
        return None
    rank_img = _normalize(clean_part_mask(rank_mask, "rank"), RANK_SIZE)
    suit_img = _normalize(suit_mask, SUIT_SIZE)
    if rank_img is None or suit_img is None:
        return None
    return rank_img, suit_img


def extract_centre_pip(
    roi: np.ndarray,
    rect: Optional[tuple] = None,
    ref_w: int = 0,
    ref_h: int = 0,
) -> Optional[np.ndarray]:
    """取出卡面中央那顆最大的花色圖案（數字牌才有；人像牌會取到人物而不可靠）。

    角落那顆花色只有十幾像素，黑桃和梅花的分數常常只差 0.01，光靠它容易認錯；
    中央的圖案大三四倍，實測 38/38 全對、分數領先幅度中位數 0.175。
    """
    if roi is None or roi.size == 0:
        return None
    if rect is None:
        rect = card_body_rect(roi)
    if rect is None:
        return None
    bx, by, bw, bh = rect
    ref_w = ref_w or bw
    ref_h = ref_h or bh

    x0 = max(0, bx + int(round(ref_w * CENTRE_X0)))
    x1 = min(roi.shape[1], bx + int(round(ref_w * CENTRE_X1)))
    y0 = max(0, by + int(round(ref_h * CENTRE_Y0)))
    y1 = min(roi.shape[0], by + int(round(ref_h * CENTRE_Y1)))
    if x1 - x0 < 12 or y1 - y0 < 12:
        return None
    sub = roi[y0:y1, x0:x1]

    mask = _ink_mask(sub)
    count, labels, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
    if count <= 1:
        return None
    min_area = ref_w * ref_h * 0.004
    best = None
    for i in range(1, count):
        area = stats[i, cv2.CC_STAT_AREA]
        cw, chh = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        if area < min_area or chh <= 0:
            continue
        if not (0.6 < cw / chh < 1.7):      # 花色圖案大致是方的，人物插圖不是
            continue
        if best is None or area > best[0]:
            best = (area, i)
    if best is None:
        return None
    i = best[1]
    x, y = stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP]
    cw, chh = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
    return _normalize((labels[y:y + chh, x:x + cw] == i).astype(np.uint8), PIP_SIZE)


# ------------------------------------------------------------ 樣板比對

def _shape_score(a: np.ndarray, b: np.ndarray) -> float:
    """兩張同尺寸的二值小圖的重疊度（IoU，0~1）。"""
    if a is None or b is None or a.shape != b.shape:
        return 0.0
    af = a.astype(np.float32) / 255.0
    bf = b.astype(np.float32) / 255.0
    inter = float(np.minimum(af, bf).sum())
    union = float(np.maximum(af, bf).sum())
    if union <= 0:
        return 0.0
    return inter / union


def _profile(img: np.ndarray) -> np.ndarray:
    """橫向與縱向的寬度輪廓。

    花色的四個圖案在 24×24 這種小尺寸下，光看重疊度很難分（黑桃和梅花的 IoU
    可以到 0.88，跟「同一個花色的兩張不同卡」的 0.91 幾乎沒差）。但它們的
    「每一列有多寬」差很多：黑桃是平滑地由尖變寬，梅花在上三分之一有一段
    卡住不變寬的肩線，紅心一開始就很寬還帶凹口，方塊則是對稱的菱形。
    """
    mask = (img > 127).astype(np.float32)
    rows = mask.sum(1)
    cols = mask.sum(0)
    rows = rows / max(1e-6, float(rows.max()))
    cols = cols / max(1e-6, float(cols.max()))
    return np.concatenate([rows, cols])


def _profile_score(a: np.ndarray, b: np.ndarray) -> float:
    if a is None or b is None or a.shape != b.shape:
        return 0.0
    return 1.0 - float(np.abs(_profile(a) - _profile(b)).mean())


def part_score(a: np.ndarray, b: np.ndarray, iou_weight: float = 0.5) -> float:
    """重疊度與寬度輪廓各半，兩者互補。"""
    return iou_weight * _shape_score(a, b) + (1.0 - iou_weight) * _profile_score(a, b)


PART_SIZES = {"rank": RANK_SIZE, "suit": SUIT_SIZE, "pip": PIP_SIZE}


def parse_part_name(fname: str):
    """rank_10_2.png → ("rank", "10")；不是樣板檔就回 None。"""
    if not fname.lower().endswith(".png"):
        return None
    bits = os.path.splitext(fname)[0].split("_")
    if len(bits) < 2 or bits[0] not in PART_SIZES:
        return None
    return bits[0], bits[1].upper()


# 一個「有用的」樣板，圖案至少要佔畫布這麼大的比例（外框面積 / 畫布面積）。
#
# 2026-08-21 量了實機上 35 個花色樣板，分佈完全分離：
#     垃圾 4 個： 0.097 ~ 0.111   ← 只有一個 8x7 的小點
#     正常 31 個：0.312 ~ 0.694
# 中間空一大段，0.22 放在中間非常安全。
#
# 那 4 個垃圾是使用者在「校準還沒對上這個長寬比」的狀態下按儲存留下來的：
# 角落裁切框沒對準，只切到花色符號的一小角，正規化之後就變成一個小圓點。
# 小圓點跟任何圓形花色都有點像，於是把整組比對拖下水 ——
# 實測整體正確率從 ≥75% 掉到 66.7%（36/54），而且症狀是「一直認不出來」。
MIN_PART_COVERAGE = 0.22


def part_coverage(img) -> float:
    """圖案的外框佔整張畫布的比例（0~1）。全空回 0。"""
    if img is None or getattr(img, "size", 0) == 0:
        return 0.0
    mask = img > 127 if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) > 127
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return 0.0
    box = (xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1)
    return float(box) / float(mask.shape[0] * mask.shape[1])


def part_is_usable(img, min_coverage: float = MIN_PART_COVERAGE) -> bool:
    """這張樣板夠不夠格拿來比對。

    擋的是「裁切框沒對準，只切到符號一角」留下來的小點。這種檔案不會報錯、
    看起來也像個正常的樣板檔，但會把同標籤的比對整組拖下水。
    """
    return part_coverage(img) >= min_coverage


def bundled_fingerprints(bundled_dir: Optional[str]) -> dict:
    """內建樣板的「檔名 → 內容雜湊」。用來判斷 parts/ 裡哪一個還是內建的。"""
    out: dict = {}
    if not bundled_dir or not os.path.isdir(bundled_dir):
        return out
    for name in os.listdir(bundled_dir):
        if not name.lower().endswith(".png"):
            continue
        try:
            with open(os.path.join(bundled_dir, name), "rb") as f:
                out[name] = hashlib.md5(f.read()).digest()
        except OSError:
            continue
    return out


def is_bundled_copy(parts_dir: str, fname: str, fingerprints: dict) -> bool:
    """`parts/` 裡這個檔案是不是「還沒被換掉的內建樣板」。

    **一定要比內容，不能只看檔名。** 這是 2026-08-21 找到的核心 bug：

    內建檔叫 `suit_S_1.png` ~ `suit_S_8.png`，而 `next_part_path()` 會把使用者
    自己抓的樣板寫進「目前空出來的最小編號」—— 內建檔被刪掉之後，那就是
    `suit_S_1.png`。於是只看檔名的程式會把**使用者實機抓的清晰樣板當成內建糊圖**，
    後果有三個，而且全都沒有任何錯誤訊息：

    1. `load_part_templates()` 把它丟進 fallback，只要同標籤還有別的樣板就整組不用
       —— 存了等於沒存。
    2. `next_part_path()` 把它當成「該順手刪掉的內建檔」——
       **下一次儲存會把上一次儲存的成果刪掉**，所以「多抓幾次」永遠不會累積。
    3. 「清掉所有內建樣板」會把它一起刪掉，而按鈕上明明寫著「你自己抓的不會動」。

    使用者的實際狀況（2026-08-21 實測）：67 個樣板裡有 19 個是自己抓的、卻坐在
    內建的檔名上，每個花色真正拿來比對的只剩 1~2 張。難怪方塊會被認成愛心。
    """
    expected = fingerprints.get(fname)
    if expected is None:
        return False
    try:
        with open(os.path.join(parts_dir, fname), "rb") as f:
            return hashlib.md5(f.read()).digest() == expected
    except OSError:
        return False


# 自己抓的樣板要累積到幾張，才敢完全不用內建的那一組。
#
# 2026-08-21 拿使用者實機的樣板量出來的（留一法，查詢與樣板都是實機圖）：
#
#     政策                                    角落花色      點數
#     只要有 1 張自己的就丟掉內建（舊行為）        78.1%       88.4%
#     自己的滿 3 張才丟（同一個比較組一起判斷）     100.0%       95.3%
#
# 舊行為是為了解決「糊掉的內建 7 搶走清楚的 2」而寫的，但它在**樣板還很少**的時候
# 反而是致命的：使用者的 suit_D 只有 1 張可用，一丟掉內建那 8 張，方塊就只能拿
# 1 張去跟 3 張紅心比 —— 這就是「方塊被認成愛心」的直接原因。
MIN_OWN_TO_DROP_BUNDLED = 3

# 「會互相搶」的標籤要一起決定要不要丟內建。
#
# 花色是先用顏色縮成兩個候選才比形狀，所以 H 只跟 D 競爭、S 只跟 C 競爭。
# 如果 H 有 3 張自己的、D 只有 1 張，各自決定的結果會是「3 張清楚的 H
# vs 1 張清楚的 D + 8 張糊的 D」—— 一場不公平的比賽，而且偏向錯的那邊。
# 同組一起判斷就不會出現這種情況。
COMPARISON_GROUPS: dict = {
    "suit": (("H", "D"), ("S", "C")),
    "pip": (("H", "D"), ("S", "C")),
    "rank": (tuple(RANKS),),
}


def _group_for(kind: str, key: str) -> tuple:
    for group in COMPARISON_GROUPS.get(kind, ()):
        if key in group:
            return group
    return (key,)


def load_part_templates(parts_dir: str, bundled_dir: Optional[str] = None) -> dict:
    """載入 parts 資料夾裡的點數/花色樣板。

    檔名：rank_A.png、rank_10.png、suit_S.png ...（同一個可以有多張：rank_A_2.png）

    `bundled_dir`（通常是 defaults/parts）用來分辨哪些檔案是內建的
    —— **比內容不比檔名**，原因見 `is_bundled_copy()`。

    內建那批是從 512 寬的縮圖放大來的，筆畫比實機糊一圈，混在一起比對時糊掉的
    「7」有可能比清楚的「2」更像螢幕上那個 2。所以自己的樣板夠多之後就不用內建的；
    但**夠多**是關鍵 —— 只有 1 張自己的就丟掉內建 8 張，結果比混著用還糟
    （見 `MIN_OWN_TO_DROP_BUNDLED` 上面那張實測表）。
    """
    out: dict = {"rank": {}, "suit": {}, "pip": {}}
    if not os.path.isdir(parts_dir):
        return out

    fingerprints = bundled_fingerprints(bundled_dir)

    own: dict = {"rank": {}, "suit": {}, "pip": {}}
    fallback: dict = {"rank": {}, "suit": {}, "pip": {}}
    for fname in sorted(os.listdir(parts_dir)):
        parsed = parse_part_name(fname)
        if parsed is None:
            continue
        kind, key = parsed
        img = cv2.imread(os.path.join(parts_dir, fname), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        size = PART_SIZES[kind]
        if (img.shape[1], img.shape[0]) != size:
            img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
        cleaned = clean_part_mask((img > 127).astype(np.uint8) * 255, kind)
        # **先判斷夠不夠格，再置中。** 順序反了會出事：centre_mask 是平移，
        # 貼在畫布邊緣的圖案平移之後會被切掉一部分，外框變小、佔滿度掉下來，
        # 於是一張好樣板被判成「裁壞的小點」而安靜丟掉。
        # 實測使用者的樣板：先置中會誤殺 6 張（unusable_parts 只認出 6 張真的壞的，
        # load_part_templates 卻丟了 12 張）—— 又是一個「存了卻沒生效」。
        if not part_is_usable(cleaned):
            # **不刪檔**，只是不拿來比對。使用者的檔案一律不動，這是這個專案的鐵則；
            # 但也不能讓一個裁壞的小點把整組比對拖下水。
            # 想知道跳過了哪些，用 unusable_parts()。
            continue
        img = centre_mask(cleaned)
        bucket = fallback if is_bundled_copy(parts_dir, fname, fingerprints) else own
        bucket[kind].setdefault(key, []).append(img)

    for kind in out:
        keys = set(own[kind]) | set(fallback[kind])
        for key in keys:
            group = _group_for(kind, key)
            enough = all(len(own[kind].get(k, [])) >= MIN_OWN_TO_DROP_BUNDLED
                         for k in group)
            mine = own[kind].get(key, [])
            theirs = fallback[kind].get(key, [])
            pool = list(mine) if (enough and mine) else list(mine) + list(theirs)
            if pool:
                out[kind][key] = pool
    return out


def part_inventory(parts_dir: str, bundled_dir: Optional[str] = None) -> dict:
    """每個標籤的樣板統計，給 GUI 的「蒐集進度」用。

    回傳 `{kind: {key: {"own": n, "bundled": n, "junk": n, "in_use": n,
                        "dropping_bundled": bool}}}`

    為什麼要有這個：載入時「裁壞的安靜跳過」「內建的不用」都是對的行為，
    但使用者完全看不到。他實際的狀況是 suit_D 有 3 個檔案、其中 2 個是裁壞的小點，
    真正拿來比對的只有 1 張 —— 畫面上卻只顯示「已有自己的樣板 ✓」。
    難怪他覺得「多抓幾次好像都沒變化」。
    """
    fingerprints = bundled_fingerprints(bundled_dir)
    stats: dict = {"rank": {}, "suit": {}, "pip": {}}
    if not os.path.isdir(parts_dir):
        return stats
    for fname in sorted(os.listdir(parts_dir)):
        parsed = parse_part_name(fname)
        if parsed is None:
            continue
        kind, key = parsed
        row = stats[kind].setdefault(
            key, {"own": 0, "bundled": 0, "junk": 0, "in_use": 0,
                  "dropping_bundled": False})
        img = cv2.imread(os.path.join(parts_dir, fname), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        size = PART_SIZES[kind]
        if (img.shape[1], img.shape[0]) != size:
            img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
        cleaned = clean_part_mask((img > 127).astype(np.uint8) * 255, kind)
        if not part_is_usable(cleaned):
            row["junk"] += 1
            continue
        if is_bundled_copy(parts_dir, fname, fingerprints):
            row["bundled"] += 1
        else:
            row["own"] += 1

    loaded = load_part_templates(parts_dir, bundled_dir)
    for kind, rows in stats.items():
        for key, row in rows.items():
            row["in_use"] = len(loaded[kind].get(key, []))
            group = _group_for(kind, key)
            row["dropping_bundled"] = all(
                stats[kind].get(k, {}).get("own", 0) >= MIN_OWN_TO_DROP_BUNDLED
                for k in group)
    return stats


def unusable_parts(parts_dir: str) -> list[tuple[str, float]]:
    """列出 parts 資料夾裡「裁壞了、不會被拿來比對」的樣板檔。

    回傳 [(檔名, 佔滿度), ...]，由小到大。給 bot 啟動時與 check_setup 報告用 ——
    載入時只是安靜跳過，不講的話使用者永遠不知道自己存了幾個廢檔。
    """
    bad: list[tuple[str, float]] = []
    if not os.path.isdir(parts_dir):
        return bad
    for fname in sorted(os.listdir(parts_dir)):
        if parse_part_name(fname) is None:
            continue
        img = cv2.imread(os.path.join(parts_dir, fname), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        coverage = part_coverage(img)
        if coverage < MIN_PART_COVERAGE:
            bad.append((fname, round(coverage, 3)))
    return sorted(bad, key=lambda r: r[1])


# 同一個點數／花色最多留幾張「自己抓的」樣板。
#
# 比對是取所有樣板裡的最高分，所以多存只會更容易命中（代價只是多幾次 24x32 的
# 小圖比對，可以忽略）。原本是 8，但實測使用者的 rank_5 早就存滿 8 張、
# 再抓也存不進去 —— 而他正是覺得「多抓幾次好像都沒變化」的人。
MAX_OWN_PER_LABEL = 16


def next_part_path(parts_dir: str, bundled_dir: str, kind: str, key: str,
                   max_own: int = MAX_OWN_PER_LABEL):
    """決定下一個要寫入的樣板檔路徑，並回傳應該順手刪掉的內建檔。

    回傳 `(路徑, [要刪掉的內建檔絕對路徑])`；已經存夠自己的樣板時回傳 `(None, [])`。

    ## 兩個踩過的坑

    **坑一（2026-08-20）**：內建樣板每個花色剛好 8 張（suit_S_1~8…），而
    「同一個標籤最多留 8 張」的上限把內建的也算進去，於是一開始就「已經滿了」——
    按幾次「全部儲存」都不會存下任何花色，畫面也不報錯。
    所以上限**只算使用者自己抓的**。

    **坑二（2026-08-21，就是坑一的修法本身帶出來的）**：
    「哪些是內建的」原本只比檔名。內建檔被刪掉之後，編號 1~8 就空出來了，
    於是下一次儲存又寫進 `suit_D_1.png` —— 一個**檔名看起來像內建、內容是使用者
    自己抓的**檔案。它會同時被判成 stale（下次儲存刪掉它）與 fallback
    （比對時不用它）。淨效果：**每次儲存都把上一次的成果刪掉，永遠累積不起來。**

    修法有三層：
    1. 「是不是內建」改成比**內容**（`is_bundled_copy`），修好已經存在的檔案；
    2. 新檔的編號一律從「內建的最大編號 + 1」開始，讓檔名**不可能**再撞上內建的；
    3. **儲存時不再刪掉內建檔**（回傳的 stale 永遠是空的，見下）。

    ## 為什麼不再刪內建檔

    「存自己的就順手刪內建的」是為了修坑一而加的，當時上限會把內建的算進去。
    現在上限只算自己的，刪除已經沒有必要 —— 而且變成有害：
    `load_part_templates()` 要等自己的累積到 `MIN_OWN_TO_DROP_BUNDLED` 張才會
    不用內建的，在那之前**需要內建那批當墊背**。第一次儲存就把它們刪掉，
    等於把墊背抽掉，方塊只剩 1 張樣板去跟紅心比。

    想清掉內建的仍然可以，但要使用者明確按「清掉所有內建樣板」——
    存檔這條路上不再有任何刪檔動作，這也讓「多抓幾次」不可能再倒退。
    """
    prefix = f"{kind}_{key}_"
    fingerprints = bundled_fingerprints(bundled_dir)
    existing = [f for f in os.listdir(parts_dir)
                if f.lower().endswith(".png") and f.startswith(prefix)]
    own = [f for f in existing if not is_bundled_copy(parts_dir, f, fingerprints)]
    if len(own) >= max_own:
        return None, []

    # 從「內建的最大編號 + 1」起跳，新檔名不會再撞上內建的
    i = _first_free_index(parts_dir, prefix, _highest_bundled_index(fingerprints, prefix) + 1)
    # 第二個回傳值保留成「要順手刪掉的檔案」，但**永遠是空的**（見上面的說明）。
    return os.path.join(parts_dir, f"{prefix}{i}.png"), []


def _highest_bundled_index(fingerprints: dict, prefix: str) -> int:
    highest = 0
    for name in fingerprints:
        if not name.startswith(prefix):
            continue
        stem = os.path.splitext(name)[0][len(prefix):]
        if stem.isdigit():
            highest = max(highest, int(stem))
    return highest


def _first_free_index(parts_dir: str, prefix: str, start: int) -> int:
    i = max(1, start)
    while os.path.exists(os.path.join(parts_dir, f"{prefix}{i}.png")):
        i += 1
    return i


def part_sources(parts_dir: str, bundled_dir: str) -> dict:
    """每個標籤現在是「你自己的樣板」還是「還在用內建的」。

    回傳 {"rank": {"2": True, "3": False, ...}, ...}，True = 已有自己的樣板。
    """
    fingerprints = bundled_fingerprints(bundled_dir)
    out: dict = {"rank": {}, "suit": {}, "pip": {}}
    if not os.path.isdir(parts_dir):
        return out
    for fname in sorted(os.listdir(parts_dir)):
        parsed = parse_part_name(fname)
        if parsed is None:
            continue
        kind, key = parsed
        mine = not is_bundled_copy(parts_dir, fname, fingerprints)
        out[kind][key] = out[kind].get(key, False) or mine
    return out


def bundled_copies_present(parts_dir: str, bundled_dir: str) -> list:
    """`parts/` 裡「內容真的還是內建樣板」的檔名清單（排序過）。

    給「清掉所有內建樣板」用。以前是比檔名，於是那顆按鈕會把使用者自己抓的
    19 個樣板一起刪掉 —— 而按鈕上明明寫著「你自己抓的不會動」。
    """
    if not os.path.isdir(parts_dir):
        return []
    fingerprints = bundled_fingerprints(bundled_dir)
    return sorted(f for f in os.listdir(parts_dir)
                  if is_bundled_copy(parts_dir, f, fingerprints))


# 加上質心對位之後，正解分數中位數 0.97、最低 0.89，錯誤答案的領先幅度中位數 +0.30，
# 所以真正在把關的是「領先幅度」而不是絕對分數。絕對門檻只當作最後一道保險。
DEFAULT_MIN_SCORE = 0.72
DEFAULT_MIN_MARGIN = 0.05

# 花色的把關比點數寬鬆，理由是：
#  * 顏色已經先把候選縮到兩個（紅黑判斷實測 54/54 全對），只要二選一
#  * 花色只影響同花判斷，比大小完全用不到；認錯花色的代價遠小於整張牌認不出來
#    讓 bot 空等
# 中央大圖案又比角落那顆小花色可靠得多，所以門檻更低。
PIP_MIN_MARGIN = 0.015      # 卡面中央的大花色圖案（數字牌）
CORNER_SUIT_MIN_MARGIN = 0.015   # 角落那顆小花色（人像牌只能靠它）


def _align_variants(query: np.ndarray, radius: int = 1) -> list:
    """查詢圖的 ±radius 位移版本；比對時取最好的那個對位結果。

    樣板在載入時已經質心置中，這裡只需要補上殘餘的一格誤差。
    """
    centred = centre_mask(query)
    if radius <= 0:
        return [centred]
    return [_shift(centred, dx, dy)
            for dy in range(-radius, radius + 1)
            for dx in range(-radius, radius + 1)]


def _score_bank(query, bank: dict, align: int = 1) -> dict:
    variants = _align_variants(query, align)
    return {
        label: max(part_score(v, t) for t in imgs for v in variants)
        for label, imgs in bank.items()
    }


def _best_match(query, bank: dict, min_score: float, min_margin: float, align: int = 1,
                query2=None):
    """比對一個部位。`query2` 是同一張牌另一個角落（右下角轉正）的同部位小圖。

    整張牌會把點數與花色各印兩次：左上角一次、右下角旋轉 180 度再一次。
    只看一個角落時，模糊、反光、壓到邊界都可能讓分數剛好被別的字超車
    （實測就是 2 被 7 超車、6 被 A 超車、黑桃被梅花超車）。
    兩個角落是同一張牌的兩次獨立取樣，取平均之後雜訊互相抵銷 ——
    實測點數 47/54 → 52/54，而且原本認錯的那幾張全部翻正。
    """
    if not bank:
        return None
    scores = _score_bank(query, bank, align)
    if query2 is not None:
        other = _score_bank(query2, bank, align)
        # 先確認那個角落真的切到牌角。比大小畫面的牌會超出校準框，右下角切到的
        # 是牌桌背景，這種垃圾資料全部只有 0.5~0.6 分，正常牌角則是 0.8 以上，
        # 差距非常乾淨，用同一個門檻擋掉即可（擋掉就退回只看左上角）。
        if other and max(other.values()) >= min_score:
            scores = {k: (v + other.get(k, v)) / 2.0 for k, v in scores.items()}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_label, best = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else -1.0
    if best < min_score:
        return None
    if second >= 0 and (best - second) < min_margin:
        return None
    return best_label, best


def classify_parts(
    parts: dict,
    templates: dict,
    min_score: float = DEFAULT_MIN_SCORE,
    min_margin: float = DEFAULT_MIN_MARGIN,
) -> Optional[tuple[str, float]]:
    """把 extract_parts() 的結果對上樣板，回傳 ("10H", 分數) 或 None。"""
    if not parts or not templates:
        return None
    rank_bank = templates.get("rank") or {}
    suit_bank = templates.get("suit") or {}
    pip_bank = templates.get("pip") or {}
    if not rank_bank or not suit_bank:
        return None

    rank = _best_match(parts["rank"], rank_bank, min_score, min_margin,
                       query2=parts.get("rank2"))
    if rank is None:
        return None

    # 花色先用顏色縮到兩個候選（紅黑判斷實測 54/54 全對），剩下只要二選一
    candidates = RED_SUITS if parts["is_red"] else BLACK_SUITS
    suit = None

    # 數字牌優先用卡面中央的大圖案：角落那顆太小，黑桃/梅花常常只差 0.01
    if rank[0] in NUMBER_RANKS and parts.get("pip") is not None and pip_bank:
        bank = {k: v for k, v in pip_bank.items() if k in candidates}
        # 大圖案本來就分得開（實測領先幅度中位數 0.175），只要防完全平手即可
        suit = _best_match(parts["pip"], bank, min_score, PIP_MIN_MARGIN)

    if suit is None:
        # J/Q/K 沒有中央大圖案，只能靠角落那顆小花色 —— 這正是黑桃/梅花最容易
        # 打結的情況，所以左上 + 右下兩顆一起比。
        bank = {k: v for k, v in suit_bank.items() if k in candidates}
        suit = _best_match(parts["suit"], bank, min_score, CORNER_SUIT_MIN_MARGIN,
                           query2=parts.get("suit2"))
        if suit is None and bank:
            # 點數已經確定了，只有花色分不出黑桃/梅花（或紅心/方塊）時，
            # **不要整張牌報「認不出來」**。回報「認不出來」的代價是 bot 卡住
            # 空等（就是一直跳問號的那個狀況）；猜錯花色的代價只有可能少算一次同花，
            # 比大小完全用不到花色。所以顏色已經確定的前提下，直接取分數高的那個。
            suit = _best_match(parts["suit"], bank, 0.0, 0.0, query2=parts.get("suit2"))
    if suit is None:
        return None
    return rank[0] + suit[0], min(rank[1], suit[1])


def explain_parts(parts: dict, templates: dict, min_score: float = DEFAULT_MIN_SCORE,
                   min_margin: float = DEFAULT_MIN_MARGIN) -> str:
    """辨識失敗時，說明「差在哪裡」。給 log 用，讓使用者知道要補什麼樣板。"""
    if not parts:
        return "切不出左上角（卡面位置可能沒對準）"
    if not templates or not templates.get("rank") or not templates.get("suit"):
        return "沒有點數/花色樣板"

    def _top(query, bank, n=2, query2=None):
        if not bank:
            return []
        scores = _score_bank(query, bank, 1)
        if query2 is not None:
            other = _score_bank(query2, bank, 1)
            if other and max(other.values()) >= min_score:
                scores = {k: (v + other.get(k, v)) / 2.0 for k, v in scores.items()}
        return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:n]

    bits = []
    top = _top(parts["rank"], templates.get("rank") or {}, query2=parts.get("rank2"))
    if top:
        detail = "  ".join(f"{k}={v:.2f}" for k, v in top)
        why = ""
        if top[0][1] < min_score:
            why = f" ←分數未達 {min_score}"
        elif len(top) > 1 and top[0][1] - top[1][1] < min_margin:
            why = f" ←領先不足 {min_margin}"
        bits.append(f"點數 {detail}{why}")

    colour = "紅" if parts["is_red"] else "黑"
    candidates = RED_SUITS if parts["is_red"] else BLACK_SUITS
    # 只有數字牌才有中央大圖案；J/Q/K 是人像，中間讀到的是衣服，不能拿來當花色
    is_number = bool(top) and top[0][0] in NUMBER_RANKS
    used_pip = bool(is_number and parts.get("pip") is not None and (templates.get("pip") or {}))
    bank = {k: v for k, v in ((templates.get("pip") or {}) if used_pip
                              else (templates.get("suit") or {})).items()
            if k in candidates}
    top_suit = _top(parts["pip"] if used_pip else parts["suit"], bank,
                    query2=None if used_pip else parts.get("suit2"))
    if top_suit:
        detail = "  ".join(f"{k}={v:.2f}" for k, v in top_suit)
        source = "中央大圖案" if used_pip else "角落花色"
        bits.append(f"{source}({colour}) {detail}")
    if parts.get("rank2") is None:
        bits.append("（只讀得到左上角，右下角被蓋住）")
    return " | ".join(bits) if bits else "無法比對"


def recognize_by_corner(
    roi: np.ndarray,
    templates: dict,
    expected_w: int = 0,
    expected_h: int = 0,
    min_score: float = DEFAULT_MIN_SCORE,
    min_margin: float = DEFAULT_MIN_MARGIN,
    rect: Optional[tuple[int, int, int, int]] = None,
) -> Optional[tuple[str, float]]:
    """一步到位：卡面 ROI -> ("10H", 分數)。"""
    parts = extract_parts(roi, expected_w, expected_h, rect=rect)
    if parts is None:
        return None
    return classify_parts(parts, templates, min_score=min_score, min_margin=min_margin)


def missing_parts(templates: dict) -> tuple[list[str], list[str]]:
    """回傳 (還缺的點數, 還缺的花色)。"""
    have_rank = set((templates.get("rank") or {}).keys())
    have_suit = set((templates.get("suit") or {}).keys())
    return ([r for r in RANKS if r not in have_rank],
            [s for s in SUITS if s not in have_suit])
