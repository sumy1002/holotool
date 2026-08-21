"""校準的「圖示提示」：每一項要框到哪裡，用一張範例圖講清楚。

## 問題

校準的文字說明再怎麼寫都會有歧義。「框選左上角 High & Low 標題」——
要不要含外框？含不含那個問號按鈕？框大一點會不會比較保險？
使用者只能猜，猜錯了就是辨識分數莫名偏低，而且完全看不出原因。

## 做法

兩種提示同時給：

1. **範例圖**（這個模組）：`defaults/ref/*.jpg` 是六張實機畫面的乾淨縮圖，
   把這一項的**預設框**畫在上面、框外壓暗。玩家一眼就看到「喔，是框這麼大」。
   範例圖顯示在框選遮罩的角落，半透明，不會擋到瞄點。
2. **實機建議框**（`gui.py` 算好之後交給 `overlay.py` 畫）：直接在遊戲畫面上
   對應的位置畫出建議的框／十字，玩家可以照著描，或是覺得不準就自己重框。

## 座標對得起來嗎？

`defaults/ref/*.jpg` 是從 1024x438 的實機截圖縮出來的，長寬比 2.338；
`defaults_layout.SCREENSHOT_LAYOUT` 的比例值實測畫在這幾張圖上完全對得上
（logo、五格手牌、各按鈕逐項目視確認過），所以**比例值直接乘上範例圖尺寸**
就好，不需要再做內容框換算。範例圖只是示意，1~2 像素的差異無關緊要。

`max_win_marker` / `max_win_retry` 沒有範例圖 —— 那個畫面（已達最高獲得金額）
（2026-08-21 起這兩項已經有內建樣板與量測過的座標，正常情況不需要自己校準）
從頭到尾只存在於對話紀錄裡，拿不到像素。這兩項只會有實機建議框。
"""
from __future__ import annotations

import os
from typing import Optional

from .defaults_layout import SCREENSHOT_LAYOUT
from .paths import project_root

# 範例圖的尺寸（寬, 高）。來源是 1024x438 的實機截圖，等比縮小。
REF_SIZE = (640, 274)

# 校準項目的 path → (範例圖檔名不含副檔名, 這一項出現在哪個畫面)
#
# 同一張畫面會被好幾個項目共用，例如「投注並開始」那張同時是牌桌標記、
# 五格手牌與投注按鈕的範例。
REF_FOR_PATH: dict[str, str] = {
    "regions.table_marker": "start",
    "points.start_round": "start",
    **{f"regions.card_slots.{i}": "start" for i in range(5)},
    "regions.draw_prompt": "draw",
    **{f"points.hold_toggles.{i}": "draw" for i in range(5)},
    "points.draw_confirm": "draw",
    "regions.congrats_marker": "congrats",
    "points.click_continue": "congrats",
    "regions.challenge_marker": "challenge",
    "points.cashout_button": "challenge",
    "points.challenge_button": "challenge",
    "regions.highlow_card": "highlow",
    "points.high_button": "highlow",
    "points.low_button": "highlow",
    "regions.poker_fail_marker": "fail",
    "points.retry_button": "fail",
    "regions.fail_marker": "fail",
    # max_win_marker / max_win_retry 沒有可用的截圖，刻意不列。
}

# 每張範例圖對應的畫面名稱，顯示在範例圖上方
REF_TITLES: dict[str, str] = {
    "start": "投注並開始",
    "draw": "選擇要保留的牌",
    "congrats": "過關（Congratulations）",
    "challenge": "翻倍對話框",
    "highlow": "比大小",
    "fail": "失敗／再玩一次",
}

# 框外壓暗的程度。壓太多看不出畫面在哪，壓太少框不明顯。
DIM = 0.42

REGION_COLOR = (255, 232, 64)
POINT_COLOR = (96, 224, 255)


def ref_dir() -> str:
    return os.path.join(project_root(), "defaults", "ref")


def ref_key_for(path: str) -> Optional[str]:
    return REF_FOR_PATH.get(path)


def ref_path_for(path: str) -> Optional[str]:
    """回傳這一項的範例圖檔案路徑；沒有範例圖或檔案不存在時回 None。"""
    key = ref_key_for(path)
    if not key:
        return None
    candidate = os.path.join(ref_dir(), f"{key}.jpg")
    return candidate if os.path.exists(candidate) else None


def default_value(path: str) -> Optional[dict]:
    """從內建預設框選裡取這一項的座標（比例值）。找不到時回 None。"""
    node = SCREENSHOT_LAYOUT
    for part in path.split("."):
        if isinstance(node, list):
            if not part.isdigit() or int(part) >= len(node):
                return None
            node = node[int(part)]
        elif isinstance(node, dict):
            if part not in node:
                return None
            node = node[part]
        else:
            return None
    return node if isinstance(node, dict) and "x" in node else None


def suggested_value(cfg: dict, path: str) -> Optional[dict]:
    """建議框：使用者已經校準過就用他自己的值，否則退回內建預設。

    用他自己的值是刻意的 —— 重新校準通常是「上次框得不夠好，想微調」，
    這時候把上次的框畫出來最有用；跳回內建預設反而是把他的成果藏起來。
    """
    node = cfg
    try:
        for part in path.split("."):
            node = node[int(part)] if part.isdigit() else node[part]
    except (KeyError, IndexError, TypeError, ValueError):
        node = None
    if isinstance(node, dict):
        if "w" in node and "h" in node:
            if node.get("w", 0) > 0 and node.get("h", 0) > 0:
                return node
        elif node.get("x") or node.get("y"):
            return node
    return default_value(path)


def kind_of(path: str) -> str:
    return "region" if path.startswith("regions.") else "point"


def example_image(path: str, width: int = 0):
    """畫出這一項的範例圖（PIL RGB Image）。沒有範例圖時回 None。

    框內保持原亮度、框外壓暗，所以「要框的範圍」是畫面上唯一亮的地方。
    """
    ref = ref_path_for(path)
    value = default_value(path)
    if ref is None or value is None:
        return None

    from PIL import Image, ImageDraw

    image = Image.open(ref).convert("RGB")
    w, h = image.size
    kind = kind_of(path)

    radius = max(14, round(w * 0.035))
    if kind == "region":
        box = (
            round(value["x"] * w),
            round(value["y"] * h),
            round((value["x"] + value.get("w", 0)) * w),
            round((value["y"] + value.get("h", 0)) * h),
        )
    else:
        cx, cy = round(value["x"] * w), round(value["y"] * h)
        box = (cx - radius, cy - radius, cx + radius, cy + radius)

    # 貼回原圖之前一定要夾進畫面內：貼到邊界外的部分 crop() 會補黑，
    # 於是「亮起來的那一塊」反而變成一片黑，提示等於反過來。
    clipped = (
        max(0, min(w - 1, box[0])), max(0, min(h - 1, box[1])),
        max(1, min(w, box[2])), max(1, min(h, box[3])),
    )

    # 壓暗整張，再把框內原圖貼回去
    dark = Image.eval(image, lambda v: int(v * DIM))
    if clipped[2] > clipped[0] and clipped[3] > clipped[1]:
        dark.paste(image.crop(clipped), clipped[:2])
    draw = ImageDraw.Draw(dark)

    if kind == "region":
        draw.rectangle(box, outline=REGION_COLOR, width=3)
    else:
        cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
        arm = radius * 2      # 十字比圓圈長一點，遠遠看也知道中心在哪
        draw.ellipse(box, outline=POINT_COLOR, width=3)
        draw.line([cx - arm, cy, cx + arm, cy], fill=POINT_COLOR, width=2)
        draw.line([cx, cy - arm, cx, cy + arm], fill=POINT_COLOR, width=2)

    if width and width != w:
        dark = dark.resize((width, max(1, round(h * width / w))))
    return dark


def caption_for(path: str) -> str:
    """範例圖上方那行說明。"""
    key = ref_key_for(path)
    screen = REF_TITLES.get(key or "", "")
    what = "要框選的範圍" if kind_of(path) == "region" else "要點擊的位置"
    return f"範例：「{screen}」畫面 — {what}" if screen else f"範例：{what}"
