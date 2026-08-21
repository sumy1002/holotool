"""把原生解析度的截圖裁成「畫面標記樣板」，並可升級成內建預設。

## 這個工具在解什麼問題

內建的畫面標記樣板 (`app/defaults/ui/*.png`) 是從 **1024 寬**的縮圖裁出來的。
標記是點陣圖，比對前必須縮放到目前視窗的大小 —— 而大部分人的視窗都比 1024 寬，
所以實際上一直在**放大**內建樣板。放大會糊，實測使用者 1365 寬的視窗下
選牌 37% / 過關 21% / 翻倍 23%，門檻 0.80 一個都過不了。

縮小很安全、放大會糊。所以內建樣板應該用「越大越好」的原生截圖來裁。

## 怎麼取得原生截圖

**不要**用截圖工具手動裁 —— 很容易連視窗邊框／標題列一起裁進去，或被系統顯示
縮放動過，那樣座標與尺寸都會對不上。改用 GUI 主控台的
**「存一張目前畫面（PNG）」**，它直接抓用戶端區域，檔名帶著解析度：

    debug_captures/shot_1365x576_20260821-201530.png

每個標記各自只出現在自己的畫面上（過關標記只在過關畫面），所以要一個畫面存一張。

## 用法

    # 看看某張截圖裁出來長什麼樣（不寫檔）
    python app/tools/promote_ui_templates.py --dry-run \\
        --shot table_marker=debug_captures/shot_1365x576_a.png

    # 寫進 card_templates/（自己這台立刻生效）
    python app/tools/promote_ui_templates.py \\
        --shot table_marker=...png --shot congrats_marker=...png

    # 連內建預設一起升級（要發給別人時才需要）
    python app/tools/promote_ui_templates.py --promote --shot ...

`--promote` 會同時改寫 `src/defaults_layout.py` 的 `BUNDLED_MARKER_WIDTH/HEIGHT`
—— 那兩個常數就是「內建樣板是在多大的視窗下裁的」，不改的話比對會用錯倍率，
換了樣板反而更慘。

## 一定不會做的事

* 不動 `SCREENSHOT_CLIENT_WIDTH/HEIGHT`（那是**校準座標**的參考尺寸，是另一件事）。
* 不動任何校準數值。
* 不刪使用者的檔案；覆蓋前一定先留 `.bak`。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
if APP not in sys.path:
    sys.path.insert(0, APP)

from src.config import load_config  # noqa: E402
from src.defaults_layout import UI_MARKER_FILES  # noqa: E402
from src.paths import default_ui_dir, project_root, template_dir  # noqa: E402

# "regions.table_marker" -> "table_marker"
MARKER_KEYS = {path.split(".")[-1]: fname for path, fname in UI_MARKER_FILES.items()}

LAYOUT_PATH = os.path.join(APP, "src", "defaults_layout.py")


def cut(image, region: dict) -> "cv2.typing.MatLike":
    """依比例座標裁出一塊。座標是 0~1，所以任何解析度的截圖都適用。"""
    h, w = image.shape[:2]
    x = max(0, min(w - 1, round(float(region["x"]) * w)))
    y = max(0, min(h - 1, round(float(region["y"]) * h)))
    right = max(x + 1, min(w, round((float(region["x"]) + float(region["w"])) * w)))
    bottom = max(y + 1, min(h, round((float(region["y"]) + float(region["h"])) * h)))
    return image[y:bottom, x:right]


def _backup(path: str) -> None:
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")


def bump_bundled_size(width: int, height: int, layout_path: str = LAYOUT_PATH) -> bool:
    """改寫 BUNDLED_MARKER_WIDTH/HEIGHT。回傳有沒有真的改到。

    只動**行首**那兩個定義，不碰說明文字裡提到的數字（`SCREENSHOT_CLIENT_*`
    的 bump_version.py 踩過這個坑，所以這裡一開始就用 `^` 綁行首）。
    """
    with open(layout_path, encoding="utf-8") as f:
        text = f.read()
    new = re.sub(r"^BUNDLED_MARKER_WIDTH = \d+", f"BUNDLED_MARKER_WIDTH = {int(width)}",
                 text, count=1, flags=re.M)
    new = re.sub(r"^BUNDLED_MARKER_HEIGHT = \d+", f"BUNDLED_MARKER_HEIGHT = {int(height)}",
                 new, count=1, flags=re.M)
    if new == text:
        return False
    _backup(layout_path)
    with open(layout_path, "w", encoding="utf-8") as f:
        f.write(new)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--shot", action="append", default=[], metavar="標記=截圖路徑",
                        help="例如 table_marker=debug_captures/shot_1365x576_a.png。"
                             f"可用的標記：{'、'.join(sorted(MARKER_KEYS))}")
    parser.add_argument("--promote", action="store_true",
                        help="同時寫進 defaults/ui/ 並更新 BUNDLED_MARKER_WIDTH/HEIGHT")
    parser.add_argument("--dry-run", action="store_true", help="只印出來，不寫任何檔案")
    args = parser.parse_args()

    if not args.shot:
        parser.print_help()
        return 2

    cfg = load_config()
    regions = cfg.get("regions") or {}

    jobs = []
    sizes = set()
    for spec in args.shot:
        if "=" not in spec:
            print(f"[錯誤] --shot 要寫成 標記=檔案路徑，收到 {spec!r}")
            return 2
        key, path = spec.split("=", 1)
        key = key.strip()
        if key not in MARKER_KEYS:
            print(f"[錯誤] 不認得的標記 {key!r}。可用：{'、'.join(sorted(MARKER_KEYS))}")
            return 2
        if not os.path.isabs(path):
            path = os.path.join(project_root(), path)
        image = cv2.imread(path)
        if image is None:
            print(f"[錯誤] 讀不到截圖 {path}")
            return 1
        region = regions.get(key) or {}
        if float(region.get("w", 0)) <= 0:
            print(f"[錯誤] {key} 還沒有校準座標，沒辦法裁。先在「校準」分頁框選一次。")
            return 1
        piece = cut(image, region)
        h, w = image.shape[:2]
        sizes.add((w, h))
        jobs.append((key, MARKER_KEYS[key], piece, (w, h)))

    if len(sizes) > 1:
        print(f"[錯誤] 這些截圖的解析度不一致：{sorted(sizes)}。")
        print("       內建樣板只能記錄一個來源解析度，混著用會讓比對倍率算錯。")
        print("       請在同一個視窗大小下重新各存一張。")
        return 1

    (shot_w, shot_h) = next(iter(sizes))
    print(f"截圖解析度 {shot_w}x{shot_h}")
    for key, fname, piece, _ in jobs:
        ph, pw = piece.shape[:2]
        print(f"  {key:20s} → {fname:22s} {pw}x{ph}")

    if args.dry_run:
        print("（--dry-run，沒有寫任何檔案）")
        return 0

    written = 0
    for _key, fname, piece, _ in jobs:
        dest = os.path.join(template_dir(), fname)
        _backup(dest)
        if cv2.imwrite(dest, piece):
            written += 1
    print(f"已寫入 card_templates/ {written} 個檔")

    from src.config import save_config, set_template_capture_size
    set_template_capture_size(cfg, shot_w, shot_h)
    save_config(cfg)
    print(f"已把 capture_client_width/height 記成 {shot_w}x{shot_h}")

    if args.promote:
        promoted = 0
        for _key, fname, piece, _ in jobs:
            dest = os.path.join(default_ui_dir(), fname)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            _backup(dest)
            if cv2.imwrite(dest, piece):
                promoted += 1
        print(f"已升級內建預設 defaults/ui/ {promoted} 個檔")
        if bump_bundled_size(shot_w, shot_h):
            print(f"已把 BUNDLED_MARKER_WIDTH/HEIGHT 改成 {shot_w}x{shot_h}")
        else:
            print("[警告] 沒有改到 BUNDLED_MARKER_WIDTH/HEIGHT，請自己確認 "
                  "src/defaults_layout.py")
        missing = sorted(set(MARKER_KEYS) - {k for k, _, _, _ in jobs})
        if missing:
            print(f"[提醒] 這幾個標記這次沒有換，內建的還是舊的糊圖：{'、'.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
