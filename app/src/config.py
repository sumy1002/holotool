"""設定檔載入/儲存與預設結構。

所有座標皆為「比例值 (0~1)」，代表相對於遊戲視窗用戶端寬/高的百分比位置。
因此視窗移動或縮放都不影響定位，只要「長寬比」維持不變即可
（長寬比一改變，遊戲本身的 UI 排版通常也會跑掉）。
"""
from __future__ import annotations

import json
import os

from .defaults_layout import (
    SCREENSHOT_CLIENT_HEIGHT,
    SCREENSHOT_CLIENT_WIDTH,
    SCREENSHOT_LAYOUT,
)
from .geometry import is_ratio_value
from .paths import config_path, ensure_runtime_dirs, install_default_ui_templates

CONFIG_PATH = config_path()

# 設定檔版本：
#   1 = 舊的像素座標
#   2 = 比例座標
#   3 = 牌面比對改成「先對位再比」，辨識門檻整組重新調過
CONFIG_VERSION = 3

# 升級到某個版本時，這些欄位要強制吃新的預設值。
# 舊設定檔裡存著舊的調校數字，_deep_merge 會讓「使用者的舊值」蓋掉新預設，
# 於是程式改好了、參數卻還是舊的 —— 例如 part_min_score 停在 0.80，
# 讓所有 0.72~0.79 分的正確答案全被擋掉。
RETUNED_ON_UPGRADE = {
    3: ("part_min_score", "part_min_margin"),
}

DEFAULT_CONFIG = {
    "config_version": CONFIG_VERSION,

    # 遊戲視窗標題的「部分字串」，用來尋找視窗（不需完全相同）
    "window_title_substring": "hololive-Dreams",

    # 撲克規則假設：A 是否視為最大的牌（若遊戲中 A 也可當最小牌，之後可再調整策略）
    "ace_high": True,

    # 主迴圈每次偵測畫面的間隔秒數，太小會佔用過多CPU，太大會反應變慢
    "capture_interval_sec": 0.4,

    # 卡牌樣板比對的相似度門檻 (0~1)，數值越高越嚴格
    "match_threshold": 0.83,

    # 最高分與第二名之間至少要差多少才採信辨識結果（避免把相近的牌認錯）。
    # 視窗縮得越小，各張牌的分數會越接近，此時可略微調低，但太低容易誤判。
    "min_match_margin": 0.02,

    # 牌桌標記區域比對門檻（保留欄位；實際判斷改用下面的 marker_thresholds）
    "table_marker_threshold": 0.80,

    # 點數 / 花色小樣板的比對門檻（辨識牌面主要靠這個）。
    # 比對前會先把字的質心對到畫布正中央再做 ±1 像素微調，所以正解分數通常在
    # 0.9 以上；真正在把關的是「領先第二名多少」，絕對門檻只是最後一道保險。
    "part_min_score": 0.72,
    "part_min_margin": 0.05,

    # 六個畫面標記各自的比對門檻。它們的對比度與背景複雜度差很多，
    # 共用同一個門檻一定會有人過不了、有人誤判，所以拆開來調。
    "marker_thresholds": {
        "table_marker": 0.82,
        "draw_prompt": 0.78,
        "congrats_marker": 0.80,
        "challenge_marker": 0.80,
        "fail_marker": 0.82,
        "poker_fail_marker": 0.74,
        "max_win_marker": 0.78,
    },

    # 各標記往外擴張搜尋的量 [左右, 上下]，單位是「該區域自身的寬/高比例」。
    # 舊版是用「整個視窗」的比例，在大視窗下搜尋範圍會暴增而掃出假陽性。
    "marker_pads": {
        "table_marker": [0.20, 0.25],
        "draw_prompt": [0.30, 0.80],
        "congrats_marker": [0.20, 0.60],
        "challenge_marker": [0.35, 0.90],
        "fail_marker": [0.35, 0.60],
        "poker_fail_marker": [0.25, 0.80],
        "max_win_marker": [0.25, 0.80],
    },

    # 連續幾個 tick 看不到牌桌 logo 才判定「已離開牌桌 / 可能已達每日上限」。
    # 對話框出現時整個牌桌會被模糊，logo 認不出來是正常的，所以不能設太小
    # （0.4 秒 × 25 ≈ 10 秒）。
    "exit_table_ticks": 25,

    # 「選擇要保留的牌吧！」的寬鬆門檻：分數只到這裡、但五張手牌都認得出來時，
    # 一樣視為選牌畫面（投注畫面是蓋著的牌，認不出來，所以不會被誤判）。
    "draw_prompt_soft_threshold": 0.50,

    # 比大小掃描「目前這張牌」時，往右掃到畫面的哪個比例位置為止
    "highlow_scan_right": 0.62,

    # 選牌策略計算時的蒙地卡羅模擬次數，越大越準但越慢
    "monte_carlo_samples": 3000,

    # 比大小：只有在「猜中機率」大於此門檻時才繼續加倍，否則收手兌現
    "highlow_min_win_prob_to_continue": 0.5,

    # 比大小：最多連續加倍幾次就強制收手（保險用，避免無限追加風險）
    "highlow_max_chain": 6,

    # 每一輪最多允許換牌幾次（目前遊戲規則若為單次換牌請設1）
    "max_redraw_rounds": 1,

    # 每天可以達到「最高獲得金額」幾次。達到第 1 次時畫面會停在結算頁，按「再玩一次」
    # 還能繼續；達到第 2 次時遊戲會直接關掉牌桌，這時就該收工了。
    # 次數記在 data/stats_YYYY-MM-DD.json 的 max_win_count，跨程式重啟仍然有效；
    # 若手動玩過導致次數對不上，直接改那個檔案即可。
    "daily_max_wins": 2,

    # 是否啟用防呆：滑鼠移到螢幕角落時 pyautogui 會強制中止（建議保持開啟）
    "pyautogui_failsafe": True,

    # ---- 動作節奏（遊戲跑比較慢、常常漏收點擊時調這幾個）----

    # 點完之後至少等這麼久才做下一個動作。遊戲跑動畫的那一兩秒畫面認不出來是
    # 正常的，這段期間不能急著動作，否則會亂點（例如按完「大」又去點「投注並開始」）。
    "action_cooldown_sec": 1.2,

    # 同一個畫面卡這麼久還沒變，就視為遊戲漏收了上一次點擊，再點一次。
    "action_retry_sec": 2.5,

    # 畫面必須「連續認不出來」這麼久，才會去點「投注並開始」。
    "idle_confirm_sec": 1.5,

    # 按下「替換」之後，最多安靜等這麼久讓遊戲跑完發牌動畫並切換到下一個畫面。
    # 這段期間完全不做判斷；就算超時也只是回到一般偵測，**不會**自己當成湊牌失敗。
    # （湊牌失敗一定會出現「要再玩一次撲克嗎？」的標記，靠那個判斷就好。）
    "draw_result_wait_sec": 15.0,

    # 選牌時連點五張保留牌，每一下之間的間隔（連太快遊戲會漏收）
    "multi_click_gap_sec": 0.18,

    # 滑鼠按下到放開之間停留多久（秒）。太短的話，每幀輪詢一次的遊戲會整個跳過。
    "click_hold_min_sec": 0.06,
    "click_hold_max_sec": 0.12,

    # 校準當時的視窗用戶端尺寸；截圖預設是依 1024×438 量的，長寬比接近即可
    "calibration": {"client_width": 1024, "client_height": 438},

    # 長寬比容許誤差，超過此比例就會發出警告（0.02 = 2%）
    "aspect_ratio_tolerance": 0.02,

    # 以下座標全部為比例值 (0~1)，相對於視窗用戶端寬/高
    # 預設值依 1024×438 截圖框選；可在校準分頁微調
    "regions": json.loads(json.dumps(SCREENSHOT_LAYOUT["regions"])),

    "points": json.loads(json.dumps(SCREENSHOT_LAYOUT["points"])),

    "templates": {
        "table_marker_image": "card_templates/table_marker.png",
        "draw_prompt_image": "card_templates/ui_draw_prompt.png",
        "congrats_marker_image": "card_templates/ui_congrats.png",
        "challenge_marker_image": "card_templates/ui_challenge.png",
        "fail_marker_image": "card_templates/ui_fail.png",
        "poker_fail_marker_image": "card_templates/ui_poker_fail.png",
        "max_win_marker_image": "card_templates/ui_max_win.png",
        "card_back_image": "card_templates/back.png",

        # 「擷取這幾張畫面標記樣板時，遊戲視窗有多寬」。
        # 樣板是點陣圖，帶著擷取當下的解析度；實機視窗若不是這個寬度，
        # 比對前必須先把樣板縮放 (目前視窗寬 ÷ 這個值) 倍，否則一定對不起來。
        # 在「校準」分頁重新框選標記時，這個值會自動更新成當下的視窗寬度。
        "capture_client_width": SCREENSHOT_CLIENT_WIDTH,
        "capture_client_height": SCREENSHOT_CLIENT_HEIGHT,
    },
}


def load_config() -> dict:
    ensure_runtime_dirs()
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    old_version = int(cfg.get("config_version", 1) or 1)
    # 補齊新版預設值缺少的欄位（升級相容用）
    merged = _deep_merge(json.loads(json.dumps(DEFAULT_CONFIG)), cfg)

    retuned = _apply_retuning(merged, old_version)
    if retuned:
        merged["config_version"] = CONFIG_VERSION
        save_config(merged)
        print(f"[設定升級] 已把重新調校過的參數換成新預設：{', '.join(retuned)}")

    legacy_fields = find_legacy_pixel_coords(merged)
    if legacy_fields:
        print(
            "[警告] 偵測到舊版的『像素座標』設定檔（座標值超出 0~1 範圍）：\n"
            f"        {', '.join(legacy_fields[:8])}{' ...' if len(legacy_fields) > 8 else ''}\n"
            "        新版改用比例座標，請重新執行 calibrate.py 校準，否則點擊位置會完全錯誤。"
        )
    return merged


def _apply_retuning(cfg: dict, old_version: int) -> list[str]:
    """升級時把「已經重新調校過」的欄位換回新預設，其餘（座標、校準）一律保留。"""
    changed: list[str] = []
    for version, fields in sorted(RETUNED_ON_UPGRADE.items()):
        if old_version >= version:
            continue
        for field in fields:
            new_value = DEFAULT_CONFIG.get(field)
            if new_value is not None and cfg.get(field) != new_value:
                changed.append(f"{field} {cfg.get(field)} → {new_value}")
                cfg[field] = new_value
    return changed


def get_by_path(cfg: dict, path: str):
    """依「點分隔路徑」讀取設定值，數字視為串列索引。

    例如 get_by_path(cfg, "regions.card_slots.0") 會取得第 1 張手牌的區域。
    """
    node = cfg
    for part in path.split("."):
        node = node[int(part)] if part.isdigit() else node[part]
    return node


def set_by_path(cfg: dict, path: str, value) -> None:
    """依「點分隔路徑」寫入設定值，數字視為串列索引。"""
    parts = path.split(".")
    node = cfg
    for part in parts[:-1]:
        node = node[int(part)] if part.isdigit() else node[part]
    last = parts[-1]
    if last.isdigit():
        node[int(last)] = value
    else:
        node[last] = value


def find_legacy_pixel_coords(cfg: dict) -> list[str]:
    """找出所有超出 0~1 範圍的座標欄位，用來偵測尚未重新校準的舊版設定檔。"""
    bad: list[str] = []

    def _check(container: dict, path: str, keys: tuple[str, ...]) -> None:
        for key in keys:
            value = container.get(key)
            if isinstance(value, (int, float)) and not is_ratio_value(float(value)):
                bad.append(f"{path}.{key}={value}")

    regions = cfg.get("regions", {})
    for name, region in regions.items():
        if isinstance(region, dict):
            _check(region, f"regions.{name}", ("x", "y", "w", "h"))
        elif isinstance(region, list):
            for i, item in enumerate(region):
                if isinstance(item, dict):
                    _check(item, f"regions.{name}[{i}]", ("x", "y", "w", "h"))

    points = cfg.get("points", {})
    for name, point in points.items():
        if isinstance(point, dict):
            _check(point, f"points.{name}", ("x", "y"))
        elif isinstance(point, list):
            for i, item in enumerate(point):
                if isinstance(item, dict):
                    _check(item, f"points.{name}[{i}]", ("x", "y"))

    return bad


def save_config(cfg: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def apply_screenshot_layout(cfg: dict, *, install_templates: bool = True) -> dict:
    """套用從截圖量出的預設框選。不改視窗標題；校準尺寸若還是空的才補上。"""
    cfg.setdefault("regions", {})
    cfg.setdefault("points", {})
    cfg["regions"].update(json.loads(json.dumps(SCREENSHOT_LAYOUT["regions"])))
    cfg["points"].update(json.loads(json.dumps(SCREENSHOT_LAYOUT["points"])))
    cfg.setdefault("templates", {})
    cfg["templates"].update({
        "table_marker_image": "card_templates/table_marker.png",
        "draw_prompt_image": "card_templates/ui_draw_prompt.png",
        "congrats_marker_image": "card_templates/ui_congrats.png",
        "challenge_marker_image": "card_templates/ui_challenge.png",
        "fail_marker_image": "card_templates/ui_fail.png",
        "poker_fail_marker_image": "card_templates/ui_poker_fail.png",
        "max_win_marker_image": "card_templates/ui_max_win.png",
        "card_back_image": "card_templates/back.png",
    })
    cal = cfg.get("calibration") or {}
    if not cal.get("client_width") or not cal.get("client_height"):
        cfg["calibration"] = {
            "client_width": SCREENSHOT_CLIENT_WIDTH,
            "client_height": SCREENSHOT_CLIENT_HEIGHT,
        }
    if install_templates:
        install_default_ui_templates(overwrite=True)
        # 還原成內建預設樣板 = 樣板來源解析度也要跟著還原成 1024×438，
        # 否則比對時會用錯誤的倍率去縮放樣板。
        cfg["templates"]["capture_client_width"] = SCREENSHOT_CLIENT_WIDTH
        cfg["templates"]["capture_client_height"] = SCREENSHOT_CLIENT_HEIGHT
    return cfg


def set_template_capture_size(cfg: dict, client_width: int, client_height: int) -> None:
    """記錄「畫面標記樣板是在多大的視窗下擷取的」。重新擷取樣板時務必呼叫。"""
    if client_width <= 0 or client_height <= 0:
        return
    cfg.setdefault("templates", {})
    cfg["templates"]["capture_client_width"] = int(client_width)
    cfg["templates"]["capture_client_height"] = int(client_height)


def _deep_merge(base: dict, override: dict) -> dict:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = _deep_merge(base[k], v)
        else:
            base[k] = v
    return base
