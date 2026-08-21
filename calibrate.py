"""互動式校準工具（命令列版）。

一般建議直接用圖形介面：
    .venv\\Scripts\\python.exe gui.py

若想用命令列逐步校準才執行本檔：
    .venv\\Scripts\\python.exe calibrate.py

執行前請先切換到遊戲畫面，並停留在「牌桌畫面」（例如已經進入 High & Low 桌子、
可以看到開始/下注按鈕的那個畫面）。
"""
from __future__ import annotations

import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # 避免終端機編碼不同造成中文亂碼

from src import overlay
from src import window as win_mod
from src.capture import GameCapture
from src.config import CONFIG_VERSION, load_config, save_config
from src.geometry import pixels_point_to_ratio, pixels_region_to_ratio
from src.paths import template_dir

_game_hwnd: int | None = None


def _focus_game() -> None:
    """框選前先把遊戲視窗叫到前面，否則半透明遮罩底下看到的會是終端機而不是遊戲。"""
    if _game_hwnd is not None:
        win_mod.bring_to_foreground(_game_hwnd)
        time.sleep(0.35)


class _Aborted(Exception):
    """使用者在遮罩上按 Q 或滑鼠右鍵，要求中止整個校準流程。"""


def select_region(instruction: str) -> dict | None:
    _focus_game()
    result = overlay.select_region(instruction)
    if result.status == "abort":
        raise _Aborted
    return result.value


def select_point(instruction: str) -> dict | None:
    _focus_game()
    result = overlay.select_point(instruction)
    if result.status == "abort":
        raise _Aborted
    return result.value


def to_relative(abs_point: dict, rect: "win_mod.WindowRect") -> dict:
    """把螢幕絕對座標點換算成「相對於視窗用戶端的比例值 (0~1)」。"""
    return pixels_point_to_ratio(
        abs_point["x"] - rect.left,
        abs_point["y"] - rect.top,
        rect.width,
        rect.height,
    )


def region_to_relative(abs_region: dict, rect: "win_mod.WindowRect") -> dict:
    """把螢幕絕對區域換算成「相對於視窗用戶端的比例值 (0~1)」。"""
    return pixels_region_to_ratio(
        abs_region["x"] - rect.left,
        abs_region["y"] - rect.top,
        abs_region["w"],
        abs_region["h"],
        rect.width,
        rect.height,
    )


def fmt(coords: dict) -> str:
    """把比例座標印成好讀的百分比格式。"""
    return "{" + ", ".join(f"{k}={v:.4f}" for k, v in coords.items()) + "}"


def main():
    print("=== HoloTool 校準精靈 ===")
    print("目前偵測到以下視窗（僅列出有標題的）：")
    for hwnd, title in win_mod.list_visible_windows():
        print(f"  - {title}")
    print()
    substring = input("請輸入遊戲視窗標題的『部分文字』（會用來自動尋找視窗）: ").strip()
    if not substring:
        print("未輸入標題，結束。")
        sys.exit(1)

    hwnd = win_mod.find_window_by_title(substring)
    if hwnd is None:
        print(f"找不到標題包含「{substring}」的視窗，請確認遊戲已開啟後再重新執行。")
        sys.exit(1)

    global _game_hwnd
    _game_hwnd = hwnd
    win_mod.bring_to_foreground(hwnd)
    time.sleep(0.5)
    rect = win_mod.get_client_rect_on_screen(hwnd)
    print(f"已定位視窗，用戶端區域大小: {rect.width} x {rect.height}")

    cfg = load_config()
    cfg["window_title_substring"] = substring
    cfg["config_version"] = CONFIG_VERSION
    cfg["calibration"] = {"client_width": rect.width, "client_height": rect.height}
    save_config(cfg)

    print("座標會以「比例」方式儲存，所以之後縮放視窗不需要重新校準（但長寬比要維持一致）。")
    print("\n接下來會請你框選/點擊畫面上的各個位置，每次操作後視窗會自動關閉並提示下一步。")
    input("請確保目前畫面停留在『牌桌畫面』，準備好後按 Enter 開始...")

    steps_region = [
        ("table_marker", "1) 請框選一塊「只有在牌桌畫面才會出現」的獨特小區域\n（例如場景名稱文字、專屬圖示，離開牌桌後這裡就會消失或變成別的東西）"),
    ]
    for key, instruction in steps_region:
        region = select_region(instruction)
        if region is None:
            print(f"已跳過 {key}")
            continue
        cfg["regions"][key] = region_to_relative(region, rect)
        print(f"已記錄 {key}: {fmt(cfg['regions'][key])}")
        save_config(cfg)

    print("\n2) 接下來請依序框選『五張手牌』的區域，從左到右第1張到第5張")
    for i in range(5):
        region = select_region(f"請框選第 {i + 1} 張手牌的區域")
        if region is None:
            print(f"已跳過第 {i+1} 張手牌")
            continue
        cfg["regions"]["card_slots"][i] = region_to_relative(region, rect)
        print(f"第 {i+1} 張手牌區域: {fmt(cfg['regions']['card_slots'][i])}")
        save_config(cfg)

    region = select_region("3) 請框選『比大小』環節中，目前亮出的那張牌的區域")
    if region is not None:
        cfg["regions"]["highlow_card"] = region_to_relative(region, rect)
        save_config(cfg)

    print("\n接下來請點擊各個按鈕的位置：")
    point_steps = [
        ("start_round", "請點擊『開始遊戲/下注』按鈕"),
        ("draw_confirm", "請點擊『確認換牌/抽牌』按鈕"),
        ("high_button", "請點擊『大』按鈕"),
        ("low_button", "請點擊『小』按鈕"),
        ("cashout_button", "請點擊『收集/兌現』按鈕（若無此按鈕可按 Esc 跳過）"),
    ]
    for key, instruction in point_steps:
        pt = select_point(instruction)
        if pt is None:
            print(f"已跳過 {key}")
            continue
        cfg["points"][key] = to_relative(pt, rect)
        print(f"已記錄 {key}: {fmt(cfg['points'][key])}")
        save_config(cfg)

    print("\n最後，請依序點擊『可切換保留/丟棄』的 5 個手牌位置")
    print("（多數遊戲是直接點卡面本身即可切換，若不確定可直接點在剛剛框選的卡牌區域中心）")
    for i in range(5):
        pt = select_point(f"請點擊第 {i + 1} 張手牌『保留/丟棄』的切換位置")
        if pt is None:
            slot = cfg["regions"]["card_slots"][i]
            fallback = {"x": slot["x"] + slot["w"] / 2, "y": slot["y"] + slot["h"] / 2}
            cfg["points"]["hold_toggles"][i] = fallback
            print(f"已跳過，改用手牌區域中心作為預設值: {fmt(fallback)}")
        else:
            cfg["points"]["hold_toggles"][i] = to_relative(pt, rect)
            print(f"已記錄第 {i+1} 張保留/丟棄座標: {fmt(cfg['points']['hold_toggles'][i])}")
        save_config(cfg)

    save_config(cfg)

    # 擷取牌桌標記樣板圖片
    marker_region = cfg["regions"].get("table_marker")
    if marker_region and marker_region.get("w", 0) > 0:
        capture = GameCapture(substring)
        capture.locate()
        roi = capture.grab_region(marker_region)
        import cv2
        import os
        marker_dir = template_dir()
        os.makedirs(marker_dir, exist_ok=True)
        cv2.imwrite(os.path.join(marker_dir, "table_marker.png"), roi)
        print("已儲存牌桌標記樣板圖片到 card_templates/table_marker.png")

    print("\n=== 校準完成！設定已存到 config/config.json ===")
    print(f"校準時的視窗尺寸: {rect.width} x {rect.height}（座標已存成比例值）")
    print("之後可以自由縮放視窗，但請維持同樣的長寬比，否則遊戲 UI 排版會跑掉。")
    print("接下來請執行 collect_templates.py 來蒐集卡牌樣板圖片。")


if __name__ == "__main__":
    try:
        main()
    except _Aborted:
        print("\n已中止校準流程，目前為止完成的項目都已存檔。")
