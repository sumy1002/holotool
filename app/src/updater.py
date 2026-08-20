"""檢查更新 → 下載 → 驗證 → 備份 → 置換 → 重啟。

更新來源是 GitHub Releases 的資產（`HoloTool-<版本>.zip`），
裡面**只有程式內容**：`HoloTool.exe` 與 `app\\`（PyInstaller 產出 + `defaults\\`）。

╔══════════════════════════════════════════════════════════════════════╗
║ 三條不可以違反的規則（都是踩過坑換來的）                              ║
╠══════════════════════════════════════════════════════════════════════╣
║ 1. 執行中的 exe 不能覆蓋自己 —— Windows 會鎖住檔案。所以流程一定是    ║
║    「下載到暫存 → 解壓 → 關掉主程式 → 由一個外部 .bat 完成置換 →     ║
║    重新啟動」。Python 這一端絕對不去寫 HoloTool.exe。                 ║
║                                                                      ║
║ 2. 絕對不動使用者的校準與樣板 —— `PROTECTED_DIRS` 裡的資料夾，        ║
║    (a) 更新包裡本來就沒有這些資料夾（`make_release.py` 排除掉了），   ║
║    (b) 解壓時再過濾一次，                                            ║
║    (c) robocopy 再用 /XD 排除一次。                                  ║
║    三層都擋，任何一層失效都還有另外兩層。                            ║
║                                                                      ║
║ 3. 動之前先備份 —— `backups\\pre-update-<版本>-<時間戳>.zip`，        ║
║    保留最近 KEEP_BACKUPS 份才輪替刪除。                              ║
║    （以前只留一代，第二次打包就把好的那份蓋掉，等於白備份。）         ║
╚══════════════════════════════════════════════════════════════════════╝

設定檔的欄位升級不在這裡做 —— 那是 `src/config.py` 的 `CONFIG_VERSION`
與 `RETUNED_ON_UPGRADE` 的工作，新版程式第一次啟動時會自己補新欄位。
這個模組只負責搬程式碼，一個設定值都不改。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass

from .paths import log_dir, project_root
from .version import (
    __version__,
    asset_name,
    is_newer,
    release_api_url,
    releases_page_url,
)

# 這些資料夾一律不碰。名稱是相對於 project_root()（frozen 時是 exe 旁的 app\）。
PROTECTED_DIRS = ("config", "card_templates", "data", "logs", "debug_captures")

# 更新前要備份的（壓成一個 zip）。data\ 只是統計數字，掉了不心疼，不佔備份空間。
BACKUP_DIRS = ("config", "card_templates")

# 保留幾份「更新前備份」
KEEP_BACKUPS = 5

# 更新包解開之後，最上層一定要看到這個檔案，否則就是抓錯東西
SENTINEL = "HoloTool.exe"

# 下載大小上限（保險用；正常一包 60~120MB）
MAX_DOWNLOAD_BYTES = 600 * 1024 * 1024

USER_AGENT = f"HoloTool/{__version__} (+{releases_page_url()})"


class UpdateError(RuntimeError):
    """更新流程中可以「安全放棄」的錯誤 —— 丟出來時尚未動到任何原有檔案。"""


# --------------------------------------------------------------- 資料結構

@dataclass
class ReleaseInfo:
    """GitHub 上那一個 Release 的重點資訊。"""
    version: str            # 去掉 v 之後的版本號，例如 "1.0.1"
    tag: str                # 原始 tag，例如 "v1.0.1"
    notes: str              # Release 的說明文字
    zip_url: str            # 更新包的下載網址
    zip_name: str           # 更新包檔名
    size: int               # 位元組；GitHub 沒給就是 0
    sha256: str = ""        # 期望的雜湊值；抓不到就是空字串
    page_url: str = ""      # 給人看的頁面


@dataclass
class CheckResult:
    """一次「檢查更新」的結果。GUI 只看這個。"""
    current: str = __version__
    available: bool = False
    release: ReleaseInfo | None = None
    message: str = ""
    error: str = ""


# ------------------------------------------------------------------- HTTP

def _http_get(url: str, timeout: float = 15.0, accept: str | None = None) -> bytes:
    """單純把網址抓下來。錯誤一律翻譯成看得懂的中文再丟出去。"""
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(MAX_DOWNLOAD_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError(
                "GitHub 說找不到這個 repo，或這個 repo 還沒有任何 Release。\n"
                "請確認 src/version.py 裡的 GITHUB_OWNER / GITHUB_REPO 正確："
                f"\n{url}"
            ) from exc
        if exc.code == 403:
            raise UpdateError(
                "GitHub 暫時擋住了請求（未登入的查詢每小時有次數上限）。\n"
                "等一小時再試，或直接到 Release 頁面手動下載。"
            ) from exc
        raise UpdateError(f"連線 GitHub 失敗（HTTP {exc.code}）。") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"連不上網路：{exc.reason}") from exc
    except TimeoutError as exc:
        raise UpdateError("連線 GitHub 逾時，請檢查網路後再試。") from exc


def fetch_latest_release(timeout: float = 15.0) -> ReleaseInfo:
    """問 GitHub「最新的正式 Release 是哪一個」。

    這個端點會**跳過** pre-release 與草稿，所以要先自己試裝、不想讓別人
    收到更新通知時，把 Release 勾成 pre-release 就好。
    """
    raw = _http_get(release_api_url(), timeout=timeout,
                    accept="application/vnd.github+json")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise UpdateError("GitHub 回傳的內容看不懂（不是預期的 JSON）。") from exc
    if not isinstance(data, dict):
        raise UpdateError("GitHub 回傳的內容格式不對。")
    return _parse_release(data)


def _parse_release(data: dict) -> ReleaseInfo:
    """把 GitHub API 的 JSON 挑成我們要的欄位。

    找更新包的順序：
      1. 檔名剛好等於 `HoloTool-<這個 tag 的版本>.zip`
      2. 檔名以 `HoloTool-` 開頭、`.zip` 結尾的第一個
      3. 任何一個 .zip
    找雜湊的順序：
      1. 名稱是「更新包檔名 + .sha256」的資產
      2. Release 說明裡含 `sha256` 字樣那一行
    """
    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        raise UpdateError("GitHub 回傳的 Release 沒有 tag，無法判斷版本。")
    version = tag[1:] if tag[:1].lower() == "v" else tag

    assets = [a for a in (data.get("assets") or []) if isinstance(a, dict)]
    zips = [a for a in assets if str(a.get("name", "")).lower().endswith(".zip")]
    wanted = asset_name(version).lower()

    chosen = None
    for a in zips:
        if str(a.get("name", "")).lower() == wanted:
            chosen = a
            break
    if chosen is None:
        for a in zips:
            if str(a.get("name", "")).lower().startswith("holotool-"):
                chosen = a
                break
    if chosen is None and zips:
        chosen = zips[0]
    if chosen is None:
        raise UpdateError(
            f"Release {tag} 裡沒有 .zip 更新包。\n"
            "發版時請把 make_release.py 產生的 HoloTool-<版本>.zip 一起上傳。"
        )

    zip_name = str(chosen.get("name") or "update.zip")
    notes = str(data.get("body") or "").strip()
    sha = _fetch_sha_asset(assets, zip_name) or _sha256_from_notes(notes)

    return ReleaseInfo(
        version=version,
        tag=tag,
        notes=notes,
        zip_url=str(chosen.get("browser_download_url") or ""),
        zip_name=zip_name,
        size=int(chosen.get("size") or 0),
        sha256=sha,
        page_url=str(data.get("html_url") or releases_page_url()),
    )


def _fetch_sha_asset(assets: list, zip_name: str) -> str:
    """抓 `<更新包>.sha256` 這個小檔案。抓不到就回空字串，不視為錯誤。"""
    target = (zip_name + ".sha256").lower()
    for a in assets:
        if str(a.get("name", "")).lower() != target:
            continue
        url = str(a.get("browser_download_url") or "")
        if not url:
            return ""
        try:
            return _clean_sha256(_http_get(url, timeout=10).decode("utf-8", "replace"))
        except UpdateError:
            return ""
    return ""


def _clean_sha256(text: str) -> str:
    """從 `sha256sum` 風格的一行（`<雜湊>  <檔名>`）取出雜湊本身。"""
    for token in (text or "").split():
        token = token.strip().lower()
        if len(token) == 64 and all(c in "0123456789abcdef" for c in token):
            return token
    return ""


def _sha256_from_notes(notes: str) -> str:
    """Release 說明裡寫 `sha256: abcdef...` 也認得。"""
    for line in (notes or "").splitlines():
        if "sha256" in line.lower():
            found = _clean_sha256(line)
            if found:
                return found
    return ""


# --------------------------------------------------------------- 檢查更新

def check_for_update(timeout: float = 15.0) -> CheckResult:
    """比對版本。**不下載任何東西**，可以放心在背景執行緒裡呼叫。"""
    result = CheckResult(current=__version__)
    try:
        release = fetch_latest_release(timeout=timeout)
    except UpdateError as exc:
        result.error = str(exc)
        result.message = f"檢查失敗：{exc}"
        return result

    result.release = release
    if is_newer(release.version, __version__):
        result.available = True
        result.message = f"有新版本 v{release.version}（目前 v{__version__}）"
    else:
        result.message = f"已經是最新版 v{__version__}"
    return result


# ----------------------------------------------------------------- 下載

def sha256_of(path: str, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def download_release(release: ReleaseInfo, dest_dir: str,
                     progress=None, timeout: float = 60.0) -> str:
    """把更新包下載到 `dest_dir`，回傳實際存檔路徑。

    `progress(已下載位元組, 總位元組或 0)` 會被呼叫多次，給 GUI 顯示進度用。
    下載中途失敗會把半成品刪掉，不留下會被誤認成完整檔的殘骸。
    """
    if not release.zip_url:
        raise UpdateError("這個 Release 沒有可下載的網址。")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(release.zip_name))
    request = urllib.request.Request(release.zip_url,
                                     headers={"User-Agent": USER_AGENT})
    done = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            total = int(response.headers.get("Content-Length") or release.size or 0)
            with open(dest, "wb") as f:
                while True:
                    block = response.read(1 << 18)
                    if not block:
                        break
                    done += len(block)
                    if done > MAX_DOWNLOAD_BYTES:
                        raise UpdateError("下載的檔案大得不合理，已中止。")
                    f.write(block)
                    if progress:
                        progress(done, total)
    except UpdateError:
        _silent_remove(dest)
        raise
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        _silent_remove(dest)
        raise UpdateError(f"下載失敗：{exc}") from exc
    return dest


def verify_download(path: str, expected_sha256: str = "") -> str:
    """驗證下載回來的檔案。回傳實際算出的 SHA256。

    有期望值就比對；沒有期望值時**至少**要求 zip 本身結構完整
    （`zipfile` 打得開、CRC 全部對）。兩者都不通過就丟 UpdateError，
    此時什麼都還沒動到。
    """
    if not os.path.exists(path):
        raise UpdateError("下載的檔案不見了。")
    actual = sha256_of(path)
    expected = (expected_sha256 or "").strip().lower()
    if expected and actual != expected:
        raise UpdateError(
            "檔案校驗失敗（SHA256 不符），為了安全已放棄這次更新。\n"
            f"  期望：{expected}\n  實際：{actual}\n"
            "請重新檢查更新，或到 Release 頁面手動下載。"
        )
    try:
        with zipfile.ZipFile(path) as zf:
            broken = zf.testzip()
            if broken:
                raise UpdateError(f"更新包內容損毀（{broken}），已放棄這次更新。")
            names = zf.namelist()
    except zipfile.BadZipFile as exc:
        raise UpdateError("下載回來的不是有效的 zip 檔，已放棄這次更新。") from exc
    if not any(os.path.basename(n).lower() == SENTINEL.lower() for n in names):
        raise UpdateError(
            f"更新包裡找不到 {SENTINEL}，看起來不是 HoloTool 的更新包，已放棄。"
        )
    return actual


# ----------------------------------------------------------------- 備份

def backup_dir(root: str | None = None) -> str:
    """備份放在 `project_root()/backups`。"""
    return os.path.join(root or project_root(), "backups")


def backup_user_data(root: str | None = None, version: str | None = None,
                     keep: int = KEEP_BACKUPS) -> str:
    """把 config\\ 與 card_templates\\ 壓成一個 zip，回傳檔案路徑。

    沒有東西可以備份時回傳空字串（例如全新安裝還沒校準過）。
    """
    base = root or project_root()
    out_dir = backup_dir(base)
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"pre-update-{version or __version__}-{stamp}.zip"
    path = os.path.join(out_dir, name)

    added = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder in BACKUP_DIRS:
            src = os.path.join(base, folder)
            if not os.path.isdir(src):
                continue
            for dirpath, _dirnames, filenames in os.walk(src):
                for filename in filenames:
                    full = os.path.join(dirpath, filename)
                    rel = os.path.relpath(full, base)
                    try:
                        zf.write(full, rel)
                        added += 1
                    except OSError:
                        # 單一檔案讀不到（被鎖住之類）不該讓整個備份失敗，
                        # 但也不能靜靜跳過 —— 寫一行進 zip 的註解裡留痕跡。
                        zf.comment = (zf.comment or b"") + \
                            f"SKIPPED {rel}\n".encode("utf-8")
    if added == 0:
        _silent_remove(path)
        return ""
    _prune_backups(out_dir, keep=keep)
    return path


def _prune_backups(out_dir: str, keep: int = KEEP_BACKUPS) -> list[str]:
    """只留最近 `keep` 份，回傳被刪掉的檔名。

    以前只留一代，結果下一次動作就把唯一那份好的蓋掉，救不回來。
    多留幾代的成本只是幾 MB。
    """
    try:
        names = [n for n in os.listdir(out_dir)
                 if n.startswith("pre-update-") and n.endswith(".zip")]
    except OSError:
        return []
    # 檔名帶了 -YYYYmmdd-HHMMSS，字串排序就是時間排序
    names.sort()
    removed: list[str] = []
    while len(names) > max(1, keep):
        victim = names.pop(0)
        if _silent_remove(os.path.join(out_dir, victim)):
            removed.append(victim)
    return removed


# ------------------------------------------------------------- 解壓到暫存

def staging_root() -> str:
    """暫存區放在系統 TEMP，不放安裝目錄底下。

    放在安裝目錄裡的話，robocopy 會把暫存區自己也當成要複製的內容，
    而且更新失敗時會在使用者的資料夾留下一堆垃圾。
    """
    return os.path.join(tempfile.gettempdir(), "HoloTool-update")


def extract_update(zip_path: str, staging: str | None = None) -> str:
    """把更新包解到暫存區，回傳暫存區路徑。

    解壓時會擋掉三種東西：
      · 絕對路徑與 `..`（zip 路徑穿越攻擊）
      · PROTECTED_DIRS 底下的任何檔案（第二層防線，正常更新包裡本來就沒有）
      · 目錄項目以外、解出來會落在暫存區外面的路徑
    """
    target = staging or staging_root()
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target, exist_ok=True)
    target_abs = os.path.abspath(target)

    skipped: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            if name.startswith("/") or ".." in name.split("/") or ":" in name[:3]:
                skipped.append(name)
                continue
            if _is_protected(name):
                skipped.append(name)
                continue
            dest = os.path.abspath(os.path.join(target_abs, *name.split("/")))
            if not dest.startswith(target_abs + os.sep):
                skipped.append(name)
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)

    if not os.path.exists(os.path.join(target, SENTINEL)):
        shutil.rmtree(target, ignore_errors=True)
        raise UpdateError(
            f"更新包解開後最上層沒有 {SENTINEL}，結構不對，已放棄這次更新。"
        )
    if skipped:
        _log(f"[更新] 解壓時略過 {len(skipped)} 個不該出現的項目："
             f"{', '.join(skipped[:5])}{' ...' if len(skipped) > 5 else ''}")
    return target


def _is_protected(archive_name: str) -> bool:
    """這個 zip 內路徑是否落在使用者資料目錄裡。

    同時擋最上層（舊版攤平的安裝）與 `app/` 底下（新版）兩種寫法。
    """
    parts = [p for p in archive_name.replace("\\", "/").split("/") if p]
    if not parts:
        return False
    lowered = [p.lower() for p in parts]
    protected = {p.lower() for p in PROTECTED_DIRS}
    if lowered[0] in protected:
        return True
    if len(lowered) >= 2 and lowered[1] in protected:
        return True
    return False


# ---------------------------------------------------------- 置換與重啟

def install_root() -> str:
    """要被置換的那個資料夾（`HoloTool.exe` 所在處）。

    開發模式沒有 exe 可以換，直接拒絕 —— 原始碼請用 git pull。
    """
    if not getattr(sys, "frozen", False):
        raise UpdateError(
            "開發模式（直接跑原始碼）不支援自動置換。\n"
            "請用 git pull 取得新版程式碼，或改跑打包好的 HoloTool.exe。"
        )
    return os.path.dirname(os.path.abspath(sys.executable))


APPLY_SCRIPT_NAME = "holotool_apply_update.bat"

# .bat 一律只用 ASCII。Windows 的 cmd 預設不是 UTF-8，
# 帶中文的批次檔在某些機器上會整行解析失敗，除錯起來非常痛苦。
_APPLY_TEMPLATE = """@echo off
setlocal
set "ROOT={root}"
set "STAGE={stage}"
set "PID={pid}"
set "LOGFILE={logfile}"
set "ZIPFILE={zipfile}"

echo ==== HoloTool update %DATE% %TIME% ==== >> "%LOGFILE%"
echo root=%ROOT% >> "%LOGFILE%"
echo stage=%STAGE% >> "%LOGFILE%"

rem --- 1. wait for the running HoloTool.exe to exit (up to 60s) ---
set /a tries=0
:waitloop
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if errorlevel 1 goto replace
set /a tries+=1
if %tries% GEQ 60 (
  echo [ERROR] pid %PID% still alive after 60s - aborting, nothing changed >> "%LOGFILE%"
  goto finish
)
ping -n 2 127.0.0.1 >nul
goto waitloop

rem --- 2. copy program files over the install dir ---
rem     No /PURGE: we never delete anything the user owns.
rem     /XD excludes the user data dirs as a third safety net.
:replace
robocopy "%STAGE%" "%ROOT%" /E /R:3 /W:1 /NFL /NDL /NJH /NJS{excludes} >> "%LOGFILE%" 2>&1
if errorlevel 8 (
  echo [ERROR] robocopy exit code %ERRORLEVEL% - update incomplete >> "%LOGFILE%"
  goto finish
)
echo [OK] program files replaced >> "%LOGFILE%"

rem --- 3. clean up and restart ---
rmdir /S /Q "%STAGE%" 2>nul
if exist "%ZIPFILE%" del /F /Q "%ZIPFILE%" 2>nul
start "" "%ROOT%\\{sentinel}"

:finish
echo ==== done %DATE% %TIME% ==== >> "%LOGFILE%"
(goto) 2>nul & del "%~f0"
"""


def write_apply_script(staging: str, root: str, zip_path: str = "",
                       pid: int | None = None, script_dir: str | None = None) -> str:
    """產生負責置換的 .bat，回傳它的路徑。

    這支批次檔是整個更新流程唯一會寫入安裝目錄的角色，而它是在主程式
    結束之後才動手 —— 這就是「執行中的 exe 不能覆蓋自己」的解法。
    """
    out_dir = script_dir or os.path.dirname(os.path.abspath(staging))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, APPLY_SCRIPT_NAME)

    excludes = ""
    for folder in PROTECTED_DIRS:
        excludes += f' /XD "{os.path.join(root, "app", folder)}"'
        excludes += f' /XD "{os.path.join(root, folder)}"'
    excludes += f' /XD "{backup_dir(os.path.join(root, "app"))}"'
    excludes += f' /XD "{backup_dir(root)}"'

    logfile = os.path.join(log_dir(), "update.log")
    os.makedirs(os.path.dirname(logfile), exist_ok=True)

    body = _APPLY_TEMPLATE.format(
        root=root.rstrip("\\"),
        stage=staging.rstrip("\\"),
        pid=pid if pid is not None else os.getpid(),
        logfile=logfile,
        zipfile=zip_path,
        excludes=excludes,
        sentinel=SENTINEL,
    )
    # cmd 對換行不挑，但 CRLF 比較保險；ASCII 編碼確保沒有偷跑進來的中文
    with open(path, "w", encoding="ascii", newline="\r\n") as f:
        f.write(body)
    return path


def launch_apply_script(script: str) -> None:
    """把 .bat 丟出去背景執行，然後就該讓主程式結束了。

    用 DETACHED_PROCESS 讓它脫離本行程 —— 否則主程式一結束，
    子行程可能跟著被收掉，更新做一半。
    """
    creationflags = 0
    if os.name == "nt":
        creationflags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                         | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                         | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    subprocess.Popen(  # noqa: S603  （路徑是我們自己產生的）
        ["cmd", "/c", script],
        cwd=tempfile.gettempdir(),
        creationflags=creationflags,
        close_fds=True,
    )


# ------------------------------------------------------- 對外的完整流程

def prepare_update(release: ReleaseInfo, progress=None) -> dict:
    """下載 → 驗證 → 備份 → 解壓 → 產生 .bat。**不會**關掉程式。

    回傳 dict：`{"script", "staging", "zip", "backup", "sha256"}`。
    任何一步失敗都丟 UpdateError，而且此時安裝目錄完全沒被動過。
    整個流程可以在背景執行緒跑（沒有碰任何 tkinter 元件）。
    """
    root = install_root()          # 開發模式會在這裡就被擋下來
    work = staging_root()
    parent = os.path.dirname(work)

    def _say(text: str) -> None:
        if progress:
            progress(text)

    _say("正在下載更新包…")
    zip_path = download_release(
        release, parent,
        progress=lambda done, total: _say(_progress_text(done, total)),
    )

    _say("正在驗證檔案完整性…")
    verify_download(zip_path, release.sha256)
    if not release.sha256:
        _log("[更新] 這個 Release 沒有附 .sha256，只驗證了 zip 結構完整性。")

    _say("正在備份你的校準與樣板…")
    backup = backup_user_data(version=__version__)
    if backup:
        _log(f"[更新] 已備份校準與樣板：{backup}")
    else:
        _log("[更新] 沒有找到可備份的 config/card_templates（全新安裝？）")

    _say("正在解開更新包…")
    staging = extract_update(zip_path, work)

    script = write_apply_script(staging, root, zip_path=zip_path,
                                pid=os.getpid(), script_dir=parent)
    _say("準備完成，可以重新啟動了。")
    return {"script": script, "staging": staging, "zip": zip_path,
            "backup": backup, "root": root}


def _progress_text(done: int, total: int) -> str:
    mb = done / (1024 * 1024)
    if total:
        return f"下載中… {mb:.1f} / {total / (1024 * 1024):.1f} MB" \
               f"（{done * 100 // total}%）"
    return f"下載中… {mb:.1f} MB"


# ------------------------------------------------------------------ 小工具

def _silent_remove(path: str) -> bool:
    try:
        if os.path.exists(path):
            os.remove(path)
            return True
    except OSError:
        pass
    return False


def _log(message: str) -> None:
    """寫 log，但 logger 出問題時絕不讓更新流程掛掉。"""
    try:
        from . import logger
        logger.log(message)
    except Exception:
        try:
            print(message)
        except Exception:
            pass
