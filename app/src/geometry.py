"""比例座標 <-> 像素座標的轉換工具。

設計理念：
config.json 裡儲存的所有區域與按鈕座標都是「比例值 (0~1)」，代表
「相對於遊戲視窗用戶端寬/高的百分比位置」，例如 x=0.5 就是水平正中央。

這樣做的好處是視窗縮放後座標依然有效，只要「長寬比」沒有改變即可
（長寬比一改變，遊戲本身的 UI 排版通常也會跑掉，比例就不再對應）。
"""
from __future__ import annotations


def ratio_region_to_pixels(region: dict, client_width: int, client_height: int) -> tuple[int, int, int, int]:
    """把比例區域換算成相對於視窗用戶端左上角的像素 (x, y, w, h)。"""
    x = round(region["x"] * client_width)
    y = round(region["y"] * client_height)
    w = max(1, round(region["w"] * client_width))
    h = max(1, round(region["h"] * client_height))
    return x, y, w, h


def ratio_point_to_pixels(point: dict, client_width: int, client_height: int) -> tuple[int, int]:
    """把比例座標點換算成相對於視窗用戶端左上角的像素 (x, y)。"""
    return round(point["x"] * client_width), round(point["y"] * client_height)


def pixels_region_to_ratio(x: int, y: int, w: int, h: int, client_width: int, client_height: int) -> dict:
    """把像素區域換算成比例區域（校準時使用）。"""
    if client_width <= 0 or client_height <= 0:
        raise ValueError("視窗用戶端尺寸無效，無法換算比例座標")
    return {
        "x": x / client_width,
        "y": y / client_height,
        "w": w / client_width,
        "h": h / client_height,
    }


def pixels_point_to_ratio(x: int, y: int, client_width: int, client_height: int) -> dict:
    """把像素座標點換算成比例座標（校準時使用）。"""
    if client_width <= 0 or client_height <= 0:
        raise ValueError("視窗用戶端尺寸無效，無法換算比例座標")
    return {"x": x / client_width, "y": y / client_height}


def is_ratio_value(value: float) -> bool:
    """比例值應該落在 0~1 之間（允許少量誤差）。用來偵測舊版的像素座標設定檔。"""
    return -0.001 <= value <= 1.001


def aspect_ratio_delta(current_w: int, current_h: int, reference_w: int, reference_h: int) -> float:
    """回傳目前長寬比與校準時長寬比的相對差異（0 代表完全相同，0.05 代表差 5%）。"""
    if current_h <= 0 or reference_h <= 0 or reference_w <= 0:
        return 0.0
    current = current_w / current_h
    reference = reference_w / reference_h
    return abs(current - reference) / reference


def scale_factor(current_w: int, reference_w: int) -> float:
    """回傳目前視窗相對於校準時的縮放倍率（1.0 代表大小相同）。"""
    if reference_w <= 0:
        return 1.0
    return current_w / reference_w


# ===================================================================
#  內容框（content box）
# ===================================================================
#
# 遊戲**不是**把 UI 依視窗重排，而是把一個固定 16:9 的「內容框」置中放進
# 用戶端矩形，所有 UI 都排在那個框裡；只有背景圖是拉滿整個視窗的。
# 因為背景填滿了，看起來沒有黑邊，很容易誤以為是重排。
#
# 2026-08-21 用三種比例的實機截圖量出來的證據（把座標換算到內容框座標後）：
#
#     五格手牌      16:9 (1474x829)                4:3 (1269x952)            差
#     牌0    0.0725 0.3112 0.1479 0.3703    0.0725 0.3109 0.1481 0.3712   0.0009
#     牌4    0.7809 0.3112 0.1479 0.3703    0.7809 0.3109 0.1481 0.3712   0.0009
#
# 一致到 1 個像素。使用者手工校準的 21:9 換算過去也對到 0.008（手框的誤差範圍）。
#
# 樣板縮放倍率同樣只跟內容框的**高度**有關，跟視窗寬度無關：
#
#     畫面              實測倍率   內容框高度比   舊公式（視窗寬度比）
#     16:9 1474x829      1.894       1.893          1.439  ← 差 24%
#     4:3  1269x952      1.632       1.630          1.239  ← 差 24%
#     21:9 1367x574      1.308       1.311          1.335  ← 差 2%（剛好接近才沒出事）
#
# 這就是為什麼一換比例，畫面標記的分數就整排掉下來。
DESIGN_ASPECT = 16.0 / 9.0


def content_box(client_width: int, client_height: int) -> tuple[float, float, float, float]:
    """回傳內容框在用戶端矩形裡的位置 (offset_x, offset_y, width, height)。

    視窗比 16:9 寬 → 左右留白（內容框高度 = 視窗高度）。
    視窗比 16:9 窄 → 上下留白（內容框寬度 = 視窗寬度）。
    """
    if client_width <= 0 or client_height <= 0:
        # 尺寸無效時回全 0，而不是把 client 尺寸原樣回傳 —— 後者會讓
        # content_height(0, 100) 回 100，看起來像個合法的內容框高度，
        # 然後樣板倍率就用了一個憑空生出來的值。
        return 0.0, 0.0, 0.0, 0.0
    box_h = min(float(client_height), client_width / DESIGN_ASPECT)
    box_w = box_h * DESIGN_ASPECT
    return (client_width - box_w) / 2.0, (client_height - box_h) / 2.0, box_w, box_h


def content_height(client_width: int, client_height: int) -> float:
    """內容框的高度。畫面標記樣板的縮放倍率只跟這個值成正比。"""
    return content_box(client_width, client_height)[3]


def region_client_to_content(region: dict, client_width: int, client_height: int) -> dict:
    """把「相對於視窗」的比例區域換算成「相對於內容框」的比例區域。"""
    ox, oy, box_w, box_h = content_box(client_width, client_height)
    if box_w <= 0 or box_h <= 0:
        return dict(region)
    out = {
        "x": (float(region["x"]) * client_width - ox) / box_w,
        "y": (float(region["y"]) * client_height - oy) / box_h,
    }
    if "w" in region:
        out["w"] = float(region["w"]) * client_width / box_w
        out["h"] = float(region["h"]) * client_height / box_h
    return out


def region_content_to_client(region: dict, client_width: int, client_height: int) -> dict:
    """把「相對於內容框」的比例區域換算回「相對於視窗」的比例區域。"""
    ox, oy, box_w, box_h = content_box(client_width, client_height)
    if client_width <= 0 or client_height <= 0:
        return dict(region)
    out = {
        "x": (float(region["x"]) * box_w + ox) / client_width,
        "y": (float(region["y"]) * box_h + oy) / client_height,
    }
    if "w" in region:
        out["w"] = float(region["w"]) * box_w / client_width
        out["h"] = float(region["h"]) * box_h / client_height
    return out


def retarget(region: dict, from_w: int, from_h: int, to_w: int, to_h: int) -> dict:
    """把在 from 尺寸下校準的比例座標，換算成 to 尺寸下正確的比例座標。

    中途經過內容框座標，所以長寬比不同也精確 —— 這是「一組校準通吃所有比例」
    的全部祕密。同一個長寬比時等於原值（只是浮點誤差級別的差異）。
    """
    return region_content_to_client(
        region_client_to_content(region, from_w, from_h), to_w, to_h)


def retarget_bottom_anchored(point: dict, from_w: int, from_h: int,
                             to_w: int, to_h: int) -> dict:
    """對話框最下面那排動作按鈕用的換算：x 照內容框，**y 貼視窗底**。

    遊戲裡不是所有東西都排在內容框裡。牌桌 UI（logo、手牌、大／小）在內容框，
    但對話框底部那排按鈕（取消／進行挑戰／再一次）是**釘在視窗底部**的。

    用同一組 16:9→4:3 的轉換去預測每個地標，誤差說得很清楚：

        地標              內容框預測誤差   貼視窗底預測誤差
        logo 上緣              0.8 px          117.3 px
        卡片上下緣             0.0 px          118.1 px
        大／小膠囊中心         0.3 / 0.6 px    118.4 px
        取消 上緣            118.4 px            0.3 px   ← 貼底
        進行挑戰 中心        111.0 px            7.1 px   ← 貼底

    離窗底的距離會跟著內容框一起縮放（UI 整體縮放），所以換算方式是
    「把離底距離換算成內容框單位，再套到新視窗」。
    """
    _ox_f, _oy_f, box_w_f, box_h_f = content_box(from_w, from_h)
    ox_t, _oy_t, box_w_t, box_h_t = content_box(to_w, to_h)
    if box_h_f <= 0 or to_w <= 0 or to_h <= 0:
        return dict(point)

    # x 走內容框
    content_x = (float(point["x"]) * from_w - _ox_f) / box_w_f if box_w_f else 0.0
    new_x = (content_x * box_w_t + ox_t) / to_w

    # y 量的是「離視窗底部多遠」，再依內容框縮放比放大縮小
    scale = box_h_t / box_h_f
    distance = from_h - float(point["y"]) * from_h
    new_y = (to_h - distance * scale) / to_h

    out = {"x": new_x, "y": new_y}
    if "w" in point:
        out["w"] = float(point["w"]) * box_w_t / to_w * (from_w / box_w_f) \
            if box_w_f else point["w"]
        out["h"] = float(point["h"]) * scale * from_h / to_h
    return out


def clamp_into_window(item: dict) -> dict:
    """把換算後的比例座標夾回視窗範圍內。

    換算本身是精確的，但**來源可能一開始就是錯的**。實例：`max_win_retry`
    在 21:9 的值 x=0.883 其實落在內容框外面（那個點從來沒被驗證過，是從一張
    對話框截圖目測估的），換算到 16:9 就變成 x=1.0107 —— 螢幕外。

    點擊螢幕外的座標在 Windows 上不會報錯，只是什麼都不會發生，然後就是
    一個查不出原因的「按了沒反應」。夾回來至少會落在視窗邊緣，看得出不對。
    """
    out = dict(item)
    if "w" in out and "h" in out:
        out["w"] = max(0.0, min(float(out["w"]), 1.0))
        out["h"] = max(0.0, min(float(out["h"]), 1.0))
        out["x"] = max(0.0, min(float(out["x"]), 1.0 - out["w"]))
        out["y"] = max(0.0, min(float(out["y"]), 1.0 - out["h"]))
    else:
        out["x"] = max(0.0, min(float(out["x"]), 1.0))
        out["y"] = max(0.0, min(float(out["y"]), 1.0))
    return out


def clamp_window_to_screen(x, y, width: int, height: int,
                           screen_w: int, screen_h: int) -> tuple[int, int]:
    """把視窗左上角座標夾回螢幕範圍內，讓整個視窗都看得到。

    給迷你懸浮視窗用（`src/minipanel.py`）。它沒有標題列也不出現在工作列，
    位置一旦落在畫面外就完全找不回來 —— 而那時主視窗已經被收起來了。
    存下來的座標很可能是在另一台螢幕或更大的解析度下記的，所以每次顯示前都要夾一次。

    刻意放在 geometry 而不是 minipanel：這裡沒有 tkinter 依賴，
    在沒有 GUI 的環境（CI、純文字的機器）也測得到。
    """
    max_x = max(0, screen_w - width)
    max_y = max(0, screen_h - height)
    return max(0, min(int(x), max_x)), max(0, min(int(y), max_y))
