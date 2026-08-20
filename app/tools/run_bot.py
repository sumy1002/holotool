"""主程式進入點。

執行方式：
    .venv\\Scripts\\python.exe run_bot.py           # 正式模式，會實際控制滑鼠點擊
    .venv\\Scripts\\python.exe run_bot.py --dry-run  # 除錯模式，只會印出判斷結果，不會點擊

使用方式：
    1. 自己先手動操作進入 High & Low 的牌桌畫面
    2. 執行本程式（會顯示「Bot 已就緒」）
    3. 切回遊戲畫面，按下 F9 開始自動遊玩
    4. 達到每日次數上限、遊戲把你踢出牌桌畫面時，程式會自動偵測並停止
    5. 也可以隨時按 F9 手動停止，或按 F10 緊急停止（滑鼠移到螢幕角落也會觸發 pyautogui 防呆停止）
"""
from __future__ import annotations

import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # 避免終端機編碼不同造成中文亂碼

import _bootstrap  # noqa: F401  讓這個子資料夾找得到專案根目錄的 src 套件

from src.bot import build_and_run
from src.config import load_config
from src.paths import prepare_runtime


def main():
    prepare_runtime()
    parser = argparse.ArgumentParser(description="Hololive Dreams High & Low 自動化工具")
    parser.add_argument("--dry-run", action="store_true", help="除錯模式：只印出辨識與決策結果，不會實際點擊滑鼠")
    args = parser.parse_args()

    cfg = load_config()
    if not cfg.get("window_title_substring"):
        print("尚未完成校準，請先執行: python calibrate.py")
        return

    build_and_run(cfg, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
