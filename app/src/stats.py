"""每日出牌統計紀錄，以及機率估計（貝氏平滑：均勻先驗 + 近期歷史 + 今日即時資料）。

設計理念：
- 一開始資料不足時，退回標準 52 張牌均勻機率，行為等同傳統撲克機率策略。
- 隨著今天記錄的牌越多，機率估計會逐漸貼近「今天實際觀察到的出牌分布」。
- 過去幾天的歷史紀錄僅作為很弱的輔助先驗，權重遠低於今天的即時資料。
"""
from __future__ import annotations

import glob
import json
import os
from datetime import date
from typing import Optional

from .handeval import RANKS, SUITS, full_deck
from .paths import data_dir

DATA_DIR = data_dir()

ALL_LABELS = [c.label for c in full_deck()]


def _today_str() -> str:
    return date.today().isoformat()


def _stats_path(day: str) -> str:
    return os.path.join(DATA_DIR, f"stats_{day}.json")


def _empty_stats() -> dict:
    return {
        "date": _today_str(),
        "card_counts": {label: 0 for label in ALL_LABELS},
        "rounds_started": 0,
        "tickets_qualified": 0,
        "tickets_failed": 0,
        "highlow_rounds": 0,
        "highlow_wins": 0,
        "highlow_losses": 0,
        "events": [],  # 詳細事件紀錄，方便事後除錯與分析
    }


class DailyStats:
    def __init__(self, day: Optional[str] = None):
        self.day = day or _today_str()
        os.makedirs(DATA_DIR, exist_ok=True)
        self.path = _stats_path(self.day)
        self.data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            base = _empty_stats()
            base.update(loaded)
            for label in ALL_LABELS:
                base["card_counts"].setdefault(label, 0)
            return base
        return _empty_stats()

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def record_card(self, label: str) -> None:
        if label in self.data["card_counts"]:
            self.data["card_counts"][label] += 1
            self.save()

    def record_event(self, kind: str, detail: dict) -> None:
        self.data["events"].append({"kind": kind, "detail": detail})
        # 事件列表僅保留最近 500 筆，避免檔案無限膨脹
        self.data["events"] = self.data["events"][-500:]
        self.save()

    def bump(self, field: str, delta: int = 1) -> None:
        self.data[field] = self.data.get(field, 0) + delta
        self.save()

    # ---------- 機率估計 ----------

    def get_card_probabilities(
        self,
        uniform_alpha: float = 1.0,
        history_weight: float = 0.3,
        history_days: int = 7,
    ) -> dict[str, float]:
        """回傳 52 張牌各自的估計機率（總和為1）。"""
        counts = {label: float(uniform_alpha) for label in ALL_LABELS}

        # 近期歷史（弱先驗）
        if history_weight > 0:
            hist = self._load_recent_history(history_days)
            for label, c in hist.items():
                counts[label] += c * history_weight

        # 今天的即時觀察（權重最高，最貼近當下的實際分布）
        for label, c in self.data["card_counts"].items():
            counts[label] += c

        total = sum(counts.values())
        return {label: (v / total) for label, v in counts.items()}

    def get_rank_probabilities(self, **kwargs) -> dict[str, float]:
        """把 52 張牌的機率依點數(不分花色)加總，用於比大小的高低機率計算。"""
        card_probs = self.get_card_probabilities(**kwargs)
        rank_probs: dict[str, float] = {r: 0.0 for r in RANKS}
        for label, p in card_probs.items():
            rank = label[:-1]
            rank_probs[rank] += p
        return rank_probs

    def _load_recent_history(self, days: int) -> dict[str, float]:
        agg: dict[str, float] = {label: 0.0 for label in ALL_LABELS}
        files = sorted(glob.glob(os.path.join(DATA_DIR, "stats_*.json")), reverse=True)
        used = 0
        for path in files:
            fname = os.path.basename(path)
            if fname == os.path.basename(self.path):
                continue  # 跳過今天自己的檔案，避免重複計算
            try:
                with open(path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                for label, c in d.get("card_counts", {}).items():
                    if label in agg:
                        agg[label] += c
                used += 1
            except Exception:
                continue
            if used >= days:
                break
        return agg
