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
