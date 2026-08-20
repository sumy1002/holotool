"""離線檢查工具：拿現成的截圖跑一遍畫面標記比對，看每個標記各拿幾分。

用途：不用開遊戲，就能知道「哪個畫面認不出來、分數差多少、門檻該調到多少」。

用法：

    .venv\\Scripts\\python.exe check_markers.py debug_captures\\*.png
    .venv\\Scripts\\python.exe check_markers.py debug_captures --width 1937

參數：
    --width N   把截圖縮放成 N 像素寬再比對（預設用 config 的 calibration 寬度）。
                用來模擬「實機視窗是這個寬度時，分數會是多少」。

輸出的每一欄就是 detect_frame 會算出來的分數，右邊標 * 代表超過該標記的門檻。
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import cv2


import _bootstrap  # noqa: F401  讓這個子資料夾找得到專案根目錄的 src 套件

from src.config import load_config  # noqa: E402
from src.paths import resolve_data_path  # noqa: E402
from src.recognize import load_single_template  # noqa: E402
from src.state_machine import (  # noqa: E402
    DEFAULT_MARKER_PADS,
    DEFAULT_MARKER_THRESHOLDS,
    _expand_region,
    expected_marker_scale,
)
from src.recognize import marker_score  # noqa: E402

MARKERS = [
    ("table_marker", "table_marker_image", "牌桌"),
    ("draw_prompt", "draw_prompt_image", "選牌"),
    ("congrats_marker", "congrats_marker_image", "過關"),
    ("challenge_marker", "challenge_marker_image", "翻倍"),
    ("fail_marker", "fail_marker_image", "失敗"),
    ("poker_fail_marker", "poker_fail_marker_image", "湊牌失敗"),
]


def collect_images(patterns: list[str]) -> list[str]:
    files: list[str] = []
    for pattern in patterns:
        if os.path.isdir(pattern):
            files += sorted(glob.glob(os.path.join(pattern, "*.png")))
        else:
            files += sorted(glob.glob(pattern))
    return [f for f in files if os.path.isfile(f)]


def main() -> int:
    parser = argparse.ArgumentParser(description="離線檢查畫面標記比對分數")
    parser.add_argument("images", nargs="+", help="截圖檔或資料夾")
    parser.add_argument("--width", type=int, default=0, help="把截圖縮放成這個寬度再比對")
    args = parser.parse_args()

    cfg = load_config()
    regions = cfg["regions"]
    thresholds = dict(DEFAULT_MARKER_THRESHOLDS)
    thresholds.update(cfg.get("marker_thresholds", {}) or {})
    pads = dict(DEFAULT_MARKER_PADS)
    for key, value in (cfg.get("marker_pads", {}) or {}).items():
        if isinstance(value, (list, tuple)) and len(value) == 2:
            pads[key] = (float(value[0]), float(value[1]))

    templates = {}
    for region_key, cfg_key, _label in MARKERS:
        rel = cfg.get("templates", {}).get(cfg_key, "")
        templates[region_key] = load_single_template(resolve_data_path(rel)) if rel else None

    target_w = args.width or int(cfg.get("calibration", {}).get("client_width") or 0)
    files = collect_images(args.images)
    if not files:
        print("找不到任何截圖檔")
        return 1

    ref_w = cfg.get("templates", {}).get("capture_client_width") or 1024
    print(f"樣板擷取寬度 = {ref_w}px")
    print(f"門檻：" + "  ".join(f"{lab}={thresholds[k]:.2f}" for k, _c, lab in MARKERS))
    header = f"{'截圖':<26}{'倍率':>6}" + "".join(f"{lab:>10}" for _k, _c, lab in MARKERS)
    print(header)
    print("-" * len(header))

    for path in files:
        img = cv2.imread(path)
        if img is None:
            continue
        if target_w and img.shape[1] != target_w:
            h = int(round(img.shape[0] * target_w / img.shape[1]))
            img = cv2.resize(img, (target_w, h))
        ih, iw = img.shape[:2]
        scale = expected_marker_scale(cfg, iw)

        cells = []
        for region_key, _cfg_key, _label in MARKERS:
            tmpl = templates.get(region_key)
            region = regions.get(region_key, {})
            if tmpl is None or not region or region.get("w", 0) <= 0:
                cells.append(f"{'-':>10}")
                continue
            pad = pads.get(region_key, (0.25, 0.5))
            r = _expand_region(region, pad_x=pad[0], pad_y=pad[1])
            x, y = int(r["x"] * iw), int(r["y"] * ih)
            w, h = int(r["w"] * iw), int(r["h"] * ih)
            value = marker_score(img[y:y + h, x:x + w], tmpl, expected_scale=scale)
            flag = "*" if value >= thresholds.get(region_key, 0.80) else " "
            cells.append(f"{value:9.2f}{flag}")
        name = os.path.basename(path)
        print(f"{name[:26]:<26}{scale:6.2f}" + "".join(cells))

    print("\n* = 超過該標記的門檻（會被判定成這個畫面）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
