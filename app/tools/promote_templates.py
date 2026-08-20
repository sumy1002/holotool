"""把「你自己在實機抓的點數/花色樣板」升級成內建預設，讓它們進得了版控。

執行方式（在專案根目錄）：
    app\\.venv\\Scripts\\python.exe app\\tools\\promote_templates.py            # 只看，不動
    app\\.venv\\Scripts\\python.exe app\\tools\\promote_templates.py --apply    # 真的複製

--------------------------------------------------------------------------
這支工具解決什麼問題

`app\\card_templates\\` 是**你的**資料，不進版控（見 .gitignore 的說明），
所以別人 clone 或安裝之後，只會拿到 `app\\defaults\\parts\\` 那批從縮圖
放大來的模糊樣板 —— 也就是「2、5、8 一直跳問號」那個狀態。

想讓別人（或你自己重灌之後）開箱就有好用的樣板，就把你抓的那些**複製一份**
到 `app\\defaults\\parts\\`。`defaults\\` 是程式內容，會進版控、會被打包進
exe、也會被放進更新包。

為什麼是「複製」而不是「搬移」：
  · `card_templates\\parts\\` 那份要留著繼續用（它才是實際比對時的來源）
  · `load_part_templates()` 靠「檔名有沒有出現在 defaults 裡」來分辨內建與
    自有。複製過去之後，同名檔案就會被重新歸類成「內建」——所以這支工具
    複製時會**改名**成 `<kind>_<key>_b<n>.png`，讓你原本的檔案繼續被視為
    自有樣板，辨識行為完全不變。

安全性：只讀 `card_templates\\parts\\`、只寫 `defaults\\parts\\`，
不刪任何東西，同名檔案預設跳過（要覆蓋請加 --overwrite）。
"""
from __future__ import annotations

import argparse
import os
import shutil

import _bootstrap  # noqa: F401

from src.cardparts import parse_part_name
from src.paths import default_parts_dir, parts_dir, project_root

# 複製過去時用的檔名樣式：rank_2_b1.png、suit_S_b3.png
# 用 b（bundled）開頭的編號跟你原本的 `_1 _2 _3` 區隔開，
# 才不會在下一次抓樣板時撞名。
PROMOTED_SUFFIX = "b"


def own_templates(src: str, bundled: str) -> list[str]:
    """列出「你自己抓的」樣板檔名。

    判斷標準跟 `cardparts.load_part_templates()` 完全一致：
    檔名沒有出現在 defaults\\parts\\ 裡的，就是你自己抓的。
    """
    if not os.path.isdir(src):
        return []
    bundled_names = set()
    if os.path.isdir(bundled):
        bundled_names = {n for n in os.listdir(bundled) if n.lower().endswith(".png")}
    out = []
    for name in sorted(os.listdir(src)):
        if parse_part_name(name) is None:
            continue
        if name not in bundled_names:
            out.append(name)
    return out


def _target_name(bundled: str, kind: str, key: str) -> str:
    i = 1
    while os.path.exists(os.path.join(bundled, f"{kind}_{key}_{PROMOTED_SUFFIX}{i}.png")):
        i += 1
    return f"{kind}_{key}_{PROMOTED_SUFFIX}{i}.png"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="把自己抓的點數/花色樣板複製到 defaults\\parts（進版控用）")
    parser.add_argument("--apply", action="store_true",
                        help="真的複製（不加這個參數只會列出會做什麼）")
    parser.add_argument("--overwrite", action="store_true",
                        help="同名檔案也覆蓋（預設跳過）")
    args = parser.parse_args()

    src = parts_dir()
    bundled = default_parts_dir()
    print(f"專案根目錄：{project_root()}")
    print(f"你的樣板  ：{src}")
    print(f"內建樣板  ：{bundled}")
    print()

    mine = own_templates(src, bundled)
    if not mine:
        print("目前 card_templates\\parts\\ 裡**沒有**你自己抓的樣板"
              "（全部都是內建那批的複本）。")
        print()
        print("要先在 GUI 的「點數/花色樣板」分頁把牌抓起來，再回來跑這支工具。")
        print("提醒：先把四個花色重新存一次 —— 之前有個 bug 讓花色一張都存不進去。")
        return

    by_label: dict[tuple[str, str], list[str]] = {}
    for name in mine:
        parsed = parse_part_name(name)
        if parsed:
            by_label.setdefault(parsed, []).append(name)

    print(f"找到 {len(mine)} 個你自己抓的樣板，涵蓋 {len(by_label)} 個標籤：")
    for (kind, key), names in sorted(by_label.items()):
        print(f"  {kind}_{key:<3} {len(names)} 張")
    print()

    if not args.apply:
        print("這是預演，什麼都沒有動。確認沒問題後加 --apply 再跑一次。")
        return

    os.makedirs(bundled, exist_ok=True)
    copied = 0
    for (kind, key), names in sorted(by_label.items()):
        for name in names:
            target = _target_name(bundled, kind, key)
            dest = os.path.join(bundled, target)
            if os.path.exists(dest) and not args.overwrite:
                continue
            shutil.copy2(os.path.join(src, name), dest)
            print(f"  {name}  →  defaults/parts/{target}")
            copied += 1

    print()
    print(f"完成，複製了 {copied} 個檔案到 defaults\\parts\\。")
    print()
    print("接下來：")
    print("  1. git add app/defaults/parts && git commit -m \"更新內建樣板\"")
    print("  2. 下一次 make_release.py 產生的更新包就會帶著這批樣板")
    print("  3. 別人裝好之後，install_default_parts(overwrite=False) 會複製到")
    print("     他的 card_templates\\parts\\ —— 不會蓋掉他自己抓的任何東西")


if __name__ == "__main__":
    main()
