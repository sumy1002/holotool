"""修好「搬過位置」的虛擬環境。

執行方式（在專案根目錄）：
    app\\.venv\\Scripts\\python.exe app\\packaging\\repair_venv.py

## 為什麼會壞

Windows 的虛擬環境裡，`Scripts\\` 底下那些 `pip.exe`、`pyinstaller.exe`…
其實是很小的啟動器程式，**把「建立當下的 python.exe 絕對路徑」寫死在檔頭**。

所以整個 `.venv` 一搬家（例如從 `F:\\holotool\\.venv` 搬進
`F:\\holotool\\app\\.venv`），這些啟動器就會去找一個已經不存在的路徑，
執行起來只丟一個結束碼 1。看起來像 PyInstaller 壞了，其實是啟動器壞了。

`python.exe` 本身不受影響 —— 它是靠旁邊的 `pyvenv.cfg` 定位的。
所以只要用 `python.exe -m pip ...` 就能把那些啟動器重新產生一次。

## 這支腳本做什麼

1. 確認 `pyvenv.cfg` 指向的基礎 Python 還在
2. `python -m pip install --force-reinstall --no-deps pip`  → 修好 pip.exe
3. 依 requirements.txt 重裝一次（`--force-reinstall --no-deps`，只重建啟動器，
   不會重新下載相依套件的完整依賴樹）
4. 順便把 pyinstaller 也裝好

跑完之後 `Scripts\\` 底下的 .exe 就會指向新的位置。

> 其實平常只要習慣用 `python.exe -m 模組名` 就完全不會遇到這個問題，
> 打包腳本現在也都改成這樣了。這支腳本是給你想直接敲 `pip install` 時用的。
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
PROJECT = os.path.dirname(APP)
REQUIREMENTS = os.path.join(PROJECT, "requirements.txt")


def _venv_python() -> str:
    for base in (APP, PROJECT):
        candidate = os.path.join(base, ".venv", "Scripts", "python.exe")
        if os.path.exists(candidate):
            return candidate
    return sys.executable


def _run(args: list[str], what: str) -> bool:
    print(f"\n=== {what} ===")
    result = subprocess.run(args)
    if result.returncode != 0:
        print(f"  [失敗] 結束碼 {result.returncode}")
        return False
    return True


def main() -> None:
    python = _venv_python()
    print(f"要修的虛擬環境：{python}")
    if not os.path.exists(python):
        raise SystemExit("找不到虛擬環境的 python.exe，請確認 .venv 的位置。")

    cfg = os.path.join(os.path.dirname(os.path.dirname(python)), "pyvenv.cfg")
    if os.path.exists(cfg):
        with open(cfg, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.lower().startswith("home"):
                    home = line.split("=", 1)[1].strip()
                    status = "存在" if os.path.isdir(home) else "**不存在**"
                    print(f"基礎 Python：{home}（{status}）")
                    if not os.path.isdir(home):
                        raise SystemExit(
                            "基礎 Python 已經不在原處，虛擬環境救不回來。\n"
                            "請重建：\n"
                            "    python -m venv app\\.venv\n"
                            "    app\\.venv\\Scripts\\python.exe -m pip install "
                            "-r requirements.txt"
                        )
                    break

    ok = True
    ok &= _run([python, "-m", "pip", "install", "--force-reinstall", "--no-deps",
                "pip", "setuptools", "wheel"], "重建 pip / setuptools 的啟動器")
    if os.path.exists(REQUIREMENTS):
        ok &= _run([python, "-m", "pip", "install", "--force-reinstall", "--no-deps",
                    "-r", REQUIREMENTS], "依 requirements.txt 重建啟動器")
    ok &= _run([python, "-m", "pip", "install", "pyinstaller"], "確認 PyInstaller")

    print("\n" + "=" * 58)
    if ok:
        print("修好了。接著就可以跑：")
        print(f'  "{python}" app\\packaging\\build_exe.py')
    else:
        print("有步驟失敗，往上看訊息。最保險的做法是直接重建虛擬環境：")
        print("  python -m venv app\\.venv")
        print("  app\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt")
    print("=" * 58)


if __name__ == "__main__":
    main()
