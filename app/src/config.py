"""設定檔載入/儲存與預設結構。

所有座標皆為「比例值 (0~1)」，代表相對於遊戲視窗用戶端寬/高的百分比位置。
因此視窗移動或縮放都不影響定位，只要「長寬比」維持不變即可
（長寬比一改變，遊戲本身的 UI 排版通常也會跑掉）。
"""
from __future__ import annotations

import json
import os

from . import profiles as profiles_mod
from .defaults_layout import (
    BUNDLED_MARKER_HEIGHT,
    BUNDLED_MARKER_WIDTH,
    SCREENSHOT_CLIENT_HEIGHT,
    SCREENSHOT_CLIENT_WIDTH,
    SCREENSHOT_LAYOUT,
    UI_MARKER_FILES,
)
from .geometry import is_ratio_value
from .paths import (
    config_path,
    default_ui_dir,
    ensure_runtime_dirs,
    install_default_ui_templates,
    resolve_data_path,
)

CONFIG_PATH = config_path()

# 設定檔版本：
#   1 = 舊的像素座標
#   2 = 比例座標
#   3 = 牌面比對改成「先對位再比」，辨識門檻整組重新調過
#   4 = 校準改成「每種長寬比一組」（calibration_profiles）。
#       舊的頂層 regions/points 會被原封不動搬進第一組 profile。
#   5 = match_threshold 的預設改成 0.2。整張卡面比對只是備援路徑，門檻訂高
#       等於讓備援永遠不生效；使用者要求過好幾次，但這一項原本沒放進
#       RETUNED_ON_UPGRADE，所以他設定檔裡的 0.83 一直贏過新預設。
#   6 = 內建畫面標記樣板換成 1365x576 原生解析度（原本是 1024 縮圖再放大），
#       draw_prompt 與 fail_marker 的門檻跟著重新量過。還在用內建樣板的人，
#       `templates.capture_client_width` 也會一起同步（自己抓過樣板的人不動）。
CONFIG_VERSION = 6

# 升級到某個版本時，這些欄位要強制吃新的預設值。
# 舊設定檔裡存著舊的調校數字，_deep_merge 會讓「使用者的舊值」蓋掉新預設，
# 於是程式改好了、參數卻還是舊的 —— 例如 part_min_score 停在 0.80，
# 讓所有 0.72~0.79 分的正確答案全被擋掉。
RETUNED_ON_UPGRADE = {
    3: ("part_min_score", "part_min_margin"),
    5: ("match_threshold",),
    # 6 = 內建畫面標記樣板換成 1365x576 原生解析度（原本是 1024 縮圖放大），
    #     兩個標記的門檻跟著重新量過。舊設定檔裡的 0.78 / 0.82 會讓
    #     draw_prompt 與 fail_marker 永遠過不了門檻。
    6: ("marker_thresholds",),
}

DEFAULT_CONFIG = {
    "config_version": CONFIG_VERSION,

    # 遊戲視窗標題的「部分字串」，用來尋找視窗（不需完全相同）
    "window_title_substring": "hololive-Dreams",

    # 撲克規則假設：A 是否視為最大的牌（若遊戲中 A 也可當最小牌，之後可再調整策略）
    "ace_high": True,

    # 主迴圈每次偵測畫面的間隔秒數，太小會佔用過多CPU，太大會反應變慢
    "capture_interval_sec": 0.4,

    # 「整張卡面」樣板比對的相似度門檻 (0~1)，數值越高越嚴格。
    #
    # 牌面辨識早就改成讀左上角的點數＋花色小樣板（真正在把關的是
    # part_min_score / part_min_margin），整張卡面比對只是備援路徑 ——
    # 門檻訂高等於讓備援永遠不生效。使用者要求把它降到 0.2。
    #
    # ⚠️ 這一項**一定要**放在 RETUNED_ON_UPGRADE 裡（config_version 5）。
    # 只改這行預設值是沒有用的：`_deep_merge` 讓使用者設定檔裡的舊值贏，
    # 所以他的 0.83 會一直留著 —— 這就是「我已經講過很多次預設要改成 0.2」
    # 卻每次打開設定分頁都還是看到 0.83 的原因。
    "match_threshold": 0.2,

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
    # ⚠️ 這一項在 config_version 6 被放進 RETUNED_ON_UPGRADE。理由見
    # state_machine.DEFAULT_MARKER_THRESHOLDS 上面那段量測紀錄。
    "marker_thresholds": {
        "table_marker": 0.82,
        "draw_prompt": 0.67,
        "congrats_marker": 0.80,
        "challenge_marker": 0.80,
        "fail_marker": 0.79,
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

    # 長寬比容許誤差。誤差在這個範圍內就算「同一種比例」，可以共用同一組校準；
    # 超過就會去找／建立另一組（見 src/profiles.py）。0.02 = 2%。
    "aspect_ratio_tolerance": 0.02,

    # 每種長寬比一組校準。空的時候 load_config() 會把下面的 regions/points
    # 原封不動搬進第一組。詳細規則見 src/profiles.py 的說明。
    "calibration_profiles": [],

    # 目前生效的是哪一組（profile 的 label）。None 代表「正在借用別的比例的座標」，
    # 這個狀態下存檔**不會**寫回被借的那一組，而是生出一組屬於目前比例的新的。
    "active_profile": None,

    # 選好遊戲視窗（或視窗被拉成別的比例）時，自動偵測長寬比並套用對應的校準組。
    # 在主控台的下拉選單手動指定比例時會被設成 False —— 不然視窗尺寸一變就被
    # 自動偵測改回去，那個選單等於沒用。
    "auto_detect_profile": True,

    # 額外要一起併入機率模型的 data 資料夾（絕對路徑）。
    # exe 版與原始碼版各有一份 data\\，「補算未記錄的數值」會自己找 dist\\ 底下
    # 那幾份；裝到 Program Files 或別顆硬碟的那一份程式猜不到，寫在這裡即可。
    "extra_data_dirs": [],

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
        "capture_client_width": BUNDLED_MARKER_WIDTH,
        "capture_client_height": BUNDLED_MARKER_HEIGHT,
    },
}


def load_config() -> dict:
    ensure_runtime_dirs()
    if not os.path.exists(CONFIG_PATH):
        fresh = json.loads(json.dumps(DEFAULT_CONFIG))
        profiles_mod.ensure_profiles(fresh)
        save_config(fresh)
        return fresh
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    old_version = int(cfg.get("config_version", 1) or 1)
    # 補齊新版預設值缺少的欄位（升級相容用）
    merged = _deep_merge(json.loads(json.dumps(DEFAULT_CONFIG)), cfg)

    retuned = _apply_retuning(merged, old_version)
    resized = _sync_bundled_marker_size(merged) if old_version < 6 else False

    # 把舊設定檔的頂層 regions/points 搬進 profile。座標值一個都不改。
    migrated = profiles_mod.ensure_profiles(merged)
    if migrated:
        label = merged.get("active_profile") or "?"
        print(f"[設定升級] 校準資料已收進「{label}」這一組（座標未變動）。"
              "之後每種視窗長寬比可以各存一組，程式會依視窗當下的比例自動挑。")

    if retuned or migrated or resized:
        merged["config_version"] = CONFIG_VERSION
        save_config(merged)
    if retuned:
        print(f"[設定升級] 已把重新調校過的參數換成新預設：{', '.join(retuned)}")
    if resized:
        print(f"[設定升級] 畫面標記樣板還是內建的那份，已把來源解析度更新成 "
              f"{BUNDLED_MARKER_WIDTH}x{BUNDLED_MARKER_HEIGHT}（內建圖已換成原生解析度）。")

    legacy_fields = find_legacy_pixel_coords(merged)
    if legacy_fields:
        print(
            "[警告] 偵測到舊版的『像素座標』設定檔（座標值超出 0~1 範圍）：\n"
            f"        {', '.join(legacy_fields[:8])}{' ...' if len(legacy_fields) > 8 else ''}\n"
            "        新版改用比例座標，請重新執行 calibrate.py 校準，否則點擊位置會完全錯誤。"
        )
    return merged


def _marker_image_paths(cfg: dict) -> dict:
    """{內建檔名: card_templates 裡那份的絕對路徑}。"""
    out = {}
    for cfg_key, rel in (cfg.get("templates") or {}).items():
        if not cfg_key.endswith("_image") or not isinstance(rel, str) or not rel:
            continue
        fname = os.path.basename(rel)
        if fname in UI_MARKER_FILES.values():
            out[fname] = resolve_data_path(rel)
    return out


def _same_bytes(a: str, b: str) -> bool:
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def _sync_bundled_marker_size(cfg: dict) -> bool:
    """使用者還在用內建標記樣板時，把「來源解析度」同步成內建的新解析度。

    2026-08-21 把內建標記圖從 1024 縮圖換成 1365 原生截圖。設定檔裡的
    `capture_client_width` 若還停在 1024，比對時會用錯倍率 ——
    **換了樣板反而比原本更慘**。

    但這一項**不能無條件覆蓋**：只要使用者自己在實機重新框選過標記，
    那個數字就是他的截圖解析度，蓋掉等於把他的樣板全部縮放錯。
    所以判斷方式是「card_templates 裡那幾張是不是內建的原封複本」，
    而且**只比檔案內容、不比檔名** —— 比檔名這件事在點數/花色樣板上踩過：
    內建檔名是 `suit_D_1..8`，使用者自己抓的也會被寫成那種名字，
    於是「看起來像內建」的其實是他的成果，一比就把人家的東西當成內建處理。
    """
    bundled_dir = default_ui_dir()
    pairs = _marker_image_paths(cfg)
    present = [(name, path) for name, path in pairs.items() if os.path.exists(path)]
    if not present:
        return False
    for name, path in present:
        if not _same_bytes(path, os.path.join(bundled_dir, name)):
            return False        # 有一張是他自己抓的 → 整組都不要動
    templates = cfg.setdefault("templates", {})
    if (templates.get("capture_client_width") == BUNDLED_MARKER_WIDTH
            and templates.get("capture_client_height") == BUNDLED_MARKER_HEIGHT):
        return False
    templates["capture_client_width"] = BUNDLED_MARKER_WIDTH
    templates["capture_client_height"] = BUNDLED_MARKER_HEIGHT
    return True


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
    """存檔。**存之前一定會把頂層 regions/points 寫回生效中的那一組 profile。**

    這件事刻意放在這裡而不是叫各處自己記得：忘記同步的後果是使用者剛剛校準好的
    座標下次啟動就被舊 profile 蓋回去 —— 沉默地弄丟校準資料，正是這個專案
    最不能再犯的錯。
    """
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    profiles_mod.sync_active(cfg)
    # "_" 開頭的是執行期狀態（例如借用中的暫存標籤），不寫進檔案
    payload = {k: v for k, v in cfg.items() if not k.startswith("_")}
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    # 先寫暫存再換掉：寫入中途斷掉不會留下半截的 config.json
    os.replace(tmp, CONFIG_PATH)


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
        cfg["templates"]["capture_client_width"] = BUNDLED_MARKER_WIDTH
        cfg["templates"]["capture_client_height"] = BUNDLED_MARKER_HEIGHT
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
