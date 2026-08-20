"""卡牌樣板擷取工具。

用途：在遊戲中實際看到某張牌時，用這個工具把畫面上對應區域截圖存成樣板，
存放在 card_templates/ 資料夾，檔名格式為「點數+花色.png」，
例如：10H.png（紅心10）、AS.png（黑桃A）、QD.png（方塊Q）。

點數請用: 2 3 4 5 6 7 8 9 10 J Q K A
花色請用: S(黑桃) H(紅心) D(方塊) C(梅花)
鬼牌請輸入: JK

小技巧：
- 遊戲視窗只要「可見」即可（不需要切到最前景），你可以把遊戲和這個終端機視窗並排，
  一邊看牌一邊在終端機輸入指令，不需要每次都切換視窗焦點。
- 同一張牌可以多擷取幾次存成不同樣板（工具會自動用 _1 _2 編號），
  比對時只要符合其中一張樣板的相似度就算辨識成功，能提升穩定度。
"""
from __future__ import annotations

import os
import sys

import cv2

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # 避免終端機編碼不同造成中文亂碼

import _bootstrap  # noqa: F401  讓這個子資料夾找得到專案根目錄的 src 套件

from src.capture import GameCapture
from src.config import load_config
from src.handeval import Card
from src.paths import template_dir

TEMPLATE_DIR = template_dir()


def next_available_path(label: str) -> str:
    base = os.path.join(TEMPLATE_DIR, f"{label}.png")
    if not os.path.exists(base):
        return base
    i = 1
    while True:
        candidate = os.path.join(TEMPLATE_DIR, f"{label}_{i}.png")
        if not os.path.exists(candidate):
            return candidate
        i += 1


def save_roi(roi, label: str) -> str:
    os.makedirs(TEMPLATE_DIR, exist_ok=True)
    path = next_available_path(label)
    cv2.imwrite(path, roi)
    return path


def main():
    cfg = load_config()
    if not cfg.get("window_title_substring"):
        print("尚未設定遊戲視窗，請先執行 calibrate.py 完成校準。")
        return

    capture = GameCapture(cfg["window_title_substring"])
    if not capture.locate():
        print(f"找不到標題包含「{cfg['window_title_substring']}」的視窗，請先開啟遊戲。")
        return

    slots = cfg["regions"]["card_slots"]
    highlow = cfg["regions"]["highlow_card"]

    print("=== 卡牌樣板擷取工具 ===")
    print("選項: 1-5 = 擷取對應手牌位置, 6 = 擷取比大小亮牌位置, q = 離開\n")

    while True:
        choice = input("請確認遊戲畫面後輸入選項 (1-6 / q): ").strip().lower()
        if choice == "q":
            break
        if choice not in {"1", "2", "3", "4", "5", "6"}:
            print("請輸入 1-6 或 q")
            continue

        if not capture.is_window_valid():
            if not capture.locate():
                print("遊戲視窗不見了，請確認遊戲仍在執行。")
                continue

        if choice == "6":
            region = highlow
        else:
            region = slots[int(choice) - 1]

        if region["w"] <= 0 or region["h"] <= 0:
            print("這個位置尚未校準，請先執行 calibrate.py。")
            continue

        roi = capture.grab_region(region)
        label = input("請輸入這張牌代號（例如 10H, AS, QD；直接按 Enter 取消）: ").strip().upper()
        if not label:
            print("已取消。")
            continue
        try:
            Card.from_label(label)
        except ValueError as e:
            print(f"格式錯誤: {e}，請用「點數+花色」格式，例如 10H, AS, QD")
            continue

        path = save_roi(roi, label)
        print(f"已儲存: {path}\n")

    print("結束擷取工具。")


if __name__ == "__main__":
    main()
