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

    if p_higher >= p_lower:
        return HighLowDecision(choice="high", win_prob=p_higher)
    return HighLowDecision(choice="low", win_prob=p_lower)


def should_continue_highlow(win_prob: float, chain_count: int, config: dict) -> bool:
    if chain_count >= config.get("highlow_max_chain", 6):
        return False
    return win_prob >= config.get("highlow_min_win_prob_to_continue", 0.5)
