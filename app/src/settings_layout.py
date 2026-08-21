"""「設定」分頁的欄位分組。

## 為什麼要分組

原本二十幾個欄位平鋪成一長串，使用者要調「遊戲漏收點擊」的那幾個秒數，得先
在門檻、策略、雜項之間找。現在分成三塊：

1. **比對門檻設定**（預設展開）—— 認不到牌、認不到畫面標記時調這裡。最常動。
2. **動作時間設定**（預設收合）—— 遊戲跑得慢、常漏收點擊時調這裡。調過一次
   通常就不用再動，而且它九個欄位，一直攤開會把下面的東西擠出視窗外。
3. **其餘** —— 策略與雜項，直接放在外面。

## 為什麼放在 src\\ 而不是 gui.py

放在這裡才測得到。gui.py 一 import 就會拉進 tkinter、cv2 與 win32，在沒有
GUI 的環境根本跑不起來；分組資料本身只是純資料，值得有測試守著
「每個欄位都被歸到某一組、沒有重複也沒有漏掉、路徑真的存在」。
"""
from __future__ import annotations

# (設定路徑, 顯示名稱, 說明)
THRESHOLD_FIELDS: list[tuple[str, str, str]] = [
    ("match_threshold", "卡牌比對相似度門檻", "0~1，越高越嚴格。辨識不到牌可略微調低"),
    ("min_match_margin", "辨識領先差距門檻", "最高分需領先第二名多少才採信，避免認錯相近的牌"),
    ("part_min_score", "點數/花色樣板門檻", "讀卡片左上角小樣板的絕對門檻；一直跳問號時略微調低"),
    ("part_min_margin", "點數/花色領先門檻", "最高分需領先第二名多少才採信（0.05 = 5%）"),
    ("marker_thresholds.table_marker", "牌桌標記門檻", "低於此值視為已離開牌桌（對話框會模糊 logo，屬正常）"),
    ("marker_thresholds.draw_prompt", "選牌畫面門檻", "「選擇要保留的牌吧！」的比對門檻"),
    ("draw_prompt_soft_threshold", "選牌畫面寬鬆門檻", "分數只到這裡、但五張手牌都認得出來時，一樣視為選牌畫面"),
    ("marker_thresholds.congrats_marker", "過關畫面門檻", "「Congratulations！」的比對門檻"),
    ("marker_thresholds.challenge_marker", "翻倍對話門檻", "「要挑戰嗎？」的比對門檻"),
    ("marker_thresholds.fail_marker", "比大小失敗門檻", "「失敗」大字的比對門檻"),
    ("marker_thresholds.poker_fail_marker", "湊牌失敗門檻", "「要再玩一次撲克嗎？」的比對門檻"),
    ("marker_thresholds.max_win_marker", "達到上限門檻", "「已達最高獲得金額，遊戲結束」的比對門檻"),
]

TIMING_FIELDS: list[tuple[str, str, str]] = [
    ("capture_interval_sec", "畫面偵測間隔（秒）", "越小反應越快，但越吃 CPU"),
    ("action_cooldown_sec", "動作冷卻（秒）", "點完後至少等這麼久；遊戲跑得慢就調大，避免亂點"),
    ("action_retry_sec", "漏收重試（秒）", "同一個畫面卡這麼久沒變，就再點一次"),
    ("idle_confirm_sec", "待機確認（秒）", "畫面連續認不出來這麼久，才會去點「投注並開始」"),
    ("draw_result_wait_sec", "換牌後等待（秒）",
     "按下「替換」後安靜等發牌動畫的上限；超時只會回到一般偵測，不會亂點"),
    ("multi_click_gap_sec", "連點間隔（秒）", "選牌時每點一張牌之間停多久"),
    ("click_hold_min_sec", "滑鼠按住最短（秒）", "按下到放開的停留時間；遊戲漏收點擊就調大"),
    ("click_hold_max_sec", "滑鼠按住最長（秒）", "同上，實際會在最短~最長之間隨機"),
    ("exit_table_ticks", "離桌判定 tick 數", "連續幾次看不到牌桌 logo 才停止（0.4 秒 × 25 ≈ 10 秒）"),
]

OTHER_FIELDS: list[tuple[str, str, str]] = [
    ("daily_max_wins", "每日上限次數", "達到最高金額幾次之後就收工（遊戲規則是 2 次）"),
    ("monte_carlo_samples", "選牌模擬次數", "越大越準但越慢，建議 1000~5000"),
    ("highlow_min_win_prob_to_continue", "比大小續押勝率門檻", "預估勝率低於此值就收手兌現"),
    ("highlow_max_chain", "比大小最多連續加倍次數", "保險上限，避免無限追加"),
    ("aspect_ratio_tolerance", "長寬比容許誤差", "超過就算成另一種比例，另存一組校準。0.02 = 2%"),
]

# (標題, 說明, 欄位, 預設是否展開)。只有需要收合的才放進來，OTHER_FIELDS 直接攤開。
SETTING_SECTIONS: list[tuple[str, str, list, bool]] = [
    ("比對門檻設定", "認不到牌、認不到畫面標記的時候調這一區。數字越高越嚴格。",
     THRESHOLD_FIELDS, True),
    ("動作時間設定", "遊戲跑得慢、或常常漏收點擊的時候調這一區。單位大多是秒。",
     TIMING_FIELDS, False),
]

# 完整清單。存檔與「全部還原成預設值」都以這一份為準，所以任何新欄位只要加進
# 上面三組之一就會自動被涵蓋，不會出現「畫面上看得到、存檔時被忽略」。
SETTING_FIELDS: list[tuple[str, str, str]] = THRESHOLD_FIELDS + TIMING_FIELDS + OTHER_FIELDS

# 這幾個要存成整數，其餘一律 float
INT_SETTINGS = frozenset({
    "monte_carlo_samples", "highlow_max_chain", "exit_table_ticks", "daily_max_wins",
})
