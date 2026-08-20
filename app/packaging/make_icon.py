"""把一張圖片轉成 Windows 用的 icon.ico（多尺寸）。

執行方式（在專案根目錄）：
    .venv\\Scripts\\python.exe app\\packaging\\make_icon.py            # 讀 app\\packaging\\icon.png
    .venv\\Scripts\\python.exe app\\packaging\\make_icon.py 別的圖.png

產出 app\\packaging\\icon.ico，之後 build_exe.py 與安裝程式都會自動抓來用。

為什麼要多尺寸：Windows 在不同地方用不同大小的圖示（工作列 16/24、
桌面 48、大圖示檢視 256）。只塞一個 256 的話，縮到 16 會糊成一團。
"""
from __future__ import annotations

import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SOURCE = os.path.join(HERE, "icon.png")
OUTPUT = os.path.join(HERE, "icon.ico")

# Windows 會用到的尺寸，全部塞進同一個 .ico
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _square(img: Image.Image) -> Image.Image:
    """補成正方形（置中，四周透明）。非正方形直接縮放會變形。"""
    if img.width == img.height:
        return img
    side = max(img.width, img.height)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    return canvas


def make_icon(source: str = DEFAULT_SOURCE, output: str = OUTPUT) -> str:
    if not os.path.exists(source):
        raise SystemExit(
            f"找不到圖檔：{source}\n"
            "請把要當圖示的圖片存成 icon.png 放在 app\\packaging\\ 資料夾，再執行一次。"
        )
    img = Image.open(source).convert("RGBA")
    img = _square(img)
    # 先放大到 256 再往下縮，比從原圖各縮一次乾淨
    if img.width != 256:
        img = img.resize((256, 256), Image.LANCZOS)
    img.save(output, format="ICO", sizes=SIZES)
    print(f"已產生 {output}（{len(SIZES)} 種尺寸，來源 {os.path.basename(source)}）")
    return output


if __name__ == "__main__":
    make_icon(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SOURCE)
