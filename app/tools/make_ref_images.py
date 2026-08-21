"""產生「校準範例圖」defaults/ref/*.jpg。

校準框選時，右上角會顯示一張半透明的範例圖，上面畫著「這一項應該框到哪裡」。
那些範例圖就是這個腳本產生的：拿 debug_captures/ 裡的實機截圖，把當初除錯時
畫在上面的彩色框線抹掉，再縮成 640x274 存成 JPEG。

## 為什麼要抹掉框線

debug_captures 裡沒有一張是乾淨的截圖 —— 每一張都被某個除錯腳本畫上了紅框或
綠框（還有 "26x36" 這種尺寸標註）。直接拿來當範例圖會讓玩家看到兩層框，
根本分不出哪個才是要框的範圍。

## 抹除策略（踩過的坑）

第一版對紅、綠、黃三種顏色一起做遮罩，結果**把牌面的紅心與方塊、還有金色的
「200」「翻倍機會！」全部一起抹掉了** —— 那些是遊戲本來就有的顏色。

所以現在是：

* 用**綠框**版本的截圖為主。遊戲畫面裡完全沒有綠色，整片綠都可以安心抹掉
  （連 "26x36" 這種綠色小字一起消失）。
* 只有「投注並開始」畫面沒有綠框版本，只能用紅框版本。那個畫面的五張牌是
  蓋著的（沒有紅心方塊），所以紅色遮罩安全；但仍然只抹「細長的線」，
  實心色塊一律保留，當作雙重保險。

執行方式（在 app\\ 底下）：

    .venv\\Scripts\\python.exe tools\\make_ref_images.py
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.calibguide import REF_SIZE  # noqa: E402
from src.paths import project_root  # noqa: E402

# 每張範例圖用哪張實機截圖、上面的框線是什麼顏色
SOURCES: dict[str, tuple[str, str]] = {
    "start": ("crop_btn_start.png", "red"),
    "draw": ("cards_draw.png", "green"),
    "congrats": ("purple_congrats.png", "green"),
    "challenge": ("purple_challenge.png", "green"),
    "highlow": ("cards_highlow.png", "green"),
    "fail": ("purple_fail.png", "green"),
}

JPEG_QUALITY = 85


def colour_mask(bgr: np.ndarray, which: str) -> np.ndarray:
    """找出除錯框線的顏色。條件刻意訂得嚴，寧可漏抹也不要傷到遊戲畫面。"""
    b, g, r = (bgr[:, :, i].astype(int) for i in range(3))
    if which == "red":
        mask = (r > 190) & (g < 70) & (b < 70)
    else:
        mask = (g > 140) & (r < 150) & (b < 150) & (g - r > 40) & (g - b > 40)
    return mask.astype(np.uint8) * 255


def thin_lines_only(mask: np.ndarray) -> np.ndarray:
    """只留下細長的框線；實心色塊（紅心、方塊、粗體字）整塊排除。

    做法是先把「侵蝕之後還活著」的部分當成實心區塊扣掉，剩下的細結構再用
    長條 kernel 做開運算，確認它真的是一條線而不是雜點。
    """
    solid = cv2.dilate(cv2.erode(mask, np.ones((5, 5), np.uint8)), np.ones((9, 9), np.uint8))
    thin = cv2.bitwise_and(mask, cv2.bitwise_not(solid))
    horizontal = cv2.morphologyEx(thin, cv2.MORPH_OPEN, np.ones((1, 21), np.uint8))
    vertical = cv2.morphologyEx(thin, cv2.MORPH_OPEN, np.ones((21, 1), np.uint8))
    lines = cv2.dilate(cv2.bitwise_or(horizontal, vertical), np.ones((3, 3), np.uint8))
    return cv2.bitwise_and(lines, mask)


def build_one(src_path: str, which: str) -> np.ndarray:
    bgr = cv2.imread(src_path)
    if bgr is None:
        raise FileNotFoundError(src_path)
    mask = colour_mask(bgr, which)
    if which == "red":
        mask = thin_lines_only(mask)
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
    cleaned = cv2.inpaint(bgr, mask, 5, cv2.INPAINT_TELEA)
    return cv2.resize(cleaned, REF_SIZE, interpolation=cv2.INTER_AREA)


def main() -> int:
    root = project_root()
    src_dir = os.path.join(root, "debug_captures")
    out_dir = os.path.join(root, "defaults", "ref")
    os.makedirs(out_dir, exist_ok=True)

    missing = []
    for key, (fname, which) in SOURCES.items():
        src = os.path.join(src_dir, fname)
        if not os.path.exists(src):
            missing.append(fname)
            continue
        out = os.path.join(out_dir, f"{key}.jpg")
        cv2.imwrite(out, build_one(src, which), [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        print(f"{key:10s} <- {fname:24s} ({which:5s})  {os.path.getsize(out):,} bytes")

    if missing:
        print("\n[警告] 找不到這些來源截圖，對應的範例圖沒有重新產生："
              + "、".join(missing))
        print("        （已經產生好的 defaults/ref/*.jpg 不受影響，可以直接用）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
