"""讀取／修改 `app\\src\\version.py` 的 `__version__`。

這支工具存在的理由很簡單：手動編輯那一行太容易出錯 ——
改了忘記存、存了但打包的是別的資料夾、或是被 git 的 discard changes 還原回去，
結果「怎麼打包都是舊版本」。交給腳本做就不會有這些事。

用法：
    python bump_version.py --print          只印出目前版本（給批次檔用）
    python bump_version.py --bump           修訂號 +1，印出新版本
    python bump_version.py --set 1.3.0      直接指定
    python bump_version.py --bump --minor   次版本 +1、修訂號歸零

`--print` / `--bump` / `--set` 的標準輸出**只有版本號一行**，方便批次檔用
`for /f` 接。其他訊息一律走 stderr。
"""
from __future__ import annotations

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # app\packaging\
ROOT = os.path.dirname(HERE)                        # app\
VERSION_FILE = os.path.join(ROOT, "src", "version.py")

# 行首必須是 __version__，不能有前導空白 —— version.py 的說明文字裡有一行
# 縮排過的 `    __version__ = "1.0.1"` 當範例，那一行絕對不能被改到。
PATTERN = re.compile(r'^(__version__\s*=\s*")([^"]*)(")', re.MULTILINE)


def read_text(path: str = VERSION_FILE) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def current_version(text: str) -> str:
    matches = PATTERN.findall(text)
    if len(matches) != 1:
        raise SystemExit(
            f"在 {VERSION_FILE} 裡找到 {len(matches)} 個 __version__ 定義，"
            "預期剛好 1 個。請先手動確認這個檔案。"
        )
    return matches[0][1]


def next_version(version: str, part: str = "patch") -> str:
    bits = version.split(".")
    while len(bits) < 3:
        bits.append("0")
    try:
        major, minor, patch = (int(b) for b in bits[:3])
    except ValueError:
        raise SystemExit(f"目前的版本號 {version!r} 不是 x.y.z 格式，無法自動加一。")
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def write_version(new: str, path: str = VERSION_FILE) -> str:
    text = read_text(path)
    old = current_version(text)
    updated, count = PATTERN.subn(lambda m: m.group(1) + new + m.group(3), text, count=1)
    if count != 1:
        raise SystemExit("改寫 __version__ 失敗，檔案沒有被動到。")
    # 先寫暫存檔再換掉，中途出錯不會留下半截的 version.py
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(updated)
    os.replace(tmp, path)
    print(f"__version__ {old} -> {new}", file=sys.stderr)
    return new


def main() -> None:
    parser = argparse.ArgumentParser(description="讀取／修改 __version__")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--print", action="store_true", dest="show",
                       help="只印出目前版本")
    group.add_argument("--bump", action="store_true", help="版本號加一")
    group.add_argument("--set", dest="explicit", help="直接指定版本號")
    parser.add_argument("--minor", action="store_true", help="搭配 --bump：次版本 +1")
    parser.add_argument("--major", action="store_true", help="搭配 --bump：主版本 +1")
    args = parser.parse_args()

    if args.show:
        print(current_version(read_text()))
        return

    if args.explicit:
        if not re.fullmatch(r"\d+\.\d+\.\d+", args.explicit):
            raise SystemExit(f"{args.explicit!r} 不是 x.y.z 格式。")
        print(write_version(args.explicit))
        return

    part = "major" if args.major else ("minor" if args.minor else "patch")
    print(write_version(next_version(current_version(read_text()), part)))


if __name__ == "__main__":
    main()
