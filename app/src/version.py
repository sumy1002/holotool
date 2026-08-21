"""版本號的**唯一來源**，以及 GitHub repo 的位置。

要發新版時，這個檔案裡的 `__version__` 是唯一要改的地方：

    __version__ = "1.0.1"

改完之後這三邊會自動跟著變：
  · GUI 主控台「版本與更新」顯示的目前版本
  · `packaging/build_installer.py` 產生的安裝檔版本（`--version` 的預設值）
  · `packaging/make_release.py` 產生的更新包檔名與 `src/updater.py` 的比對基準

刻意不引用任何其他模組（連 `paths` 都不引用），這樣打包腳本、更新模組、
測試都能安全地 import 它，不會有循環相依。

--------------------------------------------------------------------------
版本號規則（語意化版本，Semantic Versioning）
    主版本.次版本.修訂號     例如 1.4.2
  · 修訂號：只修 bug、調參數
  · 次版本：加新功能，舊設定檔照樣能用
  · 主版本：設定檔或資料格式不相容（記得同時處理 `config.CONFIG_VERSION`）

**`__version__` 跟 `config.CONFIG_VERSION` 是兩件不同的事**，不要混用：
前者是「程式的版本」，後者是「設定檔結構的版本」。程式改十次、設定檔格式
沒動的話，`CONFIG_VERSION` 就不該動 —— 動了會讓 `RETUNED_ON_UPGRADE`
無謂地覆蓋掉使用者調過的參數。
"""
from __future__ import annotations

__version__ = "1.0.18"

# ---------------------------------------------------------------- GitHub

# 更新來源。改成自己的帳號 / repo 名稱，兩者都要跟 GitHub 上完全一致
# （大小寫在網址上不敏感，但寫對比較不會混淆）。
GITHUB_OWNER = "sumy1002"
GITHUB_REPO = "holotool"

# Release 的 tag 命名規則：v1.0.1。`make_release.py` 會印出要用的 tag。
TAG_PREFIX = "v"


def release_api_url() -> str:
    """GitHub API：取得「最新一個正式 Release」。

    注意這個端點會**跳過** pre-release 與草稿，所以想先自己試裝、不要讓
    其他人收到更新通知時，把 Release 勾成 pre-release 就好。
    """
    return f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"


def releases_page_url() -> str:
    """給人看的 Release 頁面（自動更新失敗時請使用者自己去下載）。"""
    return f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"


def asset_name(version: str | None = None) -> str:
    """更新包的檔名，例如 `HoloTool-1.0.1.zip`。

    `make_release.py` 產生的檔名與 `updater.py` 尋找的檔名都走這裡，
    改命名規則只要改這一個函式。
    """
    return f"HoloTool-{version or __version__}.zip"


# ------------------------------------------------------------ 版本比較

def version_tuple(text: str | None = None) -> tuple[int, ...]:
    """把 "v1.2.3" / "1.2" / "1.2.3-beta1" 轉成可以比大小的數字序列。

    只取開頭連續的數字段落；碰到不是純數字的段落就停（`-beta1` 之類的
    後置字串一律忽略）。長度一律補到 3 段，避免 (1, 2) 和 (1, 2, 0)
    被判定為不同。

    >>> version_tuple("v1.2.3")
    (1, 2, 3)
    >>> version_tuple("1.2")
    (1, 2, 0)
    >>> version_tuple("1.2.3-beta1")
    (1, 2, 3)
    """
    raw = (text if text is not None else __version__).strip()
    if raw[:1].lower() == "v":
        raw = raw[1:]
    parts: list[int] = []
    for chunk in raw.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(candidate: str, current: str | None = None) -> bool:
    """`candidate` 是否比 `current`（預設為目前程式版本）新。

    版本號解析不出來時一律回傳 False —— 「看不懂就當作沒有新版」比
    「看不懂就叫使用者更新」安全得多。

    `candidate` 是 None 或空字串時直接回 False，**不可以**讓它掉進
    `version_tuple()` 的預設值 —— 那個預設值是「目前程式版本」，
    於是 `is_newer(None)` 會變成「拿自己跟 current 比」，一旦
    `__version__` 比 current 新就回報「有更新」，等於憑空冒出一個
    不存在的新版本。
    """
    if not candidate:
        return False
    try:
        new = version_tuple(candidate)
        old = version_tuple(current)
    except (AttributeError, TypeError, ValueError):
        return False
    if new == (0, 0, 0):
        return False
    return new > old
