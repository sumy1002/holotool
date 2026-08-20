"""每種視窗長寬比各存一組校準（calibration profile）。

## 為什麼需要這個

`config.json` 裡所有座標都是「相對於視窗用戶端寬/高的比例」，所以視窗**等比例**
放大縮小完全沒問題，長寬比一改變就全部跑掉。

一開始以為遊戲是「依視窗重排 UI」（使用者的描述是「背景會拉寬，UI 可能會改位子」），
那種情況沒有數學解，只能每種比例各校準一次。**2026-08-21 量了實機截圖之後發現不是。**

真相是：遊戲把一個固定 **16:9 的內容框置中**放進用戶端矩形，UI 全部排在那個框裡，
只有**背景圖**拉滿整個視窗 —— 因為背景填滿了，看起來沒有黑邊，才會像是重排。
（唯一的例外是對話框最下面那排動作按鈕，它們釘在視窗底部；見
`geometry.retarget_bottom_anchored`。）

所以一組校準就能**精確算出**其他所有比例（`derive()`），實測誤差 1~4 像素。
這個模組於是變成兩件事：

1. 一個 cache：算過的比例存起來，切換時直接取用；
2. 一個 override 機制：換算出來的如果還是有偏差，使用者可以在那個比例下重新
   框選覆蓋掉，而且**不會影響其他比例的校準**。

`derived_from` 標記「這組是算出來的」，使用者親手校準過就會清掉。

## 資料長相

```json
"active_profile": "21:9",
"calibration_profiles": [
  {
    "label": "21:9",
    "client_width": 1843,
    "client_height": 778,
    "seeded_from": null,          // 從別的比例複製來、還沒真正校準過
    "derived_from": "21:9",       // 從 21:9 換算出來的（精確，但沒人工確認）
    "regions": { ... },
    "points":  { ... }
  }
]
```

`regions` / `points` 這兩個頂層欄位仍然存在，**它是「目前生效的那一組」的工作副本**。
程式其他地方（capture / recognize / bot / GUI 校準）完全不用改讀取方式，
只有在「視窗比例變了」的時候由這裡把對應的 profile 複製進去。

## 三條不可以違反的規則

1. **絕對不動使用者沒在編輯的 profile。** 存檔只會寫進 `active_profile` 指到的那一組。
2. **換算出新的一組時，來源那一組毫髮無傷。** 使用者在 16:9 隨手微調一個框，
   絕對不能波及辛苦校準好的 21:9 —— 這正是這個專案最痛的那個坑。
3. **遷移舊設定檔時，座標值一個都不准改。** 只是把它們搬進 profile 裡。
"""
from __future__ import annotations

import copy
from typing import Any, Optional

from .geometry import clamp_into_window, retarget, retarget_bottom_anchored

# 常見的長寬比。實機量到的數字不會剛好整除（1843×778 = 2.3688，21:9 = 2.3333），
# 所以是「最接近且誤差在 LABEL_TOLERANCE 以內」才套用這個好唸的名字。
CANONICAL_RATIOS: tuple[tuple[str, float], ...] = (
    ("5:4", 5 / 4),
    ("4:3", 4 / 3),
    ("3:2", 3 / 2),
    ("16:10", 16 / 10),
    ("16:9", 16 / 9),
    ("21:9", 21 / 9),
    ("32:9", 32 / 9),
)

# 命名用的容許誤差：2.37 距離 21:9 (2.3333) 是 1.6%，要收進來；
# 2.00 距離最近的 16:9 (1.7778) 是 12.5%，要落到 "2.00:1" 這種通用名字。
LABEL_TOLERANCE = 0.04

# profile 數量上限。使用者只有三種比例，設 8 是為了「手殘拖到怪比例」時
# 還有餘裕，同時避免無上限累積。超過時淘汰最久沒用到的（不含目前生效的那組）。
MAX_PROFILES = 8

DEFAULT_TOLERANCE = 0.02

# 這些頂層 key 是執行期狀態，不寫進 config.json（save_config 會濾掉 "_" 開頭的 key）。
PENDING_LABEL_KEY = "_pending_profile_label"
PENDING_SIZE_KEY = "_pending_profile_size"


# --------------------------------------------------------------- 長寬比

def aspect_of(width: int, height: int) -> float:
    """回傳長寬比；尺寸無效時回 0.0（呼叫端要當成「不知道」處理）。"""
    if not width or not height or width <= 0 or height <= 0:
        return 0.0
    return width / height


def label_for(width: int, height: int) -> str:
    """把尺寸轉成好唸的比例名稱，例如 1843×778 -> "21:9"。"""
    aspect = aspect_of(width, height)
    if aspect <= 0:
        return "?"
    best_name, best_delta = None, None
    for name, ref in CANONICAL_RATIOS:
        delta = abs(aspect - ref) / ref
        if best_delta is None or delta < best_delta:
            best_name, best_delta = name, delta
    if best_delta is not None and best_delta <= LABEL_TOLERANCE:
        return str(best_name)
    return f"{aspect:.2f}:1"


def relative_delta(aspect_a: float, aspect_b: float) -> float:
    """兩個長寬比的相對差異。任一邊無效時回無限大（= 完全不匹配）。"""
    if aspect_a <= 0 or aspect_b <= 0:
        return float("inf")
    return abs(aspect_a - aspect_b) / aspect_b


def profile_aspect(profile: dict) -> float:
    return aspect_of(profile.get("client_width", 0), profile.get("client_height", 0))


# --------------------------------------------------------------- 存取

def get_profiles(cfg: dict) -> list[dict]:
    profiles = cfg.get("calibration_profiles")
    if not isinstance(profiles, list):
        profiles = []
        cfg["calibration_profiles"] = profiles
    return profiles


def find_by_label(cfg: dict, label: str) -> Optional[dict]:
    for profile in get_profiles(cfg):
        if profile.get("label") == label:
            return profile
    return None


def active_profile(cfg: dict) -> Optional[dict]:
    label = cfg.get("active_profile")
    return find_by_label(cfg, label) if label else None


def _make_profile(label: str, width: int, height: int, regions: dict, points: dict,
                  seeded_from: Optional[str] = None) -> dict:
    """一律深拷貝座標 —— profile 與工作副本共用同一個 dict 會讓「隨手微調」
    在使用者還沒按存檔前就寫進 profile，那就等於沒有規則 2 的保護。"""
    return {
        "label": label,
        "client_width": int(width),
        "client_height": int(height),
        "seeded_from": seeded_from,
        # 由別的比例換算出來的就記來源；使用者親手重新框選過之後會被清成 None。
        "derived_from": None,
        "regions": copy.deepcopy(regions or {}),
        "points": copy.deepcopy(points or {}),
    }


# --------------------------------------------------------------- 遷移

def ensure_profiles(cfg: dict) -> bool:
    """把舊設定檔的頂層 regions/points 搬進第一組 profile。

    回傳是否有變動（呼叫端據此決定要不要存檔）。**座標值不做任何修改。**
    """
    profiles = get_profiles(cfg)
    if profiles:
        # 已經有 profile 了，只補齊缺欄位，不動座標
        changed = False
        for profile in profiles:
            if "seeded_from" not in profile:
                profile["seeded_from"] = None
                changed = True
            if "derived_from" not in profile:
                profile["derived_from"] = None
                changed = True
            if not profile.get("label"):
                profile["label"] = label_for(profile.get("client_width", 0),
                                            profile.get("client_height", 0))
                changed = True
        return changed

    regions = cfg.get("regions") or {}
    points = cfg.get("points") or {}
    if not regions and not points:
        return False

    cal = cfg.get("calibration") or {}
    width = int(cal.get("client_width") or 0)
    height = int(cal.get("client_height") or 0)
    if width <= 0 or height <= 0:
        # 沒有記錄校準尺寸的舊檔。座標還是要保住，用一個明確的「未知」標籤收起來，
        # 這樣至少不會憑空消失，使用者一校準就會生出帶尺寸的正式 profile。
        label = "未知比例"
    else:
        label = label_for(width, height)

    profiles.append(_make_profile(label, width, height, regions, points))
    cfg["active_profile"] = label
    return True


# --------------------------------------------------------------- 選用

def find_match(cfg: dict, width: int, height: int,
               tolerance: float = DEFAULT_TOLERANCE) -> tuple[Optional[dict], float]:
    """回傳 (最接近的 profile, 相對誤差)。誤差 <= tolerance 才算「對得上」。"""
    aspect = aspect_of(width, height)
    if aspect <= 0:
        return None, float("inf")
    best, best_delta = None, float("inf")
    for profile in get_profiles(cfg):
        delta = relative_delta(aspect, profile_aspect(profile))
        if delta < best_delta:
            best, best_delta = profile, delta
    return best, best_delta


def activate(cfg: dict, profile: dict) -> None:
    """把 profile 的座標複製成頂層工作副本，並標記為生效中。"""
    cfg["regions"] = copy.deepcopy(profile.get("regions") or {})
    cfg["points"] = copy.deepcopy(profile.get("points") or {})
    cfg["calibration"] = {
        "client_width": int(profile.get("client_width") or 0),
        "client_height": int(profile.get("client_height") or 0),
    }
    cfg["active_profile"] = profile.get("label")
    cfg.pop(PENDING_LABEL_KEY, None)
    cfg.pop(PENDING_SIZE_KEY, None)


# 這些項目**釘在視窗底部**，不在 16:9 內容框裡（見 geometry.retarget_bottom_anchored
# 的實測表）。對話框最下面那排動作按鈕都是這一類。
#
# 只有「按鈕」是貼底的；對話框裡的**文字**（要挑戰嗎？／要再玩一次撲克嗎？）
# 實測是內容框錨定，所以 max_win_marker 這種文字標記不放進來。
BOTTOM_ANCHORED_KEYS = frozenset({
    "challenge_button",
    "cashout_button",
    "retry_button",
    "max_win_retry",
})


def _retarget_tree(node: Any, from_w: int, from_h: int, to_w: int, to_h: int,
                   key: Optional[str] = None) -> Any:
    """把一整棵 regions / points 換算到新的視窗尺寸。

    dict 裡有 x/y 就當座標換算，list 就逐項遞迴（card_slots、hold_toggles），
    其他型別原樣保留。`key` 用來判斷這一項是不是貼視窗底的按鈕。
    """
    if isinstance(node, dict):
        if "x" in node and "y" in node:
            if key in BOTTOM_ANCHORED_KEYS:
                converted = retarget_bottom_anchored(node, from_w, from_h, to_w, to_h)
            else:
                converted = retarget(node, from_w, from_h, to_w, to_h)
            # 保住原本 dict 裡其他欄位（目前沒有，但別讓未來新增的欄位悄悄消失）
            converted = clamp_into_window(converted)
            out = {k: v for k, v in node.items() if k not in converted}
            out.update({k: round(float(v), 4) for k, v in converted.items()})
            return out
        return {k: _retarget_tree(v, from_w, from_h, to_w, to_h, k)
                for k, v in node.items()}
    if isinstance(node, list):
        return [_retarget_tree(v, from_w, from_h, to_w, to_h, key) for v in node]
    return node


def derive(cfg: dict, source: dict, width: int, height: int,
           label: Optional[str] = None) -> dict:
    """從別的比例的校準**精確算出**這個比例的校準。

    這不是「借用」也不是「猜」：遊戲把一個固定 16:9 的內容框置中放進視窗，
    所有 UI 都排在那個框裡（見 geometry.content_box）。所以只要知道原本是在
    多大的視窗下校準的，就能把座標換算到任何長寬比，精度是像素級的。

    實測驗證：16:9 與 4:3 各自量出來的五格手牌，換算到內容框座標後
    一致到 0.0009（約 1 像素）。

    產出的 profile 會標上 `derived_from`，代表「算出來的、還沒人工確認過」——
    真的有偏差時使用者仍然可以重新框選覆蓋它。
    """
    src_w = int(source.get("client_width") or 0)
    src_h = int(source.get("client_height") or 0)
    label = label or label_for(width, height)
    if src_w <= 0 or src_h <= 0:
        # 不知道原本是在多大的視窗校準的，換算不了 —— 只能原樣複製
        created = _make_profile(label, width, height,
                                source.get("regions") or {},
                                source.get("points") or {},
                                seeded_from=source.get("label"))
        created["derived_from"] = None
        return created

    created = _make_profile(
        label, width, height,
        _retarget_tree(source.get("regions") or {}, src_w, src_h, width, height),
        _retarget_tree(source.get("points") or {}, src_w, src_h, width, height),
    )
    created["derived_from"] = source.get("label")
    return created


def borrow(cfg: dict, profile: dict, width: int, height: int) -> None:
    """借用別的比例的座標先頂著，但**不**標記為生效中。

    `active_profile` 故意設成 None：這樣 `sync_active()` 不會把使用者在這個
    比例下的臨時調整寫回被借的那一組。等他真的存檔時，`sync_active()` 會用
    `_pending_*` 生出一組屬於這個比例的新 profile。
    """
    cfg["regions"] = copy.deepcopy(profile.get("regions") or {})
    cfg["points"] = copy.deepcopy(profile.get("points") or {})
    cfg["calibration"] = {"client_width": int(width), "client_height": int(height)}
    cfg["active_profile"] = None
    cfg[PENDING_LABEL_KEY] = label_for(width, height)
    cfg[PENDING_SIZE_KEY] = [int(width), int(height)]


def select_for_window(cfg: dict, width: int, height: int,
                      tolerance: Optional[float] = None) -> dict:
    """依視窗當下的尺寸挑一組校準來用。

    回傳一個描述這次選擇結果的 dict：

        {"label": "16:9", "matched": bool, "borrowed_from": str|None,
         "delta": float, "profile": dict|None, "switched": bool,
         "seeded_from": str|None}

    `matched` False 代表沒有這個比例的校準，目前是借用 `borrowed_from` 那一組，
    座標會歪 —— 呼叫端應該把這件事明確講給使用者聽，不要默默跑下去。
    """
    if tolerance is None:
        tolerance = float(cfg.get("aspect_ratio_tolerance") or DEFAULT_TOLERANCE)

    wanted_label = label_for(width, height)
    previous = cfg.get("active_profile")
    profile, delta = find_match(cfg, width, height, tolerance)

    if profile is None:
        # 一組都沒有（全新設定檔）。頂層座標就是預設值，直接收成這個比例的 profile。
        profiles = get_profiles(cfg)
        created = _make_profile(wanted_label, width, height,
                                cfg.get("regions") or {}, cfg.get("points") or {})
        profiles.append(created)
        activate(cfg, created)
        return {"label": wanted_label, "matched": True, "borrowed_from": None,
                "delta": 0.0, "profile": created, "switched": previous != wanted_label,
                "seeded_from": None}

    if delta <= tolerance:
        activate(cfg, profile)
        return {"label": profile.get("label"), "matched": True, "borrowed_from": None,
                "delta": delta, "profile": profile,
                "switched": previous != profile.get("label"),
                "seeded_from": profile.get("seeded_from")}

    # 沒有這個比例的校準 —— 但不必借、也不必猜：直接從最接近的那組**算**出來。
    # 遊戲的 UI 排在一個置中的 16:9 內容框裡，所以換算是精確的（見 derive()）。
    derived = derive(cfg, profile, width, height, wanted_label)
    profiles = get_profiles(cfg)
    for i, item in enumerate(list(profiles)):
        if item.get("label") == wanted_label:
            profiles.pop(i)
            break
    profiles.append(derived)
    _enforce_limit(profiles, keep_label=wanted_label)
    activate(cfg, derived)
    return {"label": wanted_label, "matched": True, "borrowed_from": None,
            "delta": delta, "profile": derived,
            "switched": previous != wanted_label,
            "seeded_from": None, "derived_from": derived.get("derived_from")}


# --------------------------------------------------------------- 寫回

def sync_active(cfg: dict) -> Optional[dict]:
    """存檔前把頂層工作副本寫回「生效中」的那一組 profile。

    這個函式由 `config.save_config()` 呼叫，不需要各處自己記得 —— 忘記呼叫的
    後果是使用者剛校準的東西下次啟動就被舊 profile 蓋回去。

    借用狀態（`active_profile` is None）下第一次存檔，會用 `_pending_*`
    生出一組新的 profile 並標上 `seeded_from`，代表「這組是複製來的、還沒真正
    校準過」。**不會**碰被借的那一組。
    """
    regions = cfg.get("regions")
    points = cfg.get("points")
    if not isinstance(regions, dict) or not isinstance(points, dict):
        return None

    profile = active_profile(cfg)
    if profile is not None:
        profile["regions"] = copy.deepcopy(regions)
        profile["points"] = copy.deepcopy(points)
        cal = cfg.get("calibration") or {}
        width = int(cal.get("client_width") or 0)
        height = int(cal.get("client_height") or 0)
        if width > 0 and height > 0:
            profile["client_width"] = width
            profile["client_height"] = height
        # 使用者真的在這個比例下存過檔了，就不再算「複製來的」或「換算來的」
        profile["seeded_from"] = None
        profile["derived_from"] = None
        return profile

    label = cfg.get(PENDING_LABEL_KEY)
    if not label:
        return None
    size = cfg.get(PENDING_SIZE_KEY) or []
    width = int(size[0]) if len(size) > 0 else 0
    height = int(size[1]) if len(size) > 1 else 0
    borrowed_from = None
    existing, delta = find_match(cfg, width, height, DEFAULT_TOLERANCE)
    if existing is not None and delta > DEFAULT_TOLERANCE:
        borrowed_from = existing.get("label")

    created = _make_profile(label, width, height, regions, points,
                            seeded_from=borrowed_from)
    profiles = get_profiles(cfg)
    # 同名的先移掉（例如「未知比例」升級成正式標籤），避免出現兩組同名
    for i, item in enumerate(list(profiles)):
        if item.get("label") == label:
            profiles.pop(i)
            break
    profiles.append(created)
    _enforce_limit(profiles, keep_label=label)
    cfg["active_profile"] = label
    cfg.pop(PENDING_LABEL_KEY, None)
    cfg.pop(PENDING_SIZE_KEY, None)
    return created


def _enforce_limit(profiles: list[dict], keep_label: Optional[str] = None) -> None:
    """超過上限時，先丟「複製來的、沒真正校準過」的那些，再丟最舊的。

    永遠不會丟 keep_label 那一組。
    """
    while len(profiles) > MAX_PROFILES:
        victim = None
        for profile in profiles:
            if profile.get("label") == keep_label:
                continue
            if profile.get("seeded_from"):
                victim = profile
                break
        if victim is None:
            for profile in profiles:
                if profile.get("label") != keep_label:
                    victim = profile
                    break
        if victim is None:
            return
        profiles.remove(victim)


def save_as(cfg: dict, width: int, height: int,
            label: Optional[str] = None) -> dict:
    """把目前的頂層座標明確存成「這個比例」的校準（GUI 按鈕用）。

    已經有同比例的 profile 就覆蓋那一組，不會新增重複的。

    新建的那一組如果是在「借用別人座標」的狀態下按的，會標上 `seeded_from`
    —— 因為那些座標其實是複製來的、還沒真正在這個比例下量過。使用者實際校準
    並存檔之後，`sync_active()` 才會把這個標記清掉。
    """
    label = label or label_for(width, height)
    profiles = get_profiles(cfg)
    existing, delta = find_match(cfg, width, height, DEFAULT_TOLERANCE)
    if existing is not None and delta <= DEFAULT_TOLERANCE:
        existing["label"] = label
        existing["client_width"] = int(width)
        existing["client_height"] = int(height)
        existing["regions"] = copy.deepcopy(cfg.get("regions") or {})
        existing["points"] = copy.deepcopy(cfg.get("points") or {})
        existing["seeded_from"] = None
        existing["derived_from"] = None
        target = existing
    else:
        seeded_from = None
        if cfg.get("active_profile") is None and existing is not None:
            seeded_from = existing.get("label")
        target = _make_profile(label, width, height,
                               cfg.get("regions") or {}, cfg.get("points") or {},
                               seeded_from=seeded_from)
        profiles.append(target)
        _enforce_limit(profiles, keep_label=label)
    cfg["active_profile"] = label
    cfg["calibration"] = {"client_width": int(width), "client_height": int(height)}
    cfg.pop(PENDING_LABEL_KEY, None)
    cfg.pop(PENDING_SIZE_KEY, None)
    return target


def remove(cfg: dict, label: str) -> bool:
    """刪掉一組校準。生效中的那一組會連帶把 active_profile 清掉。"""
    profiles = get_profiles(cfg)
    for i, profile in enumerate(profiles):
        if profile.get("label") == label:
            profiles.pop(i)
            if cfg.get("active_profile") == label:
                cfg["active_profile"] = None
            return True
    return False


# --------------------------------------------------------------- 顯示

def describe(cfg: dict) -> list[str]:
    """給 GUI / check_setup 用的人話清單。"""
    lines: list[str] = []
    active = cfg.get("active_profile")
    for profile in get_profiles(cfg):
        label = profile.get("label", "?")
        width = profile.get("client_width") or 0
        height = profile.get("client_height") or 0
        size = f"{width}x{height}" if width and height else "尺寸未紀錄"
        marks = []
        if label == active:
            marks.append("生效中")
        if profile.get("derived_from"):
            marks.append(f"由 {profile['derived_from']} 換算而來")
        if profile.get("seeded_from"):
            marks.append(f"複製自 {profile['seeded_from']}，尚未校準")
        suffix = f"（{'、'.join(marks)}）" if marks else ""
        lines.append(f"{label}  {size}{suffix}")
    return lines


def summarize_selection(selection: dict) -> str:
    """把 select_for_window() 的結果講成一句話。"""
    label = selection.get("label", "?")
    if selection.get("derived_from"):
        return (f"沒有 {label} 的校準，已從 {selection['derived_from']} 換算出一組"
                "（遊戲的 UI 排在置中的 16:9 內容框裡，換算是精確的）。"
                "若還是有偏差，可在「校準」分頁重新框選覆蓋。")
    if selection.get("matched"):
        if selection.get("seeded_from"):
            return (f"套用 {label} 的校準（這組是從 {selection['seeded_from']} 複製來的，"
                    "還沒在這個比例下校準過，座標可能會歪）")
        return f"套用 {label} 的校準"
    return (f"沒有 {label} 的校準，先借用 {selection.get('borrowed_from')} 的座標"
            f"（長寬比差 {selection.get('delta', 0):.1%}）。"
            "請在「校準」分頁按『另存為這個比例的校準』後重新框選，否則位置會歪。")
