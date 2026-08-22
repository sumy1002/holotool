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
import json
import os
import subprocess
import sys
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))   # app\packaging\
ROOT = os.path.dirname(HERE)                        # app\
PROJECT = os.path.dirname(ROOT)                     # 最外層
DIST = os.path.join(ROOT, "dist")
BUNDLE_DIR = os.path.join(DIST, "HoloTool")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.updater import PATCH_MARKER, PROTECTED_DIRS, SENTINEL   # noqa: E402
from src.version import parse_patch_asset_name                   # noqa: E402
from src.version import (                                        # noqa: E402
    __version__,
    asset_name,
    patch_asset_name,
    version_tuple,
    TAG_PREFIX,
)

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



# --------------------------------------------------------------- 差分更新包

# 差分包大於整包的這個比例時就不要產了。
#
# 差分的意義是「別再重抓那 70 MB 一模一樣的相依套件」。萬一某一版真的動到
# opencv / numpy（升級套件、換 Python 版本），差分包會跟整包差不多大，
# 那時候多上傳一個檔只是多佔空間、也多一條會出錯的路。
PATCH_MAX_RATIO = 0.6


def _zip_fingerprints(path: str) -> dict:
    """讀 zip 的中央目錄，回傳 {內部路徑: (CRC32, 原始大小)}。

    **不解壓、不算 SHA256。** CRC32 + 大小是 zip 本來就存好的，讀一個 76 MB
    的更新包只要幾毫秒。這裡要回答的問題是「我自己這兩次打包，這個檔一不一樣」，
    不是防篡改 —— 整包的 SHA256 另外算，那個才是給使用者驗的。
    """
    out = {}
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if _skip(name):
                continue
            out[name] = (info.CRC, info.file_size)
    return out


def _dir_fingerprints(root: str) -> dict:
    """同上，但對象是一個「上一版的安裝資料夾」。

    `release.bat` 每次發版前都會把當時的 build 原封複製到
    `<磁碟>\holotool-test-<舊版本>`（那是拿來測自動更新的「舊版」），
    所以就算 `app\dist\` 裡的舊 zip 被清掉了，通常還有這一份可以當基準。

    **一定要套用跟打包相同的排除規則** —— 那個資料夾裡有使用者的
    `config\`、`card_templates\`、`logs\`，不濾掉的話會被算成「有變動」
    而塞進差分包，然後 `_assert_no_user_data()` 直接讓發版失敗。
    """
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in EXCLUDE_DIRS]
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            if _skip(rel):
                continue
            crc = 0
            try:
                with open(full, "rb") as f:
                    for block in iter(lambda: f.read(1 << 20), b""):
                        crc = zlib.crc32(block, crc)
                size = os.path.getsize(full)
            except OSError:
                continue
            out[rel] = (crc, size)
    return out


def _previous_bundles(out_dir: str) -> list:
    """所有可以當「上一版」的東西，回傳 [(版本, 讀取函式, 路徑)]。

    兩種來源，缺一不可：

    * `app\dist\HoloTool-<版本>.zip` —— 上一次發版的整包。最準。
    * `<磁碟>\holotool-test-<版本>\` —— `release.bat` 發版前留的那份 build。
      **舊 zip 很佔空間（一個 76 MB），被清掉是很正常的事**，
      這一條就是為那種情況準備的。
    """
    found = []
    if os.path.isdir(out_dir):
        for name in os.listdir(out_dir):
            if not (name.startswith("HoloTool-") and name.endswith(".zip")):
                continue
            if parse_patch_asset_name(name) is not None:   # 差分包不是基準線
                continue
            found.append((name[len("HoloTool-"):-len(".zip")],
                          _zip_fingerprints, os.path.join(out_dir, name)))

    # release.bat 的測試副本放在專案所在磁碟的根目錄旁邊
    neighbours = os.path.dirname(PROJECT) or PROJECT
    prefix = "holotool-test-"
    try:
        entries = os.listdir(neighbours)
    except OSError:
        entries = []
    for name in entries:
        full = os.path.join(neighbours, name)
        if name.lower().startswith(prefix) and os.path.isdir(full):
            found.append((name[len(prefix):], _dir_fingerprints, full))
    return found


def _previous_bundle(out_dir: str, version: str):
    """挑版本比現在小、而且最大的那一個。同版本時優先用 zip（比較準也比較快）。"""
    best = None
    for other, reader, path in _previous_bundles(out_dir):
        if version_tuple(other) >= version_tuple(version):
            continue
        if best is None or version_tuple(other) > version_tuple(best[0]) or (
                version_tuple(other) == version_tuple(best[0])
                and reader is _zip_fingerprints):
            best = (other, reader, path)
    return best


def build_patch_zip(out_dir: str, version: str, full_zip: str):
    """只把「跟上一版不同的檔案」壓成一包。沒有上一版或不划算時回傳 None。

    **刪除的檔案表達不出來** —— 置換用的 robocopy 本來就沒有 /PURGE，
    整包更新也不會刪任何東西，所以兩邊行為一致，不算新的缺口。
    """
    previous = _previous_bundle(out_dir, version)
    if previous is None:
        print(f"\n（找不到任何更早的版本可以當基準，這一版只出整包。"
              f"\n  找過：{out_dir}\\HoloTool-*.zip"
              f"\n  　　　{os.path.dirname(PROJECT)}\\holotool-test-*\\"
              f"\n  下一版起就有基準可以做差分了 —— 別把這兩個地方都清掉。）")
        return None
    base, reader, base_path = previous
    print(f"\n差分基準：{base_path}")
    old = reader(base_path)
    new = _zip_fingerprints(full_zip)
    changed = sorted(name for name, fp in new.items() if old.get(name) != fp)
    if not changed:
        print(f"\n（跟 {base} 相比一個檔案都沒變，不產差分包）")
        return None

    zip_path = os.path.join(out_dir, patch_asset_name(version, base))
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(full_zip) as src, \
            zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name in changed:
            zf.writestr(name, src.read(name))
        zf.writestr(PATCH_MARKER, json.dumps(
            {"version": version, "base": base}, ensure_ascii=False))

    full_size = os.path.getsize(full_zip)
    size = os.path.getsize(zip_path)
    if full_size and size > full_size * PATCH_MAX_RATIO:
        os.remove(zip_path)
        print(f"\n（跟 {base} 相比變動太多：差分 {size / 1048576:.1f} MB vs "
              f"整包 {full_size / 1048576:.1f} MB，不划算，這一版只出整包）")
        return None

    _assert_no_user_data(zip_path)
    saved = f"，省下 {100 - size * 100 / full_size:.0f}%" if full_size else ""
    print(f"\n差分更新包（從 {base} 升上來）：{len(changed)} 個檔案有變動，"
          f"{size / 1048576:.1f} MB —— 整包是 {full_size / 1048576:.1f} MB{saved}")
    for name in changed[:12]:
        print(f"    {name}")
    if len(changed) > 12:
        print(f"    …（還有 {len(changed) - 12} 個）")
    return zip_path


# ------------------------------------------------------------ 舊產物清理
#
# 每發一版就多一個 76 MB 的整包 zip；release.bat 又會在磁碟根目錄留一份
# `holotool-test-<版本>\`（約 120 MB 的完整 build，拿來測自動更新用）。
# 都不清的話，發 20 版就是好幾 GB 的死資料。
#
# 差分功能只需要「最近的上一版」當基準，所以各保留最近 KEEP_RELEASES 份
# 就綽綽有餘 —— 再舊的基準做出來的差分也不會被任何人用到
# （updater 只認「基底 == 目前安裝版本」的差分包）。
KEEP_RELEASES = 2


def prune_old_releases(out_dir: str, keep: int = KEEP_RELEASES) -> list[str]:
    """刪掉太舊的整包 / 差分 zip（連同 .sha256），回傳刪掉的檔名。

    規則：整包 zip 依版本新→舊排序，保留前 `keep` 個；差分包只保留
    「目標版本還在保留名單裡」的那些。剛打包出來的一定是最新版，永遠在名單裡。
    """
    if keep < 1 or not os.path.isdir(out_dir):
        return []
    fulls: list[tuple[tuple, str]] = []
    patches: list[tuple[str, str]] = []       # (目標版本, 檔名)
    for name in os.listdir(out_dir):
        if not (name.startswith("HoloTool-") and name.lower().endswith(".zip")):
            continue
        parsed = parse_patch_asset_name(name)
        if parsed is not None:
            patches.append((parsed[0], name))
        else:
            fulls.append((version_tuple(name[len("HoloTool-"):-len(".zip")]), name))

    fulls.sort(reverse=True)
    kept_versions = {v for v, _n in fulls[:keep]}
    victims = [n for _v, n in fulls[keep:]]
    victims += [n for target, n in patches
                if version_tuple(target) not in kept_versions]

    removed: list[str] = []
    for name in victims:
        for path in (os.path.join(out_dir, name),
                     os.path.join(out_dir, name + ".sha256")):
            try:
                if os.path.exists(path):
                    os.remove(path)
                    removed.append(os.path.basename(path))
            except OSError:
                pass
    return removed


def prune_test_copies(keep: int = KEEP_RELEASES,
                      neighbours: str | None = None) -> list[str]:
    """刪掉太舊的 `holotool-test-<版本>\\` 資料夾，回傳刪掉的資料夾名。

    三道保險，寧可少刪不可誤刪：
      · 名稱必須是 `holotool-test-` 開頭而且版本號解析得出來
      · 裡面必須真的有 HoloTool.exe（確定是一份 build，不是別人的資料夾）
      · 依版本新→舊排序，保留前 `keep` 個
    """
    if keep < 1:
        return []
    base = neighbours if neighbours is not None else (os.path.dirname(PROJECT) or PROJECT)
    prefix = "holotool-test-"
    found: list[tuple[tuple, str]] = []
    try:
        entries = os.listdir(base)
    except OSError:
        return []
    for name in entries:
        if not name.lower().startswith(prefix):
            continue
        full = os.path.join(base, name)
        if not os.path.isdir(full):
            continue
        parsed = version_tuple(name[len(prefix):])
        if parsed == (0, 0, 0):
            continue                        # 版本號看不懂的不敢動
        if not os.path.exists(os.path.join(full, SENTINEL)):
            continue                        # 不是 HoloTool 的 build，不敢動
        found.append((parsed, full))

    found.sort(reverse=True)
    removed: list[str] = []
    import shutil
    for _version, full in found[keep:]:
        try:
            shutil.rmtree(full)
            removed.append(os.path.basename(full))
        except OSError:
            pass
    return removed


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
    parser.add_argument("--no-prune", action="store_true",
                        help="不要清掉舊版的 zip 與 holotool-test-* 測試資料夾")
    parser.add_argument("--keep-releases", type=int, default=KEEP_RELEASES,
                        help=f"保留最近幾版的 zip 與測試資料夾（預設 {KEEP_RELEASES}）")
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

    patch_path = build_patch_zip(args.out, version, zip_path)
    patch_sha = write_sha256(patch_path) if patch_path else ""

    if not args.no_prune:
        # 清舊產物一定要排在差分包**做完之後** —— 差分要拿上一版的 zip 當基準。
        gone = prune_old_releases(args.out, keep=max(1, args.keep_releases))
        gone += [n + "\\" for n in prune_test_copies(keep=max(1, args.keep_releases))]
        if gone:
            print(f"\n已清掉 {len(gone)} 個舊版產物（各保留最近 "
                  f"{max(1, args.keep_releases)} 版；不想清就加 --no-prune）：")
            for name in gone:
                print(f"    {name}")

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
    if patch_path:
        print(f"差分更新包  ：{patch_path}   "
              f"({os.path.getsize(patch_path) / (1024 * 1024):.1f} MB)")
        print(f"差分 SHA256 ：{patch_sha}")
    if setup:
        print(f"安裝檔      ：{setup}")
    print()
    print("接下來在 GitHub 上發布這一版（網頁操作，不用打指令）：")
    print("  1. 進 repo → 右邊 Releases → Draft a new release")
    print(f"  2. Choose a tag → 輸入 {tag} → Create new tag on publish")
    print(f"  3. Release title 填 {tag}，說明寫這一版改了什麼")
    print("  4. 把下面這些檔案拖進 Attach binaries 區塊：")
    print(f"       {os.path.basename(zip_path)}")
    print(f"       {os.path.basename(zip_path)}.sha256")
    if patch_path:
        print(f"       {os.path.basename(patch_path)}          ← 差分包，"
              "上一版的人只會下載這個")
        print(f"       {os.path.basename(patch_path)}.sha256")
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
