"""一鍵產生可以拿給別人的安裝程式 `dist\\HoloToolSetup.exe`。

執行方式（在專案根目錄）：
    .venv\\Scripts\\python.exe app\\packaging\\build_installer.py

它會依序做三件事：
    1. `make_icon.py`   —— 把 app\\packaging\\icon.png 轉成 icon.ico（有圖才做）
    2. `build_exe.py`   —— 用 PyInstaller 打包成 dist\\HoloTool\\（含 Python，
                           對方電腦不需要安裝 Python）
    3. `ISCC.exe`       —— 用 Inno Setup 把整包壓成單一安裝檔

常用參數：
    --skip-build        跳過步驟 2（剛剛才打包過、只想重做安裝檔時用）
    --version 1.2.0     指定版本號（**平常不需要指定** —— 預設會讀
                        `app\\src\\version.py` 的 __version__，那是版本號的
                        唯一來源。手動指定只是為了臨時測試，不要用它發版，
                        否則安裝檔的版本會跟程式自己回報的版本不一致，
                        「檢查更新」就會比對到錯的東西。）

第一次使用要先裝 Inno Setup 6（免費，只有你這台打包機需要，
拿到安裝檔的人不用裝）：https://jrsoftware.org/isdl.php
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # app\packaging\
ROOT = os.path.dirname(HERE)                        # app\
PROJECT = os.path.dirname(ROOT)                     # 最外層
DIST = os.path.join(ROOT, "dist")
BUNDLE_DIR = os.path.join(DIST, "HoloTool")
ISS_PATH = os.path.join(HERE, "HoloTool.iss")
ICON_PNG = os.path.join(HERE, "icon.png")
ICON_ICO = os.path.join(HERE, "icon.ico")

# 版本號的唯一來源是 app\src\version.py。以前預設值寫死在這裡的 "1.0.0"，
# 於是「安裝檔說 1.2.0、程式自己說 1.0.0」，檢查更新就會比對到錯的版本。
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from src.version import __version__ as DEFAULT_VERSION  # noqa: E402

ISCC_CANDIDATES = [
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
    r"C:\Program Files\Inno Setup 5\ISCC.exe",
]


def _find_python() -> str:
    """找虛擬環境的 python.exe；找不到就用目前這一個。

    一律用 python.exe 去跑東西，不要叫 Scripts\\ 底下那些 .exe ——
    虛擬環境搬過位置之後，那些啟動器裡寫死的舊路徑會失效。
    """
    for base in (ROOT, PROJECT):
        for sub in ("Scripts", "bin"):
            candidate = os.path.join(base, ".venv", sub,
                                     "python.exe" if os.name == "nt" else "python")
            if os.path.exists(candidate):
                return candidate
    return sys.executable


def find_iscc() -> str:
    """找 Inno Setup 的命令列編譯器。找不到就給明確的安裝指引。"""
    on_path = shutil.which("ISCC")
    if on_path:
        return on_path
    for path in ISCC_CANDIDATES:
        if os.path.exists(path):
            return path
    # 註冊表裡也可能有（自訂安裝路徑）
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
                try:
                    key = winreg.OpenKey(
                        hive,
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
                        r"\Inno Setup 6_is1",
                        0, winreg.KEY_READ | view,
                    )
                    loc, _ = winreg.QueryValueEx(key, "InstallLocation")
                    candidate = os.path.join(loc, "ISCC.exe")
                    if os.path.exists(candidate):
                        return candidate
                except OSError:
                    continue
    except ImportError:
        pass
    raise SystemExit(
        "找不到 Inno Setup 的編譯器 ISCC.exe。\n\n"
        "請先安裝 Inno Setup 6（免費，約 5MB）：\n"
        "    https://jrsoftware.org/isdl.php\n"
        "下載 innosetup-6.x.x.exe，一路 Next 裝完之後再執行一次這個腳本。\n\n"
        "（只有你這台「打包用」的電腦需要裝。拿到 HoloToolSetup.exe 的人\n"
        "  什麼都不用裝，雙擊就能安裝。）"
    )


def has_chinese_language(iscc: str) -> bool:
    """Inno Setup 內建語言不含繁體中文（那是非官方翻譯，要自己放進去）。

    有就用，沒有就退回英文介面 —— 安裝畫面只有 Next/Install/Finish，
    看得懂英文按鈕就夠了，我們自訂的說明文字本來就是中文。
    """
    langs = os.path.join(os.path.dirname(iscc), "Languages", "ChineseTraditional.isl")
    return os.path.exists(langs)


def run(cmd: list[str], what: str) -> None:
    print(f"\n=== {what} ===")
    print(" ".join(f'"{c}"' if " " in c else c for c in cmd))
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"{what} 失敗（結束碼 {result.returncode}）")


def main() -> None:
    parser = argparse.ArgumentParser(description="打包 HoloTool 安裝程式")
    parser.add_argument("--skip-build", action="store_true",
                        help="跳過 PyInstaller，直接用現有的 dist\\HoloTool\\")
    parser.add_argument("--version", default=DEFAULT_VERSION,
                        help=f"版本號，預設讀 src/version.py（目前 {DEFAULT_VERSION}）")
    args = parser.parse_args()

    if args.version != DEFAULT_VERSION:
        print(f"[注意] 你指定的版本 {args.version} 跟程式自己回報的 "
              f"{DEFAULT_VERSION} 不一致。")
        print("       要正式發版請改 app\\src\\version.py 的 __version__，"
              "不要用 --version。")

    os.chdir(ROOT)
    python = _find_python()
    print(f"使用的 Python：{python}")

    # 1. 圖示
    if os.path.exists(ICON_PNG):
        run([python, os.path.join(HERE, "make_icon.py")], "產生圖示 icon.ico")
    elif not os.path.exists(ICON_ICO):
        print("\n[提醒] 找不到 app\\packaging\\icon.png，這次會用 Windows 預設圖示。")
        print("       想換成自己的圖：把圖片存成 icon.png 放進 app\\packaging\\，再跑一次。")

    # 2. 打包 exe
    if not args.skip_build:
        run([python, os.path.join(HERE, "build_exe.py")], "PyInstaller 打包")
    if not os.path.exists(os.path.join(BUNDLE_DIR, "HoloTool.exe")):
        raise SystemExit(
            "找不到 dist\\HoloTool\\HoloTool.exe。\n"
            "請先不要加 --skip-build，讓它完整跑一次打包。"
        )

    # 3. 編譯安裝程式
    iscc = find_iscc()
    cmd = [iscc, f"/DMyAppVersion={args.version}"]
    if os.path.exists(ICON_ICO):
        cmd.append("/DHaveIcon")
    if has_chinese_language(iscc):
        cmd.append("/DHaveChinese")
    else:
        print("\n[提醒] Inno Setup 沒有繁體中文語言檔，安裝畫面會是英文。")
        print("       想要中文介面的話，到 https://jrsoftware.org/files/istrans/")
        print("       下載 ChineseTraditional.isl，放進 Inno Setup 的 Languages 資料夾。")
    cmd.append(ISS_PATH)
    run(cmd, "Inno Setup 編譯安裝程式")

    setup = os.path.join(DIST, "HoloToolSetup.exe")
    if not os.path.exists(setup):
        raise SystemExit("編譯完成但找不到 dist\\HoloToolSetup.exe，請看上面的訊息。")

    size_mb = os.path.getsize(setup) / (1024 * 1024)
    print("\n" + "=" * 60)
    print("完成！要拿給別人的就是這一個檔案：")
    print(f"  {setup}   ({size_mb:.1f} MB)")
    print()
    print("對方只要雙擊它，選一個資料夾按 Install 就好；")
    print("不需要安裝 Python，也不需要裝任何其他東西。")
    print("=" * 60)


if __name__ == "__main__":
    main()
