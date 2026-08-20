"""把專案資料夾整理乾淨：最外層只留下四樣東西。

執行方式（在專案根目錄）：
    .venv\\Scripts\\python.exe app\\packaging\\tidy_project.py
.venv 已經搬進 app\\ 之後則是：
    app\\.venv\\Scripts\\python.exe app\\packaging\\tidy_project.py

整理後的樣子：

    holotool\\
      gui.py               ← 程式本人
      README.md
      requirements.txt
      app\\                 ← 其餘全部收在這裡面
        src\\  tools\\  packaging\\  docs\\  tests\\
        config\\  card_templates\\  defaults\\  data\\  logs\\  debug_captures\\
        dist\\  .venv\\

這支腳本**可以重複執行**，該搬的搬完之後再跑一次不會有任何動作。
它也不會亂刪東西 —— 只有在「新位置已經有同一個檔案」時，才刪最外層的舊副本。

`.venv` 是唯一的例外：如果你正是用它裡面的 python.exe 在跑這支腳本，
Windows 會鎖住執行中的檔案而搬不動。遇到這種情況它會產生一個
`move_venv.bat`，關掉終端機之後雙擊它就會搬完。
"""
from __future__ import annotations

import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))     # app\packaging\
APP = os.path.dirname(HERE)                           # app\
PROJECT = os.path.dirname(APP)                        # 最外層
APP_NAME = os.path.basename(APP)

# 最外層允許留下的東西
KEEP_AT_TOP = {"gui.py", "README.md", "requirements.txt", APP_NAME}

# 上一階段搬過家的檔案：舊位置 → 現在應該在哪（相對 app\）
RELOCATED = {
    "run_bot.py": "tools/run_bot.py",
    "calibrate.py": "tools/calibrate.py",
    "collect_templates.py": "tools/collect_templates.py",
    "check_setup.py": "tools/check_setup.py",
    "check_markers.py": "tools/check_markers.py",
    "build_exe.py": "packaging/build_exe.py",
    "build_installer.py": "packaging/build_installer.py",
    "make_icon.py": "packaging/make_icon.py",
    "tidy_project.py": "packaging/tidy_project.py",
    "installer/HoloTool.iss": "packaging/HoloTool.iss",
    "診斷報告.md": "docs/診斷報告.md",
}

# 圖示來源：不管原本叫什麼，統一變成 app\packaging\icon.png
ICON_CANDIDATES = ("main.png", "icon.png", "logo.png")

# 產物／暫存，砍掉即可（下次打包會自己重建）
DISPOSABLE = ("build", "HoloTool.spec", "HoloTool.lnk", "__pycache__")

# 要收進 app\ 的資料夾
MOVE_INTO_APP = (
    "src", "tools", "packaging", "docs", "tests",
    "config", "card_templates", "defaults", "data", "logs", "debug_captures",
    "dist", ".venv",
)


def _running_from(path: str) -> bool:
    """目前這個 python.exe 是不是就在 path 底下？在的話 Windows 會鎖住搬不動。"""
    try:
        return os.path.commonpath(
            [os.path.abspath(sys.executable), os.path.abspath(path)]
        ) == os.path.abspath(path)
    except ValueError:      # 不同磁碟機
        return False


def _merge_move(src: str, dst: str) -> int:
    """把 src\\ 的內容搬進 dst\\。兩邊都有的同名檔案，**保留 dst 那一份**。

    為什麼需要合併而不是單純 rename：更新程式碼時新檔案會先被放到
    app\\packaging\\ 這種新位置，於是「目的地已經存在」。這時直接跳過會留下
    半新半舊兩份；直接覆蓋又會把剛更新的檔案蓋回舊版。
    所以規則定成「新位置的優先」，舊的那份搬完就刪掉。
    """
    moved = 0
    for dirpath, _dirnames, filenames in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        target_dir = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target_dir, exist_ok=True)
        for name in filenames:
            s = os.path.join(dirpath, name)
            d = os.path.join(target_dir, name)
            if os.path.exists(d):
                os.remove(s)            # 新位置已經有了，舊的直接丟掉
            else:
                shutil.move(s, d)
                moved += 1
    shutil.rmtree(src, ignore_errors=True)
    return moved


def _write_move_venv_bat(src: str, dst: str) -> str:
    bat = os.path.join(PROJECT, "move_venv.bat")
    with open(bat, "w", encoding="cp950", errors="replace", newline="\r\n") as f:
        f.write(
            "@echo off\n"
            "chcp 950 >nul\n"
            "echo 正在搬移虛擬環境，請稍候...\n"
            "timeout /t 2 >nul\n"
            f'move "{src}" "{dst}"\n'
            "if errorlevel 1 (\n"
            "  echo.\n"
            "  echo 搬移失敗。請確認沒有終端機或編輯器正在使用這個虛擬環境，再試一次。\n"
            ") else (\n"
            "  echo 完成！之後指令請改用 app\\.venv\\Scripts\\python.exe\n"
            '  del "%~f0"\n'
            ")\n"
            "pause\n"
        )
    return bat


def main() -> None:
    done: list[str] = []
    skipped: list[str] = []
    notes: list[str] = []

    # ---- 1. 上一階段的舊副本：新位置有東西才刪 ----
    for old_rel, new_rel in RELOCATED.items():
        old = os.path.join(PROJECT, old_rel.replace("/", os.sep))
        new = os.path.join(APP, new_rel.replace("/", os.sep))
        if not os.path.exists(old):
            continue
        if not (os.path.isfile(new) and os.path.getsize(new) > 0):
            skipped.append(f"{old_rel}（{APP_NAME}\\{new_rel} 還沒有檔案，先不刪）")
            continue
        os.remove(old)
        done.append(f"刪除舊副本 {old_rel}")

    # ---- 2. 圖示統一放 app\packaging\icon.png ----
    target_icon = os.path.join(APP, "packaging", "icon.png")
    for name in ICON_CANDIDATES:
        src = os.path.join(PROJECT, name)
        if not os.path.isfile(src):
            continue
        if os.path.exists(target_icon):
            os.remove(src)
            done.append(f"刪除重複的圖片 {name}")
        else:
            os.makedirs(os.path.dirname(target_icon), exist_ok=True)
            shutil.move(src, target_icon)
            done.append(f"搬移 {name} → {APP_NAME}\\packaging\\icon.png")
        break

    # ---- 3. 產物與暫存 ----
    for rel in DISPOSABLE:
        path = os.path.join(PROJECT, rel)
        if not os.path.exists(path):
            continue
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            done.append(f"清掉 {rel}")
        except OSError as e:
            skipped.append(f"{rel}（{e}）")
    for root, dirnames, _files in os.walk(APP):
        for name in list(dirnames):
            if name == "__pycache__":
                shutil.rmtree(os.path.join(root, name), ignore_errors=True)
                dirnames.remove(name)

    # ---- 4. 把剩下的資料夾收進 app\ ----
    for name in MOVE_INTO_APP:
        src = os.path.join(PROJECT, name)
        dst = os.path.join(APP, name)
        if not os.path.isdir(src):
            continue
        if name == ".venv" and _running_from(src):
            bat = _write_move_venv_bat(src, dst)
            notes.append(
                ".venv 搬不動 —— 你正在用它裡面的 python.exe 執行這支腳本。\n"
                f"      請關掉這個終端機，再雙擊最外層的 {os.path.basename(bat)} 完成搬移。\n"
                "      （搬完之後指令要改成 app\\.venv\\Scripts\\python.exe ...）"
            )
            continue
        try:
            if os.path.exists(dst):
                n = _merge_move(src, dst)
                done.append(f"合併 {name}\\ → {APP_NAME}\\{name}\\（搬了 {n} 個檔，"
                            "同名的以新位置那份為準）")
            else:
                shutil.move(src, dst)
                done.append(f"搬移 {name}\\ → {APP_NAME}\\{name}\\")
        except OSError as e:
            skipped.append(f"{name}\\（{e}）")

    # ---- 5. 空掉的資料夾 ----
    leftover = os.path.join(PROJECT, "installer")
    if os.path.isdir(leftover) and not os.listdir(leftover):
        os.rmdir(leftover)
        done.append("移除空資料夾 installer\\")

    # ---- 報告 ----
    print("=" * 60)
    if done:
        print(f"整理完成，共 {len(done)} 項：")
        for line in done:
            print("  ·", line)
    else:
        print("已經是整理過的狀態，沒有東西需要搬。")
    if notes:
        print("\n要你動手的部分：")
        for line in notes:
            print("  ·", line)
    if skipped:
        print("\n沒有處理（請自己確認）：")
        for line in skipped:
            print("  ·", line)
    print("=" * 60)

    print("\n目前最外層：")
    for name in sorted(os.listdir(PROJECT)):
        mark = "\\" if os.path.isdir(os.path.join(PROJECT, name)) else ""
        extra = "" if name in KEEP_AT_TOP or name == "move_venv.bat" else "   ← 還沒收進去"
        print(f"  {name}{mark}{extra}")


if __name__ == "__main__":
    main()
