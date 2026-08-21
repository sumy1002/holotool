"""將牌型評估 (handeval) 與統計機率 (stats) 結合，產生實際的遊戲決策。"""
from __future__ import annotations

from dataclasses import dataclass

from .handeval import Card, evaluate_hold_options, hand_name, rank_value, MIN_QUALIFY_CATEGORY
from .stats import DailyStats


@dataclass
class HoldDecision:
    hold_mask: int          # bit i = 1 表示保留第 i 張(0~4)
    discard_idx: list[int]  # 需要丟棄(換牌)的手牌索引
    p_qualify: float
    expected_hand: str


@dataclass
class HighLowDecision:
    choice: str            # "high" 或 "low"
    win_prob: float
    should_cashout_if_lose_choice: bool = False


def decide_hold(cards: list[Card], stats: DailyStats, samples: int = 3000) -> HoldDecision:
    card_probs = stats.get_card_probabilities()
    results = evaluate_hold_options(cards, card_probs, samples=samples, min_qualify=MIN_QUALIFY_CATEGORY)
    best = results[0]
    hold_idx = set(best["held_idx"])
    discard_idx = [i for i in range(5) if i not in hold_idx]
    return HoldDecision(
        hold_mask=best["mask"],
        discard_idx=discard_idx,
        p_qualify=best["p_qualify"],
        expected_hand=hand_name(round(best["expected_category"])),
    )


def decide_high_or_low(current_rank: str, stats: DailyStats, ace_high: bool = True) -> HighLowDecision:
    """依今日統計機率估計「下一張牌比目前大/比目前小」的機率，選擇機率較高的一邊。

    遊戲規則：數字相同時再抽一次，所以平手不計入任何一邊。
    """
    if current_rank == "JK":
        return HighLowDecision(choice="high", win_prob=0.5)

    rank_probs = stats.get_rank_probabilities()
    cur_val = rank_value(current_rank)
    if not ace_high and current_rank == "A":
        cur_val = 1  # 若 A 視為最小牌，比較基準改為 1

    p_higher = 0.0
    p_lower = 0.0
    for r, p in rank_probs.items():
        v = rank_value(r)
        if not ace_high and r == "A":
            v = 1
        if v > cur_val:
            p_higher += p
        elif v < cur_val:
            p_lower += p
        # 相等的情況（同點數）不計入任何一邊，視為和局

    # **回報「有勝負時猜中的機率」，不是「猜中的機率」。**
    #
    # 同點數會重抽、不計勝負，所以平手那一塊機率既不算贏也不算輸 ——
    # 它應該從分母裡拿掉。以前直接回 p_higher（分母含平手）會系統性低估：
    # 一副均勻的牌拿到 8 時，上面 6 種、下面 6 種、平手 1 種，
    # 舊算法回 6/13 = **0.462**，看起來像「勝率不到五成」，
    # 但實際上有勝負時就是 6/12 = **0.500** 的公平硬幣。
    # 使用者實機那行 `目前牌=8H 預估勝率=47.1%` 就是這樣來的，
    # 然後 0.471 < 0.5 就被判定成「不值得再翻」而去兌現。
    best, other = ((p_higher, p_lower) if p_higher >= p_lower
                   else (p_lower, p_higher))
    decisive = best + other
    win_prob = best / decisive if decisive > 0 else 0.5
    return HighLowDecision(choice="high" if p_higher >= p_lower else "low",
                           win_prob=win_prob)


def should_continue_highlow(win_prob: float, chain_count: int, config: dict) -> bool:
    """還要不要再翻一次？

    ## 這個工具的目標是「衝到 12800 上限」，不是「期望硬幣數最大」

    使用者講得很清楚：**程式的目標只有一個，就是完成 12800、每天兩次。**
    那是一個「全有全無」的目標 —— 中途兌現拿走 400 枚，這一輪就**永遠到不了
    12800**，對這個目標來說跟輸掉沒有兩樣（只是帳面好看一點）。

    但預設值原本是「勝率低於 0.5 就收手兌現」，那是在最大化**期望硬幣數**，
    完全是另一個目標。實機 log 就撞上了：

        [比大小階段] 目前牌=8H 建議=HIGH 預估勝率=47.1%
        [翻倍對話] 取消兌現（預估下一手勝率 47.1%）

    連 400 都不到就收手了。

    所以預設改成 `highlow_min_win_prob_to_continue = 0`（永遠不收手）、
    `highlow_max_chain = 0`（不設連勝上限，讓遊戲自己在 12800 喊停）。
    想回到「賺硬幣」的玩法就把門檻調回 0.5、上限調回 6。
    """
    cap = int(config.get("highlow_max_chain", 0) or 0)
    if cap > 0 and chain_count >= cap:
        return False
    return win_prob >= float(config.get("highlow_min_win_prob_to_continue", 0.0) or 0.0)
