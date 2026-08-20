"""產生要上傳到 GitHub Releases 的更新包。

執行方式（在專案根目錄）：
    app\\.venv\\Scripts\\python.exe app\\packaging\\make_release.py

它會做三件事：
    1. 呼叫 build_exe.py 打包（加 --skip-build 可跳過）
    2. 把 app\\dist\\HoloTool\\ 壓成 app\\dist\\HoloTool-<版本>.zip
       —— **只放程式內容**，使用者的 config\\ card_templates\\ data\\
          logs\\ debug_captures\\ backups\\ 全部排除
    3. 算 SHA256 存成同名的 .sha256

最後印出「要在 GitHub 上怎麼發這個版本」的步驟。

常用參數：
    --skip-build     跳過 PyInstaller，直接用現有的 app\\dist\\HoloTool\\
    --installer      順便產生 HoloToolSetup.exe（給第一次安裝的人用）
    --out DIR        改變輸出資料夾（預設 app\\dist）

--------------------------------------------------------------------------
為什麼更新包裡不能有 config\\ 與 card_templates\\

那是使用者逐格校準的座標與自己在實機抓的點數/花色樣板。之前有一次打包腳本
用「檔案時間誰比較新誰是母本」做雙向同步，把使用者調好的五格手牌位置和
40 幾個樣板整組蓋掉，備份還只留一代又被下一次打包覆蓋，救不回來。

現在的規矩是：**更新包裡連這些資料夾都不存在**，所以無論置換邏輯怎麼寫，
物理上都不可能覆蓋到。`defaults\\` 例外 —— 那是內建（比較模糊的）樣板，
屬於程式內容；`paths.install_default_parts(overwrite=False)` 只會在使用者
還沒有同名檔案時才複製過去，不會蓋掉使用者自己抓的。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))   # app\packaging\
ROOT = os.path.dirname(HERE)                        # app\
PROJECT = os.path.dirname(ROOT)                     # 最外層
DIST = os.path.join(ROOT, "dist")
BUNDLE_DIR = os.path.join(DIST, "HoloTool")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.updater import PROTECTED_DIRS, SENTINEL          # noqa: E402
from src.version import __version__, asset_name, TAG_PREFIX  # noqa: E402

# 這些資料夾（不管出現在第幾層）一律不進更新包
EXCLUDE_DIRS = {p.lower() for p in PROTECTED_DIRS} | {"backups", "__pycache__"}
EXCLUDE_NAMES = {"holotool_apply_update.bat"}


def _find_python() -> str:
    """找虛擬環境的 python.exe；找不到就用目前這一個。

    一律用 `python.exe -m 模組`，不要叫 Scripts\\ 底下的 .exe ——
    虛擬環境搬過位置之後，那些啟動器裡寫死的舊路徑會失效。
    """
    for base in (ROOT, PROJECT):
        for sub in ("Scripts", "bin"):
            candidate = os.path.join(base, ".venv", sub,
                                     "python.exe" if os.name == "nt" else "python")
            if os.path.exists(candidate):
                return candidate
    return sys.executable


def _run(cmd: list[str], what: str) -> None:
    print(f"\n=== {what} ===")
    print(" ".join(f'"{c}"' if " " in c else c for c in cmd))
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"{what} 失敗（結束碼 {result.returncode}）")


def _skip(rel_path: str) -> bool:
    parts = [p.lower() for p in rel_path.replace("\\", "/").split("/") if p]
    if any(p in EXCLUDE_DIRS for p in parts[:-1]):
        return True
    return parts[-1] in EXCLUDE_NAMES


def build_zip(out_dir: str, version: str) -> str:
    """把 dist\\HoloTool\\ 壓成更新包，回傳 zip 路徑。"""
    if not os.path.exists(os.path.join(BUNDLE_DIR, SENTINEL)):
        raise SystemExit(
            f"找不到 {os.path.join(BUNDLE_DIR, SENTINEL)}。\n"
            "請先不要加 --skip-build，讓 build_exe.py 完整跑一次。"
        )
    os.makedirs(out_dir, exist_ok=True)
    zip_path = os.path.join(out_dir, asset_name(version))
    if os.path.exists(zip_path):
        os.remove(zip_path)

    added = 0
    skipped = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as zf:
        for dirpath, dirnames, filenames in os.walk(BUNDLE_DIR):
            # 直接把不要的資料夾從走訪清單裡拿掉，連進去看都不看
            dirnames[:] = [d for d in dirnames if d.lower() not in EXCLUDE_DIRS]
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, BUNDLE_DIR)
                if _skip(rel):
                    skipped += 1
                    continue
                zf.write(full, rel.replace("\\", "/"))
                added += 1

    print(f"\n更新包內容：{added} 個檔案"
          f"{f'（排除 {skipped} 個）' if skipped else ''}")
    _assert_no_user_data(zip_path)
    return zip_path


def _assert_no_user_data(zip_path: str) -> None:
    """最後一道自我檢查：更新包裡真的一個使用者資料檔都沒有。

    這個檢查值得留著。萬一哪天有人改了排除清單卻沒改這裡，
    發版當下就會失敗，而不是等到使用者的樣板被蓋掉才發現。
    """
    bad: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            parts = [p.lower() for p in name.split("/") if p]
            if any(p in {d.lower() for d in PROTECTED_DIRS} for p in parts[:-1]):
                bad.append(name)
    if bad:
        os.remove(zip_path)
        raise SystemExit(
            "更新包裡出現了使用者資料，已刪除這個 zip 並中止發版：\n  "
            + "\n  ".join(bad[:10])
        )
    print("自我檢查通過：更新包裡沒有任何 config / card_templates / data / logs 檔案。")


def write_sha256(zip_path: str) -> str:
    digest = hashlib.sha256()
    with open(zip_path, "rb") as f:
        while True:
            block = f.read(1 << 20)
            if not block:
                break
            digest.update(block)
    value = digest.hexdigest()
    out = zip_path + ".sha256"
    with open(out, "w", encoding="ascii", newline="\n") as f:
        f.write(f"{value}  {os.path.basename(zip_path)}\n")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="產生 GitHub Release 用的更新包")
    parser.add_argument("--skip-build", action="store_true",
                        help="跳過 PyInstaller，直接用現有的 app\\dist\\HoloTool\\")
    parser.add_argument("--installer", action="store_true",
                        help="順便產生 HoloToolSetup.exe（給第一次安裝的人）")
    parser.add_argument("--out", default=DIST, help="輸出資料夾，預設 app\\dist")
    args = parser.parse_args()

    version = __version__
    tag = f"{TAG_PREFIX}{version}"
    print(f"要發布的版本：{version}（tag {tag}）")
    print("版本號改的地方只有一個：app\\src\\version.py 的 __version__")

    os.chdir(ROOT)
    python = _find_python()

    if not args.skip_build:
        _run([python, os.path.join(HERE, "build_exe.py")], "PyInstaller 打包")

    zip_path = build_zip(args.out, version)
    sha = write_sha256(zip_path)
    size_mb = os.path.getsize(zip_path) / (1024 * 1024)

    setup = ""
    if args.installer:
        _run([python, os.path.join(HERE, "build_installer.py"),
              "--skip-build", "--version", version], "Inno Setup 安裝檔")
        candidate = os.path.join(DIST, "HoloToolSetup.exe")
        setup = candidate if os.path.exists(candidate) else ""

    print("\n" + "=" * 66)
    print(f"更新包已產生：{zip_path}   ({size_mb:.1f} MB)")
    print(f"SHA256      ：{sha}")
    print(f"雜湊檔      ：{zip_path}.sha256")
    if setup:
        print(f"安裝檔      ：{setup}")
    print()
    print("接下來在 GitHub 上發布這一版（網頁操作，不用打指令）：")
    print("  1. 進 repo → 右邊 Releases → Draft a new release")
    print(f"  2. Choose a tag → 輸入 {tag} → Create new tag on publish")
    print(f"  3. Release title 填 {tag}，說明寫這一版改了什麼")
    print("  4. 把下面這兩個檔案拖進 Attach binaries 區塊：")
    print(f"       {os.path.basename(zip_path)}")
    print(f"       {os.path.basename(zip_path)}.sha256")
    if setup:
        print(f"     （想讓新人直接下載安裝檔，也可以一起拖 {os.path.basename(setup)}）")
    print("  5. 想先自己試裝就勾 Set as a pre-release —— 勾了之後")
    print("     其他人按「檢查更新」不會看到這一版。試完再取消勾選。")
    print("  6. Publish release")
    print()
    print("發布完成後，舊版按「檢查更新」就會看到 "
          f"v{version}。")
    print("=" * 66)


if __name__ == "__main__":
    main()
