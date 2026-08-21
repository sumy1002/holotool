"""把 HoloTool GUI 打包成可雙擊開啟的 Windows exe。

執行方式（在專案根目錄）：
    .venv\\Scripts\\python.exe app\\packaging\\build_exe.py

完成後請雙擊：
    dist\\HoloTool\\HoloTool.exe
或桌面上的「HoloTool」捷徑。

要做成給別人的安裝檔請改跑 app\\packaging\\build_installer.py（它會先呼叫這一支）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

# HERE    = app\packaging\
# ROOT    = app\        —— 程式與資料都在這裡，src / config / card_templates 都在底下
# PROJECT = 最外層       —— 只放 gui.py / README.md / requirements.txt
# PyInstaller 的暫存與 .spec 都丟在 app\packaging\ 底下，不弄髒任何一層。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PROJECT = os.path.dirname(ROOT)
DIST_DIR = os.path.join(ROOT, "dist", "HoloTool")
# 裝好之後 exe 旁邊只會有一個資料夾，程式內容與所有資料都在裡面。
# 名稱要和 src/paths.py 的 BUNDLE_SUBDIR、HoloTool.iss 的路徑一致。
BUNDLE_SUBDIR = "app"
DATA_DIR = os.path.join(DIST_DIR, BUNDLE_SUBDIR)
WORK_PATH = os.path.join(HERE, "build")
SPEC_PATH = os.path.join(HERE, "HoloTool.spec")


def _create_shortcut(target: str, shortcut_path: str, workdir: str) -> None:
    # 用 PowerShell 建立 .lnk，避免額外依賴
    ps = (
        "$s = New-Object -ComObject WScript.Shell; "
        f"$l = $s.CreateShortcut('{shortcut_path.replace(chr(39), chr(39)+chr(39))}'); "
        f"$l.TargetPath = '{target.replace(chr(39), chr(39)+chr(39))}'; "
        f"$l.WorkingDirectory = '{workdir.replace(chr(39), chr(39)+chr(39))}'; "
        "$l.Description = 'Hololive Dreams High & Low 自動化工具'; "
        "$l.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        check=False,
        cwd=ROOT,
    )


# 實際在用的資料（校準、樣板、統計）平常是存在 exe 旁邊，也就是 dist\HoloTool\。
# PyInstaller 的 --noconfirm 會把整個 dist\HoloTool\ 砍掉重建 ——
# 如果不先搬回專案根目錄，辛苦蒐集的樣板與校準會在打包當下全部消失。
RUNTIME_DATA_DIRS = ("config", "card_templates", "data")


def _live_dir(folder: str) -> str:
    """exe 那邊某個資料的實際位置。

    新版放在 dist\\HoloTool\\app\\，舊版是攤平在 dist\\HoloTool\\。
    升級的時候兩種都要認得，否則會把舊版蒐集的樣板當成不存在而漏掉。
    """
    nested = os.path.join(DATA_DIR, folder)
    if os.path.isdir(nested):
        return nested
    return os.path.join(DIST_DIR, folder)


def _promote_runtime_data() -> int:
    """把 dist\\HoloTool\\ 裡「比較新」的資料搬回 app\\，讓 app\\ 成為母本。

    平常執行的是 exe，所以最新的校準與樣板都在 dist 那邊；app\\ 那份反而是舊的。
    打包前先反向同步一次，PyInstaller 砍掉 dist 之後才能原封不動放回去。

    **時間新舊只能判斷「哪個晚寫」，不能判斷「哪個才是對的」。**
    2026-08-20 就吃過虧：從原始碼開 gui.py 會讀寫 app\\config\\config.json，
    而 exe 讀寫的是 dist\\HoloTool\\config\\config.json。開一次 gui.py 就讓
    app\\ 那份「變新」，於是這裡把舊的、沒有逐格校準的版本判定為母本，
    把 exe 那份調好的五格手牌位置蓋掉了。
    所以現在：**只要兩邊內容不同，被換掉的那份一律留一個 .bak**。
    """
    moved = 0
    for folder in RUNTIME_DATA_DIRS:
        live = _live_dir(folder)
        master = os.path.join(ROOT, folder)
        if not os.path.isdir(live):
            continue
        for dirpath, _dirnames, filenames in os.walk(live):
            rel = os.path.relpath(dirpath, live)
            dest_dir = master if rel == "." else os.path.join(master, rel)
            os.makedirs(dest_dir, exist_ok=True)
            for name in filenames:
                s = os.path.join(dirpath, name)
                d = os.path.join(dest_dir, name)
                if not os.path.exists(d):
                    shutil.copy2(s, d)
                    moved += 1
                elif _differs(s, d):
                    newer_is_dist = os.path.getmtime(s) > os.path.getmtime(d) + 1
                    loser, winner = (d, s) if newer_is_dist else (s, d)
                    _keep_backup(loser)
                    if newer_is_dist:
                        shutil.copy2(winner, d)
                        moved += 1
    return moved


def _differs(a: str, b: str) -> bool:
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return True
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() != fb.read()
    except OSError:
        return True


def _keep_backup(path: str) -> None:
    """把即將被取代（或被冷落）的那份留下來，檔名帶編號，不會互相覆蓋。"""
    base = path + ".bak"
    i = 0
    candidate = base
    while os.path.exists(candidate):
        i += 1
        candidate = f"{base}{i}"
    try:
        shutil.copy2(path, candidate)
        print(f"  [保留] {os.path.basename(path)} 兩邊內容不同，"
              f"已備份成 {os.path.basename(candidate)}")
    except OSError:
        pass


def _report_card_slots() -> None:
    """把即將被打包進去的五格手牌位置印出來，順便提醒是不是被還原成等寬了。

    五格是逐格校準的，實機量出來間距**並不等寬**。如果印出來變成完全等距、
    而且寬高都一樣，那多半是不小心按到「套用截圖預設框選」或讀到舊設定，
    這時候先別打包，回 GUI 確認一下。
    """
    path = os.path.join(ROOT, "config", "config.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            slots = json.load(f)["regions"]["card_slots"]
    except (OSError, ValueError, KeyError):
        return
    xs = [round(s["x"], 4) for s in slots]
    print(f"即將打包的五格手牌 x = {xs}")
    gaps = [round(b - a, 4) for a, b in zip(xs, xs[1:])]
    same_size = len({(round(s["w"], 4), round(s["h"], 4)) for s in slots}) == 1
    if len(set(gaps)) == 1 and same_size:
        print("  [注意] 五格完全等距而且大小一樣，看起來是預設值而不是你逐格校準的。")
        print("         若不是你故意的，先去 GUI 校準分頁確認，不要急著打包。")


def _find_pyinstaller():
    """回傳「用哪個 python 跑 PyInstaller」的指令前綴，例如
    `[".../app/.venv/Scripts/python.exe", "-m", "PyInstaller"]`。

    **一定要用 `python.exe -m PyInstaller`，不可以直接叫 `pyinstaller.exe`。**
    Windows 的虛擬環境裡，`Scripts\\*.exe` 那些啟動器把「當初建立時的
    python.exe 絕對路徑」寫死在檔頭。整個 .venv 搬過位置之後（例如從
    最外層搬進 app\\），那些 .exe 就會指向一個不存在的路徑而直接失敗，
    只丟一個結束碼 1，看起來像 PyInstaller 壞掉，其實是啟動器壞掉。
    `python.exe` 自己則是靠旁邊的 pyvenv.cfg 定位，搬家之後照常可用。
    """
    python = _find_python()
    probe = subprocess.run(
        [python, "-c", "import PyInstaller"],
        capture_output=True,
    )
    if probe.returncode != 0:
        return None
    return [python, "-m", "PyInstaller"]


def _supports_contents_directory(python: str) -> bool:
    """PyInstaller 有沒有 --contents-directory（6.0 以後才有）。"""
    probe = subprocess.run(
        [python, "-c",
         "import PyInstaller,sys;"
         "sys.stdout.write(PyInstaller.__version__)"],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        return False
    try:
        major = int(probe.stdout.strip().split(".")[0])
    except (ValueError, IndexError):
        return False
    return major >= 6


def _locked_files(folder: str, limit: int = 6) -> list[str]:
    """列出 folder 底下「現在打不開來寫」的執行檔／DLL。

    Windows 上這幾乎一定代表**那個程式正在執行**：執行中的 exe / dll 映像檔
    是以 FILE_SHARE_READ 開著的，所以任何人都讀得到、但誰都不能寫或刪。

    為什麼要先檢查：PyInstaller 的 `--noconfirm` 會在 COLLECT 階段把整個
    `dist\\HoloTool\\` 砍掉重建。HoloTool 還開著的時候那一步一定失敗，而
    PyInstaller 只回一個結束碼 1 —— 前面 Analysis / PYZ / EXE 都成功了，
    看起來像「打包壞掉」，實際上只是要先關掉程式。這種錯誤訊息不值得再查一次。

    用 `open(path, "r+b")` 探測：要求寫入權限但不寫任何東西，所以不會改到檔案。
    """
    if not os.path.isdir(folder):
        return []
    locked: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(folder):
        for name in filenames:
            if not name.lower().endswith((".exe", ".dll", ".pyd")):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, "r+b"):
                    pass
            except OSError:
                locked.append(path)
                if len(locked) >= limit:
                    return locked
    return locked


def _short(path: str) -> str:
    """盡量印相對路徑，跨磁碟時退回絕對路徑。

    `os.path.relpath` 在 Windows 上遇到**不同磁碟**會丟
    `ValueError: path is on mount 'C:', start on mount 'F:'`。
    這個函式只用在「打包失敗、要把原因印出來」的路徑上 ——
    在那裡丟例外等於把真正的錯誤原因換成一個不相干的 traceback，
    是所有壞法裡最糟的一種。
    """
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:
        return path


def _abort_if_output_locked() -> None:
    locked = _locked_files(DIST_DIR)
    if not locked:
        return
    listing = "\n".join(f"    {_short(p)}" for p in locked)
    raise SystemExit(
        "\n打包中止：dist\\HoloTool\\ 裡有檔案正在使用中，PyInstaller 沒辦法把它清掉。\n\n"
        f"{listing}\n\n"
        "**請先把 HoloTool 完全關掉再重跑一次**：\n"
        "  · 主視窗關掉\n"
        "  · 「縮成迷你視窗」的那個小方塊也要關（它沒有標題列，很容易忘記）\n"
        "  · 工作管理員裡如果還看得到 HoloTool.exe，直接結束它\n\n"
        "（這次什麼都沒有被改動，dist\\HoloTool 還是舊版，可以照常使用。）"
    )


def _run_pyinstaller(cmd: list[str]) -> tuple[int, str]:
    """跑 PyInstaller，一邊即時印出、一邊完整寫進 log 檔。

    回傳 (結束碼, log 路徑)。

    為什麼要留 log：PyInstaller 動輒印幾百行，真正的錯誤在中間某一行，
    而終端機的回捲緩衝區常常已經蓋掉了 —— 尤其是從 release.bat 一路跑下來的時候。
    有一個檔案在，就永遠問得出「到底是哪一行爆的」。
    """
    os.makedirs(WORK_PATH, exist_ok=True)
    log_path = os.path.join(WORK_PATH, "pyinstaller-last.log")
    with open(log_path, "w", encoding="utf-8") as log:
        process = subprocess.Popen(
            cmd, cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        with process:
            assert process.stdout is not None
            for line in process.stdout:
                line = line.rstrip("\n")
                log.write(line + "\n")
                try:
                    print(line)
                except UnicodeEncodeError:
                    # 某些主控台編碼吃不下 PyInstaller 印出來的路徑，不該中斷打包
                    print(line.encode("ascii", "replace").decode("ascii"))
            code = process.wait()
    return code, log_path


def _tail(path: str, lines: int = 30) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-lines:])
    except OSError:
        return ""


def _find_python() -> str:
    """找虛擬環境的 python.exe；找不到就用目前這一個。"""
    for base in (ROOT, PROJECT):
        for sub in ("Scripts", "bin"):
            candidate = os.path.join(base, ".venv", sub,
                                     "python.exe" if os.name == "nt" else "python")
            if os.path.exists(candidate):
                return candidate
    return sys.executable


def _backup_dist() -> str:
    """打包前整包備份，萬一同步邏輯有漏還救得回來。

    保留**兩代**。之前只留一代，結果第一次打包把好的備份起來、第二次打包又拿
    已經壞掉的 dist 去覆蓋那份好備份，等於白備份。兩代就有緩衝。
    """
    if not os.path.isdir(DIST_DIR):
        return ""
    backup = os.path.join(ROOT, "dist", "HoloTool_backup")
    older = os.path.join(ROOT, "dist", "HoloTool_backup_prev")
    try:
        if os.path.isdir(backup):
            shutil.rmtree(older, ignore_errors=True)
            os.rename(backup, older)
        shutil.copytree(DIST_DIR, backup)
        return backup
    except OSError:
        return ""


def _print_version_banner() -> str:
    """把「這次要打包的是哪個版本」印在最前面。

    2026-08-20 浪費過一輪：改了 version.py 但看到的還是舊版本號，
    其實是在看另一個資料夾的 exe（安裝版）。版本號印在開頭與結尾各一次，
    就不會再有「到底打包成功了沒」的疑問。
    """
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    try:
        from src.version import __version__ as version
    except ImportError:
        return "?"
    print("=" * 60)
    print(f"  要打包的版本：v{version}")
    print(f"  來源：{os.path.join(ROOT, 'src', 'version.py')}")
    print("=" * 60)
    return version


def main() -> None:
    os.chdir(ROOT)

    version = _print_version_banner()
    # 先確認 dist\HoloTool 可以被清掉，再花好幾分鐘做 Analysis。
    # 反過來的順序（PyInstaller 跑完才發現刪不掉）就是「等三分鐘換一個結束碼 1」。
    _abort_if_output_locked()
    backup = _backup_dist()
    if backup:
        print(f"已備份目前的 dist\\HoloTool → {backup}")
    moved = _promote_runtime_data()
    if moved:
        print(f"已把 {moved} 個較新的校準/樣板檔從 dist 搬回 app\\（避免打包時被清掉）")
    _report_card_slots()

    python = _find_python()
    print(f"使用的 Python：{python}")
    pyinstaller = _find_pyinstaller()
    if pyinstaller is None:
        print("這個環境沒有 PyInstaller，正在安裝...")
        subprocess.check_call([python, "-m", "pip", "install", "pyinstaller"])
        pyinstaller = _find_pyinstaller()
    if pyinstaller is None:
        raise SystemExit(
            f"裝完之後 {python} 還是 import 不到 PyInstaller。\n"
            "請手動確認：\n"
            f'    "{python}" -m pip install --force-reinstall pyinstaller'
        )

    # 有 icon.png 就先轉成 icon.ico（Windows 的 exe 只吃 .ico）
    icon_png = os.path.join(HERE, "icon.png")
    icon_ico = os.path.join(HERE, "icon.ico")
    if os.path.exists(icon_png):
        try:
            from make_icon import make_icon
            make_icon(icon_png, icon_ico)
        except Exception as e:      # 圖示失敗不該擋住打包
            print(f"[提醒] 產生圖示失敗（{e}），沿用舊的 icon.ico 或 Windows 預設圖示")

    cmd = [
        *pyinstaller,
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "--name", "HoloTool",
        "--paths", ROOT,
        # 產物一律放 app\dist\，暫存與 .spec 放 app\packaging\，不弄髒最外層
        "--distpath", os.path.join(ROOT, "dist"),
        "--workpath", WORK_PATH,
        "--specpath", HERE,
        "--collect-all", "cv2",
        "--collect-submodules", "keyboard",
        "--hidden-import", "win32gui",
        "--hidden-import", "win32con",
        "--hidden-import", "win32api",
        "--hidden-import", "win32ui",
        "--hidden-import", "pythoncom",
        "--hidden-import", "pywintypes",
        "--hidden-import", "mss",
        "--hidden-import", "PIL._tkinter_finder",
        "--hidden-import", "numpy",
        "--hidden-import", "src",
        "--hidden-import", "src.bot",
        "--hidden-import", "src.config",
        "--hidden-import", "src.cardparts",
        "--hidden-import", "src.defaults_layout",
        "--hidden-import", "src.settings_layout",
        "--hidden-import", "src.calibguide",
        "--hidden-import", "src.reconcile",
        "--hidden-import", "src.version",
        "--hidden-import", "src.updater",
    ]
    if os.path.exists(icon_ico):
        cmd += ["--icon", icon_ico]

    # 把 PyInstaller 平常攤在 exe 旁邊的 _internal\ 換成 app\，
    # 這樣資料夾裡就只剩一個 HoloTool.exe 和一個 app\（校準、樣板也都放進去）。
    # --contents-directory 是 PyInstaller 6.0 才有的，舊版就維持攤平的樣子。
    if _supports_contents_directory(python):
        cmd += ["--contents-directory", BUNDLE_SUBDIR]
    else:
        print("[提醒] 這個 PyInstaller 版本太舊，沒有 --contents-directory，")
        print("       裝好之後 exe 旁邊還是會看到 _internal\\ 等資料夾。")
        print(f'       想要乾淨版請升級："{python}" -m pip install -U pyinstaller')

    cmd.append(os.path.join(PROJECT, "gui.py"))
    print("開始打包，第一次可能需要幾分鐘...")
    returncode, log_path = _run_pyinstaller(cmd)
    if returncode != 0:
        # 失敗當下再檢查一次鎖定狀況：打包途中才把 HoloTool 打開的話，
        # 開頭那次檢查是過的，但 COLLECT 階段還是會被卡住。
        locked = _locked_files(DIST_DIR)
        hint = ""
        if locked:
            hint = ("  · **dist\\HoloTool 裡有檔案正在使用中** ← 最可能就是這個。\n"
                    "    請把 HoloTool 完全關掉（含「縮成迷你視窗」那個小方塊）再重跑。\n"
                    f"    例如：{_short(locked[0])}\n")
        raise SystemExit(
            f"\nPyInstaller 失敗（結束碼 {returncode}）。\n\n"
            f"完整輸出留在：{log_path}\n"
            "最後 30 行：\n"
            "------------------------------------------------------------\n"
            f"{_tail(log_path)}"
            "------------------------------------------------------------\n\n"
            "常見原因：\n"
            f"{hint}"
            "  · 虛擬環境搬過位置 → 先跑一次 app\\packaging\\repair_venv.py\n"
            "  · 少裝套件 → \"{}\" -m pip install -r \"{}\"".format(
                python, os.path.join(PROJECT, "requirements.txt"))
        )

    exe_path = os.path.join(DIST_DIR, "HoloTool.exe")
    if not os.path.exists(exe_path):
        raise SystemExit("打包失敗：找不到 dist\\HoloTool\\HoloTool.exe")

    # 把設定/樣板放回去，避免 exe 從空白狀態開始
    # （上面 _promote_runtime_data() 已經確保 app\ 這份是最新的）
    # 注意：要放進 dist\HoloTool\app\ 而不是 exe 旁邊 —— 跟 paths.project_root()
    # 在 frozen 模式下的判斷一致，也才能讓安裝後的資料夾只看得到一個 exe。
    for folder in ("config", "card_templates", "data", "logs", "defaults"):
        src = os.path.join(ROOT, folder)
        dst = os.path.join(DATA_DIR, folder)
        if os.path.isdir(src):
            os.makedirs(dst, exist_ok=True)
            for dirpath, _dirnames, filenames in os.walk(src):
                rel = os.path.relpath(dirpath, src)
                dest_dir = dst if rel == "." else os.path.join(dst, rel)
                os.makedirs(dest_dir, exist_ok=True)
                for name in filenames:
                    s = os.path.join(dirpath, name)
                    d = os.path.join(dest_dir, name)
                    ui_markers = {
                        "table_marker.png", "ui_draw_prompt.png", "ui_congrats.png",
                        "ui_challenge.png", "ui_fail.png", "ui_poker_fail.png",
                    }
                    # defaults\ref\ 是校準用的範例圖，屬於程式資產而不是使用者資料，
                    # 每次打包都要用新的覆蓋 —— 不然改過的範例圖永遠進不到 exe 版。
                    is_reference = os.path.basename(dirpath) == "ref"
                    if os.path.isfile(s) and (not os.path.exists(d)
                                              or name in ui_markers or is_reference):
                        shutil.copy2(s, d)

    # 捷徑只放桌面。以前也會在專案根目錄放一份 HoloTool.lnk，但那會弄髒最外層。
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if not os.path.isdir(desktop):
        try:
            import ctypes.wintypes
            buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
            ctypes.windll.shell32.SHGetFolderPathW(None, 0, None, 0, buf)  # CSIDL_DESKTOP
            desktop = buf.value
        except Exception:
            desktop = ""
    if desktop and os.path.isdir(desktop):
        _create_shortcut(exe_path, os.path.join(desktop, "HoloTool.lnk"), DIST_DIR)

    print()
    print("=" * 60)
    print(f"  打包完成：v{version}")
    print()
    print("  ★ 一定要開這一個檔案，才會看到新版本：")
    print(f"    {exe_path}")
    print()
    print("  桌面捷徑可能指向別的地方（例如用 HoloToolSetup.exe 安裝到")
    print("  %LOCALAPPDATA%\\HoloTool 的那一份）。那一份不會因為重新打包")
    print("  而更新 —— 看到舊版本號時，先確認你開的是上面那個路徑。")
    print("=" * 60)
    print("設定檔與卡牌樣板會存在 exe 同一個資料夾裡。")


if __name__ == "__main__":
    main()
