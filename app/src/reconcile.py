"""把「已經玩過、但沒進到機率模型」的牌補回每日統計。

## 為什麼需要這個

`stats.DailyStats` 的機率估計靠 `card_counts` 吃資料，而那份計數只有在
`bot.py` 正常跑到「選牌階段 / 比大小階段」時才會 +1。實際上有好幾條路會漏：

* **exe 版與原始碼版各有一份 data/**。平常跑 `dist\\HoloTool\\HoloTool.exe`，
  它的統計寫在自己的 `app\\data\\`；偶爾用原始碼跑一次，那一份就完全看不到
  對面累積的資料。等於同一天的牌被拆成兩本帳，兩邊的機率都偏保守。
* **只開「顯示即時辨識」看畫面時**牌是認出來了，但沒有經過 `_handle_*`，
  一張都不會記。
* **程式中途被關掉／當掉**，那一輪的牌只留在 log 裡。

log 檔反而是最完整的：`[選牌階段] 手牌=(...)` 與 `[比大小階段] 目前牌=X`
每看到一次新的牌就會寫一行。所以「補算」= 拿 log 與其他 data 資料夾當作
真實來源，把差額補進統計檔。

## 兩個必須做到的性質

1. **可以重複按。** 按第二次要回報「無其餘資料」，不能每按一次就把同一批牌
   再加一遍 —— 那會讓機率模型嚴重偏斜，而且完全看不出來哪裡錯了。
   做法是在統計檔裡記帳：log 記「掃到第幾行」，外部 data 記「上次看到的計數
   快照」，下次只補快照之後的增量。
2. **只增不減。** 補算永遠不會把既有的計數改小。萬一 log 被清掉或外部資料夾
   被砍掉，統計檔裡的數字原樣保留。

## 不解析哪些 log 行

`偵測中 ... 手牌=3/5 比大小=6C` 這種每個 tick 都印一次的行**絕對不能算** ——
一張牌會在畫面上停留好幾秒，那樣算下來同一張牌會被記上十幾次。
只認 `[選牌階段]` / `[比大小階段]` 這兩種「狀態變了才印一次」的行。
"""
from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Optional

from .handeval import full_deck
from .paths import data_dir, log_dir, project_root
from .stats import DailyStats

VALID_LABELS = frozenset(c.label for c in full_deck())

# 只認「狀態變了才印一次」的那兩種行。
HAND_LINE = re.compile(r"\[選牌階段\][^\n]*?手牌=\(([^)]*)\)")
HIGHLOW_LINE = re.compile(r"\[比大小階段\][^\n]*?目前牌=([0-9AJQKahjqk]+[SHDCshdc])")
LABEL_TOKEN = re.compile(r"'([^']+)'|\"([^\"]+)\"")

LOG_NAME = re.compile(r"^bot_(\d{4}-\d{2}-\d{2})\.log$")


@dataclass
class ReconcileReport:
    """補算結果。`total_added == 0` 就是使用者要看到的「無其餘資料」。"""

    added: dict[str, int] = field(default_factory=dict)
    per_day: dict[str, int] = field(default_factory=dict)
    scanned_logs: list[str] = field(default_factory=list)
    merged_sources: list[str] = field(default_factory=list)
    skipped_sources: list[str] = field(default_factory=list)
    new_log_lines: int = 0

    @property
    def total_added(self) -> int:
        return sum(self.added.values())

    def summary(self) -> str:
        if self.total_added == 0:
            return "無其餘資料：所有已知的牌都已經算進機率模型了。"
        days = "、".join(f"{d} 補 {n} 張" for d, n in sorted(self.per_day.items()) if n)
        top = "、".join(
            f"{label}×{n}" for label, n in
            sorted(self.added.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
        )
        parts = [f"補進 {self.total_added} 張牌（{days}）"]
        if top:
            parts.append(f"最多的是 {top}")
        if self.merged_sources:
            parts.append(f"另外併入 {len(self.merged_sources)} 個資料夾的統計")
        return "；".join(parts) + "。"


# --------------------------------------------------------------- log 解析

def parse_log_cards(text: str, start_line: int = 0) -> tuple[list[str], int]:
    """從 log 文字裡挑出牌面標籤。

    `start_line` 之前的行整段跳過（已經算過了）。回傳 (標籤清單, 總行數)，
    總行數要存回統計檔當下次的起點。
    """
    labels: list[str] = []
    total = 0
    for index, line in enumerate(text.splitlines()):
        total = index + 1
        if index < start_line:
            continue
        hand = HAND_LINE.search(line)
        if hand:
            for match in LABEL_TOKEN.finditer(hand.group(1)):
                label = (match.group(1) or match.group(2) or "").strip().upper()
                if label in VALID_LABELS:
                    labels.append(label)
            continue
        highlow = HIGHLOW_LINE.search(line)
        if highlow:
            label = highlow.group(1).strip().upper()
            if label in VALID_LABELS:
                labels.append(label)
    return labels, total


def log_files_by_day() -> dict[str, str]:
    """logs/ 裡每一天對應的 log 檔路徑。"""
    out: dict[str, str] = {}
    for path in glob.glob(os.path.join(log_dir(), "bot_*.log")):
        match = LOG_NAME.match(os.path.basename(path))
        if match:
            out[match.group(1)] = path
    return out


# ------------------------------------------------------- 其他 data 資料夾

def candidate_data_dirs(cfg: Optional[dict] = None) -> list[str]:
    """找出「另一份安裝」的 data 資料夾。

    使用者平常跑 `dist\\HoloTool\\HoloTool.exe`，那一份的統計寫在
    `dist\\HoloTool\\app\\data`，跟原始碼版的 `app\\data` 是兩本帳。
    這裡把看得到的都列出來（自己那份會被排除）。

    `cfg["extra_data_dirs"]` 可以手動補上任何路徑 —— 裝到 Program Files
    或別顆硬碟的那份，程式沒辦法自己猜到。
    """
    root = project_root()
    mine = os.path.normcase(os.path.abspath(data_dir()))
    patterns = [
        os.path.join(root, "dist", "*", "app", "data"),   # 新版：exe 旁邊的 app\
        os.path.join(root, "dist", "*", "data"),           # 舊版：攤平在 exe 旁邊
        # 反過來的方向：從 exe 版看回原始碼版（exe 在 <root>\dist\HoloTool\app）
        os.path.join(root, "..", "..", "..", "data"),
    ]
    found: list[str] = []
    for pattern in patterns:
        for path in glob.glob(pattern):
            if not os.path.isdir(path):
                continue
            absolute = os.path.abspath(path)
            if os.path.normcase(absolute) == mine:
                continue
            if absolute not in found:
                found.append(absolute)
    for extra in (cfg or {}).get("extra_data_dirs") or []:
        absolute = os.path.abspath(str(extra))
        if os.path.isdir(absolute) and os.path.normcase(absolute) != mine \
                and absolute not in found:
            found.append(absolute)
    return found


def _read_counts(path: str) -> dict[str, int]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    counts = data.get("card_counts") or {}
    return {k: int(v) for k, v in counts.items()
            if k in VALID_LABELS and isinstance(v, (int, float)) and v > 0}


# --------------------------------------------------------------- 記帳

def _bookkeeping(stats: DailyStats) -> dict:
    book = stats.data.setdefault("reconcile", {})
    book.setdefault("logs", {})
    book.setdefault("merged", {})
    return book


def _apply(stats: DailyStats, labels: Iterable[str]) -> dict[str, int]:
    """把標籤加進 card_counts（不存檔，由呼叫端統一存）。"""
    added: dict[str, int] = {}
    counts = stats.data.setdefault("card_counts", {})
    for label in labels:
        counts[label] = int(counts.get(label, 0)) + 1
        added[label] = added.get(label, 0) + 1
    return added


def reconcile_day(day: str, cfg: Optional[dict] = None,
                  report: Optional[ReconcileReport] = None) -> ReconcileReport:
    """補算某一天。回傳（或就地更新）報告。"""
    report = report or ReconcileReport()
    stats = DailyStats(day)
    book = _bookkeeping(stats)
    day_added = 0
    # 沒有任何變動就不要存檔。往回掃七天時，那些完全沒玩過的日子若也生出一個
    # 空的 stats 檔，會把 `_load_recent_history` 的 history_days 額度吃掉 ——
    # 機率模型能看到的真實歷史反而變短。
    dirty = False

    # 1) log：只讀上次沒讀完的行
    log_path = log_files_by_day().get(day)
    if log_path:
        name = os.path.basename(log_path)
        seen = int((book["logs"].get(name) or {}).get("lines") or 0)
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            text = ""
        labels, total_lines = parse_log_cards(text, start_line=seen)
        if total_lines < seen:
            # log 被清空或換過（行數變少了）。從頭重掃會把舊資料再加一次，
            # 所以只把游標移到新的結尾，這一輪不補任何東西。
            book["logs"][name] = {"lines": total_lines}
            dirty = True
        elif labels or total_lines != seen:
            book["logs"][name] = {"lines": total_lines}
            dirty = True
            report.new_log_lines += max(0, total_lines - seen)
            if labels:
                for label, count in _apply(stats, labels).items():
                    report.added[label] = report.added.get(label, 0) + count
                day_added += len(labels)
            if name not in report.scanned_logs:
                report.scanned_logs.append(name)

    # 2) 其他 data 資料夾的同一天統計檔
    for folder in candidate_data_dirs(cfg):
        source = os.path.join(folder, f"stats_{day}.json")
        if not os.path.exists(source):
            continue
        counts = _read_counts(source)
        if not counts:
            continue
        snapshot = book["merged"].get(source) or {}
        delta: dict[str, int] = {}
        for label, value in counts.items():
            gap = value - int(snapshot.get(label, 0))
            if gap > 0:
                delta[label] = gap
        # 快照一律更新成目前值，即使沒有增量 —— 否則對面把統計改小之後
        # 會一直被當成「還有增量」。
        if counts != snapshot:
            book["merged"][source] = counts
            dirty = True
        if delta:
            for label, count in delta.items():
                stats.data["card_counts"][label] = \
                    int(stats.data["card_counts"].get(label, 0)) + count
                report.added[label] = report.added.get(label, 0) + count
                day_added += count
            if source not in report.merged_sources:
                report.merged_sources.append(source)
        elif source not in report.skipped_sources:
            report.skipped_sources.append(source)

    report.per_day[day] = report.per_day.get(day, 0) + day_added
    # 記帳本身也要落地，否則下次又會把同一批當成新資料
    if dirty:
        stats.save()
    return report


def reconcile(days: int = 7, cfg: Optional[dict] = None,
              today: Optional[str] = None) -> ReconcileReport:
    """補算今天與前 `days - 1` 天。

    往回補是有意義的：機率模型會把最近幾天的紀錄當成弱先驗（`history_weight`），
    所以前幾天漏掉的資料補回去之後，今天的估計也會更貼近實際牌堆。
    """
    base = date.fromisoformat(today) if today else date.today()
    report = ReconcileReport()
    for offset in range(max(1, days)):
        reconcile_day((base - timedelta(days=offset)).isoformat(), cfg, report)
    return report
