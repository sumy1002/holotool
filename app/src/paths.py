"""專案路徑：開發模式與打包成 exe 後都能找到設定檔、樣板與紀錄。

**裝好之後的資料夾長這樣** —— 使用者看到的只有一個 exe：

  HoloTool.exe
  app/                  ← 其餘全部躲在這裡
    （PyInstaller 的執行檔內容）
    config/  card_templates/  defaults/  data/  logs/  debug_captures/
    unins000.exe        ← 解除安裝程式也放進來

以前是全部攤在 exe 旁邊，一裝完就看到 _internal、config、data、logs…
一大堆資料夾，很難找到要點的到底是哪個。

開發模式（直接跑原始碼）則是：專案根目錄只有 gui.py / README.md /
requirements.txt，其餘收在 app\\ 裡，`project_root()` 指的就是那個 app\\。
兩邊的子資料夾名稱一致，所以打包時直接搬過去就好。
"""
from __future__ import annotations

import os
import shutil
import sys

# exe 旁邊那個「什麼都塞在裡面」的資料夾名稱。
# 必須和 build_exe.py 的 --contents-directory、HoloTool.iss 的路徑一致。
BUNDLE_SUBDIR = "app"


def project_root() -> str:
    """資料的根目錄（config / card_templates / data / logs 的上一層）。"""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        inner = os.path.join(exe_dir, BUNDLE_SUBDIR)
        # 找不到子資料夾就退回 exe 旁邊 —— 舊版是攤平的，這樣才不會讓
        # 已經裝好、校準好的舊安裝突然找不到自己的設定。
        return inner if os.path.isdir(inner) else exe_dir
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_path() -> str:
    return os.path.join(project_root(), "config", "config.json")


def template_dir() -> str:
    return os.path.join(project_root(), "card_templates")


def parts_dir() -> str:
    """點數 / 花色的小樣板（rank_A_1.png、suit_S_1.png ...）。"""
    return os.path.join(template_dir(), "parts")


def data_dir() -> str:
    return os.path.join(project_root(), "data")


def log_dir() -> str:
    return os.path.join(project_root(), "logs")


def resolve_data_path(relative: str) -> str:
    """把相對路徑（例如 card_templates/table_marker.png）轉成絕對路徑。"""
    if os.path.isabs(relative):
        return relative
    return os.path.join(project_root(), relative)


def ensure_runtime_dirs() -> None:
    os.makedirs(os.path.join(project_root(), "config"), exist_ok=True)
    os.makedirs(template_dir(), exist_ok=True)
    os.makedirs(parts_dir(), exist_ok=True)
    os.makedirs(data_dir(), exist_ok=True)
    os.makedirs(log_dir(), exist_ok=True)
    os.makedirs(os.path.join(project_root(), "debug_captures"), exist_ok=True)


def default_ui_dir() -> str:
    return os.path.join(project_root(), "defaults", "ui")


def default_parts_dir() -> str:
    return os.path.join(project_root(), "defaults", "parts")


def install_default_parts(overwrite: bool = False) -> list[str]:
    """把內建的點數/花色樣板複製到 card_templates/parts。

    預設 **不覆蓋**：使用者自己在實機蒐集的樣板一定比內建的準，不要蓋掉。
    """
    src = default_parts_dir()
    dst = parts_dir()
    os.makedirs(dst, exist_ok=True)
    written: list[str] = []
    if not os.path.isdir(src):
        return written
    for name in os.listdir(src):
        if not name.lower().endswith(".png"):
            continue
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if overwrite or not os.path.exists(d):
            shutil.copy2(s, d)
            written.append(name)
    return written


def install_default_ui_templates(overwrite: bool = True) -> list[str]:
    """把截圖預設的畫面標記樣板複製到 card_templates。"""
    src = default_ui_dir()
    dst = template_dir()
    os.makedirs(dst, exist_ok=True)
    written: list[str] = []
    if not os.path.isdir(src):
        return written
    for name in os.listdir(src):
        if not name.lower().endswith(".png"):
            continue
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if overwrite or not os.path.exists(d):
            shutil.copy2(s, d)
            written.append(name)
    return written


def prepare_runtime() -> str:
    """切換工作目錄到資料根目錄，並建立必要資料夾。啟動 GUI / exe 時呼叫。

    順便補齊**缺少的**內建樣板（`overwrite=False`，永遠不會蓋掉使用者自己抓的）。

    畫面標記那一行是 2026-08-21 補的，而且是必要的：card_templates 是使用者
    資料夾，既不進版控、也不放進更新包（`PROTECTED_DIRS`）。所以新增一張內建
    標記圖（例如 `ui_max_win.png`）時，它只會跟著 defaults/ui 送到使用者
    手上 —— 如果沒有人把它複製過去，card_templates 裡就永遠沒有那張圖，
    程式也就永遠讀不到（那正是「上限=無」的成因）。
    以前只有按「套用截圖預設框選」才會複製，而那顆按鈕會**覆蓋**六張標記，
    自己重新框選過的人根本不敢按。
    """
    root = project_root()
    os.chdir(root)
    ensure_runtime_dirs()
    install_default_parts(overwrite=False)
    install_default_ui_templates(overwrite=False)
    return root
