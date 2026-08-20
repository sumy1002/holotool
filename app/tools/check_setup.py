"""設定狀態檢查工具：一眼看出校準到哪一步、還缺什麼。

執行方式：
    .venv\\Scripts\\python.exe check_setup.py
"""
from __future__ import annotations

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import _bootstrap  # noqa: F401  讓這個子資料夾找得到專案根目錄的 src 套件

from src import window as win_mod
from src.config import CONFIG_PATH, load_config
from src import profiles as profiles_mod
from src.geometry import aspect_ratio_delta, scale_factor
from src.handeval import full_deck
from src.paths import template_dir

TEMPLATE_DIR = template_dir()

OK = "[ OK ]"
NG = "[ 缺 ]"


def is_region_set(region: dict) -> bool:
    return bool(region) and region.get("w", 0) > 0 and region.get("h", 0) > 0


def is_point_set(point: dict) -> bool:
    return bool(point) and (point.get("x", 0) > 0 or point.get("y", 0) > 0)


def collected_card_labels() -> set[str]:
    """從 card_templates 資料夾找出已蒐集到哪些牌（檔名 10H.png、10H_1.png 都算 10H）。"""
    found = set()
    if not os.path.isdir(TEMPLATE_DIR):
        return found
    valid = {c.label for c in full_deck()}
    for fname in os.listdir(TEMPLATE_DIR):
        if not fname.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
            continue
        label = os.path.splitext(fname)[0].split("_")[0].upper()
        if label in valid:
            found.add(label)
    return found


def main() -> None:
    print("=" * 58)
    print(" HoloTool 設定狀態檢查")
    print("=" * 58)

    todo: list[str] = []

    # 1. 設定檔
    if not os.path.exists(CONFIG_PATH):
        print(f"{NG} 設定檔不存在：{CONFIG_PATH}")
        print("\n>>> 下一步：執行圖形介面開始設定")
        print("    .venv\\Scripts\\python.exe gui.py")
        return
    cfg = load_config()
    print(f"{OK} 設定檔存在：{CONFIG_PATH}")

    # 2. 遊戲視窗
    title = cfg.get("window_title_substring", "")
    if not title:
        print(f"{NG} 遊戲視窗：尚未設定（window_title_substring 是空的）")
        todo.append("在 gui.py 的「主控台」分頁選擇遊戲視窗")
    else:
        hwnd = win_mod.find_window_by_title(title)
        if hwnd is None:
            print(f"{NG} 遊戲視窗：設定為「{title}」，但目前找不到這個視窗（遊戲沒開？）")
            todo.append("開啟遊戲後再重新檢查")
        else:
            rect = win_mod.get_client_rect_on_screen(hwnd)
            print(f"{OK} 遊戲視窗：找到「{title}」，目前尺寸 {rect.width}x{rect.height}")

            tol = cfg.get("aspect_ratio_tolerance", 0.02)
            aspect_label = profiles_mod.label_for(rect.width, rect.height)
            match, delta_profile = profiles_mod.find_match(cfg, rect.width, rect.height, tol)
            if match is None:
                print(f"{NG} 長寬比 {aspect_label}：一組校準都沒有")
                todo.append("到「校準」分頁依序校準全部項目")
            elif delta_profile > tol:
                print(f"{NG} 長寬比 {aspect_label}：沒有這個比例的校準，"
                      f"會借用「{match.get('label')}」（差 {delta_profile:.1%}），位置會歪")
                todo.append(f"在 {aspect_label} 下按「另存為目前比例的校準」後重新框選")
            elif match.get("seeded_from"):
                print(f"{NG} 長寬比 {aspect_label}：這組是從「{match['seeded_from']}」"
                      "複製來的，還沒真正校準過")
                todo.append(f"在 {aspect_label} 下重新框選一次")
            else:
                print(f"{OK} 長寬比 {aspect_label}：使用「{match.get('label')}」這一組校準")

            ref = cfg.get("calibration", {})
            ref_w, ref_h = ref.get("client_width", 0), ref.get("client_height", 0)
            if ref_w > 0 and ref_h > 0:
                delta = aspect_ratio_delta(rect.width, rect.height, ref_w, ref_h)
                scale = scale_factor(rect.width, ref_w)
                print(f"       ↳ 這組量於 {ref_w}x{ref_h}，目前縮放 {scale:.2f} 倍，"
                      f"長寬比差異 {delta:.1%}")
                if scale < 0.6:
                    print(f"       ↳ 提醒：視窗偏小，卡牌辨識率可能下降，建議放大一點")

    # 3. 各長寬比的校準組
    lines = profiles_mod.describe(cfg)
    if lines:
        print(f"{OK} 已存的校準組（{len(lines)} 組）：")
        for line in lines:
            print(f"       • {line}")
    else:
        print(f"{NG} 校準組：尚未建立任何一組")

    # 4. 區域
    regions = cfg.get("regions", {})
    print(f"{OK if is_region_set(regions.get('table_marker', {})) else NG} 牌桌標記區域")
    print(f"{OK if is_region_set(regions.get('draw_prompt', {})) else NG} 選牌提示文字")
    slots = regions.get("card_slots", [])
    done_slots = sum(1 for s in slots if is_region_set(s))
    print(f"{OK if done_slots == 5 else NG} 五張手牌區域：{done_slots}/5 已校準")
    print(f"{OK if is_region_set(regions.get('highlow_card', {})) else NG} 比大小亮牌區域")
    print(f"{OK if is_region_set(regions.get('congrats_marker', {})) else NG} 過關畫面標記")
    print(f"{OK if is_region_set(regions.get('challenge_marker', {})) else NG} 翻倍對話框標記")
    print(f"{OK if is_region_set(regions.get('fail_marker', {})) else NG} 比大小失敗標記")
    print(f"{OK if is_region_set(regions.get('poker_fail_marker', {})) else NG} 湊牌失敗標記")
    max_win_tmpl = os.path.join(TEMPLATE_DIR, "ui_max_win.png")
    if is_region_set(regions.get("max_win_marker", {})) and os.path.exists(max_win_tmpl):
        print(f"{OK} 已達最高獲得金額標記（每日上限偵測）")
    else:
        print(f"{NG} 已達最高獲得金額標記（每日上限偵測）：還沒校準，無法自動判斷每日兩次額度用完")
        todo.append("在「校準」分頁校準『已達最高獲得金額標記』與『上限畫面的「再玩一次」』")

    # 5. 按鈕
    points = cfg.get("points", {})
    button_names = {
        "start_round": "「投注並開始」按鈕",
        "draw_confirm": "「替換」按鈕",
        "click_continue": "「點擊繼續」",
        "challenge_button": "「進行挑戰」按鈕",
        "cashout_button": "對話框「取消」（兌現）",
        "high_button": "「大」按鈕",
        "low_button": "「小」按鈕",
        "retry_button": "「再一次」按鈕",
        "max_win_retry": "上限畫面的「再玩一次」",
    }
    for key, name in button_names.items():
        mark = OK if is_point_set(points.get(key, {})) else NG
        print(f"{mark} {name}")
    holds = points.get("hold_toggles", [])
    done_holds = sum(1 for p in holds if is_point_set(p))
    print(f"{OK if done_holds == 5 else NG} 保留點擊座標：{done_holds}/5 已校準")

    # 6. 牌桌標記圖片
    marker_path = cfg.get("templates", {}).get("table_marker_image", "")
    if marker_path and os.path.exists(marker_path):
        print(f"{OK} 牌桌標記樣板圖片：{marker_path}")
    else:
        print(f"{NG} 牌桌標記樣板圖片：找不到 {marker_path or '(未設定)'}")
        todo.append("校準「牌桌標記區域」時會自動存這張圖")

    # 6b. 樣板解析度 vs 實際視窗解析度
    tmpl_w = cfg.get("templates", {}).get("capture_client_width") or 1024
    cal_w = cfg.get("calibration", {}).get("client_width") or 0
    if cal_w and abs(cal_w / float(tmpl_w) - 1.0) > 0.05:
        print(
            f"{NG} 畫面標記樣板是在 {tmpl_w} 寬的視窗下擷取的，但你的視窗是 {cal_w} 寬"
            f"（比對時要縮放 {cal_w / tmpl_w:.2f} 倍）"
        )
        todo.append(
            "建議在「校準」分頁對著實機畫面重新框選六個畫面標記，讓樣板以你自己的"
            "解析度重存（重存後不要再按「套用截圖預設框選」，那會還原成 1024 寬的預設圖）"
        )
    else:
        print(f"{OK} 畫面標記樣板解析度：{tmpl_w} 寬（與目前視窗相符）")

    # 7. 點數 / 花色樣板
    from src.cardparts import missing_parts as _missing_parts
    from src.recognize import load_part_templates as _load_parts
    parts = _load_parts()
    miss_rank, miss_suit = _missing_parts(parts)
    n_rank = len(parts.get("rank") or {})
    n_suit = len(parts.get("suit") or {})
    n_pip = len(parts.get("pip") or {})
    if not miss_rank and not miss_suit:
        print(f"{OK} 點數/花色樣板：點數 {n_rank}/13、花色 {n_suit}/4（中央大圖案 {n_pip}/4）")
    else:
        print(f"{NG} 點數/花色樣板：點數 {n_rank}/13、花色 {n_suit}/4（中央大圖案 {n_pip}/4）")
        if miss_rank:
            print(f"       ↳ 還缺點數：{'  '.join(miss_rank)}")
        if miss_suit:
            print(f"       ↳ 還缺花色：{'  '.join(miss_suit)}")
        todo.append("在 gui.py 的「點數/花色樣板」分頁補齊（只要 13 點數 + 4 花色，不用 52 張）")

    # 7b. 整張卡面樣板（備援機制，可有可無）
    found = collected_card_labels()
    total = len(full_deck())
    if found:
        print(f"{OK} 整張卡面樣板（備援）：{len(found)}/{total} 張")

    # 總結
    print("-" * 58)
    core_ready = (
        bool(title)
        and done_slots == 5
        and is_region_set(regions.get("table_marker", {}))
        and is_region_set(regions.get("highlow_card", {}))
        and done_holds == 5
    )
    print(">>> 建議直接用圖形介面操作，校準、蒐集樣板、啟動都在裡面：")
    print("    .venv\\Scripts\\python.exe gui.py")
    print()
    if not core_ready:
        print("    目前該做的是：到「校準」分頁按「依序校準全部項目」")
    elif miss_rank or miss_suit:
        print("    座標校準已完成！接著到「點數/花色樣板」分頁補齊缺的點數/花色")
        print("    （只要 13 個點數 + 4 個花色，玩一兩局就能湊齊）")
    else:
        print("    全部就緒！到「主控台」分頁，保持勾選「除錯模式」先確認辨識正確")

    if todo:
        print("\n待辦事項：")
        for i, item in enumerate(dict.fromkeys(todo), 1):
            print(f"  {i}. {item}")


if __name__ == "__main__":
    main()
