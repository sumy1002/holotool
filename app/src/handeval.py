"""撲克牌型評估，以及「該保留哪些牌」的期望值計算。

規則假設（可依實際遊戲調整）：
- 牌型大小依標準撲克規則：高牌 < 一對 < 兩對 < 三條 < 順子 < 同花 < 葫蘆 < 鐵支 < 同花順
- 順子允許 A-2-3-4-5 (最小順)
- 「門票」門檻預設為兩對 (MIN_QUALIFY_CATEGORY)，可依實際遊戲規則調整
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from typing import Optional

import numpy as np

RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
SUITS = ["S", "H", "D", "C"]  # 黑桃 紅心 方塊 梅花

HAND_NAMES = {
    0: "高牌",
    1: "一對",
    2: "兩對",
    3: "三條",
    4: "順子",
    5: "同花",
    6: "葫蘆",
    7: "鐵支",
    8: "同花順",
}

# 門票門檻：至少兩對才能進階到比大小環節
MIN_QUALIFY_CATEGORY = 2


@dataclass(frozen=True)
class Card:
    rank: str  # "2".."10","J","Q","K","A"
    suit: str  # "S","H","D","C"

    @property
    def label(self) -> str:
        return f"{self.rank}{self.suit}"

    @staticmethod
    def from_label(label: str) -> "Card":
        label = label.strip().upper()
        if label in ("JK", "JOKER", "JO", "JKX"):
            return Card("JK", "X")  # 鬼牌，選牌階段當萬能牌
        suit = label[-1]
        rank = label[:-1]
        if rank not in RANKS or suit not in SUITS:
            raise ValueError(f"無法解析卡牌標籤: {label}")
        return Card(rank, suit)


def rank_value(rank: str) -> int:
    if rank == "JK":
        return 0
    return RANKS.index(rank) + 2  # 2..14


def full_deck() -> list[Card]:
    return [Card(r, s) for r in RANKS for s in SUITS]


def classify_hand(cards: list[Card]) -> tuple[int, tuple[int, ...]]:
    """回傳 (牌型類別 0~8, 用於同類別比大小的 tiebreak tuple)。鬼牌視為萬能牌。"""
    jokers = [c for c in cards if c.rank == "JK"]
    normal = [c for c in cards if c.rank != "JK"]
    if not jokers:
        return _classify_plain(normal if normal else cards)

    from itertools import product
    replacements = [Card(r, s) for r in RANKS for s in SUITS]
    best: tuple[int, tuple[int, ...]] = (-1, ())
    for combo in product(replacements, repeat=len(jokers)):
        cat = _classify_plain(normal + list(combo))
        if cat > best:
            best = cat
    return best


def _classify_plain(cards: list[Card]) -> tuple[int, tuple[int, ...]]:
    """不含鬼牌的標準撲克牌型判定。"""
    values = sorted((rank_value(c.rank) for c in cards), reverse=True)
    suits = [c.suit for c in cards]
    is_flush = len(set(suits)) == 1

    unique_vals = sorted(set(values))
    is_straight = False
    straight_high = None
    if len(unique_vals) == 5:
        if unique_vals[-1] - unique_vals[0] == 4:
            is_straight = True
            straight_high = unique_vals[-1]
        elif unique_vals == [2, 3, 4, 5, 14]:  # A-2-3-4-5 最小順
            is_straight = True
            straight_high = 5

    counts = Counter(values)
    groups = sorted(counts.items(), key=lambda kv: (-kv[1], -kv[0]))
    count_pattern = [g[1] for g in groups]
    tiebreak = tuple(g[0] for g in groups)

    if is_straight and is_flush:
        category = 8
    elif count_pattern[0] == 4:
        category = 7
    elif count_pattern[0] == 3 and len(count_pattern) > 1 and count_pattern[1] == 2:
        category = 6
    elif is_flush:
        category = 5
    elif is_straight:
        category = 4
        tiebreak = (straight_high,)
    elif count_pattern[0] == 3:
        category = 3
    elif count_pattern[0] == 2 and len(count_pattern) > 1 and count_pattern[1] == 2:
        category = 2
    elif count_pattern[0] == 2:
        category = 1
    else:
        category = 0

    return category, tiebreak


def hand_name(category: int) -> str:
    return HAND_NAMES.get(category, "?")


SUIT_CODE = {"S": 0, "H": 1, "D": 2, "C": 3}


def _weighted_pool(exclude: list[Card], card_weights: dict[str, float]) -> tuple[list[Card], np.ndarray]:
    exclude_labels = {c.label for c in exclude}
    pool = [c for c in full_deck() if c.label not in exclude_labels]
    weights = np.array([max(card_weights.get(c.label, 0.0), 1e-9) for c in pool], dtype=np.float64)
    weights = weights / weights.sum()
    return pool, weights


def classify_hand_batch(values: np.ndarray, suits: np.ndarray) -> np.ndarray:
    """向量化版本的牌型評估，可一次評估大量(N筆)手牌，用於蒙地卡羅模擬加速。

    values: shape (N, 5)，每張牌點數 2~14
    suits:  shape (N, 5)，每張牌花色代碼 0~3
    回傳: shape (N,) 的牌型類別 (0~8)
    """
    n = values.shape[0]
    rank_range = np.arange(2, 15)  # 13 種點數
    # counts[n, r] = 這手牌中點數 rank_range[r] 出現的次數
    counts = (values[:, :, None] == rank_range[None, None, :]).sum(axis=1)  # (N, 13)
    sorted_counts = -np.sort(-counts, axis=1)  # 每列由大到小排序
    c0 = sorted_counts[:, 0]
    c1 = sorted_counts[:, 1]

    is_flush = (suits == suits[:, 0:1]).all(axis=1)

    sorted_vals_asc = np.sort(values, axis=1)
    all_unique = c0 == 1
    is_wide_straight = all_unique & ((sorted_vals_asc[:, -1] - sorted_vals_asc[:, 0]) == 4)
    is_wheel = all_unique & (sorted_vals_asc == np.array([2, 3, 4, 5, 14])).all(axis=1)
    is_straight = is_wide_straight | is_wheel

    cond_straight_flush = is_straight & is_flush
    cond_four = c0 == 4
    cond_full = (c0 == 3) & (c1 == 2)
    cond_flush = is_flush & ~is_straight
    cond_straight = is_straight & ~is_flush
    cond_three = (c0 == 3) & ~cond_full
    cond_two_pair = (c0 == 2) & (c1 == 2)
    cond_pair = (c0 == 2) & ~cond_two_pair

    category = np.select(
        [cond_straight_flush, cond_four, cond_full, cond_flush, cond_straight, cond_three, cond_two_pair, cond_pair],
        [8, 7, 6, 5, 4, 3, 2, 1],
        default=0,
    )
    return category


def _weighted_sample_without_replacement_batch(
    weights: np.ndarray, n_draw: int, samples: int, rng: np.random.Generator
) -> np.ndarray:
    """一次產生 `samples` 組「不重複加權抽樣」的索引 (Efraimidis-Spirakis 演算法)，
    回傳 shape (samples, n_draw) 的池內索引陣列。比逐筆呼叫 rng.choice 快非常多。
    """
    u = rng.random((samples, weights.shape[0]))
    # 避免 0 次方/極端權重造成數值問題
    safe_w = np.clip(weights, 1e-12, None)
    keys = u ** (1.0 / safe_w)  # 權重越大，key 越容易接近 1（越容易被抽中）
    # 取每列 key 最大的 n_draw 個索引
    part = np.argpartition(-keys, n_draw - 1, axis=1)[:, :n_draw]
    return part


def evaluate_hold_options(
    current_cards: list[Card],
    card_weights: dict[str, float],
    samples: int = 3000,
    min_qualify: int = MIN_QUALIFY_CATEGORY,
    rng: Optional[np.random.Generator] = None,
) -> list[dict]:
    """窮舉 32 種保留/丟棄組合，用向量化蒙地卡羅模擬估計每種組合「達到門票門檻」的機率
    與「期望牌型類別」，回傳依 (門票機率, 期望牌型) 排序後的結果列表（最佳排最前面）。

    card_weights: {"10H": 機率權重, ...} 通常來自 stats.get_card_probabilities()
    """
    if rng is None:
        rng = np.random.default_rng()

    assert len(current_cards) == 5

    pool, weights = _weighted_pool(current_cards, card_weights)
    pool_values = np.array([rank_value(c.rank) for c in pool])
    pool_suits = np.array([SUIT_CODE[c.suit] for c in pool])

    results = []
    for mask in range(32):
        held_idx = [i for i in range(5) if (mask >> i) & 1]
        held = [current_cards[i] for i in held_idx]
        n_draw = 5 - len(held)

        held_values = np.array([rank_value(c.rank) for c in held], dtype=np.int64)
        held_suits = np.array([SUIT_CODE.get(c.suit, -1) for c in held], dtype=np.int64)

        if n_draw == 0:
            category, _ = classify_hand(held)
            results.append({
                "mask": mask,
                "held_idx": held_idx,
                "p_qualify": 1.0 if category >= min_qualify else 0.0,
                "expected_category": float(category),
            })
            continue

        # 手中有鬼牌時向量化牌型判定會失效，改走較少次數的 python 模擬
        if any(c.rank == "JK" for c in held):
            n = min(samples, 400)
            qualify_count = 0
            cat_sum = 0.0
            for _ in range(n):
                pick = rng.choice(len(pool), size=n_draw, replace=False, p=weights)
                category, _ = classify_hand(held + [pool[int(i)] for i in pick])
                cat_sum += category
                if category >= min_qualify:
                    qualify_count += 1
            results.append({
                "mask": mask,
                "held_idx": held_idx,
                "p_qualify": qualify_count / n,
                "expected_category": cat_sum / n,
            })
            continue

        draw_idx = _weighted_sample_without_replacement_batch(weights, n_draw, samples, rng)  # (samples, n_draw)
        drawn_values = pool_values[draw_idx]  # (samples, n_draw)
        drawn_suits = pool_suits[draw_idx]

        held_values_b = np.broadcast_to(held_values, (samples, len(held)))
        held_suits_b = np.broadcast_to(held_suits, (samples, len(held)))
        full_values = np.concatenate([held_values_b, drawn_values], axis=1)
        full_suits = np.concatenate([held_suits_b, drawn_suits], axis=1)

        categories = classify_hand_batch(full_values, full_suits)
        p_qualify = float((categories >= min_qualify).mean())
        expected_category = float(categories.mean())

        results.append({
            "mask": mask,
            "held_idx": held_idx,
            "p_qualify": p_qualify,
            "expected_category": expected_category,
        })

    results.sort(key=lambda r: (r["p_qualify"], r["expected_category"]), reverse=True)
    return results
