"""比大小的目標是「衝到 12800 上限」，不是「期望硬幣數最大」。

## 實機事故（2026-08-22 00:19）

    [翻倍對話] 進行挑戰（目前連勝 0）
    [比大小階段] 目前牌=8H 建議=HIGH 預估勝率=47.1% (連續第 1 次)
    [翻倍對話] 取消兌現（預估下一手勝率 47.1%）

連 400 都不到就收手了。使用者的目標講得很清楚：
**程式的目標只有一個，就是完成 12800、每天兩次。**
那是全有全無的目標 —— 中途兌現這一輪就永遠到不了 12800。

## 兩個各自獨立的成因

### 1. 勝率的分母算錯：平手不該算在裡面

同點數會**重抽、不計勝負**，所以平手那一塊機率既不算贏也不算輸。
舊算法直接回 `p_higher`（分母含平手），系統性低估：

| 手上的牌 | 上面 | 下面 | 平手 | 舊算法 | 正確（有勝負時） |
|---|---|---|---|---|---|
| 8 | 6/13 | 6/13 | 1/13 | **0.462** | **0.500** |
| 7 | 7/13 | 5/13 | 1/13 | 0.538 | 0.583 |

一副均勻的牌裡，舊算法**只有 8 這一個點數會掉到 0.5 以下** ——
實機那行正好就是 8H。（用當天觀察到的頻率時，7 或 9 也可能踩線。）

### 2. 預設策略跟目標互相矛盾

`highlow_min_win_prob_to_continue = 0.5` 是在最大化「期望硬幣數」：
勝率不到五成就落袋為安。那在賭場邏輯下是對的，但這個工具要的是上限，
不是硬幣。預設改成 **0 = 永遠不收手**；`highlow_max_chain` 也改成
**0 = 不設上限**（遊戲自己會在 12800 喊停）。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.config import DEFAULT_CONFIG, RETUNED_ON_UPGRADE  # noqa: E402
from src.strategy import decide_high_or_low, should_continue_highlow  # noqa: E402


class _Stats:
    """一副均勻的牌。"""

    def __init__(self, probs=None):
        self.probs = probs

    def get_rank_probabilities(self):
        if self.probs is not None:
            return self.probs
        ranks = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
        return {r: 1 / len(ranks) for r in ranks}


class TestWinProbabilityExcludesTies(unittest.TestCase):
    def test_a_middle_card_is_a_fair_coin_not_a_losing_bet(self):
        """8 是公平硬幣。舊算法回 0.462，讓它看起來像個爛賭注。"""
        decision = decide_high_or_low("8", _Stats())
        self.assertAlmostEqual(decision.win_prob, 0.5, places=3)

    def test_an_off_centre_card_beats_the_old_number(self):
        for rank, expected in (("7", 7 / 12), ("9", 7 / 12), ("2", 12 / 12)):
            with self.subTest(rank=rank):
                self.assertAlmostEqual(decide_high_or_low(rank, _Stats()).win_prob,
                                       expected, places=3)

    def test_the_side_with_more_room_is_still_the_one_chosen(self):
        self.assertEqual(decide_high_or_low("2", _Stats()).choice, "high")
        self.assertEqual(decide_high_or_low("A", _Stats()).choice, "low")

    def test_never_reports_more_than_one(self):
        """分母只剩「有勝負」的那一塊，很容易不小心算出 >1。"""
        for rank in ("2", "5", "8", "10", "K", "A"):
            self.assertLessEqual(decide_high_or_low(rank, _Stats()).win_prob, 1.0)

    def test_a_degenerate_distribution_does_not_divide_by_zero(self):
        """今天只看過一種點數，而且就是手上這張 —— 分母會是 0。"""
        decision = decide_high_or_low("8", _Stats({"8": 1.0}))
        self.assertEqual(decision.win_prob, 0.5)


class TestDefaultPolicyChasesTheCap(unittest.TestCase):
    def test_the_defaults_never_cash_out(self):
        self.assertEqual(DEFAULT_CONFIG["highlow_min_win_prob_to_continue"], 0.0)
        self.assertEqual(DEFAULT_CONFIG["highlow_max_chain"], 0)

    def test_it_keeps_going_even_on_a_bad_card(self):
        cfg = DEFAULT_CONFIG
        for chain in (0, 1, 5, 12, 40):
            self.assertTrue(should_continue_highlow(0.05, chain, cfg),
                            f"連勝 {chain} 次時收手了 —— 這一輪就到不了 12800")

    def test_the_exact_situation_from_the_log(self):
        """8H、勝率 0.471（舊算法的數字）也不可以收手。"""
        self.assertTrue(should_continue_highlow(0.471, 1, DEFAULT_CONFIG))

    def test_old_style_settings_still_work(self):
        """想回到「賺硬幣」的玩法：門檻 0.5、上限 6。"""
        cfg = {"highlow_min_win_prob_to_continue": 0.5, "highlow_max_chain": 6}
        self.assertFalse(should_continue_highlow(0.471, 1, cfg))
        self.assertTrue(should_continue_highlow(0.58, 1, cfg))
        self.assertFalse(should_continue_highlow(0.99, 6, cfg),
                         "連勝上限要擋得住")

    def test_zero_cap_means_no_cap_not_stop_immediately(self):
        """`chain >= 0` 一定成立 —— 把 0 當成一般上限會變成「一次都不翻」。"""
        cfg = {"highlow_min_win_prob_to_continue": 0.0, "highlow_max_chain": 0}
        self.assertTrue(should_continue_highlow(0.5, 0, cfg))
        self.assertTrue(should_continue_highlow(0.5, 99, cfg))

    def test_missing_keys_fall_back_to_chasing_the_cap(self):
        self.assertTrue(should_continue_highlow(0.1, 3, {}))


class TestUpgradeForcesTheNewPolicy(unittest.TestCase):
    def test_old_config_files_get_the_new_defaults(self):
        """只改預設值沒有用 —— `_deep_merge` 會讓使用者設定檔裡的 0.5 / 6 贏。

        這正是 `match_threshold` 卡在 0.83 很久的那個坑。
        """
        forced = RETUNED_ON_UPGRADE.get(8) or ()
        self.assertIn("highlow_min_win_prob_to_continue", forced)
        self.assertIn("highlow_max_chain", forced)


if __name__ == "__main__":
    unittest.main()
