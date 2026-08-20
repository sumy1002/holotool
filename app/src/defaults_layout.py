"""預設框選（比例座標）。

這些數字是使用者在自己的實機視窗（1843×778）校準之後量出來的，
比原本從 1024×438 截圖目測的準。所有值都是比例，所以換視窗大小仍然適用，
只要長寬比一樣即可。之後仍可在「校準」分頁逐項微調。
"""
from __future__ import annotations

# 五張手牌逐格校準出來的實際位置（間距其實不完全等寬）
_CARD_SLOTS = (
    {"x": 0.1715, "y": 0.2956, "w": 0.1264, "h": 0.3985},
    {"x": 0.3071, "y": 0.2918, "w": 0.1226, "h": 0.4010},
    {"x": 0.4390, "y": 0.2931, "w": 0.1237, "h": 0.3985},
    {"x": 0.5714, "y": 0.2905, "w": 0.1232, "h": 0.4023},
    {"x": 0.7021, "y": 0.2931, "w": 0.1275, "h": 0.4087},
)
_HOLD_XS = (0.2383, 0.3730, 0.5078, 0.6426, 0.7773)

# 內建預設樣板 (defaults/ui/) 的來源截圖尺寸。
# 比對畫面標記時要靠這個推算「樣板需要放大幾倍」，不可以省略。
SCREENSHOT_CLIENT_WIDTH = 1024
SCREENSHOT_CLIENT_HEIGHT = 438

SCREENSHOT_LAYOUT: dict = {
    "regions": {
        "table_marker": {"x": 0.1289, "y": 0.0365, "w": 0.1445, "h": 0.1826},
        "draw_prompt": {"x": 0.4277, "y": 0.2100, "w": 0.1533, "h": 0.0525},
        "congrats_marker": {"x": 0.3418, "y": 0.0228, "w": 0.3281, "h": 0.0959},
        # 「要挑戰嗎？」兩種翻倍對話框都有這行，金額會變所以不要框金額
        "challenge_marker": {"x": 0.4199, "y": 0.4977, "w": 0.1680, "h": 0.0822},
        # 「失敗」大字。注意這個位置的標題其實會變（失敗／無對子／一對／兩對…），
        # 判斷「這一局結束了」要以 poker_fail_marker 為主。
        "fail_marker": {"x": 0.4277, "y": 0.1540, "w": 0.1484, "h": 0.1187},
        # 「要再玩一次撲克嗎？」湊牌失敗專用，不要跟比大小的「失敗」搞混
        "poker_fail_marker": {"x": 0.2800, "y": 0.4590, "w": 0.4390, "h": 0.0890},
        # 「已達最高獲得金額，遊戲結束」：每天兩次額度用掉一次的訊號
        "max_win_marker": {"x": 0.4050, "y": 0.8720, "w": 0.1920, "h": 0.0500},
        "card_slots": [dict(s) for s in _CARD_SLOTS],
        "highlow_card": {"x": 0.1445, "y": 0.3288, "w": 0.1172, "h": 0.3790},
    },
    "points": {
        "start_round": {"x": 0.5000, "y": 0.8128},
        "draw_confirm": {"x": 0.5000, "y": 0.8128},
        "click_continue": {"x": 0.5000, "y": 0.9452},
        "challenge_button": {"x": 0.7930, "y": 0.8950},
        "cashout_button": {"x": 0.6465, "y": 0.8950},
        # 「太大」按鈕橫跨 x=0.667~0.833、「太小」x=0.667~0.837，取正中央
        "high_button": {"x": 0.7500, "y": 0.4600},
        "low_button": {"x": 0.7500, "y": 0.5900},
        "retry_button": {"x": 0.7740, "y": 0.9170},
        # 上限畫面右下角的「再玩一次」，位置跟失敗畫面那顆不一樣
        "max_win_retry": {"x": 0.8830, "y": 0.8990},
        "hold_toggles": [{"x": x, "y": 0.4977} for x in _HOLD_XS],
    },
}

# 校準項目 path → 樣板檔名；框選當下就要存圖，不要等全部校準結束
UI_MARKER_FILES = {
    "regions.table_marker": "table_marker.png",
    "regions.draw_prompt": "ui_draw_prompt.png",
    "regions.congrats_marker": "ui_congrats.png",
    "regions.challenge_marker": "ui_challenge.png",
    "regions.fail_marker": "ui_fail.png",
    "regions.poker_fail_marker": "ui_poker_fail.png",
    "regions.max_win_marker": "ui_max_win.png",
}
